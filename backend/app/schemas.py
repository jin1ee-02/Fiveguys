"""Request and response contracts for the model-only MVP."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatTurn(StrictModel):
    speaker: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("speaker", "text")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        return value


class ConversationInput(StrictModel):
    recent_messages: list[ChatTurn] = Field(default_factory=list, max_length=10)
    speaker: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=2000)
    relationship: str | None = Field(default=None, max_length=100)
    summary: str | None = Field(default=None, max_length=1000)
    previous_temperature: float = Field(default=0, ge=0, le=100)
    # A room request names its room; the server then fills core_state from the room (anything a client sends there is replaced).
    room_id: str | None = Field(default=None, max_length=80)
    core_state: dict | None = None

    @field_validator("speaker", "text")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        return value


class DraftInput(ConversationInput):
    suggest_rewrite: bool = True
    # LLMediator-style manual activation: the user asks for alternatives even when nothing was flagged.
    force_rewrite: bool = False


class ReceiptResult(StrictModel):
    status: Literal["received"]
    received: ConversationInput


class ModelFeatures(StrictModel):
    """Simple schema sent to the LLM; score ranges are checked separately."""

    hostility: int
    sarcasm: int
    blame: int
    repair: int
    escalation_delta: int
    emotion: int
    confidence: int
    rationale: str


class LightOutput(ModelFeatures):
    """Light path: conflict signals and expressed emotion from one call. Alternatives come from their own call (AlternativesOutput)."""


class FeatureScores(ModelFeatures):
    hostility: int = Field(ge=0, le=4)
    sarcasm: int = Field(ge=0, le=4)
    blame: int = Field(ge=0, le=4)
    repair: int = Field(ge=0, le=4)
    escalation_delta: int = Field(ge=-2, le=2)
    # Speaker's expressed anger/tension in this message; None for results stored before v2.
    emotion: int | None = Field(default=None, ge=0, le=4)
    confidence: int = Field(ge=0, le=4)
    rationale: str = Field(max_length=500)
    model_version: str | None = None


class RewriteOutput(StrictModel):
    rewritten_text: str


class MessageResult(StrictModel):
    features: FeatureScores
    raw_conflict: float
    previous_temperature: float
    temperature: float


class DraftResult(StrictModel):
    features: FeatureScores
    draft_risk: float
    current_temperature: float
    threshold: int
    decision: Literal["rewrite_suggested", "below_threshold", "low_confidence"]
    rewritten_text: str | None
    alternatives: list[str] = Field(default_factory=list, max_length=3)
    provider: str = "openai"


Speaker = Literal["A", "B"]
EmotionName = Literal["neutral", "happy", "sad", "angry", "surprised", "fear", "disgust", "contempt"]


class EmotionInput(StrictModel):
    speaker: Speaker
    relationship: str = Field(default="친구", max_length=100)
    recent_messages: list[ChatTurn] = Field(max_length=10)


class EmotionResult(StrictModel):
    subject: Speaker
    status: Literal["ok", "uncertain", "insufficient_context"]
    level: int | None = Field(ge=1, le=5)
    trend: Literal["up", "flat", "down"] | None
    recommendation: str
    contextCount: int
    confidence: float | None = Field(ge=0, le=1)
    provider: str


class ReactionInput(ConversationInput):
    speaker: Speaker
    recipient: Speaker
    draft_revision: str = Field(min_length=1, max_length=80)


class ReactionResult(StrictModel):
    status: Literal["ok", "uncertain", "insufficient_context"]
    recipient: Speaker
    draft_revision: str
    emotion: EmotionName | None
    probabilities: dict[str, float]
    intensity: float | None = Field(ge=0, le=1)
    confidence: float | None = Field(ge=0, le=1)
    explanation: str
    provider: str


# Simple schemas for generation; validate all model-controlled fields afterwards.
class AlternativesOutput(StrictModel):
    # Written before the alternatives so they stay the target speaker's own point (who says what to whom); not returned to the client.
    intent: str = ""
    alternatives: list[str]


class EmotionOutput(StrictModel):
    current_score: float
    previous_score: float | None
    confidence: float


class ReactionOutput(StrictModel):
    emotion: EmotionName
    probabilities: list[float]  # fixed order matches EmotionName
    intensity: float
    confidence: float


class VerdictInput(StrictModel):
    room_id: str = Field(min_length=1, max_length=80)
    requester: Speaker
    mode: Literal["WWE", "UFC"]
    relationship: str = Field(max_length=100)
    recent_messages: list[ChatTurn] = Field(min_length=2, max_length=10)
    context: str = Field(default="", max_length=1000)
    request_id: str = Field(min_length=1, max_length=80)


class SideScore(StrictModel):
    # Each reason precedes its score so the model commits to the evidence before the number.
    logicReason: str
    logic: float
    emotionControlReason: str
    emotionControl: float
    evidenceReason: str
    evidence: float
    strength: str
    improvement: str


class DebateOutput(StrictModel):
    strategy: str  # private plan, logged for explainability but hidden from other agents
    text: str  # public utterance
    evidence_indices: list[int]


class JudgeOutput(StrictModel):
    plaintiff: SideScore  # always A, regardless of requester
    defendant: SideScore  # always B
    summary: str
    recommendation: str
    humor: str
    unresolved: bool
    belief: float  # 0~1, how far the judge leans to A; tracked across rounds for stability


class FactItem(StrictModel):
    text: str
    basis: Literal["A", "B", "both"]
    evidence_indices: list[int]


class CoreStateOutput(StrictModel):
    position_a: str
    position_b: str
    issues: list[str]
    facts: list[FactItem]
    background: str


class TrajectoryPoint(StrictModel):
    index: int
    speaker: Speaker
    emotion: int | None
    conflict: float


class CoreState(CoreStateOutput):
    """Shared state of PDF p14: positions, emotion trajectory, issues, facts."""

    trajectory: list[TrajectoryPoint] = Field(default_factory=list)


class DebateEntry(StrictModel):
    role: Literal["prosecutor", "defense", "factcheck", "judge"]
    round: int
    text: str
    evidenceIndices: list[int] = Field(default_factory=list)
    strategy: str = ""
    belief: float | None = None
    persona: str | None = None  # judge panel member


class RoundTrace(StrictModel):
    round: int
    belief: float  # panel mean
    scoreDelta: float | None
    beliefDelta: float | None
    unresolved: bool
    beliefs: list[float] = Field(default_factory=list)  # one per judge
    votes: dict[str, int] = Field(default_factory=dict)  # A / even / B
    ksDelta: float | None = None  # KS distance between this and the previous round's vote distribution


class MediationOutput(StrictModel):
    text: str


class MediationResult(StrictModel):
    text: str
    provider: str


class PanelScore(StrictModel):
    """One judge's scores in the final round; the card shows their mean."""

    persona: str
    belief: float
    plaintiff: dict[str, float]
    defendant: dict[str, float]


class VerdictResult(JudgeOutput):
    verdictId: str
    mode: Literal["WWE", "UFC"]
    debateLog: list[DebateEntry]
    rounds: int
    stopReason: Literal["stable", "max_rounds"]
    panelScores: list[PanelScore] = Field(default_factory=list)
    snapshotVersion: int
    provider: str
    promptVersion: str
    coreState: CoreState | None = None
    roundTrace: list[RoundTrace] = Field(default_factory=list)
    modelCalls: int = 0
    judges: int = 1
    # Token and latency totals of this verdict (AgenticSimLaw logs API metadata); None when the provider reports none.
    usage: dict[str, float] | None = None


class RoomSettings(StrictModel):
    purifyEnabled: bool = True
    thermometerEnabled: bool = True
    reactionEnabled: bool = True


class RoomCreate(StrictModel):
    relationship: str = Field(default="친구", min_length=1, max_length=100)


class RoomJoin(StrictModel):
    invite_code: str = Field(min_length=10, max_length=100)


class RoomUpdate(RoomCreate):
    settings: RoomSettings


class SendInput(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(min_length=1, max_length=80)

    @field_validator("text")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        return value.strip()
