"""Hybrid analysis: the LLM and Jev rate the same message independently and are combined.

The LLM (generative) understands Korean nuance and writes the alternatives; Jev (TypeSafe's classifier)
returns calibrated probabilities but is trained mainly on English. Two independent raters give
  - averaged signals, so one model's outlier moves the score less,
  - an agreement check: when they clearly disagree the result is held back as uncertain instead of
    interrupting the user (the false-positive concern of the project plan, p24),
  - reaction preview from Jev, whose Choice primitive is exactly an emotion distribution.
"""

import contextvars
from concurrent.futures import ThreadPoolExecutor

from app.schemas import FeatureScores

SIGNALS = ("hostility", "sarcasm", "blame", "repair")
DISAGREEMENT = 2  # a gap of two rubric levels (e.g. "없음" vs "일부 비꼼") on any attack signal


def combine(llm: FeatureScores, jev: FeatureScores) -> FeatureScores:
    mean = lambda key: int((getattr(llm, key) + getattr(jev, key)) / 2 + 0.5)
    gap = max(abs(getattr(llm, key) - getattr(jev, key)) for key in ("hostility", "sarcasm", "blame"))
    scores = {key: mean(key) for key in SIGNALS}
    delta = (llm.escalation_delta + jev.escalation_delta) / 2
    scores["escalation_delta"] = int(delta + 0.5) if delta >= 0 else -int(-delta + 0.5)
    # Disagreement lowers confidence to the "hold back" gate; otherwise the more cautious rater decides.
    confidence = 1 if gap >= DISAGREEMENT else min(llm.confidence, jev.confidence)
    note = " (두 모델의 판단이 엇갈려 보류했어요.)" if gap >= DISAGREEMENT else ""
    return FeatureScores(**scores, confidence=confidence, rationale=(llm.rationale + note)[:500], model_version=f"hybrid/{llm.model_version}+{jev.model_version}")


class HybridAnalyzer:
    def __init__(self, llm, jev):
        self.llm, self.jev = llm, jev
        self.model = f"{llm.model}+{jev.model}"

    @property
    def cache_enabled(self):
        return self.llm.cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, value):
        self.llm.cache_enabled = self.jev.cache_enabled = value

    def light(self, request):
        with ThreadPoolExecutor(max_workers=2) as pool:
            second = pool.submit(contextvars.copy_context().run, self.jev.analyze, request)
            features, alternatives = self.llm.light(request)
            try:
                rated = second.result()
            except Exception:
                # The second opinion is optional: keep chatting on the LLM result and say so in the label.
                return features.model_copy(update={"model_version": f"{features.model_version} (jev 응답 없음)"}), alternatives
        return combine(features, rated), alternatives

    def analyze(self, request):
        return self.light(request)[0]

    def alternatives(self, request):
        return self.llm.alternatives(request)

    def reaction(self, request):
        return self.jev.reaction(request)
