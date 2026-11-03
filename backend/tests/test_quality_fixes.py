"""Fixes from the 2026-11-03 real-model run: verdict conclusion, debate stopping, verdict window, harsh rewrites, scores."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.analysis import get_analyzer
from app.main import app
from app.offline import OfflineProvider
from app.provider import OpenAIProvider, get_generation_factory, get_provider
from app.schemas import AlternativesOutput, DebateOutput, DebatePoint, DraftInput, FeatureScores, JudgeOutput, KeyMoment, SideScore, VerdictInput
from app.scoring import conflict_score
from app.store import Store
from app.verdict import aggregate, check_conclusion, run_debate

MESSAGES = [{"speaker": "A", "text": "오늘 7시 약속 기억나? 30분째 기다려"}, {"speaker": "B", "text": "씨발 그래 내가 쓰레기다 됐냐? 꺼져 그냥"}]
SIDE = SideScore(logic=60, emotionControl=60, evidence=60, logicReason="논리", emotionControlReason="감정", evidenceReason="근거", strength="강점", improvement="개선점")


def judgment(cause=50, conduct=50, conclusion="", moments=(), unresolved=False, belief=0.5, points=()):
    return JudgeOutput(debatePoints=list(points), plaintiff=SIDE, defendant=SIDE, keyMoments=list(moments), causeA=cause, causeReason=f"원인 {cause}",
                       conductA=conduct, conductReason=f"방식 {conduct}", conclusion=conclusion, summary="요약", recommendation="제안", humor="", unresolved=unresolved, belief=belief)


class ConclusionTests(unittest.TestCase):
    def test_deciding_messages_are_held_to_the_conversation(self):
        checked = check_conclusion(judgment(cause=140, conduct=20, moments=[
            KeyMoment(index=1, quote="'꺼져 그냥'", effect="worsened", why="욕설로 대화를 끊었다.", speaker="A"),  # speaker comes from the message
            KeyMoment(index=0, quote="지어낸 문장", effect="worsened", why="약속을 짚었다."),  # not in the message: replaced by its text
            KeyMoment(index=7, quote="없는 메시지", effect="helped", why="범위 밖"),
            KeyMoment(index=1, quote="꺼져", effect="worsened", why="중복"),
        ]), MESSAGES)
        self.assertEqual([(m.index, m.speaker, m.quote) for m in checked.keyMoments], [(0, "A", MESSAGES[0]["text"][:40]), (1, "B", "꺼져 그냥")])
        self.assertEqual((checked.causeA, checked.conductA), (100, 20))
        # A judge that wrote no conclusion still gives one, from its split.
        self.assertEqual(checked.conclusion, "책임이 비슷합니다. 두 사람 모두 돌아볼 점이 있어요.")
        self.assertIn("B 쪽", check_conclusion(judgment(cause=20, conduct=20), MESSAGES).conclusion)

    def test_panel_averages_the_split_and_takes_the_words_of_the_closest_judge(self):
        merged = aggregate([judgment(20, 10, "B 책임 큼"), judgment(30, 40, "B 조금 더"), judgment(70, 70, "A 책임")])
        self.assertEqual((merged.causeA, merged.conductA, merged.conclusion, merged.causeReason), (40, 40, "B 조금 더", "원인 30"))


class StoppingJudge:
    """Every judge says something is unresolved, yet no score or lean moves: gpt-6-luna's behaviour in every verdict."""
    model = "fake"

    def structured(self, prompt, payload, schema, tokens, shared=None):
        if schema is DebateOutput:
            return DebateOutput(strategy="전략", text=f"{payload['role']} 발언", evidence_indices=[])
        points = [DebatePoint(role=o["role"], claim=o["text"], side="A", item="logic", evidence_indices=[0], accepted=True, direction="none", reason="이미 반영") for o in payload.get("opinions", [])]
        return judgment(cause=30, conduct=20, conclusion="이번 다툼은 B 쪽 책임이 더 큽니다.", unresolved=True, points=points)


class StoppingTests(unittest.TestCase):
    def test_debate_stops_when_nothing_moves_even_if_judges_say_unresolved(self):
        request = VerdictInput(room_id="r", requester="A", mode="UFC", relationship="연인", recent_messages=MESSAGES, request_id="r")
        snapshot = {"messages": MESSAGES, "relationship": "연인", "temperature": 40, "version": 2}
        result = run_debate(request, snapshot, StoppingJudge())
        self.assertEqual((result.rounds, result.stopReason, result.modelCalls), (1, "stable", 9))
        self.assertEqual((result.causeA, result.conductA, result.conclusion), (30, 20, "이번 다툼은 B 쪽 책임이 더 큽니다."))


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "mvp.sqlite3")
        self.patch = patch("app.main.get_store", return_value=self.store)
        self.patch.start()
        offline = OfflineProvider()
        app.dependency_overrides[get_analyzer] = lambda: offline
        app.dependency_overrides[get_provider] = lambda: offline
        app.dependency_overrides[get_generation_factory] = lambda: lambda: offline
        self.client = TestClient(app)
        self.a = self.store.create("룸메이트")
        self.b = self.store.join(self.a["inviteCode"])

    def tearDown(self):
        app.dependency_overrides.clear()
        self.patch.stop()
        self.directory.cleanup()

    def test_verdict_reads_up_to_30_messages_and_renumbers_the_core_state_evidence(self):
        room = self.a["roomId"]
        turns = [("A" if i % 2 == 0 else "B", f"{i}번째 메시지, 약속했잖아") for i in range(14)]
        for i, (who, text) in enumerate(turns):
            token = (self.a if who == "A" else self.b)["token"]
            self.assertEqual(self.client.post(f"/v1/rooms/{room}/messages", json={"text": text, "request_id": f"m{i}"}, headers={"Authorization": "Bearer " + token}).status_code, 200)
        core = self.store.core(room)
        request = {"room_id": room, "requester": "A", "mode": "UFC", "relationship": "룸메이트", "request_id": "v", "recent_messages": [{"speaker": w, "text": t} for w, t in turns]}
        body = self.client.post("/v1/verdicts", json=request, headers={"Authorization": "Bearer " + self.a["token"]}).json()
        self.assertEqual(len(body["coreState"]["trajectory"]), 14)
        # The stored Core State numbered its evidence within the last 10 messages; the verdict shifts it by 4.
        self.assertEqual([f["evidence_indices"] for f in body["coreState"]["facts"]], [[i + 4 for i in f["evidence_indices"]] for f in core["facts"]])
        self.assertTrue(body["conclusion"])


class HarshRewriteTests(unittest.TestCase):
    def test_a_rewrite_that_keeps_a_threat_is_asked_again_then_dropped(self):
        replies = iter([
            AlternativesOutput(alternatives=["더 얘기 안 할래. 싫으면 네가 나가서 살아.", "지금은 그만 얘기하고 싶어.", "조금 있다가 다시 얘기하자."]),
            AlternativesOutput(alternatives=["지금은 더 얘기하기 힘들어.", "됐어, 네가 나가서 살든가.", "조금 쉬고 저녁에 다시 얘기하자."]),
        ])
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "test-model"
        provider.client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs: SimpleNamespace(output_parsed=next(replies))))
        kept = provider.alternatives(DraftInput(speaker="B", text="됐어 너랑 말하기 싫어. 그렇게 싫으면 네가 나가서 살든가"))
        self.assertEqual(kept, ["지금은 더 얘기하기 힘들어.", "조금 쉬고 저녁에 다시 얘기하자."])


class ScoreTests(unittest.TestCase):
    def features(self, **values):
        return FeatureScores(**{"hostility": 0, "sarcasm": 0, "blame": 0, "repair": 0, "escalation_delta": 0, "confidence": 3, "rationale": "t", **values})

    def test_an_insult_or_threat_alone_reads_as_one(self):
        self.assertEqual(conflict_score(self.features(hostility=4)), 90)
        self.assertGreaterEqual(conflict_score(self.features(hostility=3, escalation_delta=1)), 70)  # "닥쳐 … 차단한다"
        self.assertLess(conflict_score(self.features(hostility=1, blame=1)), 30)

    def test_blame_alone_does_not_force_a_rewrite_in_a_calm_room(self):
        app.dependency_overrides[get_analyzer] = lambda: SimpleNamespace(model="fake", analyze=lambda request: self.features(hostility=1, blame=3, escalation_delta=1, confidence=4))
        try:
            body = TestClient(app).post("/v1/drafts/analyze", json={"speaker": "A", "text": "이번이 세 번째야. 지난주도 그 전주도 너 당일에 취소했잖아", "relationship": "연인"}).json()
        finally:
            app.dependency_overrides.clear()
        self.assertEqual((body["decision"], body["draft_risk"] < body["threshold"]), ("below_threshold", True))


if __name__ == "__main__":
    unittest.main()
