import type { Message, Speaker } from '../types/api';
import type { FeatureSource, Thermometer, ThermometerLevel, Thermometers } from '../types/features';

// Each person's thermometer accumulates the conflict scores of that person's own messages (backend/app/scoring.py).
// It rises fast and cools slowly: a harsher message moves it 80% of the way to its score, a calmer one 40%.
// A symmetric half-and-half mix (until 2026-11-03) needed three 100-point messages in a row to reach the top stage
// and dropped during a quarrel's peak whenever the next insult scored a little lower than the last one.
// The room temperature keeps 0.7 / 0.3. The 40 / 60 / 80 bands are where the rewrite threshold tightens.
export const THERMOMETER_STAGES = ['평온', '주의', '경고', '위험', '폭발'] as const;
const RISE = 0.8;
const COOL = 0.4;
const ADVICE = [
  '차분하게 원하는 점을 이야기해보세요.',
  '불편했던 점을 구체적으로 정리해보세요.',
  '잠깐 쉬면서 원하는 점을 정리해보세요.',
  '답장을 잠시 미루고 충분히 쉬어보세요.',
  '대화를 잠시 멈추고 진정한 뒤 이어가보세요.',
];

export function thermometerLevel(temperature: number): ThermometerLevel {
  return temperature < 20 ? 1 : temperature < 40 ? 2 : temperature < 60 ? 3 : temperature < 80 ? 4 : 5;
}

/** One person's thermometer from the conflict scores (0-100) of their own messages, oldest first. */
export function personalThermometer(subject: Speaker, scores: number[], source: FeatureSource, provider?: string | null): Thermometer | null {
  if (!scores.length) return null;
  let previous = 0;
  let temperature = 0;
  for (const score of scores) {
    previous = temperature;
    const rate = score > temperature ? RISE : COOL;
    temperature = Math.round((temperature + rate * (score - temperature)) * 10) / 10;
  }
  const level = thermometerLevel(temperature);
  // The first message has nothing to compare with, so it shows no trend.
  const change = scores.length > 1 ? temperature - previous : null;
  const trend = change === null ? null : change >= 1 ? 'up' : change <= -1 ? 'down' : 'flat';
  return { subject, temperature, level, trend, messageCount: scores.length, recommendation: ADVICE[level - 1], source, provider };
}

/** Both thermometers of a room from the conflict score stored with each message; no model call of its own. */
export function roomThermometers(messages: Message[], viewer: Speaker): Thermometers {
  const of = (who: Speaker) => {
    const own = messages.filter((m) => m.speaker === who && m.result.mode === 'ai');
    const scores = own.map((m) => m.result.mode === 'ai' ? m.result.data.raw_conflict : 0);
    const last = own[own.length - 1];
    return personalThermometer(who, scores, 'api', last && last.result.mode === 'ai' ? last.result.data.features.model_version : null);
  };
  return { mine: of(viewer), partner: of(viewer === 'A' ? 'B' : 'A') };
}
