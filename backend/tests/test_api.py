import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.provider import get_provider, get_generation_factory
from app.analysis import get_analyzer
from app.schemas import FeatureScores


class FakeProvider:
    def __init__(self, scores: FeatureScores):
        self.scores = scores
        self.model = 'fake'
        self.rewrite_calls = 0

    def analyze(self, request):
        return self.scores

    def rewrite(self, request):
        self.rewrite_calls += 1
        return "지난번에도 비슷한 일이 있어서 이번에는 답답해."

    def alternatives(self, request):
        self.rewrite_calls += 1
        return ["지난번에도 비슷한 일이 있어서 이번에는 답답해.", "가능한 시간을 알려줘.", "청소 약속을 지켜줬으면 해."]


def override(fake):
    app.dependency_overrides[get_analyzer] = lambda: fake
    app.dependency_overrides[get_provider] = lambda: fake
    app.dependency_overrides[get_generation_factory] = lambda: lambda: fake


class ApiTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_sent_message_updates_temperature(self):
        fake = FakeProvider(FeatureScores(hostility=4, sarcasm=0, blame=4, repair=0,
                                          escalation_delta=2, confidence=4, rationale="직접 비난"))
        override(fake)
        response = TestClient(app).post("/v1/messages/analyze", json={
            "recent_messages": [], "speaker": "A", "text": "다 네 탓이야.", "previous_temperature": 50
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["raw_conflict"], 100)
        self.assertEqual(body["temperature"], 65)

    def test_draft_only_rewrites_above_threshold(self):
        fake = FakeProvider(FeatureScores(hostility=4, sarcasm=4, blame=4, repair=0,
                                          escalation_delta=2, confidence=4, rationale="조롱과 비난"))
        override(fake)
        response = TestClient(app).post("/v1/drafts/analyze", json={
            "recent_messages": [], "speaker": "A", "text": "넌 맨날 그딴 식이지ㅋㅋ",
            "previous_temperature": 50
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["decision"], "rewrite_suggested")
        self.assertEqual(body["current_temperature"], 50)
        self.assertEqual(fake.rewrite_calls, 1)
        self.assertTrue(body["rewritten_text"])

    def test_low_confidence_does_not_rewrite(self):
        fake = FakeProvider(FeatureScores(hostility=4, sarcasm=4, blame=4, repair=0,
                                          escalation_delta=2, confidence=1, rationale="문맥 부족"))
        override(fake)
        response = TestClient(app).post("/v1/drafts/analyze", json={
            "speaker": "A", "text": "됐어", "previous_temperature": 80
        })
        self.assertEqual(response.json()["decision"], "low_confidence")
        self.assertEqual(fake.rewrite_calls, 0)

    def test_rewrite_can_be_skipped_to_check_risk_only(self):
        fake = FakeProvider(FeatureScores(hostility=4, sarcasm=4, blame=4, repair=0,
                                          escalation_delta=2, confidence=4, rationale="공격"))
        override(fake)
        response = TestClient(app).post("/v1/drafts/analyze", json={
            "speaker": "A", "text": "다 네 탓이야", "suggest_rewrite": False
        })
        self.assertEqual(response.json()["decision"], "rewrite_suggested")
        self.assertIsNone(response.json()["rewritten_text"])
        self.assertEqual(fake.rewrite_calls, 0)

    def test_request_limits(self):
        fake = FakeProvider(FeatureScores(hostility=0, sarcasm=0, blame=0, repair=0,
                                          escalation_delta=0, confidence=3, rationale="중립"))
        override(fake)
        response = TestClient(app).post("/v1/messages/analyze", json={
            "speaker": "A", "text": " ", "previous_temperature": 101
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
