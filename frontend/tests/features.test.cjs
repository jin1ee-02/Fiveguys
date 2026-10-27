const assert = require('node:assert/strict');
const { test } = require('node:test');

process.env.EXPO_PUBLIC_API_MODE = 'local';
process.env.EXPO_PUBLIC_FEATURE_MODE = 'mock';
const { deliverMessage } = require('../.test-build/api/client');
const { analyzeMyEmotion, reviewDraft, requestVerdict } = require('../.test-build/api/features');
const { DEMO_DRAFT, mockDraftReview, mockEmotion } = require('../.test-build/mocks/features');
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
    const emotion = await analyzeMyEmotion({ speaker: 'A', relationship: '친구', recent_messages: [{ speaker: 'A', text: '함께 해결해보자' }] });
    assert.equal(emotion.subject, 'A');
    assert.equal(emotion.source, 'mock');
  } finally { global.fetch = previousFetch; }
});

test('상대의 공격적 발화를 나의 감정 점수로 표시하지 않는다', () => {
  const base = { speaker: 'A', relationship: '친구' };
  const own = { speaker: 'A', text: '함께 해결해보자' };
  const before = mockEmotion({ ...base, recent_messages: [own] });
  const after = mockEmotion({ ...base, recent_messages: [own, { speaker: 'B', text: '닥쳐' }] });
  assert.equal(after.subject, 'A');
  assert.equal(after.level, before.level);
  assert.equal(after.trend, 'flat');
  assert.equal(after.contextCount, 2);
});

test('나의 감정 변화는 나의 발화끼리 비교한다', () => {
  const result = mockEmotion({ speaker: 'A', relationship: '친구', recent_messages: [
    { speaker: 'A', text: '답답해' }, { speaker: 'B', text: '닥쳐' }, { speaker: 'A', text: '함께 해보자' },
  ] });
  assert.equal(result.level, 1);
  assert.equal(result.trend, 'down');
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
