import { useEffect, useRef, useState } from 'react';
import { router } from 'expo-router';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { FEATURE_SOURCE, requestVerdict } from '../api/features';
import { newRequestId } from '../api/client';
import { getSavedVerdict, getVerdictHistory } from '../api/rooms';
import { RubricTable, ScoreCard, SCORE_ITEMS } from '../components/features/ScoreCard';
import { SourceBadge } from '../components/features/SourceBadge';
import { ActionButton, colors, ui } from '../components/ui';
import { useChat } from '../state/ChatContext';
import type { VerdictMode, VerdictRequest, VerdictResult } from '../types/features';

const phases = ['대화와 추가 상황 확인', '검사 · 변호사 의견 정리', '근거와 양쪽 주장 비교', '판결 카드 작성'];
const roles = { prosecutor: '검사', defense: '변호사', factcheck: '팩트체크', judge: '판사' };
const personas = { logic: '논리 심사', empathy: '공감 심사', evidence: '근거 심사' };

export default function VerdictScreen() {
  const { messages, relationship, lastVerdict, setLastVerdict, beginInteraction, session, speaker, aiProvider } = useChat();
  const [history, setHistory] = useState<Omit<VerdictResult, 'source'>[]>([]);
  const [stage, setStage] = useState<'form' | 'loading' | 'result' | 'error'>(lastVerdict ? 'result' : 'form');
  const [mode, setMode] = useState<VerdictMode>(lastVerdict?.snapshot.mode ?? 'WWE');
  const [context, setContext] = useState('');
  const [error, setError] = useState('');
  const [step, setStep] = useState(0);
  const [showLog, setShowLog] = useState(false);
  const [showStrategy, setShowStrategy] = useState(false);
  const [showCore, setShowCore] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [showRubric, setShowRubric] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const running = useRef(false);
  const finishInteraction = useRef<(() => void) | null>(null);
  const lastAttempt = useRef<{ input: VerdictRequest; requestId: string } | null>(null);
  const recent = messages.slice(-10);
  const hasBoth = recent.some((turn) => turn.speaker === 'A') && recent.some((turn) => turn.speaker === 'B');
  const result = lastVerdict?.result;

  useEffect(() => () => { controller.current?.abort(); finishInteraction.current?.(); }, []);
  useEffect(() => {
    if (!session || FEATURE_SOURCE !== 'api') return;
    let active = true;
    getVerdictHistory(session.roomId).then((value) => { if (active) setHistory(value); }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '판결 기록을 불러오지 못했어요.'); });
    return () => { active = false; };
  }, [session]);
  useEffect(() => {
    if (stage !== 'loading' || FEATURE_SOURCE !== 'mock') return;
    const timer = setInterval(() => setStep((current) => Math.min(current + 1, phases.length - 1)), 500);
    return () => clearInterval(timer);
  }, [stage]);

  async function run(input: VerdictRequest, requestId = newRequestId()) {
    if (running.current) return;
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController; running.current = true;
    const finish = beginInteraction();
    finishInteraction.current = finish;
    input = { ...input, request_id: requestId };
    lastAttempt.current = { input, requestId };
    setStage('loading'); setError(''); setStep(0);
    try {
      const data = await requestVerdict(input, requestController.signal);
      if (!requestController.signal.aborted) {
        setLastVerdict({ result: data, snapshot: input }); setHistory((current) => [data, ...current.filter((item) => item.verdictId !== data.verdictId)].slice(0, 20)); setShowLog(false); setStage('result');
      }
    } catch (cause) {
      if (!requestController.signal.aborted) { setError(cause instanceof Error ? cause.message : '판결을 받지 못했어요.'); setStage('error'); }
    } finally { finish(); if (controller.current === requestController) running.current = false; }
  }
  function start() {
    if (!hasBoth) return;
    if (FEATURE_SOURCE === 'api' && !session) { setError('대화방을 만든 뒤 요청해주세요.'); return; }
    run({ room_id: session?.roomId ?? 'presentation-room', requester: session?.speaker ?? speaker, mode, relationship, context: context.trim(), recent_messages: recent.map(({ speaker, text }) => ({ speaker, text })) });
  }
  function cancel() { controller.current?.abort(); finishInteraction.current?.(); running.current = false; setStage(lastVerdict ? 'result' : 'form'); }
  function retry() { const attempt = lastAttempt.current; if (attempt) run(attempt.input, attempt.requestId); }

  async function reopen(verdictId: string) {
    if (running.current) return;
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController; running.current = true; setError('');
    const finish = beginInteraction();
    finishInteraction.current = finish;
    try {
      const saved = await getSavedVerdict(verdictId, requestController.signal);
      if (!requestController.signal.aborted) { setLastVerdict({ result: { ...saved.result, source: 'api' }, snapshot: saved.snapshot }); setStage('result'); }
    }
    catch (cause) { if (!requestController.signal.aborted) setError(cause instanceof Error ? cause.message : '판결 기록을 불러오지 못했어요.'); }
    finally { finish(); if (controller.current === requestController) running.current = false; }
  }

  return (
    <ScrollView style={ui.page} contentContainerStyle={ui.content} keyboardShouldPersistTaps="handled">
      <View style={[ui.row, { justifyContent: 'space-between' }]}><Text style={ui.eyebrow}>KU래쪄용 · 다툼판결</Text><ActionButton title="채팅으로" secondary onPress={() => { controller.current?.abort(); router.replace('/chat'); }} style={styles.smallButton} /></View>
      <Text style={ui.title}>{stage === 'result' ? '서로의 입장을 살펴봤어요' : stage === 'loading' ? '다툼을 살펴보는 중' : '잠깐, 판결을 부탁해요'}</Text>
      <Text style={ui.subtitle}>논리력 · 감정 조절 · 근거를 비교하고 대화를 이어갈 방법을 정리합니다.</Text>
      <SourceBadge source={FEATURE_SOURCE} provider={aiProvider} />

      {stage === 'form' && <>
        <View style={ui.card}><Text style={ui.label}>어떤 분위기로 볼까요?</Text><View style={ui.row}>{(['WWE', 'UFC'] as const).map((value) => <ActionButton key={value} title={value} secondary={mode !== value} onPress={() => setMode(value)} style={{ flex: 1 }} />)}</View><Text style={ui.subtitle}>{mode === 'WWE' ? '상황 중심의 가벼운 유머를 곁들여 돌아봅니다.' : '주장과 근거에 집중해 차분하게 돌아봅니다.'} 두 모드는 같은 점수 기준을 사용합니다.</Text></View>
        <View style={ui.card}>
          <Text style={ui.label}>이번 판결에 사용할 대화</Text><Text style={ui.subtitle}>{relationship} · 최근 {recent.length}개 메시지 · 사용자 A / B</Text>
          <ActionButton title={showContext ? '대화 접기' : '전달할 대화 보기'} secondary onPress={() => setShowContext(!showContext)} disabled={!recent.length} />
          {showContext && recent.map((turn) => <View key={turn.id} style={styles.turn}><Text style={ui.eyebrow}>사용자 {turn.speaker}</Text><Text style={ui.subtitle}>{turn.text}</Text></View>)}
          {!hasBoth && <><Text style={ui.subtitle}>양쪽의 말을 비교할 수 있도록 나와 상대의 메시지를 한 개 이상 입력해주세요.</Text><ActionButton title="채팅으로 돌아가기" secondary onPress={() => router.replace('/chat')} /></>}
          <Text style={ui.subtitle}>요청 시점의 대화를 고정해서 분석합니다. 나중에 추가한 메시지는 새 요청에 포함됩니다.</Text>
        </View>
        <View style={ui.card}><Text style={ui.label}>추가로 알려줄 상황 (선택)</Text><TextInput accessibilityLabel="판결에 참고할 추가 상황" multiline maxLength={1000} value={context} onChangeText={setContext} placeholder="예: 이번 주 청소는 상대 차례였어요." placeholderTextColor={colors.muted} style={styles.input} /><Text style={styles.caption}>{context.length} / 1000</Text></View>
        <ActionButton title="이 대화로 다툼판결 요청하기" onPress={start} disabled={!hasBoth} />
      </>}

      {stage === 'loading' && <View style={ui.card}>
        <ActivityIndicator size="large" color={colors.primary} accessibilityLabel="판결 응답 대기 중" />
        <Text style={ui.label}>잠시만 기다려주세요.</Text>
        {FEATURE_SOURCE === 'mock' ? <>{phases.map((phase, index) => <Text key={phase} style={[ui.subtitle, index === step && { color: colors.primary, fontWeight: '800' }]}>{index < step ? '✓' : index === step ? '●' : '○'} {phase}</Text>)}<Text style={styles.caption}>발표용 진행 연출입니다. 실제 AI 토론이 실행되는 것은 아닙니다.</Text></> : <Text style={ui.subtitle}>대화를 쟁점·입장·사실관계로 정리한 뒤 검사 · 변호사 · 팩트체크가 토론하고 관점이 다른 심사위원단이 채점합니다. 판단이 안정되면 일찍 끝나요.</Text>}
        <ActionButton title="요청 취소" secondary onPress={cancel} />
      </View>}

      {stage === 'error' && <View style={ui.card}><Text accessibilityRole="alert" style={ui.error}>{error}</Text><ActionButton title="같은 요청으로 다시 시도" onPress={retry} /><ActionButton title="요청 내용 수정하기" secondary onPress={() => setStage('form')} />{result && <ActionButton title="이전 결과 보기" secondary onPress={() => setStage('result')} />}</View>}

      {stage === 'result' && result && <>
        <View style={styles.resultHeader}><Text style={ui.eyebrow}>{result.mode} · {lastVerdict.snapshot.relationship}</Text><Text style={ui.subtitle}>요청한 대화 {lastVerdict.snapshot.recent_messages.length}개 기준</Text><SourceBadge source={result.source} provider={result.provider} /></View>
        <View style={styles.scores}><ScoreCard title={session?.speaker === 'B' ? '상대 (A)' : '나 (A)'} score={result.plaintiff} /><ScoreCard title={session?.speaker === 'B' ? '나 (B)' : '상대 (B)'} score={result.defendant} accent="#397596" /></View>
        <View style={ui.card}>
          <Text style={ui.label}>점수는 이렇게 매겨졌어요</Text>
          <Text style={styles.caption}>A는 A가 보낸 메시지만, B는 B가 보낸 메시지만 채점합니다. 심사위원이 항목마다 실제 발화를 인용한 근거를 먼저 쓰고, 그 근거가 속한 구간 안에서 점수를 정합니다. 카드의 점수는 심사위원{result.panelScores?.length ? ` ${result.panelScores.length}명` : '단'}의 평균이고, 근거 문장은 평균에 가장 가까운 점수를 준 심사위원의 것입니다. 두 모드(WWE·UFC)의 기준은 같습니다.</Text>
          {result.panelScores && result.panelScores.length > 0 && <View style={{ gap: 4 }}>
            <Text style={ui.eyebrow}>심사위원별 점수 (논리력 / 감정 조절 / 근거)</Text>
            {result.panelScores.map((panel) => <Text key={panel.persona} style={styles.caption}>{personas[panel.persona] ?? panel.persona} · A {SCORE_ITEMS.map(({ key }) => Math.round(panel.plaintiff[key])).join(' / ')} · B {SCORE_ITEMS.map(({ key }) => Math.round(panel.defendant[key])).join(' / ')} · A 쪽으로 {Math.round(panel.belief * 100)}%</Text>)}
          </View>}
          <ActionButton title={showRubric ? '채점 기준 접기' : '채점 기준 보기'} secondary onPress={() => setShowRubric(!showRubric)} />
          {showRubric && <RubricTable />}
        </View>
        {result.rounds && <Text style={styles.caption}>{result.rounds}라운드 · {result.stopReason === 'stable' ? '판단이 안정되어 조기 종료' : '설정된 최대 라운드로 종료'}{result.modelCalls ? ` · 모델 호출 ${result.modelCalls}회` : ''}{result.usage ? ` · 입력 ${Math.round(result.usage.inputTokens)}토큰(캐시 ${Math.round(result.usage.cachedTokens)}) · ${Math.round(result.usage.seconds)}초` : ''} · 대화 버전 {result.snapshotVersion}</Text>}
        {result.roundTrace && result.roundTrace.length > 0 && <View style={ui.card}>
          <Text style={ui.label}>라운드별 판단 변화{result.judges && result.judges > 1 ? ` · 심사위원 ${result.judges}명` : ''}</Text>
          {result.roundTrace.map((trace) => <View key={trace.round} style={{ gap: 4 }}>
            <Text style={styles.caption}>{trace.round}라운드 · A 쪽으로 {Math.round(trace.belief * 100)}%{trace.beliefDelta !== null ? ` · 기울기 변화 ${trace.beliefDelta.toFixed(2)} · 점수 변화 최대 ${Math.round(trace.scoreDelta ?? 0)}점` : ' · 첫 라운드'}{trace.votes ? ` · 의견 A ${trace.votes.A ?? 0} / 비슷 ${trace.votes.even ?? 0} / B ${trace.votes.B ?? 0}` : ''}</Text>
            <View style={styles.track}><View style={{ height: 6, borderRadius: 3, backgroundColor: colors.primary, width: `${Math.round(trace.belief * 100)}%` }} /></View>
          </View>)}
          <Text style={styles.caption}>승패가 아니라 심사위원단의 판단이 라운드 사이에 얼마나 움직였는지를 보여줍니다. 점수와 의견 분포가 더 움직이지 않으면 토론을 멈춥니다.</Text>
        </View>}
        {result.coreState && <View style={ui.card}>
          <ActionButton title={showCore ? '대화 정리 접기' : '정리된 쟁점과 입장 보기'} secondary onPress={() => setShowCore(!showCore)} />
          {showCore && <>
            <Text style={ui.eyebrow}>쟁점</Text>{result.coreState.issues.map((issue) => <Text key={issue} style={ui.subtitle}>· {issue}</Text>)}
            <Text style={ui.eyebrow}>A의 입장</Text><Text style={ui.subtitle}>{result.coreState.position_a}</Text>
            <Text style={ui.eyebrow}>B의 입장</Text><Text style={ui.subtitle}>{result.coreState.position_b}</Text>
            {result.coreState.facts.length > 0 && <><Text style={ui.eyebrow}>대화에서 확인되는 내용</Text>{result.coreState.facts.map((fact, index) => <Text key={index} style={ui.subtitle}>· {fact.text} <Text style={styles.caption}>({fact.basis === 'both' ? '양쪽 인정' : `${fact.basis}의 말`}{fact.evidence_indices.length ? ` · ${fact.evidence_indices.map((i) => i + 1).join(', ')}번 메시지` : ''})</Text></Text>)}</>}
            <Text style={ui.eyebrow}>상황 설명</Text><Text style={ui.subtitle}>{result.coreState.background}</Text>
            {result.coreState.trajectory.length > 0 && <><Text style={ui.eyebrow}>감정 궤적</Text><View style={styles.trajectory}>{result.coreState.trajectory.map((point) => <View key={point.index} style={{ alignItems: 'center', gap: 2 }}><View style={{ width: 14, height: 6 + (point.emotion ?? 0) * 8, borderRadius: 3, backgroundColor: point.speaker === 'A' ? colors.primary : '#397596' }} /><Text style={styles.caption}>{point.speaker}</Text></View>)}</View></>}
          </>}
        </View>}
        <View style={ui.card}><Text style={ui.label}>판결 요약</Text><Text style={ui.subtitle}>{result.summary}</Text><Text style={ui.eyebrow}>다음 대화를 위한 제안</Text><Text style={ui.subtitle}>{result.recommendation}</Text></View>
        {result.humor ? <View style={[ui.card, { backgroundColor: '#FFF8DA' }]}><Text style={ui.label}>한 줄로 돌아보기</Text><Text style={ui.subtitle}>{result.humor}</Text></View> : null}
        <View style={ui.card}><ActionButton title={showLog ? '토론 과정 접기' : '토론 과정 보기'} secondary onPress={() => setShowLog(!showLog)} />{showLog && <><Text style={styles.caption}>{result.source === 'mock' ? '역할별 발언의 UI 예시입니다.' : '역할별 에이전트가 순서대로 주고받은 발언입니다.'}</Text><ActionButton title={showStrategy ? '발언 전 전략 숨기기' : '발언 전 전략도 보기'} secondary onPress={() => setShowStrategy(!showStrategy)} />{result.debateLog.map((entry, index) => <View key={`${entry.role}-${index}`} style={styles.turn}><Text style={ui.eyebrow}>{entry.persona ? personas[entry.persona] : roles[entry.role]} · {entry.round}라운드{typeof entry.belief === 'number' ? ` · A 쪽으로 ${Math.round(entry.belief * 100)}%` : ''}</Text>{showStrategy && entry.strategy ? <Text style={styles.strategy}>전략: {entry.strategy}</Text> : null}<Text style={ui.subtitle}>{entry.text}</Text>{entry.evidenceIndices && entry.evidenceIndices.length > 0 ? <Text style={styles.caption}>근거: {entry.evidenceIndices.map((i) => i + 1).join(', ')}번 메시지</Text> : null}</View>)}</>}</View>
        <ActionButton title="새 대화로 판결 요청하기" secondary onPress={() => { setContext(''); setShowContext(false); setStage('form'); }} />
        <ActionButton title="대화 이어가기" onPress={() => router.replace('/chat')} />
      </>}
      {FEATURE_SOURCE === 'api' && stage !== 'loading' && history.length > 0 && <View style={ui.card}><Text style={ui.label}>저장된 판결</Text>{history.map((item) => <ActionButton key={item.verdictId} title={`${item.mode} · ${item.summary.slice(0, 36)}`} secondary onPress={() => reopen(item.verdictId)} />)}</View>}
      {stage !== 'error' && error ? <Text style={ui.error}>{error}</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  smallButton: { paddingHorizontal: 12, paddingVertical: 10 },
  input: { minHeight: 100, maxHeight: 200, backgroundColor: colors.background, borderColor: colors.border, borderWidth: 1, borderRadius: 12, padding: 14, color: colors.ink, fontSize: 14, textAlignVertical: 'top' },
  caption: { color: colors.muted, fontSize: 12, lineHeight: 19 },
  turn: { padding: 12, backgroundColor: colors.background, borderRadius: 12, gap: 6 },
  scores: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  resultHeader: { gap: 6 },
  track: { height: 6, borderRadius: 3, backgroundColor: colors.border, overflow: 'hidden' },
  trajectory: { flexDirection: 'row', alignItems: 'flex-end', gap: 6 },
  strategy: { color: colors.muted, fontSize: 12, lineHeight: 18, fontStyle: 'italic' },
});
