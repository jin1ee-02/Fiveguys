import { personalThermometer } from '../api/thermometer';
import type { ChatTurn, Speaker } from '../types/api';
import type { DraftPreviewRequest, DraftReview, Thermometers, VerdictRequest, VerdictResult } from '../types/features';

// Fixtures for frontend interaction previews. These are not AI models.
const sharp = /그딴|맨날|항상\s*그|다\s*네\s*탓|너\s*때문|멍청|바보|닥쳐|꺼져|대단하시네|미친놈/;
const boundary = /닥쳐|꺼져|말하지\s*마/;
const repair = /미안|함께|같이\s*하|이해해|괜찮아/;

export const DEMO_CONVERSATION: ChatTurn[] = [
  { speaker: 'A', text: '오늘 청소 같이 하기로 했잖아.' },
  { speaker: 'B', text: '미안, 오늘 너무 바빠서 못 했어.' },
  { speaker: 'A', text: '지난번에도 그래서 좀 답답해.' },
  { speaker: 'B', text: '내일은 같이 할 수 있을 것 같아.' },
];
export const DEMO_DRAFT = '진짜 대단하시네 ㅋㅋ 맨날 그 모양이지';

export function mockDraftReview(input: DraftPreviewRequest): DraftReview {
  const suggested = sharp.test(input.text);
  const alternatives = boundary.test(input.text)
    ? ['지금은 대화를 이어가기 어려워. 잠깐 쉬고 싶어.', '내 감정을 정리할 시간이 필요해. 잠시 멈춰줄래?', '지금 이 말은 불편하게 느껴져. 조금 차분하게 이야기하고 싶어.']
    : ['오늘은 좀 아쉬웠어. 다음에는 미리 말해주면 좋겠어.', '비슷한 일이 반복돼서 답답해. 이번에는 같이 해결해볼 수 있을까?', '내가 불편했던 부분을 설명하고 싶어. 내 입장도 들어줄래?'];
  return {
    draft: input.text,
    decision: suggested ? 'suggested' : 'safe',
    alternatives: suggested ? alternatives : [],
    explanation: suggested ? '예시 표현을 감지해 순화 패널을 보여줍니다. 대안은 UI 시연용 문장입니다.' : '시연용 확인을 마쳤어요.',
    source: 'mock',
  };
}

// Keyword stand-in for one message's conflict score (0-100); not a model.
function previewConflict(text: string): number {
  if (/닥쳐|꺼져|미친놈/.test(text)) return 95;
  if (sharp.test(text)) return 70;
  if (/답답|짜증|화가|늦네|속상/.test(text)) return 45;
  if (repair.test(text)) return 0;
  return 20;
}

/** Both thermometers for the offline preview, each from that person's own messages. */
export function mockThermometers(messages: ChatTurn[], viewer: Speaker): Thermometers {
  const of = (who: Speaker) => personalThermometer(who, messages.filter((m) => m.speaker === who).map((m) => previewConflict(m.text)), 'mock');
  return { mine: of(viewer), partner: of(viewer === 'A' ? 'B' : 'A') };
}

export function mockVerdict(input: VerdictRequest): VerdictResult {
  return {
    verdictId: `preview-${Date.now()}`,
    mode: input.mode,
    plaintiff: { logic: 72, emotionControl: 45, evidence: 80, logicReason: '약속과 반복된 상황을 이유로 든 예시입니다.', emotionControlReason: '일반화 표현이 있었다는 예시입니다.', evidenceReason: '상대도 인정한 약속을 언급했다는 예시입니다.', strength: '구체적인 약속과 반복된 상황을 짚었습니다.', improvement: '상대를 일반화하는 표현을 줄이면 요청이 더 잘 전달됩니다.' },
    defendant: { logic: 50, emotionControl: 78, evidence: 35, logicReason: '사정은 말했지만 이유가 짧았다는 예시입니다.', emotionControlReason: '사과하며 차분히 답했다는 예시입니다.', evidenceReason: '확인할 수 있는 사실이 적었다는 예시입니다.', strength: '상황을 설명하며 대화를 이어가려고 했습니다.', improvement: '약속을 지키기 어려웠다면 미리 공유하는 방식이 필요합니다.' },
    conclusion: '시연 예시: 이번 다툼은 B 쪽 책임이 조금 더 큽니다. 약속을 지키지 못했고 미리 알리지 않았기 때문이에요.',
    causeA: 30, causeReason: '시연 예시: 약속을 지키지 못한 쪽은 B라는 예시입니다.',
    conductA: 55, conductReason: '시연 예시: A가 "맨날"로 일반화해 말투의 책임은 비슷하다는 예시입니다.',
    keyMoments: [],
    summary: '약속을 지키는 것과 사정을 설명하는 것 모두 중요해요. 책임을 따지기 전에 다음 약속을 구체적으로 정해보세요.',
    panelScores: [
      { persona: 'logic', belief: 0.65, plaintiff: { logic: 75, emotionControl: 45, evidence: 80 }, defendant: { logic: 50, emotionControl: 75, evidence: 35 } },
      { persona: 'empathy', belief: 0.6, plaintiff: { logic: 70, emotionControl: 40, evidence: 80 }, defendant: { logic: 50, emotionControl: 80, evidence: 35 } },
      { persona: 'evidence', belief: 0.7, plaintiff: { logic: 71, emotionControl: 50, evidence: 80 }, defendant: { logic: 50, emotionControl: 79, evidence: 35 } },
    ],
    recommendation: '가능한 시간과 할 일을 함께 정하고, 일정이 바뀌면 먼저 알려주기로 합의해보세요.',
    humor: input.mode === 'WWE' ? '오늘의 메인 이벤트는 청소! 링 위의 말싸움은 내려놓고 빗자루를 같이 들어볼까요?' : '이번 라운드는 잠시 휴식. 다음 라운드는 서로의 이야기를 끝까지 듣기로 해요.',
    debateLog: [
      { role: 'prosecutor', round: 1, strategy: '반복된 약속 불이행을 먼저 짚는 전략 예시입니다.', text: '약속이 반복해서 지켜지지 않은 점을 검토하는 발언 예시입니다.' },
      { role: 'defense', round: 1, text: '상대의 사정과 대안을 함께 살펴보는 발언 예시입니다.' },
      { role: 'factcheck', round: 1, text: '대화에 확인된 사실과 추측을 구분하는 발언 예시입니다.' },
      { role: 'judge', round: 1, text: '양쪽의 강점과 개선할 점을 정리하는 발언 예시입니다.' },
    ],
    source: 'mock',
  };
}
