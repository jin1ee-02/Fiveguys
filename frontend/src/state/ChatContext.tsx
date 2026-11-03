import { createContext, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { API_MODE, deliverMessage, newRequestId, requestJson, setRoomToken } from '../api/client';
import { FEATURE_SOURCE } from '../api/features';
import { roomThermometers } from '../api/thermometer';
import { createRoom, getRoom, joinRoom, postMessage, saveSettings } from '../api/rooms';
import { DEMO_CONVERSATION, DEMO_DRAFT, mockThermometers } from '../mocks/features';
import type { Message, Speaker } from '../types/api';
import type { RoomSession, RoomSettings, RoomState, Thermometers, VerdictRequest, VerdictResult } from '../types/features';

interface ChatState {
  messages: Message[];
  relationship: string;
  setRelationship: (value: string) => void;
  temperature: number;
  sending: boolean;
  speaker: Speaker;
  setSpeaker: (value: Speaker) => void;
  draft: string;
  setDraft: (value: string) => void;
  demoRevision: number;
  interactionBusy: boolean;
  beginInteraction: () => () => void;
  sendMessage: (text: string, speaker: Speaker) => Promise<void>;
  settings: RoomSettings;
  updateSettings: (patch: Partial<RoomSettings>) => void;
  thermometers: Thermometers | null;
  aiProvider: string | null;
  loadDemo: () => void;
  resetConversation: () => void;
  lastVerdict: { result: VerdictResult; snapshot: VerdictRequest } | null;
  setLastVerdict: (value: { result: VerdictResult; snapshot: VerdictRequest } | null) => void;
  session: RoomSession | null;
  sessions: RoomSession[];
  ready: boolean;
  syncError: string;
  participantCount: number;
  openRoom: (session: RoomSession) => void;
  newRoom: () => Promise<void>;
  enterRoom: (invite: string) => Promise<void>;
}

const ChatContext = createContext<ChatState | undefined>(undefined);

export function ChatProvider({ children }: PropsWithChildren) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [relationship, setRelationship] = useState('친구');
  const [temperature, setTemperature] = useState(0);
  const [sending, setSending] = useState(false);
  const [speaker, setSpeaker] = useState<Speaker>('A');
  const [draft, setDraft] = useState('');
  const [demoRevision, setDemoRevision] = useState(0);
  const [interactionCount, setInteractionCount] = useState(0);
  const [settings, setSettings] = useState<RoomSettings>({ purifyEnabled: true, thermometerEnabled: true, reactionEnabled: true });
  const [session, setSession] = useState<RoomSession | null>(null);
  const [sessions, setSessions] = useState<RoomSession[]>([]);
  const [ready, setReady] = useState(false);
  const [syncError, setSyncError] = useState('');
  const [participantCount, setParticipantCount] = useState(1);
  const [aiProvider, setAiProvider] = useState<string | null>(null);
  const roomVersion = useRef(-1);
  const activeRoom = useRef<string | null>(null);
  const pendingSend = useRef<{ text: string; requestId: string } | null>(null);
  const settingsRef = useRef(settings);
  const relationshipRef = useRef(relationship);
  const settingsQueue = useRef(Promise.resolve());
  const settingsWrites = useRef(0);
  const [lastVerdict, setLastVerdict] = useState<{ result: VerdictResult; snapshot: VerdictRequest } | null>(null);
  const inFlight = useRef(false);
  const sequence = useRef(0);
  const interactionCountRef = useRef(0);
  // Each person's thermometer comes from the conflict scores of their own messages; no model call of its own.
  const roomMode = API_MODE === 'ai' && !!session;
  const viewer = session?.speaker ?? speaker;
  const thermometers = useMemo(() => !settings.thermometerEnabled ? null
    : !roomMode && FEATURE_SOURCE === 'mock' ? mockThermometers(messages, viewer) : roomThermometers(messages, viewer),
  [settings.thermometerEnabled, roomMode, messages, viewer]);

  useEffect(() => {
    AsyncStorage.getItem('ku-mvp-sessions-v1').then((raw) => {
      if (!raw) return;
      const saved = JSON.parse(raw) as { sessions: RoomSession[]; activeId: string | null };
      const valid = saved.sessions.filter((s) => typeof s.roomId === 'string' && typeof s.token === 'string' && ['A', 'B'].includes(s.speaker));
      setSessions(valid);
      if (API_MODE === 'ai') {
        const active = valid.find((s) => s.roomId === saved.activeId);
        if (active) { activeRoom.current = active.roomId; setRoomToken(active.token); setSession(active); setSpeaker(active.speaker); }
      }
    }).catch(() => setSyncError('저장된 대화방을 복원하지 못했어요.')).finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (API_MODE !== 'ai') return;
    requestJson<{ llmProvider?: string; generationModel?: string }>('/v1/config').then((config) => setAiProvider(`${config.llmProvider ?? 'openai'}/${config.generationModel ?? ''}`)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!ready) return;
    AsyncStorage.setItem('ku-mvp-sessions-v1', JSON.stringify({ sessions, activeId: session?.roomId ?? null })).catch(() => setSyncError('기기 저장소에 대화방 정보를 저장하지 못했어요.'));
  }, [sessions, session, ready]);

  function applyRoom(state: RoomState) {
    if (activeRoom.current !== state.roomId) return;
    if (state.version < roomVersion.current) return;
    if (state.version !== roomVersion.current) {
      setMessages(state.messages); setTemperature(state.temperature); roomVersion.current = state.version;
    }
    setParticipantCount(state.participantCount);
    if (!settingsWrites.current) {
      setRelationship(state.relationship); relationshipRef.current = state.relationship;
      setSettings((current) => JSON.stringify(current) === JSON.stringify(state.settings) ? current : state.settings);
      settingsRef.current = state.settings;
    }
    setSyncError('');
  }

  useEffect(() => {
    if (API_MODE !== 'ai' || !session) return;
    const controller = new AbortController();
    let running = false;
    const sync = async () => {
      if (running || inFlight.current || settingsWrites.current) return;
      running = true;
      try { const state = await getRoom(session.roomId, controller.signal); if (!controller.signal.aborted) applyRoom(state); }
      catch (cause) { if (!controller.signal.aborted) setSyncError(cause instanceof Error ? cause.message : '대화 동기화 실패'); }
      finally { running = false; }
    };
    sync();
    const timer = setInterval(sync, 2000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [session]);

  function openRoom(next: RoomSession) {
    if (inFlight.current || interactionCountRef.current || settingsWrites.current) return;
    activeRoom.current = next.roomId; roomVersion.current = -1; pendingSend.current = null;
    setRoomToken(next.token); setSession(next); setSpeaker(next.speaker);
    setMessages([]); setTemperature(0); setDraft(''); setLastVerdict(null);
    setDemoRevision((value) => value + 1);
  }

  async function newRoom() {
    if (!ready || API_MODE !== 'ai') return;
    const next = await createRoom(relationship);
    setSessions((current) => [...current, next]); openRoom(next);
  }

  async function enterRoom(invite: string) {
    if (!ready || API_MODE !== 'ai') return;
    const next = await joinRoom(invite.trim());
    setSessions((current) => [...current, next]); openRoom(next);
  }

  function persistSettings(nextRelationship: string, nextSettings: RoomSettings) {
    if (!session || API_MODE !== 'ai') return;
    settingsWrites.current += 1;
    settingsQueue.current = settingsQueue.current.then(async () => {
      try { await saveSettings(session.roomId, nextRelationship, nextSettings); }
      catch (cause) { setSyncError(cause instanceof Error ? cause.message : '설정을 저장하지 못했어요.'); }
      finally { settingsWrites.current -= 1; }
    });
  }

  function changeRelationship(value: string) {
    setRelationship(value); relationshipRef.current = value; persistSettings(value, settingsRef.current);
  }
  function updateSettings(patch: Partial<RoomSettings>) {
    const next = { ...settingsRef.current, ...patch }; settingsRef.current = next; setSettings(next); persistSettings(relationshipRef.current, next);
  }

  function loadDemo() {
    if (API_MODE === 'ai') return;
    if (inFlight.current || interactionCountRef.current) return;
    const timestamp = Date.now();
    setMessages(DEMO_CONVERSATION.map((turn, index) => ({
      ...turn, id: `demo-${timestamp}-${index}`, createdAt: new Date(timestamp - (DEMO_CONVERSATION.length - index) * 60_000).toISOString(), result: { mode: 'local' },
    })));
    setRelationship('룸메이트');
    relationshipRef.current = '룸메이트';
    setTemperature(0);
    setSettings((current) => ({ ...current, purifyEnabled: true, thermometerEnabled: true }));
    settingsRef.current = { ...settingsRef.current, purifyEnabled: true, thermometerEnabled: true };
    setLastVerdict(null);
    setSpeaker('A');
    setDraft(DEMO_DRAFT);
    setDemoRevision((value) => value + 1);
  }

  function resetConversation() {
    if (API_MODE === 'ai') return;
    if (inFlight.current || interactionCountRef.current) return;
    setMessages([]); setTemperature(0); setLastVerdict(null);
    setDraft(''); setSpeaker('A'); setDemoRevision((value) => value + 1);
  }

  function beginInteraction() {
    interactionCountRef.current += 1;
    setInteractionCount((value) => value + 1);
    let finished = false;
    return () => {
      if (finished) return;
      finished = true;
      interactionCountRef.current -= 1;
      setInteractionCount((value) => value - 1);
    };
  }

  async function sendMessage(text: string, speaker: Speaker) {
    const value = text.trim();
    if (!value || value.length > 2000) throw new Error('메시지를 1~2000자로 입력해주세요.');
    if (inFlight.current) throw new Error('이전 메시지의 전달을 기다려주세요.');
    inFlight.current = true;
    setSending(true);
    try {
      if (API_MODE === 'ai') {
        if (!session) throw new Error('대화방을 만들거나 초대 코드로 참가해주세요.');
        if (speaker !== session.speaker) throw new Error('참가한 사용자로만 메시지를 보낼 수 있어요.');
        if (!pendingSend.current || pendingSend.current.text !== value) pendingSend.current = { text: value, requestId: newRequestId() };
        const state = await postMessage(session.roomId, value, pendingSend.current.requestId);
        applyRoom(state); pendingSend.current = null;
        return;
      }
      const result = await deliverMessage({
        recent_messages: messages.slice(-10).map(({ speaker, text }) => ({ speaker, text })),
        speaker,
        text: value,
        relationship,
        previous_temperature: temperature,
      });
      const message: Message = {
        id: `${Date.now()}-${++sequence.current}`,
        createdAt: new Date().toISOString(),
        speaker,
        text: value,
        result,
      };
      setMessages((current) => [...current, message]);
      if (result.mode === 'ai') setTemperature(result.data.temperature);
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  return (
    <ChatContext.Provider value={{
      messages, relationship, setRelationship: changeRelationship, temperature, sending, sendMessage,
      speaker, setSpeaker: (value) => { if (!session) setSpeaker(value); }, draft, setDraft, demoRevision,
      interactionBusy: sending || interactionCount > 0, beginInteraction,
      settings, updateSettings,
      thermometers, aiProvider, loadDemo, resetConversation,
      lastVerdict, setLastVerdict,
      session, sessions, ready, syncError, participantCount, openRoom, newRoom, enterRoom,
    }}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  const context = useContext(ChatContext);
  if (!context) throw new Error('ChatProvider is required');
  return context;
}
