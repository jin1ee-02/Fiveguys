import type { ChatTurn, ConversationInput, Speaker } from './api';

export type FeatureSource = 'mock' | 'api';
export type EmotionLevel = 1 | 2 | 3 | 4 | 5;
export type EmotionTrend = 'up' | 'flat' | 'down';
export type VerdictMode = 'WWE' | 'UFC';

export interface RoomSettings {
  purifyEnabled: boolean;
  thermometerEnabled: boolean;
  reactionEnabled?: boolean;
  sensitivity: number; // Temporary UI value: 0.25 / 0.5 / 0.75, pending team agreement.
}

export interface DraftPreviewRequest extends ConversationInput {
  sensitivity: number;
}

export interface DraftReview {
  draft: string;
  decision: 'safe' | 'suggested' | 'uncertain';
  alternatives: string[];
  explanation: string;
  provider?: string | null;
  source: FeatureSource;
}

export interface EmotionRequest {
  speaker: Speaker;
  relationship: string;
  recent_messages: ChatTurn[];
}

export interface EmotionResult {
  subject: Speaker;
  level: EmotionLevel | null;
  trend: EmotionTrend | null;
  status?: 'ok' | 'uncertain' | 'insufficient_context';
  confidence?: number | null;
  provider?: string;
  recommendation: string;
  contextCount: number;
  source: FeatureSource;
}

export interface VerdictRequest {
  room_id: string;
  requester: Speaker;
  mode: VerdictMode;
  relationship: string;
  recent_messages: ChatTurn[];
  context: string;
  request_id?: string;
}

export interface SideScore {
  logic: number;
  emotionControl: number;
  evidence: number;
  // Why each score was given; absent on verdicts stored before the reasons were added.
  logicReason?: string;
  emotionControlReason?: string;
  evidenceReason?: string;
  strength: string;
  improvement: string;
}

export interface DebateEntry {
  role: 'prosecutor' | 'defense' | 'factcheck' | 'judge';
  round: number;
  text: string;
  evidenceIndices?: number[];
  strategy?: string; // private plan written before the public utterance
  belief?: number | null; // judge only: 0~1 lean towards A
  persona?: 'logic' | 'empathy' | 'evidence' | null; // judge panel member
}

export interface CoreState {
  position_a: string;
  position_b: string;
  issues: string[];
  facts: { text: string; basis: Speaker | 'both'; evidence_indices: number[] }[];
  background: string;
  trajectory: { index: number; speaker: Speaker; emotion: number | null; conflict: number }[];
}

export interface RoundTrace {
  round: number;
  belief: number;
  scoreDelta: number | null;
  beliefDelta: number | null;
  unresolved: boolean;
  beliefs?: number[];
  votes?: { A?: number; even?: number; B?: number };
  ksDelta?: number | null;
}

export type ScoreKey = 'logic' | 'emotionControl' | 'evidence';

export interface PanelScore {
  persona: 'logic' | 'empathy' | 'evidence';
  belief: number;
  plaintiff: Record<ScoreKey, number>;
  defendant: Record<ScoreKey, number>;
}

export interface VerdictResult {
  verdictId: string;
  mode: VerdictMode;
  plaintiff: SideScore;
  defendant: SideScore;
  summary: string;
  recommendation: string;
  humor: string;
  debateLog: DebateEntry[];
  source: FeatureSource;
  rounds?: number;
  stopReason?: 'stable' | 'max_rounds';
  panelScores?: PanelScore[]; // each judge's own scores in the last round; the cards show their mean
  snapshotVersion?: number;
  provider?: string;
  belief?: number;
  coreState?: CoreState | null;
  roundTrace?: RoundTrace[];
  modelCalls?: number;
  judges?: number;
  usage?: { inputTokens: number; cachedTokens: number; outputTokens: number; seconds: number } | null;
}

export type ReactionEmotion = 'neutral' | 'happy' | 'sad' | 'angry' | 'surprised' | 'fear' | 'disgust' | 'contempt';

export interface ReactionRequest extends ConversationInput {
  recipient: Speaker;
  draft_revision: string;
}

export interface ReactionResult {
  status: 'ok' | 'uncertain' | 'insufficient_context';
  recipient: Speaker;
  draft_revision: string;
  emotion: ReactionEmotion | null;
  probabilities: Record<ReactionEmotion, number> | Record<string, never>;
  intensity: number | null;
  confidence: number | null;
  explanation: string;
  provider: string;
  source: FeatureSource;
}

export interface RoomSession {
  roomId: string;
  speaker: Speaker;
  token: string;
  inviteCode: string | null;
}

export interface RoomState {
  roomId: string;
  relationship: string;
  version: number;
  temperature: number;
  participantCount: number;
  messages: import('./api').Message[];
  settings: RoomSettings;
  emotions?: Record<Speaker, Omit<EmotionResult, 'source'>>;
}

export interface AsyncState<T> {
  status: 'idle' | 'loading' | 'ready' | 'error';
  data: T | null;
  error: string;
}
