"""Separate classification from generation, with explicit uncertainty states."""

import os

from fastapi import HTTPException

from app.jev import get_jev
from app.offline import trivial
from app.provider import configured, get_provider, llm_mode
from app.runtime import analysis_cache
from app.schemas import FeatureScores, ReactionResult


def analysis_mode() -> str:
    """auto picks the richest setup the configured keys allow:
    both keys -> hybrid (LLM + Jev as two raters), TypeSafe key only -> jev, otherwise whatever the LLM is."""
    mode = os.getenv("ANALYSIS_PROVIDER", "auto").strip().lower()
    if mode == "auto":
        if configured("TYPESAFE_API_KEY"):
            return "hybrid" if llm_mode() == "openai" else "jev"
        return llm_mode()
    if mode not in ("hybrid", "jev", "openai", "offline"):
        raise HTTPException(503, "ANALYSIS_PROVIDER는 auto, hybrid, openai, jev 또는 offline이어야 합니다.")
    return mode


def get_analyzer():
    mode = analysis_mode()
    if mode == "hybrid":
        from app.hybrid import HybridAnalyzer
        return HybridAnalyzer(get_provider(), get_jev())
    if mode == "jev":
        return get_jev()
    if mode == "offline":
        from app.offline import OfflineProvider
        return OfflineProvider()
    return get_provider()


def analyzer_name(provider):
    return f"{analysis_mode()}/{provider.model}"


def light_analysis(request, provider) -> tuple[FeatureScores, list[str]]:
    """Light path entry: pre-filter, then one model call for the conflict signals (+ alternatives)."""
    if trivial(request.text) and request.previous_temperature < 40:
        return FeatureScores(hostility=0, sarcasm=0, blame=0, repair=0, escalation_delta=0, confidence=4, rationale="짧은 응답이라 모델 호출 없이 통과했어요.", model_version="prefilter"), []
    if hasattr(provider, "light"):
        return provider.light(request)
    return provider.analyze(request), []


def reaction_result(request, provider):
    if request.speaker == request.recipient:
        raise HTTPException(422, "보내는 사람과 받는 사람은 달라야 합니다.")
    name, probabilities, intensity, confidence, model = analysis_cache.call(["reaction-v1", analyzer_name(provider), request.model_dump(exclude={"draft_revision", "previous_temperature"})], lambda: provider.reaction(request))
    uncertain = confidence < 0.55
    return ReactionResult(status="uncertain" if uncertain else "ok", recipient=request.recipient, draft_revision=request.draft_revision, emotion=None if uncertain else name, probabilities=probabilities, intensity=None if uncertain else intensity, confidence=confidence, explanation="예상 반응이 애매해 표시를 보류했어요." if uncertain else "이 표현을 받으면 상대가 이런 감정을 느낄 수 있어요. 실제 감정과 다를 수 있습니다.", provider=model)
