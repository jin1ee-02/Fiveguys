import { useRef, useState } from 'react';
import { router } from 'expo-router';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { API_MODE } from '../api/client';
import { ActionButton, colors, ui } from '../components/ui';
import { useChat } from '../state/ChatContext';

export default function ChatListScreen() {
  const { messages, relationship, session, sessions, ready, syncError, openRoom, newRoom, enterRoom } = useChat();
  const [invite, setInvite] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  async function run(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { await operation(); setInvite(''); router.push('/chat'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '대화방에 연결하지 못했어요.'); }
    finally { setBusy(false); lock.current = false; }
  }
  return <ScrollView style={ui.page} contentContainerStyle={ui.content} keyboardShouldPersistTaps="handled">
    <Text style={ui.eyebrow}>KU래쪄용</Text><Text style={ui.title}>대화의 시작</Text>
    <Text style={ui.subtitle}>서로의 말을 이해하는 채팅을 만들어가요.</Text>
    {API_MODE === 'ai' ? <>
      <View style={ui.card}><Text style={ui.label}>새로운 대화</Text><Text style={ui.subtitle}>대화방을 만든 뒤 상대에게 초대 코드를 전달하세요. 서로 다른 기기에서 함께 대화할 수 있어요.</Text><ActionButton title={busy ? '연결 중…' : '대화방 만들기'} disabled={busy || !ready} onPress={() => run(newRoom)} /></View>
      <View style={ui.card}><Text style={ui.label}>초대받은 대화</Text><TextInput accessibilityLabel="대화방 초대 코드" value={invite} onChangeText={setInvite} autoCapitalize="none" autoCorrect={false} maxLength={100} placeholder="초대 코드를 입력하세요" placeholderTextColor={colors.muted} style={{ padding: 12, borderWidth: 1, borderColor: colors.border, borderRadius: 10, color: colors.ink }} /><ActionButton title="코드로 참가하기" disabled={busy || !ready || invite.trim().length < 10} onPress={() => run(() => enterRoom(invite))} /></View>
      {sessions.map((saved) => <View key={saved.roomId} style={ui.card}>
        <Text style={ui.label}>대화방 {saved.roomId.slice(0, 6)} · 나({saved.speaker})</Text>
        {saved.roomId === session?.roomId && <Text style={ui.subtitle}>{relationship} · {messages[messages.length - 1]?.text ?? '첫 메시지를 입력해보세요.'}</Text>}
        {saved.inviteCode && <><Text style={ui.subtitle}>상대 초대 코드 · 1회 사용</Text><Text selectable style={ui.label}>{saved.inviteCode}</Text></>}
        <ActionButton title="대화 이어가기" secondary disabled={busy || !ready} onPress={() => { openRoom(saved); router.push('/chat'); }} />
      </View>)}
    </> : <View style={ui.card}><Text style={ui.label}>시연 대화방</Text><Text style={ui.subtitle}>사용자 A · 사용자 B / {relationship}</Text><ActionButton title="시연 시작하기 →" onPress={() => router.push('/chat')} /></View>}
    {error || syncError ? <Text accessibilityRole="alert" style={ui.error}>{error || syncError}</Text> : null}
    <View style={ui.card}><Text style={ui.label}>대화를 돕는 네 가지 기능</Text><Text style={ui.subtitle}>언어 순화, 나와 상대의 감정 온도계, 상대 반응 미리보기와 다툼판결로 서로의 입장과 다음 대화를 살펴보세요. AI 모드에서는 전송 메시지와 켠 기능의 분석 입력이 설정한 모델 서비스로 전달됩니다.</Text></View>
  </ScrollView>;
}
