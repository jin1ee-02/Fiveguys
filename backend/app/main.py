"""Analysis APIs and persistent two-participant rooms for the MVP."""

from typing import Annotated, Callable
import hashlib
import json
import logging
import os
import time
import threading
from collections import OrderedDict

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import APIConnectionError, OpenAIError, RateLimitError
from pydantic import ValidationError

from app.provider import ModelOutputError, OpenAIProvider, configured, get_provider, get_generation_factory, llm_mode
from app.prompts import MEDIATOR_PROMPT
from app.schemas import MediationOutput, MediationResult, AppealInput, ConversationInput, DraftInput, DraftResult, EmotionInput, EmotionResult, MessageResult, ReactionInput, ReactionResult, ReceiptResult, RoomCreate, RoomJoin, RoomUpdate, SendInput, VerdictInput, VerdictResult
from app.scoring import RELATIONSHIP_OFFSET, conflict_score, rewrite_threshold, update_temperature
from app.analysis import analysis_mode, analyzer_name, emotion_result, get_analyzer, light_analysis, reaction_result
from app.store import get_store, room_lock
from app.verdict import create_verdict, appeal_verdict as execute_appeal
from app.prompts import PROMPT_VERSION

app = FastAPI(title="KU래쪄용 갈등 중재 MVP", version="1.0.0")
logger = logging.getLogger("uvicorn.error")
Provider = Annotated[OpenAIProvider, Depends(get_provider)]
Analyzer = Annotated[object, Depends(get_analyzer)]
GenerationFactory = Annotated[Callable, Depends(get_generation_factory)]
Authorization = Annotated[str | None, Header()]


@app.exception_handler(OpenAIError)
@app.exception_handler(ModelOutputError)
@app.exception_handler(ValidationError)
@app.exception_handler(httpx.HTTPError)
@app.exception_handler(ValueError)
def model_error_handler(request: Request, error: Exception):
    # Name the cause in the server log (class and static reason only, never chat text) so a failure can be traced.
    logger.warning("AI 호출 실패 %s %s%s", request.url.path, type(error).__name__, f": {error}" if isinstance(error, ModelOutputError) else "")
    if isinstance(error, RateLimitError):
        return JSONResponse(status_code=429, content={"detail": "AI 서비스의 사용량 한도에 걸렸어요. 잠시 후 다시 시도해주세요."}, headers={"Retry-After": "10"})
    if isinstance(error, (APIConnectionError, httpx.TransportError)):
        return JSONResponse(status_code=503, content={"detail": "AI 서비스에 연결하지 못했거나 응답이 늦어요. 잠시 후 다시 시도해주세요."})
    if isinstance(error, ModelOutputError):
        return JSONResponse(status_code=502, content={"detail": "AI 응답이 정해진 형식에 맞지 않았어요. 다시 시도해주세요."})
    return JSONResponse(status_code=502, content={"detail": "AI 요청 또는 응답 검증에 실패했습니다. 키와 모델 설정을 확인하고 다시 시도해주세요."})


# Bounded per-client admission. Trust no forwarding headers in this local service.
# Typing fires a draft check and a reaction preview per pause, and a shared gateway puts both participants on one address.
RATE_LIMIT = max(1, int(os.getenv("RATE_LIMIT_PER_MINUTE", "240")))
rate_lock = threading.Lock()
rate_entries = OrderedDict()


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.method == "POST":
        identity = request.client.host if request.client else "local"
        key = hashlib.sha256(identity.encode()).hexdigest()
        now = time.monotonic()
        with rate_lock:
            bucket = [t for t in rate_entries.get(key, []) if t > now - 60]
            if len(bucket) >= RATE_LIMIT:
                return JSONResponse(status_code=429, content={"detail": "요청이 너무 많아요. 잠시 후 다시 시도해주세요."}, headers={"Retry-After": "10"})
            bucket.append(now)
            rate_entries[key] = bucket
            rate_entries.move_to_end(key)
            while len(rate_entries) > 1024:
                rate_entries.popitem(last=False)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:8081,http://127.0.0.1:8081"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/config")
def config():
    analysis, llm = analysis_mode(), llm_mode()
    return {"analysisProvider": analysis, "llmProvider": llm, "offline": llm == "offline" or analysis == "offline",
            "analysisConfigured": analysis == "offline" or (configured("TYPESAFE_API_KEY") if analysis in ("jev", "hybrid") else True) and (configured("OPENAI_API_KEY") if analysis == "openai" else True), "generationConfigured": llm == "offline" or configured("OPENAI_API_KEY"),
            "jevModel": os.getenv("JEV_MODEL", "jev-1.13.0"), "generationModel": os.getenv("OPENAI_MODEL", "gpt-4o-mini") if llm == "openai" else "rules-v1", "storage": "sqlite", "sync": "polling", "promptVersion": PROMPT_VERSION}


@app.post("/v1/demo/messages", response_model=ReceiptResult)
def receive_demo_message(request: ConversationInput) -> ReceiptResult:
    """Echo validated input for the presentation; no AI call or persistence."""
    return ReceiptResult(status="received", received=request)


def _provider_failure(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail="AI 요청 또는 구조화 결과 검증에 실패했습니다. 모델 설정과 API 연결을 확인하세요.")


@app.post("/v1/messages/analyze", response_model=MessageResult)
def analyze_message(request: ConversationInput, provider: Analyzer) -> MessageResult:
    """Analyze a sent message and return the updated temperature; caller persists it."""
    try:
        features, _ = light_analysis(request, provider)
    except (OpenAIError, ModelOutputError, ValidationError) as error:
        raise _provider_failure(error) from error
    raw = conflict_score(features)
    return MessageResult(
        features=features,
        raw_conflict=raw,
        previous_temperature=request.previous_temperature,
        temperature=update_temperature(request.previous_temperature, raw),
    )


@app.post("/v1/drafts/analyze", response_model=DraftResult)
def analyze_draft(request: DraftInput, provider: Analyzer, generation: GenerationFactory) -> DraftResult:
    """Analyze an unsent draft without changing the conversation temperature."""
    try:
        # Light path scores the draft; an OpenAI analyzer returns no alternatives here and they are written by the
        # dedicated call below once a suggestion is due (offline rules and tests may still hand them back directly).
        features, alternatives = light_analysis(request, provider)
        risk = conflict_score(features)
        threshold = max(15, min(90, round(rewrite_threshold(request.previous_temperature) + relationship_offset(request.relationship) - (request.sensitivity - 0.5) * 40)))
        # One unmistakable signal (3+ = clear sarcasm / direct attack / strong blame) is enough even in a calm room,
        # otherwise the weighted sum can never reach the calm-room threshold. Low sensitivity opts out.
        strong = max(features.hostility, features.sarcasm, features.blame) >= 3 and request.sensitivity >= 0.5
        decision = "low_confidence" if features.confidence <= 1 else "rewrite_suggested" if risk >= threshold or strong else "below_threshold"
        if decision != "rewrite_suggested" and not request.force_rewrite:
            alternatives = []
        elif not alternatives and (request.suggest_rewrite or request.force_rewrite):
            alternatives = generation().alternatives(request)
        rewritten = alternatives[0] if alternatives else None
    except (OpenAIError, ModelOutputError, ValidationError) as error:
        raise _provider_failure(error) from error
    return DraftResult(
        features=features,
        draft_risk=risk,
        current_temperature=request.previous_temperature,
        threshold=threshold,
        decision=decision,
        rewritten_text=rewritten,
        alternatives=alternatives,
        provider=analyzer_name(provider),
    )


def relationship_offset(relationship):
    return RELATIONSHIP_OFFSET.get(relationship, 0)


@app.post("/v1/emotions/analyze", response_model=EmotionResult)
def analyze_emotion(request: EmotionInput, provider: Analyzer):
    return emotion_result(request, provider)


@app.post("/v1/reactions/preview", response_model=ReactionResult)
def preview_reaction(request: ReactionInput, provider: Analyzer):
    return reaction_result(request, provider)


@app.post("/v1/rooms")
def create_room(request: RoomCreate):
    return get_store().create(request.relationship)


@app.post("/v1/rooms/join")
def join_room(request: RoomJoin):
    return get_store().join(request.invite_code.strip())


@app.get("/v1/rooms/{room_id}")
def room_state(room_id: str, authorization: Authorization = None):
    store = get_store()
    return store.state(room_id, store.authorize(room_id, authorization))


@app.post("/v1/rooms/{room_id}/settings")
def room_settings(room_id: str, request: RoomUpdate, authorization: Authorization = None):
    store = get_store()
    subject = store.authorize(room_id, authorization)
    with room_lock(room_id):
        return store.update(room_id, subject, request)


@app.post("/v1/rooms/{room_id}/messages")
def send_message(room_id: str, request: SendInput, provider: Analyzer, authorization: Authorization = None):
    store = get_store()
    subject = store.authorize(room_id, authorization)
    with room_lock(room_id):
        existing = store.sent(room_id, request.request_id)
        if existing:
            if existing["speaker"] != subject or existing["text"] != request.text:
                raise HTTPException(409, "같은 요청 ID를 다른 메시지에 사용할 수 없습니다.")
            return store.state(room_id, subject)
        state = store.state(room_id, subject)
        input = ConversationInput(speaker=subject, text=request.text, relationship=state["relationship"], previous_temperature=state["temperature"], recent_messages=[{"speaker": m["speaker"], "text": m["text"]} for m in state["messages"][-10:]])
        features, _ = light_analysis(input, provider)
        raw = conflict_score(features)
        result = MessageResult(features=features, raw_conflict=raw, previous_temperature=state["temperature"], temperature=update_temperature(state["temperature"], raw))
        return store.append(room_id, subject, request, result, state["version"])


@app.post("/v1/rooms/{room_id}/mediation", response_model=MediationResult)
def mediation(room_id: str, provider: Provider, authorization: Authorization = None):
    """On-demand neutral suggestion for the requester only (LLMediator F2/F3); nothing is posted to the room."""
    store = get_store()
    subject = store.authorize(room_id, authorization)
    state = store.state(room_id, subject)
    messages = [{"speaker": m["speaker"], "text": m["text"]} for m in state["messages"][-10:]]
    if not messages:
        raise HTTPException(422, "대화가 시작된 뒤에 요청해주세요.")
    payload = {"requester": subject, "relationship": state["relationship"], "messages": messages}
    text = provider.structured(MEDIATOR_PROMPT, payload, MediationOutput, 400).text.strip()
    if not text or len(text) > 400:
        raise ModelOutputError("중재 제안이 유효하지 않습니다.")
    return MediationResult(text=text, provider=f"{getattr(provider, 'vendor', 'openai')}/{provider.model}")


@app.post("/v1/verdicts", response_model=VerdictResult)
def verdict(request: VerdictInput, provider: Provider, authorization: Authorization = None):
    # A verdict takes most of a minute; its own lock keeps the room open for chatting meanwhile.
    with room_lock("verdict:" + request.room_id, wait=0):
        return create_verdict(request, authorization, get_store(), provider)


@app.post("/v1/verdicts/{verdict_id}/appeals", response_model=VerdictResult)
def appeal(verdict_id: str, request: AppealInput, provider: Provider, authorization: Authorization = None):
    store = get_store()
    room_id = store.verdict(verdict_id)["room_id"]
    with room_lock("verdict:" + room_id, wait=0):
        return execute_appeal(verdict_id, request, authorization, store, provider)


@app.get("/v1/rooms/{room_id}/verdicts")
def verdict_history(room_id: str, authorization: Authorization = None):
    store = get_store()
    store.authorize(room_id, authorization)
    return store.history(room_id)


@app.get("/v1/verdicts/{verdict_id}")
def get_verdict(verdict_id: str, authorization: Authorization = None):
    store = get_store()
    record = store.verdict(verdict_id)
    store.authorize(record["room_id"], authorization)
    snapshot = json.loads(record["snapshot"])
    return {"result": json.loads(record["result"]), "snapshot": snapshot["request"], "appeals": json.loads(record["appeals"])}
