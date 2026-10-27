import { router } from 'expo-router';
import { ScrollView, Text, View } from 'react-native';
import { FeatureToggle } from '../components/features/FeatureToggle';
import { ActionButton, ui } from '../components/ui';
import { useChat } from '../state/ChatContext';

const relationships = ['친구', '연인', '직장 동료', '룸메이트', '기타'];

export default function RoomSettingsScreen() {
  const { relationship, setRelationship, interactionBusy, settings, updateSettings } = useChat();
  return (
    <ScrollView style={ui.page} contentContainerStyle={ui.content}>
      <Text style={ui.eyebrow}>대화방 설정</Text><Text style={ui.title}>더 편안한 대화</Text>
      <View style={ui.card}>
        <View style={[ui.row, { justifyContent: 'space-between' }]}><View style={{ flex: 1 }}><Text style={ui.label}>언어 순화</Text><Text style={ui.subtitle}>전하고 싶은 말을 부드럽게 다듬어요.</Text></View><FeatureToggle title="순화" enabled={settings.purifyEnabled} disabled={interactionBusy} onChange={(enabled) => updateSettings({ purifyEnabled: enabled })} /></View>
        <View style={[ui.row, { justifyContent: 'space-between' }]}><View style={{ flex: 1 }}><Text style={ui.label}>감정 온도계</Text><Text style={ui.subtitle}>대화 속 나의 감정을 돌아봐요.</Text></View><FeatureToggle title="온도계" enabled={settings.thermometerEnabled} disabled={interactionBusy} onChange={(enabled) => updateSettings({ thermometerEnabled: enabled })} /></View>
        <View style={[ui.row, { justifyContent: 'space-between' }]}><View style={{ flex: 1 }}><Text style={ui.label}>상대 반응 미리보기</Text><Text style={ui.subtitle}>보내기 전에 예상 반응을 살펴봐요.</Text></View><FeatureToggle title="상대 반응" enabled={!!settings.reactionEnabled} disabled={interactionBusy} onChange={(enabled) => updateSettings({ reactionEnabled: enabled })} /></View>
      </View>
      <Text style={ui.subtitle}>AI 모드에서는 대화 갈등 온도를 갱신하기 위해 전송 메시지를 분석합니다. 기능을 끄면 해당 기능의 추가 분석과 표시를 멈춥니다.</Text>
      <View style={ui.card}><Text style={ui.label}>어떤 사이인가요?</Text><Text style={ui.subtitle}>관계에 맞는 표현으로 대화를 이어가요.</Text><View style={[ui.row, { flexWrap: 'wrap' }]}>{relationships.map((value) => <ActionButton key={value} title={`${relationship === value ? '✓ ' : ''}${value}`} secondary={relationship !== value} disabled={interactionBusy} onPress={() => setRelationship(value)} />)}</View></View>
      <ActionButton title="채팅으로 돌아가기" secondary onPress={() => router.replace('/chat')} />
    </ScrollView>
  );
}
