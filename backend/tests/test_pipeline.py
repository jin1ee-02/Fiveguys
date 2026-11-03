"""End-to-end run of the Light and Heavy paths with the offline stand-in (no API key, no network)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.analysis import get_analyzer, light_analysis
from app.main import app
from app.offline import OfflineProvider
from app.provider import get_generation_factory, get_provider
from app.schemas import ConversationInput, DebateOutput, DebatePoint, JudgeOutput, SideScore, VerdictInput
from app.store import Store
from app.provider import ModelOutputError
from app.verdict import Moderator, aggregate, check_points, ks_distance, run_debate, validate_judge

SCENARIO = [("A", "오늘 청소 같이 하기로 했잖아."), ("B", "미안, 오늘 너무 바빠서 못 했어."), ("A", "지난번에도 그래서 좀 답답해."), ("B", "내일은 같이 할 수 있을 것 같아.")]


def rulings(payload):
    """A judge's look at every point of this round, none accepted, so no score has to move."""
    return [DebatePoint(role=o["role"], claim=o["text"], side="A", item="logic", evidence_indices=[], accepted=False, direction="none", reason="근거 메시지 없음") for o in payload.get("opinions", [])]


class DriftingJudge:
    """Judge whose belief keeps moving, so the debate must not stop early."""
    model = "fake"

    def __init__(self, beliefs):
        self.beliefs = iter(beliefs)
        self.turns = []

    def structured(self, prompt, payload, schema, tokens, shared=None):
        if schema is DebateOutput:
            self.turns.append(payload)
            return DebateOutput(strategy=f"{payload['role']} 전략", text=f"{payload['role']} 발언", evidence_indices=[])
        score = SideScore(logic=60, emotionControl=60, evidence=60, logicReason="논리 근거", emotionControlReason="감정 근거", evidenceReason="근거 설명", strength="강점", improvement="개선점")
        return JudgeOutput(debatePoints=rulings(payload), plaintiff=score, defendant=score, summary="요약", recommendation="제안", humor="", unresolved=False, belief=next(self.beliefs))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "mvp.sqlite3")
        self.store_patch = patch("app.main.get_store", return_value=self.store)
        self.store_patch.start()
        offline = OfflineProvider()
        app.dependency_overrides[get_analyzer] = lambda: offline
        app.dependency_overrides[get_provider] = lambda: offline
        app.dependency_overrides[get_generation_factory] = lambda: lambda: offline
        self.client = TestClient(app)
        self.a = self.store.create("룸메이트")
        self.b = self.store.join(self.a["inviteCode"])
        self.room = self.a["roomId"]

    def tearDown(self):
        app.dependency_overrides.clear()
        self.store_patch.stop()
        self.directory.cleanup()

    def headers(self, who):
        return {"Authorization": "Bearer " + (self.a if who == "A" else self.b)["token"]}

    def chat(self, turns):
        for index, (who, text) in enumerate(turns):
            response = self.client.post(f"/v1/rooms/{self.room}/messages", json={"text": text, "request_id": f"m{index}-{len(text)}"}, headers=self.headers(who))
            self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_light_call_returns_signals_and_three_alternatives(self):
        response = self.client.post("/v1/drafts/analyze", json={"speaker": "A", "text": "진짜 대단하시네 ㅋㅋ 맨날 그 모양이지", "relationship": "룸메이트", "previous_temperature": 30, "suggest_rewrite": False})
        body = response.json()
        self.assertEqual(body["decision"], "rewrite_suggested")
        self.assertEqual(len(body["alternatives"]), 3)
        self.assertGreaterEqual(body["features"]["sarcasm"], 3)
        self.assertNotIn("emotion", body["features"])

    def test_calm_draft_gets_no_alternatives(self):
        body = self.client.post("/v1/drafts/analyze", json={"speaker": "A", "text": "오늘 같이 청소할래?", "previous_temperature": 0}).json()
        self.assertEqual(body["decision"], "below_threshold")
        self.assertEqual(body["alternatives"], [])

    def test_prefilter_skips_the_model_for_short_acknowledgements(self):
        class Untouchable:
            model = "none"
            def light(self, request):
                raise AssertionError("model must not be called")
        features, _ = light_analysis(ConversationInput(speaker="A", text="ㅇㅇ", previous_temperature=10), Untouchable())
        self.assertEqual(features.model_version, "prefilter")
        with self.assertRaises(AssertionError):
            light_analysis(ConversationInput(speaker="A", text="ㅇㅇ", previous_temperature=70), Untouchable())

    def test_room_state_carries_the_shared_temperature_the_thermometer_shows(self):
        state = self.chat(SCENARIO + [("A", "진짜 대단하시네 ㅋㅋ 맨날 그 모양이지")])
        self.assertNotIn("emotions", state)
        last = state["messages"][-1]["result"]["data"]
        # One temperature for the room: the last message moved it up, and its stored result says from where.
        self.assertEqual(last["temperature"], state["temperature"])
        self.assertGreater(last["temperature"], last["previous_temperature"])

    def test_core_state_follows_each_message_and_reaches_light_requests(self):
        self.chat(SCENARIO[:2])
        core, version = self.store.core_with_version(self.room)
        self.assertTrue(core["issues"] and core["position_a"])
        self.assertEqual(version, self.store.state(self.room, "A")["version"])
        seen = []
        original = OfflineProvider.light
        def spy(provider, request):
            seen.append(request.core_state)
            return original(provider, request)
        with patch.object(OfflineProvider, "light", spy):
            draft = {"speaker": "A", "text": "진짜 대단하시네 ㅋㅋ 맨날 그 모양이지", "relationship": "룸메이트", "previous_temperature": 30, "suggest_rewrite": False}
            # In a room the server attaches the room's Core State, whatever the client put there.
            self.client.post("/v1/drafts/analyze", json={**draft, "room_id": self.room, "core_state": {"issues": ["가짜"]}}, headers=self.headers("A"))
            # Without a room there is no Core State.
            self.client.post("/v1/drafts/analyze", json={**draft, "core_state": {"issues": ["가짜"]}})
            # The next sent message is scored with the Core State too.
            self.chat([("A", "지난번에도 그래서 좀 답답해.")])
        self.assertEqual(seen[0]["issues"], core["issues"])
        self.assertNotIn("facts", seen[0])
        self.assertIsNone(seen[1])
        self.assertIsNotNone(seen[2])
        # Another participant's token cannot read this room's Core State through a draft.
        self.assertEqual(self.client.post("/v1/drafts/analyze", json={**draft, "room_id": self.room}, headers=self.headers("B")).status_code, 403)

    def test_verdict_builds_core_state_debates_in_order_and_stops_when_stable(self):
        state = self.chat(SCENARIO)
        request = {"room_id": self.room, "requester": "A", "mode": "WWE", "relationship": "룸메이트", "context": "이번 주 청소는 B 차례였어요.", "request_id": "v1",
                   "recent_messages": [{"speaker": m["speaker"], "text": m["text"]} for m in state["messages"]]}
        response = self.client.post("/v1/verdicts", json=request, headers=self.headers("A"))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # Round 0: the panel scores the utterances before the debate; then the debaters speak and the panel rules again.
        self.assertEqual([(entry["role"], entry["round"]) for entry in body["debateLog"][:6]], [("judge", 0)] * 3 + [("prosecutor", 1), ("defense", 1), ("factcheck", 1)])
        self.assertEqual([entry["persona"] for entry in body["debateLog"][6:9]], ["logic", "empathy", "evidence"])
        # The first speaker alternates between rounds to offset the order bias of one-by-one debate.
        self.assertEqual(Moderator(OfflineProvider()).order(2)[:2], ["defense", "prosecutor"])
        self.assertTrue(all(entry["strategy"] for entry in body["debateLog"] if entry["role"] != "judge"))
        # Every sent message already refreshed the Core State, so the verdict reuses it. The rules move no score,
        # so the panel is stable after one debate round: 3 initial judgments + 3 debaters + 3 judgments, no rebuild.
        self.assertEqual((body["rounds"], body["stopReason"], body["modelCalls"], body["judges"]), (1, "stable", 9, 3))
        self.assertEqual([t["round"] for t in body["roundTrace"]], [0, 1])
        self.assertEqual(sum(body["roundTrace"][0]["votes"].values()), 3)
        self.assertEqual(body["roundTrace"][1]["ksDelta"], 0)
        # Each judge ruled on every debater of round 1; nothing was argued in round 0.
        self.assertEqual(body["roundTrace"][0]["points"], [])
        self.assertEqual(sorted((p["persona"], p["role"]) for p in body["roundTrace"][1]["points"]), sorted((j, r) for j in ("logic", "empathy", "evidence") for r in ("prosecutor", "defense", "factcheck")))
        self.assertEqual(body["initialScores"]["A"]["logic"], body["plaintiff"]["logic"])
        self.assertEqual(len(body["coreState"]["trajectory"]), 4)
        self.assertTrue(body["coreState"]["issues"] and body["coreState"]["position_a"])
        self.assertTrue(body["humor"])
        self.assertIsNotNone(self.store.core(self.room))
        # Same request id replays the stored verdict instead of debating again.
        self.assertEqual(self.client.post("/v1/verdicts", json=request, headers=self.headers("A")).json()["verdictId"], body["verdictId"])
        # The card shows the panel mean; each judge's own scores come along so the mean can be traced.
        self.assertEqual([p["persona"] for p in body["panelScores"]], ["logic", "empathy", "evidence"])
        mean = sum(p["plaintiff"]["logic"] for p in body["panelScores"]) / 3
        self.assertAlmostEqual(body["plaintiff"]["logic"], round(mean, 1))
        # The appeal feature was removed.
        self.assertEqual(self.client.post(f"/v1/verdicts/{body['verdictId']}/appeals", json={"text": "x", "request_id": "ap1"}, headers=self.headers("B")).status_code, 404)
        self.assertEqual(len(self.client.get(f"/v1/rooms/{self.room}/verdicts", headers=self.headers("B")).json()), 1)

    def test_debate_continues_while_judge_belief_moves_and_agents_rebut(self):
        request = VerdictInput(room_id="r", requester="A", mode="UFC", relationship="친구", recent_messages=[{"speaker": "A", "text": "늦었네"}, {"speaker": "B", "text": "미안"}], request_id="r")
        snapshot = {"messages": [m.model_dump() for m in request.recent_messages], "relationship": "친구", "temperature": 20, "version": 2}
        provider = DriftingJudge([0.5, 0.8, 0.6, 0.58])
        with patch.dict("os.environ", {"VERDICT_JUDGES": "1"}):
            result = run_debate(request, snapshot, provider)
        self.assertEqual((result.rounds, result.stopReason), (3, "stable"))
        self.assertEqual([round(t.beliefDelta, 2) if t.beliefDelta is not None else None for t in result.roundTrace], [None, 0.3, 0.2, 0.02])
        # Debaters argue against the panel's current view: round 1 sees the pre-debate scores, round 2 the rulings on round 1.
        self.assertEqual((provider.turns[0]["panel_feedback"][0]["belief"], provider.turns[0]["panel_feedback"][0]["rulings"]), (0.5, []))
        self.assertEqual({r["role"] for r in provider.turns[3]["panel_feedback"][0]["rulings"]}, {"prosecutor", "defense", "factcheck"})
        # The defense speaks after the prosecutor and sees that utterance; strategies stay private.
        self.assertEqual(provider.turns[1]["transcript"], [{"role": "prosecutor", "round": 1, "text": "prosecutor 발언"}])
        self.assertEqual((provider.turns[0]["phase"], provider.turns[3]["phase"], provider.turns[6]["phase"]), ("opening", "rebuttal", "closing"))
        self.assertEqual((provider.turns[3]["role"], provider.turns[3]["own_previous_strategy"]), ("defense", "defense 전략"))
        self.assertNotIn("strategy", str(provider.turns[1]["transcript"]))

    def test_each_agent_is_its_own_object_with_private_memory(self):
        request = VerdictInput(room_id="r", requester="A", mode="UFC", relationship="친구", recent_messages=[{"speaker": "A", "text": "늦었네"}, {"speaker": "B", "text": "미안"}], request_id="r")
        snapshot = {"messages": [m.model_dump() for m in request.recent_messages], "relationship": "친구", "temperature": 20, "version": 2}
        moderator = Moderator(DriftingJudge([0.5, 0.5, 0.5, 0.5, 0.5, 0.5]), judges=2, max_rounds=2)
        moderator.run(request, snapshot)
        self.assertEqual(len({id(agent) for agent in [*moderator.debaters.values(), *moderator.judges]}), 5)
        self.assertEqual({role: agent.strategy for role, agent in moderator.debaters.items()}, {role: f"{role} 전략" for role in ("prosecutor", "defense", "factcheck")})
        self.assertTrue(all(agent.previous is not None for agent in moderator.judges))
        # The shared board carries public text only.
        self.assertNotIn("전략", str(moderator.board.transcript(2)))

    def test_one_invalid_reply_is_asked_again_instead_of_failing_the_verdict(self):
        request = VerdictInput(room_id="r", requester="A", mode="UFC", relationship="친구", recent_messages=[{"speaker": "A", "text": "늦었네"}, {"speaker": "B", "text": "미안"}], request_id="r")
        snapshot = {"messages": [m.model_dump() for m in request.recent_messages], "relationship": "친구", "temperature": 20, "version": 2}
        provider = DriftingJudge([0.5] * 9)
        answer, provider.failed = provider.structured, False
        def flaky(prompt, payload, schema, tokens, shared=None):
            if schema is DebateOutput and not provider.failed:
                provider.failed = True
                return DebateOutput(strategy="전략", text="가" * 701, evidence_indices=[])
            return answer(prompt, payload, schema, tokens, shared)
        provider.structured = flaky
        result = run_debate(request, snapshot, provider)
        self.assertEqual((result.rounds, result.stopReason), (1, "stable"))

    def test_panel_scores_are_averaged_and_vote_shift_is_measured_with_ks(self):
        def judgment(belief, logic):
            side = SideScore(logic=logic, emotionControl=50, evidence=50, logicReason=f"논리 {logic}", emotionControlReason="감정 근거", evidenceReason="근거 설명", strength="강점", improvement="개선점")
            return JudgeOutput(debatePoints=[], plaintiff=side, defendant=side, summary=f"요약 {belief}", recommendation="제안", humor="", unresolved=belief > 0.7, belief=belief)
        merged = aggregate([judgment(0.3, 40), judgment(0.6, 70), judgment(0.9, 100)])
        self.assertEqual((merged.plaintiff.logic, merged.belief, merged.unresolved), (70.0, 0.6, False))
        self.assertEqual(merged.summary, "요약 0.6")  # text of the judge closest to the panel mean
        self.assertEqual(merged.plaintiff.logicReason, "논리 70")  # reason of the judge whose score is closest to the mean
        self.assertEqual(ks_distance({"B": 3, "even": 0, "A": 0}, {"B": 3, "even": 0, "A": 0}), 0)
        self.assertEqual(ks_distance({"B": 2, "even": 1, "A": 0}, {"B": 1, "even": 1, "A": 1}), 0.3333)
        self.assertEqual(ks_distance({"B": 3, "even": 0, "A": 0}, {"B": 0, "even": 0, "A": 3}), 1)

    def test_judge_lean_is_turned_to_match_its_own_scores(self):
        def judgment(a, b, belief):
            side = lambda value: SideScore(logic=value, emotionControl=value, evidence=value, logicReason="논리 근거", emotionControlReason="감정 근거", evidenceReason="근거 설명", strength="강점", improvement="개선점")
            return JudgeOutput(debatePoints=[], plaintiff=side(a), defendant=side(b), summary="요약", recommendation="제안", humor="", unresolved=False, belief=belief)
        self.assertEqual(validate_judge(judgment(20, 80, 0.7), "UFC").belief, 0.2)  # B scored higher, yet the lean said A
        self.assertEqual(validate_judge(judgment(80, 20, 0.7), "UFC").belief, 0.7)  # consistent: kept as reported
        self.assertEqual(validate_judge(judgment(60, 60, 0.7), "UFC").belief, 0.7)  # no clear score gap: kept

    def test_accepted_debate_point_moves_the_score_it_names(self):
        request = VerdictInput(room_id="r", requester="A", mode="UFC", relationship="친구", recent_messages=[{"speaker": "A", "text": "맨날 늦네"}, {"speaker": "B", "text": "오늘 처음 늦은 거야"}], request_id="r")
        snapshot = {"messages": [m.model_dump() for m in request.recent_messages], "relationship": "친구", "temperature": 20, "version": 2}

        class PersuadedJudge(DriftingJudge):
            """Before the debate B's evidence is 30; the defense points to message 1, the judge accepts it and raises it."""
            def structured(self, prompt, payload, schema, tokens, shared=None):
                if schema is DebateOutput:
                    return DebateOutput(strategy="전략", text=f"{payload['role']} 발언", evidence_indices=[1] if payload["role"] == "defense" else [])
                points = [DebatePoint(role=o["role"], claim=o["text"], side="B", item="evidence", evidence_indices=o["evidence_indices"],
                                      accepted=o["role"] == "defense", direction="up" if o["role"] == "defense" else "none", reason="이유") for o in payload["opinions"]]
                side = lambda evidence: SideScore(logic=50, emotionControl=50, evidence=evidence, logicReason="논리", emotionControlReason="감정", evidenceReason="근거", strength="강점", improvement="개선")
                return JudgeOutput(debatePoints=points, plaintiff=side(50), defendant=side(60 if payload["opinions"] else 30), summary="요약", recommendation="제안", humor="", unresolved=False, belief=0.5)

        with patch.dict("os.environ", {"VERDICT_JUDGES": "1"}):
            result = run_debate(request, snapshot, PersuadedJudge([]))
        # The judge wrote 60; the debate may take an item at most 15 from the pre-debate score.
        self.assertEqual((result.initialScores["B"]["evidence"], result.defendant.evidence), (30, 45))
        accepted = [p for t in result.roundTrace for p in t.points if p.accepted]
        self.assertEqual((accepted[0].role, accepted[0].side, accepted[0].item, accepted[0].direction, accepted[0].applied), ("defense", "B", "evidence", "up", True))
        # Repeated in round 2, the same point is accepted again but no longer moves anything.
        self.assertTrue(len(accepted) > 1 and not accepted[1].applied)

    def test_debate_points_must_be_grounded_and_agree_with_the_score_change(self):
        side = lambda evidence: SideScore(logic=50, emotionControl=50, evidence=evidence, logicReason="논리", emotionControlReason="감정", evidenceReason="근거", strength="강점", improvement="개선")
        point = lambda **kw: DebatePoint(**{"role": "defense", "claim": "주장", "side": "B", "item": "evidence", "evidence_indices": [1], "accepted": True, "direction": "up", "reason": "이유", **kw})
        judged = lambda points, evidence: JudgeOutput(debatePoints=points, plaintiff=side(50), defendant=side(evidence), summary="요약", recommendation="제안", humor="", unresolved=False, belief=0.5)
        before = judged([], 40)
        self.assertEqual(check_points(judged([point()], 55), before, {"defense"}, 2).defendant.evidence, 55)
        # A score that moves against the accepted point, or without any accepted point, keeps the previous score and reason.
        against = check_points(judged([point()], 30), before, {"defense"}, 2)
        self.assertEqual(against.defendant.evidence, 40)
        self.assertTrue(against.debatePoints[0].reason.endswith("반영하지 않음)"))
        self.assertEqual(check_points(judged([point(accepted=False, direction="none")], 55), before, {"defense"}, 2).defendant.evidence, 40)
        # The same move a second time is not applied again, and the debate takes an item at most 15 from round 0.
        repeated = check_points(judged([point()], 55), before, {"defense"}, 2, initial=before, applied={("B", "evidence", "up")})
        self.assertEqual(repeated.defendant.evidence, 40)
        self.assertIn("이미 반영", repeated.debatePoints[0].reason)
        self.assertEqual(check_points(judged([point()], 90), before, {"defense"}, 2, initial=before).defendant.evidence, 55)
        for points, evidence, roles in [
            ([point(evidence_indices=[])], 55, {"defense"}),  # accepted with no message behind it
            ([point(evidence_indices=[5])], 55, {"defense"}),  # message number out of range
            ([point()], 55, {"defense", "factcheck"}),         # a debater nobody ruled on
        ]:
            with self.assertRaises(ModelOutputError):
                check_points(judged(points, evidence), before, roles, 2)

    def test_manual_rewrite_request_returns_alternatives_for_an_unflagged_draft(self):
        draft = {"speaker": "A", "text": "오늘 같이 청소할래?", "previous_temperature": 0}
        self.assertEqual(self.client.post("/v1/drafts/analyze", json=draft).json()["alternatives"], [])
        forced = self.client.post("/v1/drafts/analyze", json={**draft, "force_rewrite": True}).json()
        self.assertEqual((forced["decision"], len(forced["alternatives"])), ("below_threshold", 3))

    def test_mediation_is_private_to_the_requester_and_posts_nothing(self):
        self.assertEqual(self.client.post(f"/v1/rooms/{self.room}/mediation", headers=self.headers("A")).status_code, 422)
        state = self.chat(SCENARIO)
        response = self.client.post(f"/v1/rooms/{self.room}/mediation", headers=self.headers("B"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["text"])
        after = self.client.get(f"/v1/rooms/{self.room}", headers=self.headers("A")).json()
        self.assertEqual((len(after["messages"]), after["version"]), (len(state["messages"]), state["version"]))
        self.assertEqual(self.client.post(f"/v1/rooms/{self.room}/mediation").status_code, 401)


if __name__ == "__main__":
    unittest.main()
