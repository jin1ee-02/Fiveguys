import json
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.provider import OpenAIProvider
from app.schemas import ConversationInput, LightOutput, ReactionInput, ReactionOutput


class FakeResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


class ProviderTests(unittest.TestCase):
    def test_temperature_is_not_sent_to_model(self):
        fake = FakeResponses(LightOutput(hostility=0, sarcasm=0, blame=0, repair=0, emotion=0, escalation_delta=0, confidence=3, rationale="중립"))
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=fake)
        provider.model = "test-model"
        request = ConversationInput(speaker="A", text="안녕", previous_temperature=99)
        self.assertEqual(provider.analyze(request).hostility, 0)
        payload = json.loads(fake.kwargs["input"][1]["content"])
        self.assertNotIn("previous_temperature", payload)
        self.assertEqual(payload["target"], {"speaker": "A", "text": "안녕"})
        self.assertFalse(fake.kwargs["store"])

    def test_light_call_scores_only_and_leaves_alternatives_to_their_own_call(self):
        fake = FakeResponses(LightOutput(hostility=2, sarcasm=3, blame=2, repair=0, emotion=2, escalation_delta=1, confidence=3, rationale="비꼼"))
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=fake)
        provider.model = "test-model"
        features, alternatives = provider.light(ConversationInput(speaker="A", text="참 대단하시네"))
        self.assertEqual((features.sarcasm, alternatives), (3, []))
        self.assertNotIn("alternatives", LightOutput.model_fields)

    def test_token_usage_is_collected_for_the_request_that_asks_for_it(self):
        from app.runtime import usage_log, usage_total
        parsed = LightOutput(hostility=0, sarcasm=0, blame=0, repair=0, emotion=0, escalation_delta=0, confidence=3, rationale="중립")
        fake = FakeResponses(parsed)
        fake.parse = lambda **kwargs: SimpleNamespace(output_parsed=parsed, usage=SimpleNamespace(input_tokens=1200, output_tokens=80, input_tokens_details=SimpleNamespace(cached_tokens=1024)))
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=fake)
        provider.model = "test-model"
        entries = []
        token = usage_log.set(entries)
        try:
            provider.structured("지시", {"turn": 1}, LightOutput, 100, shared={"case": {}})
            provider.structured("지시", {"turn": 2}, LightOutput, 100, shared={"case": {}})
        finally:
            usage_log.reset(token)
        total = usage_total(entries)
        self.assertEqual((total["inputTokens"], total["cachedTokens"], total["outputTokens"]), (2400, 2048, 160))

    def test_out_of_range_model_output_is_rejected(self):
        fake = FakeResponses(LightOutput(hostility=9, sarcasm=0, blame=0, repair=0, emotion=0, escalation_delta=0, confidence=3, rationale="잘못된 점수"))
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=fake)
        provider.model = "test-model"
        with self.assertRaises(ValidationError):
            provider.analyze(ConversationInput(speaker="A", text="안녕"))

    def test_reaction_distribution_is_normalised_instead_of_rejected(self):
        # Sums to 1.2 and names a label that is not the largest: both used to be a 502.
        fake = FakeResponses(ReactionOutput(emotion="sad", probabilities=[0.1, 0, 0.3, 0.6, 0.1, 0.05, 0.05, 0], intensity=0.7, confidence=0.8))
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(responses=fake)
        provider.model = "test-model"
        emotion, probabilities, intensity, confidence, _ = provider.reaction(ReactionInput(speaker="A", recipient="B", text="됐어", draft_revision="r1"))
        self.assertEqual((emotion, probabilities["angry"], intensity, confidence), ("sad", 0.5, 0.7, 0.8))
        self.assertAlmostEqual(sum(probabilities.values()), 1, places=3)


if __name__ == "__main__":
    unittest.main()
