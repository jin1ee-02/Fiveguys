import { StyleSheet, Text, View } from 'react-native';
import { THERMOMETER_STAGES } from '../../api/thermometer';
import type { Speaker } from '../../types/api';
import type { Thermometer, Thermometers } from '../../types/features';
import { colors, ui } from '../ui';
import { SourceBadge } from './SourceBadge';

const STAGE_COLORS = ['#278864', '#70A96C', '#D4A324', '#DE793C', '#C85050'];

function trendText(thermometer: Thermometer) {
  return thermometer.trend === 'up' ? '상승 중 ↑' : thermometer.trend === 'down' ? '낮아지는 중 ↓' : thermometer.trend === 'flat' ? '유지 중 →' : '첫 메시지';
}

export function ThermometerCard({ thermometers, viewer }: { thermometers: Thermometers | null; viewer: Speaker }) {
  const mine = thermometers?.mine ?? null;
  const partner = thermometers?.partner ?? null;
  const shown = mine ?? partner;
  const level = mine?.level ?? null;
  return (
    <View style={styles.card}>
      <View style={[ui.row, { justifyContent: 'space-between' }]}>
        <Text style={ui.label}>감정 온도계 · 나({viewer})</Text>
        {level ? <Text style={[styles.level, { color: STAGE_COLORS[level - 1] }]}>{THERMOMETER_STAGES[level - 1]} · {level}/5</Text> : null}
      </View>
      <View style={styles.gauge} accessibilityLabel={level ? `나의 감정 온도 ${THERMOMETER_STAGES[level - 1]}, 5단계 중 ${level}단계` : '나의 감정 온도 미확정'}>
        {THERMOMETER_STAGES.map((stage, index) => (
          <View key={stage} style={{ flex: 1, gap: 5 }}>
            <View style={[styles.segment, { backgroundColor: level && index < level ? STAGE_COLORS[index] : colors.border }]} />
            <Text style={[styles.caption, level === index + 1 && { fontWeight: '800', color: STAGE_COLORS[index] }]}>{stage}</Text>
          </View>
        ))}
      </View>
      {mine ? (
        <>
          <Text style={styles.caption}>내 메시지 {mine.messageCount}개 누적 · {Math.round(mine.temperature)}/100 · {trendText(mine)}</Text>
          <Text style={styles.caption}>{mine.recommendation}</Text>
        </>
      ) : <Text style={styles.caption}>메시지를 보내면 나의 감정 온도를 표시합니다.</Text>}
      <Text style={styles.caption}>
        상대({viewer === 'A' ? 'B' : 'A'}) · {partner ? <Text style={{ fontWeight: '800', color: STAGE_COLORS[partner.level - 1] }}>{THERMOMETER_STAGES[partner.level - 1]} {partner.level}/5</Text> : '아직 메시지 없음'}{partner ? ` · ${trendText(partner)}` : ''} · 메시지에 표현된 정도이며 실제 마음과 다를 수 있어요
      </Text>
      {shown ? <SourceBadge source={shown.source} provider={shown.provider} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.surface, borderBottomWidth: 1, borderColor: colors.border, paddingHorizontal: 16, paddingVertical: 10, gap: 6 },
  level: { fontSize: 12, fontWeight: '800' },
  gauge: { flexDirection: 'row', gap: 4 },
  segment: { height: 5, borderRadius: 3 },
  caption: { fontSize: 10, color: colors.muted, lineHeight: 14 },
});
