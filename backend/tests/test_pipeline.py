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
from app.schemas import ConversationInput, DebateOutput, JudgeOutput, SideScore, VerdictInput
from app.store import Store
from app.verdict import aggregate, ks_distance, run_debate, validate_judge

SCENARIO = [("A", "오늘 청소 같이 하기로 했잖아."), ("B", "미안, 오늘 너무 바빠서 못 했어."), ("A", "지난번에도 그래서 좀 답답해."), ("B", "내일은 같이 할 수 있을 것 같아.")]


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
        return JudgeOutput(plaintiff=score, defendant=score, summary="요약", recommendation="제안", humor="", unresolved=False, belief=next(self.beliefs))


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

    def test_light_call_returns_signals_emotion_and_three_alternatives(self):
        response = self.client.post("/v1/drafts/analyze", json={"speaker": "A", "text": "진짜 대단하시네 ㅋㅋ 맨날 그 모양이지", "relationship": "룸메이트", "previous_temperature": 30, "suggest_rewrite": False})
        body = response.json()
        self.assertEqual(body["decision"], "rewrite_suggested")
        self.assertEqual(len(body["alternatives"]), 3)
        self.assertGreaterEqual(body["features"]["sarcasm"], 3)
        self.assertIsNotNone(body["features"]["emotion"])

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

    def test_room_state_carries_both_thermometers_without_extra_calls(self):
        state = self.chat(SCENARIO + [("A", "진짜 대단하시네 ㅋㅋ 맨날 그 모양이지")])
        mine, partner = state["emotions"]["A"], state["emotions"]["B"]
        self.assertEqual(mine["status"], "ok")
        self.assertGreaterEqual(mine["level"], 3)
        self.assertEqual(mine["trend"], "up")
        self.assertLessEqual(partner["level"], 2)
        self.assertGreater(state["temperature"], 0)

    def test_verdict_builds_core_state_debates_in_order_and_stops_when_stable(self):
        state = self.chat(SCENARIO)
        request = {"room_id": self.room, "requester": "A", "mode": "WWE", "relationship": "룸메이트", "context": "이번 주 청소는 B 차례였어요.", "request_id": "v1",
                   "recent_messages": [{"speaker": m["speaker"], "text": m["text"]} for m in state["messages"]]}
        response = self.client.post("/v1/verdicts", json=request, headers=self.headers("A"))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([entry["role"] for entry in body["debateLog"][:6]], ["prosecutor", "defense", "factcheck", "judge", "judge", "judge"])
        self.assertEqual([entry["persona"] for entry in body["debateLog"][3:6]], ["logic", "empathy", "evidence"])
        # The first speaker alternates between rounds to offset the order bias of one-by-one debate.
        self.assertEqual([entry["role"] for entry in body["debateLog"][6:8]], ["defense", "prosecutor"])
        self.assertTrue(all(entry["strategy"] for entry in body["debateLog"] if entry["role"] != "judge"))
        self.assertEqual((body["rounds"], body["stopReason"], body["modelCalls"], body["judges"]), (2, "stable", 13, 3))
        self.assertEqual(sum(body["roundTrace"][0]["votes"].values()), 3)
        self.assertEqual(body["roundTrace"][1]["ksDelta"], 0)
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
        provider = DriftingJudge([0.5, 0.8, 0.78])
        with patch.dict("os.environ", {"VERDICT_JUDGES": "1"}):
            result = run_debate(request, snapshot, provider)
        self.assertEqual((result.rounds, result.stopReason), (3, "stable"))
        self.assertEqual([round(t.beliefDelta, 2) if t.beliefDelta is not None else None for t in result.roundTrace], [None, 0.3, 0.02])
        # The defense speaks after the prosecutor and sees that utterance; strategies stay private.
        self.assertEqual(provider.turns[1]["transcript"], [{"role": "prosecutor", "round": 1, "text": "prosecutor 발언"}])
        self.assertEqual((provider.turns[0]["phase"], provider.turns[3]["phase"], provider.turns[6]["phase"]), ("opening", "rebuttal", "closing"))
        self.assertEqual((provider.turns[3]["role"], provider.turns[3]["own_previous_strategy"]), ("defense", "defense 전략"))
        self.assertNotIn("strategy", str(provider.turns[1]["transcript"]))

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
        self.assertEqual((result.rounds, result.stopReason), (2, "stable"))

    def test_panel_scores_are_averaged_and_vote_shift_is_measured_with_ks(self):
        def judgment(belief, logic):
            side = SideScore(logic=logic, emotionControl=50, evidence=50, logicReason=f"논리 {logic}", emotionControlReason="감정 근거", evidenceReason="근거 설명", strength="강점", improvement="개선점")
            return JudgeOutput(plaintiff=side, defendant=side, summary=f"요약 {belief}", recommendation="제안", humor="", unresolved=belief > 0.7, belief=belief)
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
            return JudgeOutput(plaintiff=side(a), defendant=side(b), summary="요약", recommendation="제안", humor="", unresolved=False, belief=belief)
        self.assertEqual(validate_judge(judgment(20, 80, 0.7), "UFC").belief, 0.2)  # B scored higher, yet the lean said A
        self.assertEqual(validate_judge(judgment(80, 20, 0.7), "UFC").belief, 0.7)  # consistent: kept as reported
        self.assertEqual(validate_judge(judgment(60, 60, 0.7), "UFC").belief, 0.7)  # no clear score gap: kept

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
