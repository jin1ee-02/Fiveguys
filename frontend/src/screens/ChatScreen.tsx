import { useEffect, useMemo, useRef, useState } from 'react';
import { router } from 'expo-router';
import { ActivityIndicator, Keyboard, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { FEATURE_SOURCE } from '../api/features';
import { newRequestId } from '../api/client';
import { MessageBubble } from '../components/MessageBubble';
import { EmotionCard } from '../components/features/EmotionCard';
import { FeatureToggle } from '../components/features/FeatureToggle';
import { PurificationSheet } from '../components/features/PurificationSheet';
import { SourceBadge } from '../components/features/SourceBadge';
import { ReactionCard } from '../components/features/ReactionCard';
import { ActionButton, colors, ui } from '../components/ui';
import { useDraftReview } from '../hooks/useDraftReview';
import { useReactionPreview } from '../hooks/useReactionPreview';
import { useChat } from '../state/ChatContext';
import type { DraftPreviewRequest, ReactionRequest } from '../types/features';

export default function ChatScreen() {
  const { messages, relationship, temperature, sending, sendMessage, settings, updateSettings, emotion, partnerEmotion, aiProvider, refreshEmotion, speaker, draft, setDraft, beginInteraction, interactionBusy, session, syncError, participantCount } = useChat();
  const insets = useSafeAreaInsets();
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [error, setError] = useState('');
  const [hint, setHint] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [purificationOpen, setPurificationOpen] = useState(false);
  const submitLock = useRef(false);
  const scroll = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);
  const busy = interactionBusy || submitting;
  const draftInput = useMemo<DraftPreviewRequest | null>(() => settings.purifyEnabled && draft.trim() ? {
    speaker, text: draft.trim(), relationship, previous_temperature: temperature, room_id: session?.roomId ?? null,
    recent_messages: messages.slice(-10).map(({ speaker, text }) => ({ speaker, text })),
  } : null, [settings.purifyEnabled, speaker, draft, relationship, temperature, messages, session?.roomId]);
  const { state: review, check } = useDraftReview(draftInput);
  const reactionInput = useMemo<ReactionRequest | null>(() => settings.reactionEnabled && draft.trim() ? {
    speaker, recipient: speaker === 'A' ? 'B' : 'A', text: draft.trim(), relationship, previous_temperature: temperature,
    recent_messages: messages.slice(-10).map(({ speaker, text }) => ({ speaker, text })), draft_revision: newRequestId(), room_id: session?.roomId ?? null,
  } : null, [settings.reactionEnabled, draft, speaker, relationship, temperature, messages, session?.roomId]);
  const reaction = useReactionPreview(reactionInput);

  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', () => setKeyboardOpen(true));
    const hide = Keyboard.addListener('keyboardDidHide', () => setKeyboardOpen(false));
    return () => { show.remove(); hide.remove(); };
  }, []);

  async function commit(text: string) {
    await sendMessage(text, speaker);
    setDraft(''); setHint(''); setPurificationOpen(false);
  }
  async function sendSelected(text: string) {
    if (submitLock.current || sending || !text.trim()) return;
    submitLock.current = true; setSubmitting(true); setError('');
    const finish = beginInteraction();
    try { await commit(text); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '전달에 실패했어요.'); }
    finally { finish(); submitLock.current = false; setSubmitting(false); }
  }
  async function send() {
    if (submitLock.current || sending || !draft.trim()) return;
    submitLock.current = true; setSubmitting(true); setError('');
    const finish = beginInteraction();
    try {
      if (draftInput) {
        const result = await check();
        if (result && result.decision !== 'safe') { Keyboard.dismiss(); setPurificationOpen(true); return; }
      }
      await commit(draft);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '표현을 확인하지 못했어요.');
      if (draftInput) { Keyboard.dismiss(); setPurificationOpen(true); }
    } finally { finish(); submitLock.current = false; setSubmitting(false); }
  }
  function showAlternatives() { Keyboard.dismiss(); setPurificationOpen(true); check().catch(() => {}); }
  function requestAlternatives() { Keyboard.dismiss(); setPurificationOpen(true); check(true, true, true).catch(() => {}); }
  function editAlternative(text: string) { setDraft(text); setPurificationOpen(false); setHint('대안을 입력창에 넣었어요. 고친 뒤 보내주세요.'); inputRef.current?.focus(); }

  return (
    <KeyboardAvoidingView style={ui.page} keyboardVerticalOffset={insets.top} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.shell}>
        <View style={styles.header}>
          <View style={ui.row}>
            <View style={styles.avatar}><Text style={styles.avatarText}>KU</Text></View>
            <View style={{ flex: 1 }}><Text style={styles.roomTitle}>{session ? '우리의 대화' : '시연 대화방'}</Text><Text style={styles.status}>{relationship} · {session ? `${participantCount}/2명 참가 · 나(${session.speaker})` : '나와 상대의 대화'}</Text></View>
            <ActionButton title="목록" secondary disabled={busy} onPress={() => router.push('/')} style={styles.smallButton} />
            <ActionButton title="설정" secondary disabled={busy} onPress={() => router.push('/settings')} style={styles.smallButton} />
          </View>
          {!keyboardOpen && <>
            <View style={styles.features}>
              <FeatureToggle title="언어 순화" enabled={settings.purifyEnabled} disabled={busy} onChange={(enabled) => updateSettings({ purifyEnabled: enabled })} />
              <FeatureToggle title="온도계" enabled={settings.thermometerEnabled} disabled={busy} onChange={(enabled) => updateSettings({ thermometerEnabled: enabled })} />
              <FeatureToggle title="상대 반응" enabled={!!settings.reactionEnabled} disabled={busy} onChange={(enabled) => updateSettings({ reactionEnabled: enabled })} />
              <ActionButton title="⚖ 판결" accessibilityLabel="다툼판결 요청" onPress={() => { Keyboard.dismiss(); router.push('/verdict'); }} disabled={busy} style={styles.smallButton} />
            </View>
            <SourceBadge source={FEATURE_SOURCE} provider={aiProvider} />
          </>}
        </View>
        {syncError ? <Text style={[ui.error, { paddingHorizontal: 16 }]}>{syncError}</Text> : null}
        {settings.thermometerEnabled && !keyboardOpen && <EmotionCard state={emotion} partner={partnerEmotion} onRetry={refreshEmotion} />}
        <ScrollView ref={scroll} style={{ flex: 1, minHeight: 0 }} contentContainerStyle={styles.messages} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive" onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}>
          {messages.length === 0 && <View style={styles.empty}><Text style={ui.label}>편하게 대화를 시작해보세요</Text><Text style={[ui.subtitle, { textAlign: 'center' }]}>전하고 싶은 말을 아래에 입력하세요.</Text></View>}
          {messages.map((message) => <MessageBubble key={message.id} message={message} viewer={session?.speaker ?? 'A'} />)}
        </ScrollView>
        <View style={styles.composer}>
          <ReactionCard state={reaction.state} onRetry={reaction.retry} />
          <View style={[ui.row, { alignItems: 'flex-end' }]}>
            <TextInput ref={inputRef} accessibilityLabel="메시지 입력" style={styles.input} value={draft} onChangeText={(text) => { setDraft(text); setError(''); setHint(''); }} multiline maxLength={2000} editable={!busy} placeholder="전하고 싶은 말을 입력하세요" placeholderTextColor={colors.muted} />
            <ActionButton title={busy ? '…' : '↑'} accessibilityLabel="메시지 보내기" disabled={busy || !draft.trim()} onPress={send} style={styles.sendButton} />
          </View>
          <View style={[ui.row, { justifyContent: 'space-between' }]}><Text style={styles.status}>{draftInput && review.status === 'loading' ? '표현을 확인하고 있어요…' : `사용자 ${speaker}의 메시지`}</Text><Text style={styles.status}>{draft.length} / 2000</Text>{busy && <ActivityIndicator size="small" color={colors.primary} accessibilityLabel="응답 대기 중" />}</View>
          {hint ? <Text style={styles.status}>{hint}</Text> : null}
          {draftInput && review.status === 'ready' && review.data?.decision !== 'safe' && <Pressable accessibilityRole="button" disabled={busy} onPress={showAlternatives} style={styles.suggestion}><Text style={styles.link}>더 부드러운 표현이 있어요 · 대안 보기 →</Text></Pressable>}
          {draftInput && review.status === 'ready' && review.data?.decision === 'safe' && <Pressable accessibilityRole="button" disabled={busy} onPress={requestAlternatives} hitSlop={6}><Text style={styles.status}>다른 표현도 보고 싶다면 · 다듬기 제안 받기 →</Text></Pressable>}
          {draftInput && review.status === 'error' && <Text style={ui.error}>순화를 확인하지 못했어요. 보내기에서 재시도하거나 원문을 선택할 수 있어요.</Text>}
          {error ? <Text accessibilityRole="alert" style={ui.error}>{error}</Text> : null}
        </View>
      </View>
      <PurificationSheet visible={purificationOpen && settings.purifyEnabled} original={draft} state={review} busy={busy} input={settings.reactionEnabled ? draftInput : null} originalReaction={settings.reactionEnabled ? reaction.state : undefined} onChoose={sendSelected} onEdit={editAlternative} onOriginal={() => sendSelected(draft)} onClose={() => setPurificationOpen(false)} onRetry={() => { check(true).catch(() => {}); }} />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  shell: { flex: 1, minHeight: 0, width: '100%', maxWidth: 440, alignSelf: 'center' },
  header: { paddingHorizontal: 16, paddingVertical: 12, gap: 9, backgroundColor: colors.surface, borderBottomWidth: 1, borderColor: colors.border },
  avatar: { width: 34, height: 34, borderRadius: 11, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: '#FFFFFF', fontSize: 12, fontWeight: '800' },
  roomTitle: { color: colors.ink, fontSize: 18, fontWeight: '800', letterSpacing: -0.4 },
  features: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8 },
  status: { color: colors.muted, fontSize: 10, lineHeight: 15 },
  link: { color: colors.primary, fontSize: 11, fontWeight: '700' },
  smallButton: { minHeight: 34, paddingHorizontal: 10, paddingVertical: 8 },
  messages: { padding: 16, gap: 14, flexGrow: 1 },
  empty: { flex: 1, minHeight: 100, justifyContent: 'center', alignItems: 'center', gap: 8 },
  composer: { paddingHorizontal: 14, paddingVertical: 12, gap: 7, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  input: { flex: 1, minWidth: 0, minHeight: 44, maxHeight: 96, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, paddingHorizontal: 12, paddingVertical: 11, color: colors.ink, fontSize: 13, lineHeight: 20, textAlignVertical: 'top' },
  sendButton: { width: 44, minHeight: 44, paddingHorizontal: 0, paddingVertical: 10, borderRadius: 14 },
  suggestion: { backgroundColor: colors.light, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 9 },
});
