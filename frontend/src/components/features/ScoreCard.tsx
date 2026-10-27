import { StyleSheet, Text, View } from 'react-native';
import type { ScoreKey, SideScore } from '../../types/features';
import { colors, ui } from '../ui';

// Same bands the judges are given in backend/app/prompts.py (JUDGE_PROMPT). Change both together.
export const SCORE_ITEMS: { key: ScoreKey; label: string; question: string; bands: [string, string, string, string] }[] = [
  { key: 'logic', label: '논리력', question: '자기 주장에 이유를 댔는가', bands: ['이유 없는 단정·회피("그냥", "몰라"), 자기모순, 논점 이탈', '주장은 있으나 이유가 빈약하거나 일반화에 기댐', '주장과 이유가 연결됨', '이유가 구체적이고 상대의 반박에도 일관되게 답함'] },
  { key: 'emotionControl', label: '감정 조절', question: '표현의 존중과 절제', bands: ['욕설, 인신공격, 비하, 위협', '비꼼, "맨날" 같은 일반화, 대화 차단, 몰아붙임', '불만을 드러내지만 상대를 공격하지 않음', '차분하고 상대 입장을 인정함'] },
  { key: 'evidence', label: '근거', question: '대화에서 확인되는 사실을 댔는가', bands: ['근거 제시 없음', '한쪽의 주장뿐이라 확인할 수 없음', '날짜·약속 같은 구체적 사실을 언급', '상대도 인정했거나 대화에서 확인되는 사실'] },
];
const RANGES = ['0~25', '26~50', '51~75', '76~100'];

export function band(value: number) {
  return value <= 25 ? 0 : value <= 50 ? 1 : value <= 75 ? 2 : 3;
}

export function ScoreCard({ title, score, accent = colors.primary }: { title: string; score: SideScore; accent?: string }) {
  const reasons: Record<ScoreKey, string | undefined> = { logic: score.logicReason, emotionControl: score.emotionControlReason, evidence: score.evidenceReason };
  return (
    <View style={styles.card}>
      <Text style={[ui.label, { color: accent }]}>{title}</Text>
      {SCORE_ITEMS.map(({ key, label, bands }) => {
        const value = score[key];
        const level = band(value);
        return <View key={key} style={{ gap: 6 }}>
          <View style={[ui.row, { justifyContent: 'space-between' }]}><Text style={ui.subtitle}>{label}</Text><Text style={[ui.label, { color: accent }]}>{value}<Text style={ui.subtitle}> / 100</Text></Text></View>
          <View style={styles.track}><View style={{ height: 7, borderRadius: 4, backgroundColor: accent, width: `${Math.min(100, Math.max(0, value))}%` }} /></View>
          <Text style={styles.band}>{RANGES[level]}구간 · {bands[level]}</Text>
          {reasons[key] ? <Text style={styles.reason}>{reasons[key]}</Text> : null}
        </View>;
      })}
      <Text style={ui.eyebrow}>잘 전달한 점</Text><Text style={ui.subtitle}>{score.strength}</Text>
      <Text style={ui.eyebrow}>더 나아질 점</Text><Text style={ui.subtitle}>{score.improvement}</Text>
    </View>
  );
}

export function RubricTable() {
  return (
    <View style={{ gap: 10 }}>
      {SCORE_ITEMS.map(({ key, label, question, bands }) => <View key={key} style={{ gap: 2 }}>
        <Text style={ui.eyebrow}>{label} · {question}</Text>
        {bands.map((text, index) => <Text key={index} style={styles.reason}>{RANGES[index]} {text}</Text>)}
      </View>)}
      <Text style={styles.reason}>추가 규칙: 욕설이나 위협이 한 번이라도 있으면 감정 조절은 50을 넘지 않습니다. 문장 길이나 유창함, 토론자의 변호, 잘못을 인정했다는 사실만으로는 점수를 올리지 않습니다. 누가 판결을 요청했는지와 갈등 온도는 점수에 쓰지 않습니다.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { flex: 1, flexBasis: 175, backgroundColor: colors.surface, borderRadius: 14, padding: 12, gap: 10, borderWidth: 1, borderColor: colors.border },
  track: { height: 7, borderRadius: 4, backgroundColor: colors.border, overflow: 'hidden' },
  band: { color: colors.ink, fontSize: 11, lineHeight: 16, fontWeight: '700' },
  reason: { color: colors.muted, fontSize: 11, lineHeight: 16 },
});
