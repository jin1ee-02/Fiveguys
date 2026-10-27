import { mockDraftReview, mockEmotion, mockVerdict } from '../mocks/features';
import type { DraftPreviewRequest, DraftReview, EmotionRequest, EmotionResult, ReactionRequest, ReactionResult, VerdictRequest, VerdictResult } from '../types/features';
import type { FeatureScores } from '../types/api';
import { newRequestId, requestJson } from './client';

const configuredSource = process.env.EXPO_PUBLIC_FEATURE_MODE ?? 'api';
if (configuredSource !== 'mock' && configuredSource !== 'api') throw new Error('EXPO_PUBLIC_FEATURE_MODE는 mock 또는 api여야 합니다.');
export const FEATURE_SOURCE = configuredSource;

function previewDelay(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const cancel = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
      const error = new Error('Preview cancelled');
      error.name = 'AbortError';
      reject(error);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', cancel);
      resolve();
    }, ms);
    if (signal?.aborted) cancel();
    else signal?.addEventListener('abort', cancel, { once: true });
  });
}

export async function reviewDraft(input: DraftPreviewRequest, signal?: AbortSignal, generate = true, manual = false): Promise<DraftReview> {
  if (FEATURE_SOURCE === 'mock') {
    await previewDelay(220, signal);
    return mockDraftReview(input);
  }
  const result = await requestJson<{
    features: FeatureScores; decision: 'rewrite_suggested' | 'below_threshold' | 'low_confidence'; rewritten_text: string | null; alternatives: string[];
  }>('/v1/drafts/analyze', { ...input, suggest_rewrite: generate, force_rewrite: manual }, { signal, timeoutMs: 150_000 });
  if (!result.features || !['rewrite_suggested', 'below_threshold', 'low_confidence'].includes(result.decision)) throw new Error('순화 응답 형식을 확인해주세요.');
  return {
    draft: input.text,
    decision: result.decision === 'rewrite_suggested' ? 'suggested' : result.decision === 'low_confidence' ? 'uncertain' : 'safe',
    alternatives: Array.isArray(result.alternatives) ? result.alternatives : result.rewritten_text ? [result.rewritten_text] : [],
    explanation: result.features.rationale,
    provider: result.features.model_version,
    source: 'api',
  };
}

export async function analyzeMyEmotion(input: EmotionRequest, signal?: AbortSignal): Promise<EmotionResult> {
  if (FEATURE_SOURCE === 'mock') {
    await previewDelay(250, signal);
    return mockEmotion(input);
  }
  const result = await requestJson<Omit<EmotionResult, 'source'>>('/v1/emotions/analyze', input, { signal });
  if (result.subject !== input.speaker || (result.level !== null && ![1, 2, 3, 4, 5].includes(result.level)) || (result.trend !== null && !['up', 'flat', 'down'].includes(result.trend)) || typeof result.recommendation !== 'string' || !Number.isInteger(result.contextCount) || result.contextCount < 0 || !['ok', 'uncertain', 'insufficient_context'].includes(result.status ?? '')) throw new Error('나의 감정 응답 형식을 확인해주세요.');
  return { ...result, source: 'api' };
}

function validateVerdict(result: Omit<VerdictResult, 'source'>): VerdictResult {
  const scores = [result.plaintiff, result.defendant];
  if (typeof result.verdictId !== 'string' || !result.verdictId || !['WWE', 'UFC'].includes(result.mode) ||
      ![result.summary, result.recommendation, result.humor].every((text) => typeof text === 'string') ||
      scores.some((score) => !score || ![score.logic, score.emotionControl, score.evidence].every((value) => Number.isFinite(value) && value >= 0 && value <= 100) || typeof score.strength !== 'string' || typeof score.improvement !== 'string') ||
      !Array.isArray(result.debateLog) || result.debateLog.some((entry) => !entry || !['prosecutor', 'defense', 'factcheck', 'judge'].includes(entry.role) || !Number.isInteger(entry.round) || entry.round < 1 || typeof entry.text !== 'string')) {
    throw new Error('판결 응답 형식을 확인해주세요.');
  }
  return { ...result, source: 'api' };
}

export async function requestVerdict(input: VerdictRequest, signal?: AbortSignal): Promise<VerdictResult> {
  if (FEATURE_SOURCE === 'mock') {
    await previewDelay(2200, signal);
    return mockVerdict(input);
  }
  return validateVerdict(await requestJson<Omit<VerdictResult, 'source'>>('/v1/verdicts', { ...input, request_id: input.request_id ?? newRequestId() }, { signal, timeoutMs: 420_000 }));
}

export async function previewReaction(input: ReactionRequest, signal?: AbortSignal): Promise<ReactionResult> {
  if (FEATURE_SOURCE === 'mock') {
    await previewDelay(200, signal);
    return { status: 'ok', recipient: input.recipient, draft_revision: input.draft_revision, emotion: 'neutral', probabilities: { neutral: 1, happy: 0, sad: 0, angry: 0, surprised: 0, fear: 0, disgust: 0, contempt: 0 }, intensity: 0.25, confidence: 1, explanation: '시연용 고정 예시입니다. AI 분석이 아닙니다.', provider: 'mock', source: 'mock' };
  }
  const result = await requestJson<Omit<ReactionResult, 'source'>>('/v1/reactions/preview', input, { signal });
  const emotions = ['neutral', 'happy', 'sad', 'angry', 'surprised', 'fear', 'disgust', 'contempt'];
  if (result.recipient !== input.recipient || result.draft_revision !== input.draft_revision || !['ok', 'uncertain', 'insufficient_context'].includes(result.status) || (result.emotion !== null && !emotions.includes(result.emotion)) || (result.intensity !== null && (!Number.isFinite(result.intensity) || result.intensity < 0 || result.intensity > 1))) throw new Error('상대 반응 응답 형식을 확인해주세요.');
  return { ...result, source: 'api' };
}
