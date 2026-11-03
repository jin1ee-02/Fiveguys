const assert = require('node:assert/strict');
const { test } = require('node:test');

process.env.EXPO_PUBLIC_API_MODE = 'local';
process.env.EXPO_PUBLIC_FEATURE_MODE = 'mock';
const { deliverMessage } = require('../.test-build/api/client');
const { reviewDraft, requestVerdict } = require('../.test-build/api/features');
const { personalThermometer, roomThermometers, thermometerLevel } = require('../.test-build/api/thermometer');
const { DEMO_DRAFT, mockDraftReview, mockThermometers } = require('../.test-build/mocks/features');
const input = { speaker: 'A', text: DEMO_DRAFT, relationship: '룸메이트', recent_messages: [], previous_temperature: 0 };

test('local + mock 기능은 서버 연결 없이 체험된다', async () => {
  const previousFetch = global.fetch;
  global.fetch = () => { throw new Error('Offline preview must not call HTTP'); };
  try {
    assert.deepEqual(await deliverMessage(input), { mode: 'local' });
    const review = await reviewDraft(input);
    assert.equal(review.source, 'mock');
    assert.equal(review.decision, 'suggested');
    assert.equal(review.alternatives.length, 3);
    const { mine, partner } = mockThermometers([{ speaker: 'A', text: '함께 해결해보자' }], 'A');
    assert.equal(mine.source, 'mock');
    assert.equal(mine.trend, null);
    assert.equal(partner, null);
  } finally { global.fetch = previousFetch; }
});

test('감정 온도계는 사람마다 자기 메시지만 반영한다', () => {
  const calm = { speaker: 'A', text: '함께 해결해보자' };
  const before = mockThermometers([calm], 'A');
  const after = mockThermometers([calm, { speaker: 'B', text: '닥쳐' }], 'A');
  assert.deepEqual(after.mine, before.mine);
  assert.equal(after.partner.subject, 'B');
  assert.ok(after.partner.temperature > after.mine.temperature);
  assert.deepEqual(mockThermometers([], 'A'), { mine: null, partner: null });
});

test('감정 온도는 거친 메시지에 빨리 오르고 차분한 메시지에 천천히 식는다', () => {
  assert.deepEqual([0, 19.9, 20, 45, 60, 80, 100].map(thermometerLevel), [1, 1, 2, 3, 4, 5, 5]);
  const once = personalThermometer('A', [100], 'api');
  assert.deepEqual([once.temperature, once.level, once.trend], [80, 5, null]);
  const calmer = personalThermometer('A', [100, 0], 'api');
  assert.deepEqual([calmer.temperature, calmer.trend, calmer.messageCount], [48, 'down', 2]);
  // A quarrel's peak stays at the top stage when the next insult scores a little lower than the last one.
  const peak = personalThermometer('B', [16, 52, 88, 100, 72], 'api');
  assert.deepEqual([peak.level, peak.trend], [5, 'down']);
  const message = (speaker, raw) => ({ id: 'm', createdAt: '', speaker, text: 't', result: { mode: 'ai', data: { features: { model_version: 'openai/gpt-4o-mini' }, raw_conflict: raw, previous_temperature: 0, temperature: 0 } } });
  const room = roomThermometers([message('A', 100), message('B', 0), message('A', 0)], 'B');
  assert.deepEqual([room.mine.subject, room.mine.temperature, room.partner.subject, room.partner.temperature, room.partner.provider], ['B', 0, 'A', 48, 'openai/gpt-4o-mini']);
  assert.deepEqual(roomThermometers([{ ...message('A', 50), result: { mode: 'local' } }], 'A'), { mine: null, partner: null });
});

test('순화 확인은 일반 문장에 대안을 강제하지 않는다', () => {
  assert.equal(mockDraftReview({ ...input, text: '오늘 같이 청소할래?' }).decision, 'safe');
  assert.deepEqual(mockDraftReview({ ...input, text: '오늘 같이 청소할래?' }).alternatives, []);
  assert.equal(mockDraftReview(input).decision, 'suggested');
});

test('초안 확인과 판결 요청을 취소하면 늦은 예시 결과를 반환하지 않는다', async () => {
  const draftController = new AbortController();
  const pending = reviewDraft(input, draftController.signal);
  draftController.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  const verdictController = new AbortController();
  verdictController.abort();
  await assert.rejects(requestVerdict({ room_id: 'demo', requester: 'A', mode: 'WWE', relationship: '친구', context: '', recent_messages: [] }, verdictController.signal), { name: 'AbortError' });
});
