"""MVP regression cases prepared for a later explicitly requested test run."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.analysis import emotion_result, get_analyzer
from app.jev import score_answer
from app.main import app
from app.offline import OfflineProvider
from app.provider import ModelOutputError, get_generation_factory
from app.schemas import DebateOutput, EmotionInput, FeatureScores, JudgeOutput, SideScore, VerdictInput
from app.store import Store
from app.verdict import run_debate


class FakeAnalyzer:
    model = "fake-analysis"

    def __init__(self):
        self.calls = 0

    def analyze(self, request):
        self.calls += 1
        return FeatureScores(hostility=0, sarcasm=0, blame=0, repair=0, escalation_delta=0, confidence=4, rationale="차분한 표현")

    def emotion(self, request):
        return 2, None, 0.95, self.model


class FakeDebate:
    model = "fake-generation"

    def structured(self, prompt, payload, schema, tokens, shared=None):
        if schema is DebateOutput:
            return DebateOutput(strategy="근거를 먼저 제시한다.", text="대화에 나타난 근거를 함께 확인합니다.", evidence_indices=[0])
        score = SideScore(logic=60, emotionControl=70, evidence=50, logicReason="논리 근거", emotionControlReason="감정 근거", evidenceReason="근거 설명", strength="요청을 제시했습니다.", improvement="가능한 시간을 구체적으로 정하세요.")
        return JudgeOutput(plaintiff=score, defendant=score, summary="양쪽의 설명을 비교했습니다.", recommendation="다음 약속을 함께 정하세요.", humor="", unresolved=False, belief=0.5)


class MvpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "mvp.sqlite3")
        self.analyzer = FakeAnalyzer()
        self.store_patch = patch("app.main.get_store", return_value=self.store)
        self.store_patch.start()
        app.dependency_overrides[get_analyzer] = lambda: self.analyzer
        # Sending a message refreshes the Core State in the background; never let a test reach a real model for it.
        app.dependency_overrides[get_generation_factory] = lambda: OfflineProvider
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.store_patch.stop()
        self.directory.cleanup()

    def test_invite_has_one_use_and_participants_are_distinct(self):
        a = self.store.create("친구")
        b = self.store.join(a["inviteCode"])
        self.assertEqual(a["roomId"], b["roomId"])
        self.assertEqual(b["speaker"], "B")
        self.assertEqual(self.store.authorize(a["roomId"], "Bearer " + a["token"]), "A")
        with self.assertRaises(HTTPException):
            self.store.join(a["inviteCode"])

    def test_room_token_cannot_read_another_room(self):
        a = self.store.create("친구")
        other = self.store.create("친구")
        response = self.client.get("/v1/rooms/" + other["roomId"], headers={"Authorization": "Bearer " + a["token"]})
        self.assertEqual(response.status_code, 403)

    def test_message_retry_does_not_duplicate_or_reanalyze(self):
        a = self.store.create("친구")
        path = f"/v1/rooms/{a['roomId']}/messages"
        headers = {"Authorization": "Bearer " + a["token"]}
        payload = {"text": "안녕", "request_id": "same-request"}
        first = self.client.post(path, json=payload, headers=headers)
        retry = self.client.post(path, json=payload, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.json()["version"], first.json()["version"])
        self.assertEqual(len(retry.json()["messages"]), 1)
        self.assertEqual(self.analyzer.calls, 1)
        changed = self.client.post(path, json={**payload, "text": "다른 메시지"}, headers=headers)
        self.assertEqual(changed.status_code, 409)

    def test_room_survives_store_recreation(self):
        a = self.store.create("룸메이트")
        reopened = Store(self.store.path)
        self.assertEqual(reopened.state(a["roomId"], "A")["relationship"], "룸메이트")

    def test_no_target_turn_is_not_calm_or_flat(self):
        request = EmotionInput(speaker="A", recent_messages=[{"speaker": "B", "text": "안녕"}])
        result = emotion_result(request, self.analyzer)
        self.assertEqual(result.status, "insufficient_context")
        self.assertIsNone(result.level)
        self.assertIsNone(result.trend)

    def test_one_target_turn_has_no_comparable_trend(self):
        request = EmotionInput(speaker="A", recent_messages=[{"speaker": "A", "text": "답답해"}])
        result = emotion_result(request, self.analyzer)
        self.assertEqual(result.level, 3)
        self.assertIsNone(result.trend)

    def test_score_rejects_inconsistent_probability_weighted_mean(self):
        answer = {"type": "score", "score": 4, "confidence": 1, "probabilities": {str(i): float(i == 0) for i in range(5)}, "legend": {str(i): str(i) for i in range(5)}}
        with self.assertRaises(ModelOutputError):
            score_answer(answer)

    def test_adaptive_stopping_finishes_at_second_stable_round(self):
        request = VerdictInput(room_id="example", requester="B", mode="UFC", relationship="친구", recent_messages=[{"speaker": "A", "text": "오늘 청소하자"}, {"speaker": "B", "text": "좋아"}], request_id="example")
        snapshot = {"messages": [m.model_dump() for m in request.recent_messages], "relationship": "친구", "temperature": 30, "version": 2}
        with patch.dict("os.environ", {"VERDICT_MAX_ROUNDS": "3"}):
            result = run_debate(request, snapshot, FakeDebate())
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.stopReason, "stable")
        self.assertEqual(len(result.debateLog), 12)  # per round: 3 debaters + 3 panel judges
        self.assertEqual(result.judges, 3)
        self.assertEqual(result.humor, "")


if __name__ == "__main__":
    unittest.main()
