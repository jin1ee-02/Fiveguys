"""Heavy path: Core State preprocessing, role debate with private strategies, judge panel, adaptive stopping."""

import contextvars
import json
import math
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from app.prompts import CORE_STATE_PROMPT, DEBATE_PROMPT, JUDGE_PROMPT, PROMPT_VERSION
from app.provider import ModelOutputError
from app.runtime import usage_log, usage_total
from app.schemas import CoreState, CoreStateOutput, DebateEntry, DebateOutput, JudgeOutput, PanelScore, RoundTrace, VerdictResult

# ChatEval: agents with the same role prompt add nothing over a single agent; diversity is what helps,
# with 3-4 roles as the sweet spot. Each judge scores the full rubric but scrutinises one angle.
JUDGE_PERSONAS = {
    "logic": "논리 심사: 주장의 일관성, 추론의 비약, 일반화 여부를 특히 꼼꼼히 봅니다.",
    "empathy": "공감 심사: 표현의 존중과 절제, 상대 입장을 인정했는지를 특히 꼼꼼히 봅니다.",
    "evidence": "근거 심사: 대화에서 확인되는 근거와 한쪽 주장만 있는 내용의 구별을 특히 꼼꼼히 봅니다.",
}
VOTES = ("B", "even", "A")  # ordered outcome scale of one judge


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


# Speaking order inside a round: claim -> rebuttal -> internal fact check, then the judge panel.
ROLES = {
    "prosecutor": "A가 실제로 한 말을 근거로 A의 불만과 주장을 제3자로서 정리하세요('저는', '제가'로 말하지 않고 'A는 ...라고 말했습니다'). B의 타당한 지적은 인정하세요.",
    "defense": "B가 실제로 한 말을 근거로 B의 상황과 주장을 제3자로서 정리하세요('저는', '제가'로 말하지 않고 'B는 ...라고 말했습니다'). A의 타당한 지적은 인정하세요.",
    "factcheck": "양쪽 주장과 대화 내부 근거를 대조하고 주장/확인된 대화/누락 정보를 구별하세요.",
}


def score_vector(judgment):
    return [getattr(side, key) for side in [judgment.plaintiff, judgment.defendant] for key in ["logic", "emotionControl", "evidence"]]


def valid_indices(indices, count, limit=10):
    return len(indices) <= limit and len(set(indices)) == len(indices) and all(0 <= index < count for index in indices)


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


def twice(operation):
    """A reply that breaks a length or index rule is asked for once more, so one of ~19 calls cannot sink the verdict."""
    try:
        return operation()
    except ModelOutputError:
        return operation()


def trajectory(messages):
    """Emotion trajectory of the Core State, read from the Light results stored with each message."""
    return [{"index": i, "speaker": m["speaker"], "emotion": m["result"]["data"]["features"].get("emotion"), "conflict": m["result"]["data"]["raw_conflict"]} for i, m in enumerate(messages)]


def build_core_state(request, snapshot, provider, previous=None):
    """Step 1 of the verdict: structure the chat log into positions / issues / facts (one call)."""
    count = len(snapshot["messages"])
    payload = {"messages": snapshot["messages"], "relationship": snapshot["relationship"], "context": request.context, "previous_core_state": previous}
    def structure():
        output = provider.structured(CORE_STATE_PROMPT, payload, CoreStateOutput, 900)
        texts = [output.position_a, output.position_b, output.background, *output.issues, *[fact.text for fact in output.facts]]
        if not output.position_a.strip() or not output.position_b.strip() or not output.issues or len(output.issues) > 4 or len(output.facts) > 6 or any(len(text) > 500 for text in texts) or any(not valid_indices(fact.evidence_indices, count) for fact in output.facts):
            raise ModelOutputError("Core State 구조화 결과가 유효하지 않습니다.")
        return output
    return CoreState(**twice(structure).model_dump(), trajectory=snapshot.get("trajectory", []))


def panel_scores(panel):
    """Each judge's own six scores, so the averaged card can be traced back to who gave what."""
    keys = ("logic", "emotionControl", "evidence")
    return [PanelScore(persona=persona, belief=j.belief, plaintiff={k: getattr(j.plaintiff, k) for k in keys}, defendant={k: getattr(j.defendant, k) for k in keys}) for persona, j in panel.items()]


def run_debate(request, snapshot, provider, calls=0):
    log, trace, previous = [], [], None
    max_rounds = max(1, min(3, int(os.getenv("VERDICT_MAX_ROUNDS", "3"))))
    score_limit = float(os.getenv("VERDICT_STABILITY_DELTA", "5"))
    belief_limit = float(os.getenv("VERDICT_BELIEF_DELTA", "0.1"))
    ks_limit = float(os.getenv("VERDICT_KS_DELTA", "0.34"))
    personas = list(JUDGE_PERSONAS)[: max(1, min(len(JUDGE_PERSONAS), int(os.getenv("VERDICT_JUDGES", "3"))))]
    usage = []
    usage_token = usage_log.set(usage)
    count = len(snapshot["messages"])
    # Identical for every call of this verdict, so it forms the cacheable prompt prefix.
    # The judges score each person's own words, so those are listed per speaker next to the full log.
    said = {speaker: [m["text"] for m in snapshot["messages"] if m["speaker"] == speaker] for speaker in "AB"}
    case = {"case": {"messages": snapshot["messages"], "utterances": said, "relationship": snapshot["relationship"], "context": request.context, "mode": request.mode, "conflictTemperature": snapshot["temperature"], "core_state": snapshot.get("core")}}
    strategies = {role: None for role in ROLES}
    panel = {}
    for round_number in range(1, max_rounds + 1):
        # AgenticSimLaw phases. One-by-one speaking has an order bias (ChatEval), so the first speaker alternates.
        phase = "opening" if round_number == 1 else "closing" if round_number == max_rounds else "rebuttal"
        order = ["prosecutor", "defense"][:: 1 if round_number % 2 else -1] + ["factcheck"]
        for role in order:
            # Agents see earlier public utterances (to rebut them) and only their own previous strategy.
            transcript = [{"role": e.role, "round": e.round, "text": e.text} for e in log if e.role != "judge" and e.round >= round_number - 1]
            turn = {"role": role, "round": round_number, "phase": phase, "instruction": ROLES[role], "transcript": transcript, "own_previous_strategy": strategies[role]}
            def speak():
                result = provider.structured(DEBATE_PROMPT, turn, DebateOutput, 1100, shared=case)
                if not result.text.strip() or len(result.text) > 700 or len(result.strategy) > 400 or not valid_indices(result.evidence_indices, count):
                    raise ModelOutputError("토론 발언 또는 근거 번호가 유효하지 않습니다.")
                return result
            result = twice(speak)
            strategies[role] = result.strategy
            log.append(DebateEntry(role=role, round=round_number, text=result.text, evidenceIndices=result.evidence_indices, strategy=result.strategy))
        opinions = [{"role": e.role, "text": e.text, "evidence_indices": e.evidenceIndices} for e in log if e.round == round_number]
        def judge(persona):
            # Each judge sees the public debate and its own earlier judgment, never the other judges (independent votes).
            turn = {"round": round_number, "persona": JUDGE_PERSONAS[persona], "opinions": opinions, "previous_judgment": panel[persona].model_dump() if panel else None}
            return twice(lambda: validate_judge(provider.structured(JUDGE_PROMPT, turn, JudgeOutput, 2000, shared=case), request.mode))
        with ThreadPoolExecutor(max_workers=len(personas)) as pool:
            judgments = list(pool.map(lambda persona: contextvars.copy_context().run(judge, persona), personas))
        panel = dict(zip(personas, judgments))
        judgment = aggregate(judgments)
        log.extend(DebateEntry(role="judge", round=round_number, text=j.summary, belief=j.belief, persona=persona) for persona, j in panel.items())
        calls += len(ROLES) + len(personas)
        # Adaptive stopping (Adaptive Stability Detection): stop when the distribution of judgments stopped moving.
        # Here the distribution is the panel's votes over B < even < A, compared across rounds with the KS statistic,
        # together with the averaged scores and belief.
        votes = {key: sum(vote(j.belief) == key for j in judgments) for key in VOTES}
        score_delta = max(abs(a - b) for a, b in zip(score_vector(previous), score_vector(judgment))) if previous else None
        belief_delta = abs(previous.belief - judgment.belief) if previous else None
        ks_delta = ks_distance(trace[-1].votes, votes) if previous else None
        trace.append(RoundTrace(round=round_number, belief=judgment.belief, scoreDelta=score_delta, beliefDelta=belief_delta, unresolved=judgment.unresolved, beliefs=[j.belief for j in judgments], votes=votes, ksDelta=ks_delta))
        stable = previous is not None and not judgment.unresolved and not previous.unresolved and score_delta <= score_limit and belief_delta <= belief_limit and ks_delta <= ks_limit
        if stable:
            break
        previous = judgment
    usage_log.reset(usage_token)
    return VerdictResult(**judgment.model_dump(), verdictId=str(uuid.uuid4()), mode=request.mode, debateLog=log, rounds=round_number, stopReason="stable" if stable else "max_rounds", panelScores=panel_scores(panel), snapshotVersion=snapshot["version"], provider=f"{getattr(provider, 'vendor', 'openai')}/{provider.model}", promptVersion=PROMPT_VERSION, coreState=snapshot.get("core"), roundTrace=trace, modelCalls=calls, judges=len(personas), usage=usage_total(usage))


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
    # Heavy path reads the room's Core State and writes the updated one back.
    core = build_core_state(request, snapshot, provider, store.core(request.room_id))
    snapshot["core"] = core.model_dump()
    result = run_debate(request, snapshot, provider, calls=1)
    store.save_verdict(request, snapshot, result, core=core.model_dump_json())
    return result
