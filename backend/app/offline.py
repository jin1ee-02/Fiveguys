"""Offline stand-in for the LLM: keyword rules so the whole pipeline runs before an API key is connected.

It implements the same interface as OpenAIProvider (light / analyze / alternatives / emotion /
reaction / structured), so nothing else changes when the real model is plugged in.
The output is NOT an AI judgment. Every result is labelled `offline/rules-v1` and the UI says so.
"""

import re

from app.schemas import CoreStateOutput, DebateOutput, FactItem, FeatureScores, JudgeOutput, LightOutput, MediationOutput, SideScore

LABEL = "offline/rules-v1"
INSULT = re.compile(r"닥쳐|꺼져|병신|멍청|바보|미친|죽을래|재수\s*없|한심")
DISMISS = re.compile(r"됐어|됐고|말하지\s*마|알아서\s*해|어쩌라고|관심\s*없|그만해")
SARCASM = re.compile(r"대단하시네|대단하네요\^|잘났|퍽이나|참\s*잘(한다|하네)|그\s*모양|역시.{0,6}(시네|구나\s*\^)|어련하시겠")
BLAME = re.compile(r"맨날|항상|매번|넌\s*늘|한\s*번도|왜\s*(또|맨날)|너\s*때문|네\s*탓|니\s*탓|그딴")
MILD = re.compile(r"또\s|지난번에도|이번에도|약속했잖아|하기로\s*했잖아")
REPAIR = re.compile(r"미안|죄송|고마워|이해해|같이\s*.{0,8}(하자|해보자|할까)|내가\s*.{0,10}할게|괜찮아|다음엔|다음부터")
UPSET = re.compile(r"답답|짜증|화나|화가|속상|열받|서운|지쳐|힘들")
PRAISE = re.compile(r"대박|축하|최고|멋지|잘했")
ACK = re.compile(r"^(ㅇㅇ|응|웅|네|넵|넹|ㅇㅋ|오키|ㅋ+|ㅎ+|ㅠ+|ㅜ+|알겠어|알았어|고마워|감사합니다|[\W_]+)[\s.!~]*$")
TOPICS = ["청소", "설거지", "약속", "시간", "연락", "답장", "돈", "과제", "회의", "일정", "정리", "빨래"]


def trivial(text: str) -> bool:
    """Pre-filter (PDF p14): short acknowledgements need no model call."""
    return len(text.strip()) <= 8 and bool(ACK.match(text.strip()))


def topic_of(texts) -> str | None:
    joined = " ".join(texts)
    return next((word for word in TOPICS if word in joined), "약속" if "늦" in joined else None)


def josa(word: str, final: str, open_: str) -> str:
    """Attach the right Korean particle (을/를, 이/가) by whether the last syllable has a final consonant."""
    code = ord(word[-1]) - 0xAC00
    return word + (final if 0 <= code < 11172 and code % 28 else open_)


def score(text: str) -> dict:
    playful = bool(PRAISE.search(text)) and not BLAME.search(text)
    hostility = 0 if playful else 4 if len(INSULT.findall(text)) > 1 else 3 if INSULT.search(text) else 2 if DISMISS.search(text) else 0
    sarcasm = 0 if playful else 3 if SARCASM.search(text) else 0
    blame = 3 if BLAME.search(text) else 1 if MILD.search(text) else 0
    repair = 0 if sarcasm else 3 if len(REPAIR.findall(text)) > 1 else 2 if REPAIR.search(text) else 0
    emotion = max(hostility, sarcasm - 1, blame - 1, 2 if UPSET.search(text) and not repair else 1 if UPSET.search(text) else 0)
    emotion = min(4, emotion + (1 if sarcasm >= 2 and blame >= 2 else 0) + (1 if re.search(r"[!?]{2,}", text) and emotion else 0))
    harsh = max(hostility, sarcasm, blame)
    return {"hostility": hostility, "sarcasm": sarcasm, "blame": blame, "repair": repair, "emotion": emotion,
            "escalation_delta": 2 if harsh >= 4 else 1 if harsh >= 2 else -1 if repair >= 2 else 0}


def rewrite(text: str, context=()) -> list[str]:
    topic = topic_of([text, *context])
    if DISMISS.search(text) or re.search(r"닥쳐|꺼져", text):
        return ["지금은 대화를 이어가기 어려워. 잠깐 쉬고 싶어.", "감정을 정리할 시간이 필요해. 조금 있다가 얘기하자.", "지금 이 대화는 나한테 불편해. 차분해진 뒤에 다시 이야기하고 싶어."]
    subject = f"{topic} 문제" if topic else "이런 일"
    return [f"{josa(subject, '이', '가')} 반복돼서 좀 속상했어. 다음엔 미리 말해주면 좋겠어.", f"{josa(subject, '이', '가')} 계속 미뤄져서 답답해.", f"{josa(subject, '은', '는')} 나한테 중요해. 언제 할 수 있는지 정해줘."]


class OfflineProvider:
    vendor = "offline"
    model = "rules-v1"
    cache_enabled = False

    def light(self, request):
        values = score(request.text)
        toxic = max(values["hostility"], values["sarcasm"], values["blame"]) >= 2
        reasons = [name for key, name in [("hostility", "날카로운 표현"), ("sarcasm", "비꼬는 표현"), ("blame", "일반화된 비난")] if values[key] >= 2]
        rationale = "규칙 기반 예시: " + (", ".join(reasons) + "이 감지됐어요." if reasons else "뚜렷한 공격 신호가 없어요.")
        features = FeatureScores(**values, confidence=3, rationale=rationale, model_version=LABEL)
        return features, rewrite(request.text, [t.text for t in request.recent_messages]) if toxic else []

    def analyze(self, request):
        return self.light(request)[0]

    def alternatives(self, request):
        return rewrite(request.text, [t.text for t in request.recent_messages])

    def emotion(self, request):
        own = [score(t.text)["emotion"] for t in request.recent_messages if t.speaker == request.speaker]
        return float(own[-1]), float(own[-2]) if len(own) > 1 else None, 0.75, LABEL

    def reaction(self, request):
        values = score(request.text)
        weights = {"neutral": 1.0, "happy": 0.1 + values["repair"], "sad": 0.2 + values["blame"] * 0.6, "angry": 0.1 + values["hostility"] + values["blame"] * 0.5,
                   "surprised": 0.2, "fear": 0.1, "disgust": 0.1 + values["hostility"] * 0.3, "contempt": 0.1 + values["sarcasm"] * 0.8}
        total = sum(weights.values())
        probabilities = {key: round(value / total, 4) for key, value in weights.items()}
        probabilities["neutral"] = round(probabilities["neutral"] + 1 - sum(probabilities.values()), 4)
        top = max(probabilities, key=probabilities.get)
        intensity = min(1.0, max(values["hostility"], values["sarcasm"], values["blame"], values["repair"]) / 4 + 0.15)
        return top, probabilities, intensity, 0.7, LABEL

    # Same seam as OpenAIProvider.structured: (static prompt, shared case, turn payload) -> schema instance.
    def structured(self, prompt, payload, schema, tokens=0, shared=None):
        case = (shared or payload).get("case", shared or payload)
        messages = case["messages"]
        by = {s: [(i, m["text"]) for i, m in enumerate(messages) if m["speaker"] == s] for s in "AB"}
        topic = topic_of([m["text"] for m in messages]) or "서로의 표현 방식"
        if schema is LightOutput:
            raise NotImplementedError
        if schema is MediationOutput:
            return MediationOutput(text=f"두 분 모두 {topic} 문제를 해결하고 싶어 하지만, 한쪽은 약속이 지켜지길 바라고 다른 쪽은 사정을 이해받길 바라는 것으로 보입니다. 지금은 누가 맞는지보다 '다음에는 언제, 어떻게 할지'를 한 가지만 구체적으로 제안해보시면 어떨까요?")
        if schema is CoreStateOutput:
            facts = [FactItem(text=text[:120], basis=speaker, evidence_indices=[i]) for speaker in "AB" for i, text in by[speaker] if MILD.search(text) or re.search(r"못\s*했|늦|했잖아|할게|바빠", text)][:6]
            background = f"{case.get('relationship') or '두 사람'} 사이에서 {josa(topic, '을', '를')} 두고 생긴 갈등입니다." + (f" 요청자의 추가 설명(주장): {payload['context']}" if payload.get("context") else "")
            position = lambda who: " / ".join(text for _, text in by[who][-2:])[:300] or "(대화에 발언 없음)"
            return CoreStateOutput(position_a=position("A"), position_b=position("B"),
                                   issues=[topic, "약속을 지키지 못했을 때 알리는 방식", "서로에게 말하는 표현"][: 3 if facts else 2], facts=facts, background=background)
        stats = {s: [score(text) for _, text in by[s]] for s in "AB"}
        mean = lambda s, key: sum(x[key] for x in stats[s]) / max(1, len(stats[s]))
        if schema is DebateOutput:
            role, said = payload["role"], bool(payload.get("transcript"))
            side, other = ("A", "B") if role == "prosecutor" else ("B", "A")
            if role == "factcheck":
                return DebateOutput(strategy="양쪽 발언에서 대화로 확인되는 내용과 한쪽 주장만 있는 내용을 나눠서 짚는다.",
                                    text=f"대화에서 확인되는 것은 {topic}에 관한 약속이 있었고 지켜지지 않았다는 점입니다. 왜 지켜지지 않았는지, 과거에도 반복됐는지는 한쪽의 말만 있어 대화만으로는 확인할 수 없습니다.",
                                    evidence_indices=[i for i, _ in (by["A"][:1] + by["B"][:1])])
            harsh = mean(other, "hostility") + mean(other, "sarcasm") + mean(other, "blame")
            return DebateOutput(strategy=f"{side}의 핵심 요구를 먼저 세우고, {other}의 {'날 선 표현' if harsh >= 2 else '설명 부족'}을 짚되 타당한 부분은 인정한다." + (" 앞선 발언의 일반화된 부분을 반박한다." if said else ""),
                                text=(f"{side}는 {topic}에 대해 '{by[side][-1][1][:60]}'라고 말하며 자신의 입장을 밝혔습니다. " if by[side] else f"{side}의 발언은 대화에 없어 직접 확인할 수 있는 입장이 없습니다. ") + ("앞선 발언이 지적한 부분은 일부 인정하지만, " if said else "") + f"{other}의 {'표현이 감정적으로 격해진 점' if harsh >= 2 else '사정이 충분히 공유되지 않은 점'}은 돌아볼 필요가 있습니다.",
                                evidence_indices=[i for i, _ in by[side][-3:]])
        if schema is JudgeOutput:
            def side(s):
                control = round(max(10, min(95, 88 - 20 * (mean(s, "hostility") + mean(s, "sarcasm")) - 8 * mean(s, "blame"))))
                evidence = round(max(15, min(90, 40 + 14 * sum(bool(MILD.search(t) or re.search(r"했잖아|때문에|바빠서|못\s*했", t)) for _, t in by[s]))))
                logic = round(max(20, min(90, 58 + 6 * mean(s, "repair") - 7 * mean(s, "blame") + 4 * min(3, len(by[s])))))
                return SideScore(logic=logic, emotionControl=control, evidence=evidence,
                                 logicReason="규칙 기반 예시: 발화 수와 사과, 일반화 표현의 빈도로 계산했어요.",
                                 emotionControlReason="규칙 기반 예시: 날카롭거나 비꼬는 표현의 빈도로 계산했어요.",
                                 evidenceReason="규칙 기반 예시: 약속이나 사정을 언급한 발화 수로 계산했어요.",
                                 strength="상대의 사정을 인정하며 대화를 이어가려 했습니다." if mean(s, "repair") >= 1 else "자신이 불편했던 점을 분명히 전달했습니다.",
                                 improvement="일반화하거나 비꼬는 표현 대신 원하는 행동을 구체적으로 요청해보세요." if control < 60 else "어려운 사정은 미리 공유하고 대안을 함께 제시해보세요.")
            a, b = side("A"), side("B")
            total = lambda x: x.logic + x.emotionControl + x.evidence
            # A panel member leans on its own angle; without a persona all three items count equally.
            angle = next((key for word, key in [("논리", "logic"), ("공감", "emotionControl"), ("근거", "evidence")] if word in payload.get("persona", "")), None)
            gap = (getattr(a, angle) - getattr(b, angle)) / 100 if angle else (total(a) - total(b)) / 300
            belief = round(max(0.15, min(0.85, 0.5 + gap)), 2)
            return JudgeOutput(plaintiff=a, defendant=b, unresolved=False, belief=belief,
                               summary=f"{josa(topic, '을', '를')} 둘러싼 갈등입니다. 약속의 중요성을 말한 쪽과 사정을 설명한 쪽 모두 이해할 수 있는 지점이 있고, 표현 방식에서 감정이 커졌습니다.",
                               recommendation=f"{josa(topic, '을', '를')} 언제 누가 할지 구체적으로 정하고, 지키기 어려우면 먼저 알려주기로 약속해보세요.",
                               humor=f"오늘의 메인 이벤트는 {topic}! 링 위의 말싸움은 잠시 내려놓고 태그팀으로 해결해볼까요?" if case.get("mode") == "WWE" else "")
        raise NotImplementedError(schema)
