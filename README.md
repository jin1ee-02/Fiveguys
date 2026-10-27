# KU래쪄용 갈등 중재 MVP

두 사람의 1대1 채팅에 언어 순화, 감정 온도계, 상대 반응 미리보기, 다툼판결을 붙인 앱입니다. Expo SDK 57(React Native) 앱과 FastAPI 서버, SQLite로 구성됩니다.

## 문서

| 문서 | 내용 |
|---|---|
| [기능 명세서](docs/기능-명세서.md) | **여기부터.** 기능별로 무엇을, 언제, 어떻게 쓰는지와 내부 동작, 판결 점수 산출, 오류 원인, 환경 변수, API |
| [AI 활용 설명](docs/AI-활용-설명.md) | 프롬프트와 파이프라인을 왜 그렇게 설계했는지 (발표·보고서용) |
| [논문·소스코드 적용 검토](docs/논문-소스코드-적용-검토.md) | 기획서의 논문 4편과 평가 자료를 어떻게 반영했는지, 평가 스크립트 사용법 |

이전의 인계·제안·방법론 문서와 API 예시 파일은 2026-10-08에 위 세 문서로 정리하고 삭제했습니다. 필요하면 git 기록(커밋 `2ee9be4`)에서 볼 수 있습니다.

## 실행

Python 3.11 이상, Node.js 22.13 이상이 필요합니다. Windows에서는 프로젝트 루트에서 실행합니다.

```powershell
.\scripts\setup.ps1 -MvpMode
```

두 터미널에서 각각 실행합니다.

```powershell
.\scripts\start-backend.ps1
```

```powershell
.\scripts\start-frontend.ps1
```

프론트 http://localhost:8081, API 문서 http://127.0.0.1:8000/docs, 현재 모델 구성 http://127.0.0.1:8000/v1/config.

macOS·Linux는 `backend`에서 가상환경을 만들어 `pip install -r requirements-dev.txt` 후 `python -m uvicorn app.main:app --port 8000 --workers 1`, `frontend`에서 `npm ci` 후 `npm run web`을 실행합니다. 두 폴더의 `.env.example`을 `.env`로 복사해 두세요.

## API 키

`backend/.env`에만 넣습니다. 프론트에는 넣지 않습니다. 값을 바꾸면 백엔드를 다시 시작합니다.

```text
OPENAI_API_KEY=sk-...        # 넣으면 실제 LLM, 비우면 오프라인 규칙(AI 아님)으로 동작
TYPESAFE_API_KEY=...         # 선택. 넣으면 LLM + JEV 하이브리드 분석이 켜짐
```

키가 없어도 전체 흐름이 실행되며 화면에 "오프라인 규칙 기반 예시 · LLM 연결 전"이라고 표시됩니다. 나머지 설정은 [기능 명세서 12장](docs/기능-명세서.md#12-환경-변수)을 보세요.

## 두 사람이 써 보기

1. 한 사람이 `대화방 만들기`를 누르고 목록에 뜬 초대 코드를 상대에게 전달합니다.
2. 상대는 **다른 브라우저나 시크릿 창, 또는 다른 기기**에서 `코드로 참가하기`를 누릅니다. 같은 브라우저의 두 탭은 저장소를 공유해 한 사람으로 취급됩니다.
3. 휴대폰에서 접속하려면 `frontend/.env`의 `EXPO_PUBLIC_API_URL`에 PC의 LAN 주소를 쓰고, `.\scripts\start-backend.ps1 -BindAddress 0.0.0.0`으로 서버를 띄우고, `backend/.env`의 `CORS_ORIGINS`에 그 웹 주소를 추가합니다.

## 확인 상태 (2026-10-11)

```bash
cd backend
python -m unittest discover -s tests     # 45개 통과
cd ../frontend
npx tsc --noEmit && npx expo lint && npm test   # 타입, 린트, 단위 테스트 5개 통과
```

- 실제 모델(`gpt-4o-mini`)로 메시지 분석, 순화, 상대 반응, 판결을 실행해 확인했습니다. 측정 결과는 기능 명세서 5.5, 5.6, 8.5, 11장에 있습니다.
- 10월 11일에 순화 대안이 초안과 무관하게 비슷해지고 화자가 뒤바뀌던 문제를 고쳤습니다(대안을 전용 호출로 분리, 프롬프트 `ku-mvp-ko-v5`). 전후 측정은 기능 명세서 5.6에 있습니다.
- 10월 27일에 판결 반론 기능을 뺐고, 판결 결과에 점수 구간의 뜻·심사위원별 원점수·채점 기준표를 붙였습니다(기능 명세서 8.3).
- 같은 날 흐름을 "입력 → Core State → Light·Heavy가 함께 사용"으로 바꿨습니다(프롬프트 `ku-mvp-ko-v7`). Core State는 메시지마다 응답 뒤에 갱신되고, 순화·상대 반응·메시지 분석과 판결이 모두 읽습니다. 판결 토론은 토론자·심사위원이 각자 독립된 객체이고 진행자가 차례를 정합니다. 순화 민감도 설정은 뺐습니다(기능 명세서 1장 전체 흐름·4.1·8.2, AI 활용 설명 3·5.2장).
- TypeSafe JEV 실제 호출, 실제 휴대폰 기기 실행, 사람이 라벨링한 대화로의 정확도 평가는 아직 하지 않았습니다.
- 단일 서버(`--workers 1`), 2초 폴링 구성이며 로그인·WebSocket·운영 배포는 범위 밖입니다.
