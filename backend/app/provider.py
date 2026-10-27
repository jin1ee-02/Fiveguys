"""LLM adapter. The rest of the pipeline does not depend on the SDK.

API connection point: every LLM call in the project goes through OpenAIProvider.structured.
To connect, set OPENAI_API_KEY in backend/.env (LLM_PROVIDER=auto picks it up). To use another
vendor, write a class with the same structured(prompt, payload, schema, tokens, shared) method
and return it from get_provider. Until then app.offline.OfflineProvider stands in.
"""

import json
import os
import time
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI

from app.prompts import LIGHT_PROMPT, REWRITE_PROMPT, ALTERNATIVES_PROMPT, EMOTION_PROMPT, REACTION_PROMPT, PROMPT_VERSION
from app.schemas import AlternativesOutput, ConversationInput, EmotionOutput, FeatureScores, LightOutput, ReactionOutput, RewriteOutput

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class ModelOutputError(Exception):
    """The model returned no usable structured answer."""


class OpenAIProvider:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key, timeout=30.0, max_retries=1)
        self.model = model
        self.cache_enabled = True
        # The same chat should get the same scores twice, so sampling is turned down. Leave OPENAI_TEMPERATURE
        # empty for models that do not accept the parameter (reasoning models).
        temperature = os.getenv("OPENAI_TEMPERATURE", "0").strip()
        self.options = {"temperature": float(temperature)} if temperature else {}

    def structured(self, prompt, payload, schema, tokens=1600, shared=None):
        """One schema-constrained call. Order is static prompt -> shared case -> turn payload,
        so repeated calls of one verdict share the longest possible prefix for prompt caching."""
        from app.runtime import model_slot, usage_log
        messages = [{"role": "system", "content": prompt}]
        if shared is not None:
            messages.append({"role": "user", "content": json.dumps(shared, ensure_ascii=False, sort_keys=True)})
        messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False)})
        started = time.monotonic()
        with model_slot():
            response = self.client.responses.parse(
                model=self.model, input=messages,
                text_format=schema, max_output_tokens=tokens, store=False, **getattr(self, "options", {}),
            )
        log, usage = usage_log.get(), getattr(response, "usage", None)
        if log is not None and usage is not None:
            # cachedTokens shows whether the shared-prefix layout actually hits the provider's prompt cache.
            log.append({"inputTokens": getattr(usage, "input_tokens", 0) or 0, "outputTokens": getattr(usage, "output_tokens", 0) or 0,
                        "cachedTokens": getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0, "seconds": time.monotonic() - started})
        if response.output_parsed is None:
            raise ModelOutputError("모델이 사용 가능한 구조화 결과를 반환하지 않았습니다.")
        return response.output_parsed

    def alternatives(self, request):
        result = self.structured(ALTERNATIVES_PROMPT, json.loads(self._payload(request)), AlternativesOutput, 700)
        alternatives = [value.strip() for value in result.alternatives]
        if len(alternatives) != 3 or len(set(alternatives)) != 3 or any(not value or len(value) > 2000 or value == request.text.strip() for value in alternatives):
            raise ModelOutputError("순화 대안 3개가 유효하지 않습니다.")
        return alternatives

    def emotion(self, request):
        result = self.structured(EMOTION_PROMPT, request.model_dump(), EmotionOutput, 300)
        import math
        values = [result.current_score, result.confidence] + ([result.previous_score] if result.previous_score is not None else [])
        if any(not math.isfinite(value) for value in values) or not 0 <= result.current_score <= 4 or not 0 <= result.confidence <= 1 or (result.previous_score is not None and not 0 <= result.previous_score <= 4):
            raise ModelOutputError("감정 점수가 유효하지 않습니다.")
        return result.current_score, result.previous_score, result.confidence, f"openai/{self.model}"

    def reaction(self, request):
        from app.jev import EMOTIONS
        result = self.structured(REACTION_PROMPT, {"sender": request.speaker, "recipient": request.recipient, "target": request.text, "relationship": request.relationship, "core_state": request.core_state, "recent_messages": [t.model_dump() for t in request.recent_messages]}, ReactionOutput, 500)
        import math
        values = [result.intensity, result.confidence, *result.probabilities]
        if len(result.probabilities) != 8 or any(not math.isfinite(value) or value < 0 for value in values) or sum(result.probabilities) <= 0:
            raise ModelOutputError("예상 반응 확률이 유효하지 않습니다.")
        # A generative model names the emotion well but its positional probability array is rough: about half of
        # gpt-4o-mini replies failed the classifier-grade check (sum of 1, label = largest). Keep the label and
        # pass the array on normalised, as a reference value only.
        total = sum(result.probabilities)
        probabilities = {name: round(value / total, 4) for name, value in zip(EMOTIONS, result.probabilities)}
        return result.emotion, probabilities, min(1.0, result.intensity), min(1.0, result.confidence), f"openai/{self.model}"

    @staticmethod
    def _payload(request: ConversationInput) -> str:
        # Delimit user text as JSON data; only the static system prompt is instructional.
        return json.dumps(
            {
                "relationship": request.relationship,
                "summary": request.summary,
                "core_state": request.core_state,
                "recent_messages": [turn.model_dump() for turn in request.recent_messages],
                "target": {"speaker": request.speaker, "text": request.text},
            },
            ensure_ascii=False,
        )

    def light(self, request: ConversationInput) -> tuple[FeatureScores, list[str]]:
        """Light path: signals + expressed emotion in a single call. Alternatives are requested separately, only when needed."""
        from app.runtime import analysis_cache
        def operation():
            result = self.structured(LIGHT_PROMPT, json.loads(self._payload(request)), LightOutput, 500)
            return FeatureScores.model_validate({**result.model_dump(), "model_version": f"openai/{self.model}"}), []
        if not getattr(self, "cache_enabled", False):
            return operation()
        return analysis_cache.call([PROMPT_VERSION, self.model, "light", self._payload(request)], operation)

    def analyze(self, request: ConversationInput) -> FeatureScores:
        return self.light(request)[0]

    def rewrite(self, request: ConversationInput) -> str:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content": self._payload(request)},
            ],
            text_format=RewriteOutput,
            max_output_tokens=250,
            store=False,
        )
        if response.output_parsed is None:
            raise ModelOutputError("순화 결과가 비어 있습니다.")
        rewritten = response.output_parsed.rewritten_text.strip()
        if not rewritten or len(rewritten) > 2000:
            raise ModelOutputError("순화 결과가 유효하지 않습니다.")
        return rewritten


def configured(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value) and not value.startswith("your_")


def llm_mode() -> str:
    """auto: use OpenAI once a key is set, otherwise the labelled offline rules."""
    mode = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    if mode == "auto":
        return "openai" if configured("OPENAI_API_KEY") else "offline"
    if mode not in ("openai", "offline"):
        raise HTTPException(503, "LLM_PROVIDER는 auto, openai 또는 offline이어야 합니다.")
    return mode


@lru_cache(maxsize=1)
def get_provider():
    if llm_mode() == "offline":
        from app.offline import OfflineProvider
        return OfflineProvider()
    if not configured("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY").strip(), model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))


def get_generation_factory():
    """Defer the generation key check until a rewrite is actually required."""
    return get_provider
