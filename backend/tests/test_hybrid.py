"""Hybrid analysis (LLM + Jev as two raters) and the Jev adapter against the documented response format."""

import json
import unittest
from unittest.mock import patch

import httpx

from app.analysis import analysis_mode
from app.hybrid import HybridAnalyzer, combine
from app.jev import JevProvider
from app.schemas import ConversationInput, FeatureScores, ReactionInput

REQUEST = ConversationInput(speaker="A", text="역시 대단하시네요^^", recent_messages=[{"speaker": "B", "text": "미안 또 늦을 것 같아."}])


def features(version, **values):
    base = {"hostility": 0, "sarcasm": 0, "blame": 0, "repair": 0, "escalation_delta": 0, "confidence": 3, "rationale": "근거"}
    return FeatureScores(**{**base, **values}, model_version=version)


class Rater:
    cache_enabled = True

    def __init__(self, model, result, fail=False):
        self.model, self.result, self.fail = model, result, fail

    def light(self, request):
        return self.result, ["대안 1", "대안 2", "대안 3"]

    def analyze(self, request):
        if self.fail:
            raise httpx.ConnectError("down")
        return self.result

    def reaction(self, request):
        return "contempt", {}, 0.5, 0.9, f"jev/{self.model}"


def jev_response(request: httpx.Request) -> httpx.Response:
    """Answers every question in the format of https://docs.typesafe.ai/api."""
    answers = {}
    for name, question in json.loads(request.content)["questions"].items():
        if question["type"] == "score":
            levels = len(question["criteria"])
            answers[name] = {"type": "score", "score": 2.9, "confidence": 0.85, "legend": {str(i): text for i, text in enumerate(question["criteria"])},
                             "probabilities": {str(i): (0.1 if i == 2 else 0.9 if i == 3 else 0.0) for i in range(levels)}}
        else:
            answers[name] = {"type": "choice", "choice": "contempt", "confidence": 0.8, "probabilities": {key: (0.72 if key == "contempt" else 0.04) for key in question["criteria"]}}
    return httpx.Response(200, json={"model": "jev-1.13.0", "answers": answers})


class HybridTests(unittest.TestCase):
    def test_agreeing_raters_are_averaged_and_keep_alternatives(self):
        analyzer = HybridAnalyzer(Rater("gpt", features("openai/gpt", sarcasm=4, blame=2, confidence=4)), Rater("jev", features("jev-1.13.0", sarcasm=3, blame=3, confidence=3)))
        result, alternatives = analyzer.light(REQUEST)
        self.assertEqual((result.sarcasm, result.blame, result.confidence), (4, 3, 3))
        self.assertEqual(result.model_version, "hybrid/openai/gpt+jev-1.13.0")
        self.assertEqual(len(alternatives), 3)

    def test_disagreement_holds_the_result_back_as_uncertain(self):
        merged = combine(features("openai/gpt", sarcasm=4, confidence=4), features("jev-1.13.0", sarcasm=1, confidence=4))
        self.assertEqual(merged.confidence, 1)
        self.assertIn("엇갈려", merged.rationale)

    def test_negative_escalation_is_rounded_symmetrically(self):
        self.assertEqual(combine(features("a", escalation_delta=-2), features("b", escalation_delta=-1)).escalation_delta, -2)
        self.assertEqual(combine(features("a", escalation_delta=2), features("b", escalation_delta=1)).escalation_delta, 2)

    def test_jev_outage_falls_back_to_the_llm_and_says_so(self):
        analyzer = HybridAnalyzer(Rater("gpt", features("openai/gpt", sarcasm=4)), Rater("jev", None, fail=True))
        result, alternatives = analyzer.light(REQUEST)
        self.assertEqual(result.sarcasm, 4)
        self.assertIn("jev 응답 없음", result.model_version)
        self.assertEqual(len(alternatives), 3)

    def test_auto_mode_follows_the_configured_keys(self):
        cases = [({"OPENAI_API_KEY": "sk-x", "TYPESAFE_API_KEY": "ts-x"}, "hybrid"), ({"OPENAI_API_KEY": "sk-x", "TYPESAFE_API_KEY": ""}, "openai"),
                 ({"OPENAI_API_KEY": "", "TYPESAFE_API_KEY": "ts-x"}, "jev"), ({"OPENAI_API_KEY": "your_api_key_here", "TYPESAFE_API_KEY": "your_typesafe_key_here"}, "offline")]
        for environment, expected in cases:
            with patch.dict("os.environ", {**environment, "ANALYSIS_PROVIDER": "auto", "LLM_PROVIDER": "auto"}):
                self.assertEqual(analysis_mode(), expected)

    def test_jev_adapter_parses_the_documented_response(self):
        provider = JevProvider("key", "jev-1.13.0")
        provider.cache_enabled = False
        sent = {}
        def handler(request):
            sent.update(json.loads(request.content))
            return jev_response(request)
        provider.client = httpx.Client(transport=httpx.MockTransport(handler))
        result = provider.analyze(REQUEST)
        self.assertEqual(set(sent["questions"]), {"hostility", "sarcasm", "blame", "repair", "escalation_delta"})
        self.assertEqual((result.sarcasm, result.escalation_delta, result.confidence, result.model_version), (3, 1, 3, "jev-1.13.0"))
        self.assertNotIn("emotion", result.model_dump())
        reaction = provider.reaction(ReactionInput(speaker="A", recipient="B", text="역시 대단하시네요^^", draft_revision="r1"))
        self.assertEqual((reaction[0], round(reaction[2], 3)), ("contempt", 0.725))


if __name__ == "__main__":
    unittest.main()
