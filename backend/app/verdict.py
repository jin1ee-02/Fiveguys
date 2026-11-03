"""Heavy path: read the room's Core State, then a debate between independent agent objects, a judge panel
and adaptive stopping.

Each debater and each judge is its own object with its own instruction, model handle and private memory
(a debater's strategies, a judge's previous judgment). They never call each other: they read the public
DebateBoard and the Moderator decides who speaks next, as in ChatEval / AgenticSimLaw.
"""

import contextvars
import json
import math
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from app import core_state
from app.prompts import DEBATE_PROMPT, JUDGE_PROMPT, PROMPT_VERSION
from app.provider import ModelOutputError
from app.runtime import usage_log, usage_total
from app.schemas import CoreState, DebateEntry, DebateOutput, JudgeOutput, PanelPoint, PanelScore, RoundTrace, VerdictResult

# ChatEval: agents with the same role prompt add nothing over a single agent; diversity is what helps,
# with 3-4 roles as the sweet spot. Each judge scores the full rubric but scrutinises one angle.
JUDGE_PERSONAS = {
    "logic": "논리 심사: 주장의 일관성, 추론의 비약, 일반화 여부를 특히 꼼꼼히 봅니다.",
    "empathy": "공감 심사: 표현의 존중과 절제, 상대 입장을 인정했는지를 특히 꼼꼼히 봅니다.",
    "evidence": "근거 심사: 대화에서 확인되는 근거와 한쪽 주장만 있는 내용의 구별을 특히 꼼꼼히 봅니다.",
}
VOTES = ("B", "even", "A")  # ordered outcome scale of one judge
ITEMS = ("logic", "emotionControl", "evidence")

# Speaking order inside a round: claim -> rebuttal -> internal fact check, then the judge panel.
ROLES = {
    "prosecutor": "A가 실제로 한 말을 근거로 A의 불만과 주장을 제3자로서 정리하세요('저는', '제가'로 말하지 않고 'A는 ...라고 말했습니다'). B의 타당한 지적은 인정하세요.",
    "defense": "B가 실제로 한 말을 근거로 B의 상황과 주장을 제3자로서 정리하세요('저는', '제가'로 말하지 않고 'B는 ...라고 말했습니다'). A의 타당한 지적은 인정하세요.",
    "factcheck": "양쪽 주장과 대화 내부 근거를 대조하고 주장/확인된 대화/누락 정보를 구별하세요.",
}


def vote(belief):
    return "A" if belief > 0.55 else "B" if belief < 0.45 else "even"


def ks_distance(before, after):
    """Kolmogorov-Smirnov statistic between two rounds' vote distributions over the ordered scale B < even < A."""
    distance = cumulative_before = cumulative_after = 0.0
    for key in VOTES:
        cumulative_before += before.get(key, 0) / max(1, sum(before.values()))
        cumulative_after += after.get(key, 0) / max(1, sum(after.values()))
        distance = max(distance, abs(cumulative_before - cumulative_after))
    return round(distance, 4)


def aggregate(judgments):
    """ChatEval aggregation: average the scores. The text comes from the judge closest to the panel mean."""
    belief = sum(j.belief for j in judgments) / len(judgments)
    chair = min(judgments, key=lambda j: abs(j.belief - belief)).model_copy(deep=True)
    for name in ("plaintiff", "defendant"):
        for key in ("logic", "emotionControl", "evidence"):
            score = lambda j: getattr(getattr(j, name), key)
            average = round(sum(map(score, judgments)) / len(judgments), 1)
            # The reason shown beside an averaged score is the one written by the judge who scored closest to it.
            nearest = min(judgments, key=lambda j: abs(score(j) - average))
            setattr(getattr(chair, name), key + "Reason", getattr(getattr(nearest, name), key + "Reason"))
            setattr(getattr(chair, name), key, average)
    chair.belief = round(belief, 3)
    chair.unresolved = sum(j.unresolved for j in judgments) * 2 > len(judgments)
    return chair


def score_vector(judgment):
    return [getattr(side, key) for side in [judgment.plaintiff, judgment.defendant] for key in ["logic", "emotionControl", "evidence"]]


valid_indices = core_state.valid_indices


def validate_judge(judgment, mode):
    if any(not math.isfinite(value) or not 0 <= value <= 100 for value in score_vector(judgment)):
        raise ModelOutputError("판결 점수 범위가 유효하지 않습니다.")
    if not math.isfinite(judgment.belief) or not 0 <= judgment.belief <= 1:
        raise ModelOutputError("판사 belief 범위가 유효하지 않습니다.")
    for side in [judgment.plaintiff, judgment.defendant]:
        if any(not value.strip() or len(value) > 300 for value in [side.strength, side.improvement, side.logicReason, side.emotionControlReason, side.evidenceReason]):
            raise ModelOutputError("판결 항목 설명이 유효하지 않습니다.")
    if any(not value.strip() or len(value) > 700 for value in [judgment.summary, judgment.recommendation]) or len(judgment.humor) > 300:
        raise ModelOutputError("판결 설명이 유효하지 않습니다.")
    if mode == "UFC":
        judgment.humor = ""
    # A judge's lean may not point away from its own scores: gpt-4o-mini reports about 0.7 towards A whoever scored higher.
    scores = score_vector(judgment)
    gap = (sum(scores[:3]) - sum(scores[3:])) / 600
    if abs(gap) >= 0.05 and gap * (judgment.belief - 0.5) <= 0:
        judgment.belief = round(0.5 + gap, 3)
    return judgment


SHIFT_LIMIT = 15  # how far the debate may take one judge's item from its pre-debate score
UNAPPLIED = " (점수 변화와 방향이 달라 반영하지 않음)"
REPEATED = " (같은 방향으로 이미 반영해 다시 반영하지 않음)"


def note(point, text):
    point.reason = point.reason[:200 - len(text)] + text


def check_points(judgment, previous, spoken, count, initial=None, applied=frozenset()):
    """The debate is the only way a score moves after round 0, and code holds it to three rules.
    An item keeps the judge's previous score (and reason) unless a point the judge accepted with message evidence
    moves it that way; each judge moves an item in one direction once per verdict (`applied`), so a debater
    repeating an accepted point cannot ratchet a score up round after round; and the debate moves an item at most
    SHIFT_LIMIT from the judge's own pre-debate score (`initial`). gpt-4o-mini re-reads items without any point,
    moves them against its own ruling, and accepts the same advocacy every round."""
    if not spoken <= {p.role for p in judgment.debatePoints}:
        raise ModelOutputError("심사위원이 토론자의 발언을 모두 검토하지 않았습니다.")
    moves = {}
    for p in judgment.debatePoints:
        if not p.claim.strip() or len(p.claim) > 200 or not p.reason.strip() or len(p.reason) > 200 or not valid_indices(p.evidence_indices, count):
            raise ModelOutputError("토론 논점 검토가 유효하지 않습니다.")
        if p.accepted and not p.evidence_indices:
            raise ModelOutputError("메시지 근거가 없는 논점은 채택할 수 없습니다.")
        if p.accepted and p.direction != "none":
            moves.setdefault((p.side, p.item), []).append(p)
    if previous is None:
        return judgment
    for side, name in (("A", "plaintiff"), ("B", "defendant")):
        now, before = getattr(judgment, name), getattr(previous, name)
        for item in ITEMS:
            points = moves.get((side, item), [])
            for p in points:
                if (side, item, p.direction) in applied:
                    note(p, REPEATED)
            fresh = {p.direction for p in points if (side, item, p.direction) not in applied}
            change = getattr(now, item) - getattr(before, item)
            if change == 0 or change > 0 and "up" in fresh or change < 0 and "down" in fresh:
                if initial is not None:
                    start = getattr(getattr(initial, name), item)
                    setattr(now, item, max(start - SHIFT_LIMIT, min(start + SHIFT_LIMIT, getattr(now, item))))
                continue
            setattr(now, item, getattr(before, item))
            setattr(now, item + "Reason", getattr(before, item + "Reason"))
            for p in points:
                if (side, item, p.direction) not in applied:
                    note(p, UNAPPLIED)
    return judgment


def moved(judgment, previous):
    """(side, item, direction) of every item this judgment moved from the previous one."""
    return {(side, item, "up" if getattr(getattr(judgment, name), item) > getattr(getattr(previous, name), item) else "down")
            for side, name in (("A", "plaintiff"), ("B", "defendant")) for item in ITEMS
            if getattr(getattr(judgment, name), item) != getattr(getattr(previous, name), item)}


def twice(operation):
    """A reply that breaks a length or index rule is asked for once more, so one of ~19 calls cannot sink the verdict."""
    try:
        return operation()
    except ModelOutputError:
        return operation()


def trajectory(messages):
    """Conflict trajectory of the Core State: each message's conflict score, read from the stored Light results."""
    return [{"index": i, "speaker": m["speaker"], "conflict": m["result"]["data"]["raw_conflict"]} for i, m in enumerate(messages)]


def build_core_state(request, snapshot, provider, previous=None):
    """Rebuild the Core State for this snapshot (used when the room's stored one is missing or stale)."""
    output = core_state.build(provider, snapshot["messages"], snapshot["relationship"], previous, request.context)
    return CoreState(**output.model_dump(), trajectory=snapshot.get("trajectory", []))


# ── Agents ──────────────────────────────────────────────────────────────────────

class DebateBoard:
    """The only thing agents share: public utterances. Strategies are never posted here."""

    def __init__(self):
        self.log: list[DebateEntry] = []

    def post(self, entry):
        self.log.append(entry)

    def transcript(self, round_number):
        """What a debater may read before speaking: public debate text of this and the previous round."""
        return [{"role": e.role, "round": e.round, "text": e.text} for e in self.log if e.role != "judge" and e.round >= round_number - 1]

    def opinions(self, round_number):
        """What a judge reads: this round's public debate (with evidence numbers)."""
        return [{"role": e.role, "text": e.text, "evidence_indices": e.evidenceIndices} for e in self.log if e.round == round_number and e.role != "judge"]


class DebaterAgent:
    """One debater (prosecutor / defense / factcheck). Keeps its own strategy private between rounds."""

    def __init__(self, role, provider):
        self.role, self.instruction, self.provider = role, ROLES[role], provider
        self.strategy = None  # private memory: only this agent ever sees it again

    def speak(self, case, board, round_number, phase, count, feedback=None):
        turn = {"role": self.role, "round": round_number, "phase": phase, "instruction": self.instruction, "transcript": board.transcript(round_number), "own_previous_strategy": self.strategy, "panel_feedback": feedback or []}

        def call():
            result = self.provider.structured(DEBATE_PROMPT, turn, DebateOutput, 1100, shared=case)
            if not result.text.strip() or len(result.text) > 700 or len(result.strategy) > 400 or not valid_indices(result.evidence_indices, count):
                raise ModelOutputError("토론 발언 또는 근거 번호가 유효하지 않습니다.")
            return result
        result = twice(call)
        self.strategy = result.strategy
        return DebateEntry(role=self.role, round=round_number, text=result.text, evidenceIndices=result.evidence_indices, strategy=result.strategy)


class JudgeAgent:
    """One panel judge. Sees the public debate and its own previous judgment, never the other judges.
    Round 0 scores the utterances alone; later rounds rule on each debater's points and adjust from there."""

    def __init__(self, persona, provider):
        self.persona, self.instruction, self.provider = persona, JUDGE_PERSONAS[persona], provider
        self.previous = None  # private memory
        self.initial = None  # round-0 judgment, the anchor the debate may move away from
        self.applied = set()  # (side, item, direction) moves already made in this verdict
        self.last_moves = set()  # moves made in the latest round

    def judge(self, case, board, round_number, mode, count):
        opinions = board.opinions(round_number)
        spoken = {o["role"] for o in opinions}
        turn = {"round": round_number, "persona": self.instruction, "opinions": opinions, "previous_judgment": self.previous.model_dump(exclude={"debatePoints"}) if self.previous else None}
        def call():
            judgment = self.provider.structured(JUDGE_PROMPT, turn, JudgeOutput, 3000, shared=case)
            # Points first: the lean is then turned to match the scores as they stand after reverts and limits.
            return validate_judge(check_points(judgment, self.previous, spoken, count, self.initial, self.applied), mode)
        judgment = twice(call)
        if self.previous is None:
            self.initial = judgment.model_copy(deep=True)
        else:
            self.last_moves = moved(judgment, self.previous)
            self.applied |= self.last_moves
        self.previous = judgment
        return judgment

    def feedback(self):
        """What the debaters may learn from this judge: its current scores and how it ruled on the last points."""
        j = self.previous
        scores = {side: {k: getattr(getattr(j, name), k) for k in ITEMS} for side, name in (("A", "plaintiff"), ("B", "defendant"))}
        return {"judge": self.instruction.split(":")[0], "scores": scores, "belief": j.belief,
                "rulings": [{"role": p.role, "claim": p.claim, "accepted": p.accepted, "reason": p.reason} for p in j.debatePoints]}


class Moderator:
    """Runs the verdict: decides who speaks, collects the panel, stops when the panel stops moving.
    It is the only component that talks to every agent; agents only read the board."""

    def __init__(self, provider, judges=None, max_rounds=None):
        self.provider = provider
        count = judges or max(1, min(len(JUDGE_PERSONAS), int(os.getenv("VERDICT_JUDGES", "3"))))
        self.debaters = {role: DebaterAgent(role, provider) for role in ROLES}
        self.judges = [JudgeAgent(persona, provider) for persona in list(JUDGE_PERSONAS)[:count]]
        self.max_rounds = max_rounds or max(1, min(3, int(os.getenv("VERDICT_MAX_ROUNDS", "3"))))
        self.board = DebateBoard()

    def order(self, round_number):
        # One-by-one speaking has an order bias (ChatEval), so the first speaker alternates.
        return ["prosecutor", "defense"][:: 1 if round_number % 2 else -1] + ["factcheck"]

    def phase(self, round_number):
        # AgenticSimLaw phases.
        return "opening" if round_number == 1 else "closing" if round_number == self.max_rounds else "rebuttal"

    def run(self, request, snapshot, calls=0):
        score_limit = float(os.getenv("VERDICT_STABILITY_DELTA", "5"))
        belief_limit = float(os.getenv("VERDICT_BELIEF_DELTA", "0.1"))
        ks_limit = float(os.getenv("VERDICT_KS_DELTA", "0.34"))
        usage = []
        usage_token = usage_log.set(usage)
        count = len(snapshot["messages"])
        # Identical for every call of this verdict, so it forms the cacheable prompt prefix.
        # The judges score each person's own words, so those are listed per speaker next to the full log.
        said = {speaker: [m["text"] for m in snapshot["messages"] if m["speaker"] == speaker] for speaker in "AB"}
        case = {"case": {"messages": snapshot["messages"], "utterances": said, "relationship": snapshot["relationship"], "context": request.context, "mode": request.mode, "conflictTemperature": snapshot["temperature"], "core_state": snapshot.get("core")}}
        trace, panel = [], {}
        try:
            # Round 0: the panel scores the utterances before anyone argues, so every later change can be traced to the debate.
            previous = self.panel_round(case, 0, request.mode, count, trace, None)
            initial = {side: {k: getattr(getattr(previous, name), k) for k in ITEMS} for side, name in (("A", "plaintiff"), ("B", "defendant"))}
            calls += len(self.judges)
            for round_number in range(1, self.max_rounds + 1):
                # Debaters argue against the panel's current scores and its rulings on their last points.
                feedback = [agent.feedback() for agent in self.judges]
                for role in self.order(round_number):
                    self.board.post(self.debaters[role].speak(case, self.board, round_number, self.phase(round_number), count, feedback))
                judgment = self.panel_round(case, round_number, request.mode, count, trace, previous)
                panel = {agent.persona: agent.previous for agent in self.judges}
                calls += len(self.debaters) + len(self.judges)
                # Adaptive stopping (Adaptive Stability Detection): stop when the distribution of judgments stopped moving.
                last = trace[-1]
                stable = not judgment.unresolved and not previous.unresolved and last.scoreDelta <= score_limit and last.beliefDelta <= belief_limit and last.ksDelta <= ks_limit
                if stable:
                    break
                previous = judgment
        finally:
            usage_log.reset(usage_token)
        panel_scores = [PanelScore(persona=persona, belief=j.belief, plaintiff={k: getattr(j.plaintiff, k) for k in ITEMS}, defendant={k: getattr(j.defendant, k) for k in ITEMS}) for persona, j in panel.items()]
        return VerdictResult(**judgment.model_dump(), initialScores=initial, verdictId=str(uuid.uuid4()), mode=request.mode, debateLog=self.board.log, rounds=round_number, stopReason="stable" if stable else "max_rounds", panelScores=panel_scores, snapshotVersion=snapshot["version"], provider=f"{getattr(self.provider, 'vendor', 'openai')}/{self.provider.model}", promptVersion=PROMPT_VERSION, coreState=snapshot.get("core"), roundTrace=trace, modelCalls=calls, judges=len(self.judges), usage=usage_total(usage))

    def panel_round(self, case, round_number, mode, count, trace, previous):
        """All judges score this round in parallel; the round is logged and its panel mean returned."""
        with ThreadPoolExecutor(max_workers=len(self.judges)) as pool:
            judgments = list(pool.map(lambda agent: contextvars.copy_context().run(agent.judge, case, self.board, round_number, mode, count), self.judges))
        for agent, j in zip(self.judges, judgments):
            self.board.post(DebateEntry(role="judge", round=round_number, text=j.summary, belief=j.belief, persona=agent.persona))
        judgment = aggregate(judgments)
        votes = {key: sum(vote(j.belief) == key for j in judgments) for key in VOTES}
        points = [PanelPoint(**p.model_dump(), persona=agent.persona, applied=p.accepted and (p.side, p.item, p.direction) in agent.last_moves and not p.reason.endswith((UNAPPLIED, REPEATED)))
                  for agent, j in zip(self.judges, judgments) for p in j.debatePoints]
        trace.append(RoundTrace(
            round=round_number, belief=judgment.belief, unresolved=judgment.unresolved, beliefs=[j.belief for j in judgments], votes=votes, points=points,
            scoreDelta=max(abs(a - b) for a, b in zip(score_vector(previous), score_vector(judgment))) if previous else None,
            beliefDelta=abs(previous.belief - judgment.belief) if previous else None,
            ksDelta=ks_distance(trace[-1].votes, votes) if previous else None))
        return judgment


def run_debate(request, snapshot, provider, calls=0):
    """A fresh set of agents for every verdict: nothing an agent remembers leaks into another verdict."""
    return Moderator(provider).run(request, snapshot, calls)


def create_verdict(request, authorization, store, provider):
    subject = store.authorize(request.room_id, authorization)
    if subject != request.requester:
        raise HTTPException(403, "요청 화자와 참가 토큰이 일치하지 않습니다.")
    existing = store.verdict_by_request(request.room_id, request.request_id)
    if existing:
        if existing["parent_id"] or json.loads(existing["snapshot"])["request"] != request.model_dump():
            raise HTTPException(409, "같은 요청 ID를 다른 판결에 사용할 수 없습니다.")
        return VerdictResult.model_validate_json(existing["result"])
    state = store.state(request.room_id, subject)
    recent = state["messages"][-10:]
    messages = [{"speaker": m["speaker"], "text": m["text"]} for m in recent]
    if set(m["speaker"] for m in messages) != {"A", "B"}:
        raise HTTPException(422, "양쪽의 메시지가 한 개 이상 필요합니다.")
    # Reject stale client snapshot rather than silently analyze a different conversation.
    if messages != [m.model_dump() for m in request.recent_messages] or request.relationship != state["relationship"]:
        raise HTTPException(409, "대화가 바뀌었어요. 채팅으로 돌아가 최신 대화를 확인해주세요.")
    snapshot = {"messages": messages, "relationship": state["relationship"], "temperature": state["temperature"], "version": state["version"], "request": request.model_dump(), "trajectory": trajectory(recent)}
    # The Core State is kept current by every sent message; rebuild it here only when the last update is missing or behind.
    stored, version = store.core_with_version(request.room_id)
    calls = 0
    if stored:
        stored = {key: value for key, value in stored.items() if key != "trajectory"}  # rows saved by older versions carry one
    if stored and version == state["version"]:
        core = CoreState(**stored, trajectory=snapshot["trajectory"])
    else:
        core = build_core_state(request, snapshot, provider, stored)
        store.save_core(request.room_id, core.model_dump_json(exclude={"trajectory"}), state["version"])
        calls = 1
    snapshot["core"] = core.model_dump()
    result = run_debate(request, snapshot, provider, calls=calls)
    store.save_verdict(request, snapshot, result)
    return result
