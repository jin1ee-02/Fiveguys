// KU래쪄용 시연 녹화: 한 화면에 사용자 A(localhost)와 B(127.0.0.1)를 나란히 띄워 실제 백엔드로 대화한다.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const OUT = path.join(__dirname, 'video');
const W = 1920, H = 1080;
const WRAPPER = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>KU래쪄용 시연</title><style>
html,body{margin:0;height:100%;overflow:hidden;background:#17201B;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif}
#cap{height:72px;display:flex;align-items:center;gap:18px;padding:0 28px;background:#1F7A50;color:#fff;box-sizing:border-box}
#step{font-size:15px;font-weight:800;background:#fff;color:#1F7A50;border-radius:999px;padding:6px 14px;white-space:nowrap}
#title{font-size:25px;font-weight:800;white-space:nowrap}#desc{font-size:17px;opacity:.92;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#row{display:flex;height:${H - 72}px}.col{flex:1;display:flex;flex-direction:column;min-width:0}
.tag{height:30px;line-height:30px;text-align:center;font-size:14px;font-weight:800;color:#fff}
#ta{background:#2C5E46}#tb{background:#2F5B74}
iframe{flex:1;border:0;width:100%;background:#F4F6F4}.col+.col{border-left:3px solid #17201B}
#ring{position:fixed;width:46px;height:46px;margin:-23px 0 0 -23px;border-radius:50%;border:4px solid #FF4D4D;background:rgba(255,77,77,.22);pointer-events:none;opacity:0;z-index:9}
#ring.on{animation:p .7s ease-out}@keyframes p{0%{opacity:1;transform:scale(.4)}70%{opacity:1;transform:scale(1)}100%{opacity:0;transform:scale(1.25)}}
#card{position:fixed;inset:0;background:#1F7A50;color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;z-index:20;transition:opacity .6s}
#card h1{font-size:64px;margin:0}#card p{font-size:26px;margin:0;opacity:.92;text-align:center;line-height:1.6}
</style></head><body>
<div id="cap"><span id="step"></span><span id="title"></span><span id="desc"></span></div>
<div id="row"><div class="col"><div class="tag" id="ta">사용자 A의 기기 · 방을 만든 사람</div><iframe id="A" src="http://localhost:8081/"></iframe></div>
<div class="col"><div class="tag" id="tb">사용자 B의 기기 · 초대 코드로 들어온 사람</div><iframe id="B" src="http://127.0.0.1:8081/"></iframe></div></div>
<div id="ring"></div><div id="card"><h1></h1><p></p></div>
<script>
window.cap=(s,t,d)=>{step.textContent=s;title.textContent=t;desc.textContent=d||''};
window.ring=(x,y)=>{const r=document.getElementById('ring');r.style.left=x+'px';r.style.top=y+'px';r.classList.remove('on');void r.offsetWidth;r.classList.add('on')};
window.card=(h,p)=>{const c=document.getElementById('card');if(h===null){c.style.opacity=0;setTimeout(()=>c.style.display='none',650);return}c.style.display='flex';c.style.opacity=1;c.querySelector('h1').textContent=h;c.querySelector('p').innerHTML=p||''};
</script></body></html>`;

let pageRef, contextRef;
(async () => {
  fs.rmSync(OUT, { recursive: true, force: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: false, args: [`--window-size=${W + 16},${H + 90}`, '--window-position=0,0', '--disable-features=LocalNetworkAccessChecks'] });
  const context = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: OUT, size: { width: W, height: H } }, locale: 'ko-KR' });
  const page = await context.newPage();
  pageRef = page; contextRef = context;
  await page.route('http://localhost:8081/__demo', (route) => route.fulfill({ contentType: 'text/html; charset=utf-8', body: WRAPPER }));
  await page.goto('http://localhost:8081/__demo', { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => window.card('KU래쪄용', '갈등 중재 1:1 채팅 · 기능 시연<br>언어 순화 · 감정 온도계 · 상대 반응 미리보기 · 다툼판결'));

  const A = page.frameLocator('#A'), B = page.frameLocator('#B');
  const frameA = await (await page.$('#A')).contentFrame();
  const sleep = (ms) => page.waitForTimeout(ms);
  const log = (m) => console.log(`[${new Date().toLocaleTimeString('ko-KR')}] ${m}`);
  let n = 0;
  const cap = async (title, desc) => { n += 1; log(`STEP ${n} ${title}`); await page.evaluate(([s, t, d]) => window.cap(s, t, d), [`${n}`, title, desc]); await sleep(1400); };
  const note = async (desc) => { await page.evaluate((d) => { document.getElementById('desc').textContent = d; }, desc); await sleep(900); };
  const vis = (loc) => loc.filter({ visible: true }).last();
  const btn = (f, name, exact = true) => vis(f.getByRole('button', { name, exact }));
  const txt = (f, text) => vis(f.getByText(text));
  async function click(loc, pause = 900) {
    await loc.waitFor({ state: 'visible', timeout: 30000 });
    await loc.scrollIntoViewIfNeeded();
    await sleep(250);
    const box = await loc.boundingBox();
    if (box) await page.evaluate(([x, y]) => window.ring(x, y), [box.x + box.width / 2, box.y + box.height / 2]);
    await sleep(450);
    await loc.click();
    await sleep(pause);
  }
  async function wheel(f, total, step = 90, selector = '[data-testid="phone-screen"]') {
    const box = await f.locator(selector).first().boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.6);
    for (let moved = 0; Math.abs(moved) < Math.abs(total); moved += step * Math.sign(total)) { await page.mouse.wheel(0, step * Math.sign(total)); await sleep(110); }
    await sleep(500);
  }
  const input = (f) => vis(f.getByLabel('메시지 입력'));
  const bubbles = (f) => f.getByText(/(AI 분석 완료|사전 필터 통과|규칙 기반 분석) ·/).filter({ visible: true });
  async function type(f, text, delay = 55) { const box = input(f); await click(box, 200); await box.pressSequentially(text, { delay }); }
  // 초안 확인과 상대 반응 카드가 모두 도착할 때까지 기다린다.
  async function settle(f, { review = true, reaction = true } = {}) {
    if (reaction) await vis(f.getByText(/전체 반응 강도|표시를 보류했어요|재시도/)).waitFor({ timeout: 45000 }).catch(() => log('반응 카드 대기 초과'));
    if (review) await vis(f.getByText(/대안 보기 →|다듬기 제안 받기 →|순화를 확인하지 못했어요/)).waitFor({ timeout: 45000 }).catch(() => log('초안 확인 대기 초과'));
    await sleep(2200);
  }
  async function sent(f, other, before, text) {
    await f.getByRole('dialog').waitFor({ state: 'hidden', timeout: 60000 }).catch(() => {});
    await bubbles(f).nth(before).waitFor({ timeout: 60000 });
    await vis(other.getByText(text, { exact: true })).waitFor({ timeout: 15000 }).catch(() => log('상대 화면 동기화 대기 초과'));
    await sleep(2000);
  }
  const dialog = (f) => f.getByRole('dialog');
  const inDialog = (f, name) => dialog(f).getByRole('button', { name, exact: true });
  async function alternativesReady(f) { await dialog(f).getByText('대안 1', { exact: true }).waitFor({ timeout: 60000 }); await sleep(1500); }
  async function dialogWheel(f, total) {
    const box = await dialog(f).boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.6);
    for (let moved = 0; moved < Math.abs(total); moved += 80) { await page.mouse.wheel(0, 80 * Math.sign(total)); await sleep(120); }
    await sleep(600);
  }

  await btn(A, '대화방 만들기').waitFor({ timeout: 240000 });
  await btn(B, '대화방 만들기').waitFor({ timeout: 240000 });
  await sleep(2500);
  await page.evaluate(() => window.card(null));
  await sleep(900);

  // ── 1. 대화방 ──────────────────────────────────────────────────────────
  await cap('실행 구성 확인', '웹에서는 휴대폰 틀 옆에 관리자 · 시연 컨트롤 패널이 보입니다. 백엔드 연결부터 확인합니다.');
  await click(btn(A, '백엔드 연결 확인'), 1800);
  await cap('대화방 만들기 (A)', 'A가 방을 만들면 사용자 A가 되고, 1회용 초대 코드가 발급됩니다.');
  await click(btn(A, '대화방 만들기'), 2500);
  await note('채팅 화면: 아직 1/2명 참가. 목록으로 돌아가 초대 코드를 확인합니다.');
  await click(btn(A, '목록'), 1500);
  await txt(A, '상대 초대 코드 · 1회 사용').scrollIntoViewIfNeeded();
  await sleep(2500);
  const stored = JSON.parse(await frameA.evaluate(() => localStorage.getItem('ku-mvp-sessions-v1')));
  const invite = stored.sessions.find((s) => s.roomId === stored.activeId).inviteCode;
  await cap('초대 코드로 참가 (B)', 'B는 다른 기기에서 초대 코드를 넣고 참가합니다. 코드는 한 번 쓰면 사라집니다.');
  const inviteBox = vis(B.getByLabel('대화방 초대 코드'));
  await click(inviteBox, 200);
  await inviteBox.pressSequentially(invite, { delay: 25 });
  await sleep(700);
  await click(btn(B, '코드로 참가하기'), 2500);
  await click(btn(A, '대화 이어가기'), 1000);
  await txt(A, /2\/2명 참가/).waitFor({ timeout: 15000 });
  await note('양쪽 상단에 "2/2명 참가"가 표시됩니다. 2초마다 서버와 동기화합니다.');
  await sleep(2500);

  // ── 2. 설정 ────────────────────────────────────────────────────────────
  await cap('대화방 설정 (A)', '기능 토글은 나에게만, 관계 유형은 방 전체에 적용됩니다.');
  await click(btn(A, '설정'), 1800);
  await click(btn(A, '높음'), 1200);
  await click(btn(A, '룸메이트'), 1800);
  await txt(B, /룸메이트 · 2\/2명 참가/).waitFor({ timeout: 15000 }).catch(() => log('B 관계 동기화 대기 초과'));
  await note('관계를 "룸메이트"로 바꾸자 B의 화면 상단도 "룸메이트"로 바뀝니다.');
  await sleep(2200);
  await click(btn(A, '채팅으로 돌아가기'), 1500);

  // ── 3. 메시지 전송과 분석 ──────────────────────────────────────────────
  await cap('메시지 전송과 분석', '입력을 1초 멈추면 초안 확인과 상대 반응 미리보기가 실행됩니다. 문제 없는 문장은 그대로 전송됩니다.');
  const a1 = '오늘 저녁에 거실이랑 화장실 청소 같이 하기로 했잖아. 기억하지?';
  await type(A, a1);
  await settle(A);
  await note('상대 반응 미리보기(감정 · 강도 · 신뢰도)와 "다듬기 제안 받기"가 표시됩니다. 전송합니다.');
  await click(btn(A, '메시지 보내기'), 500);
  await sent(A, B, 0, a1);
  await note('말풍선 아래 "AI 분석 완료", 상단 감정 온도계 카드에 내 단계가 표시됩니다.');
  await sleep(2000);

  await cap('사전 필터 (B)', '"ㅇㅇ" 같은 단순 응답은 갈등 온도가 낮을 때 모델을 부르지 않고 통과합니다.');
  await type(B, 'ㅇㅇ', 200);
  await sleep(1500);
  await click(btn(B, '메시지 보내기'), 500);
  await sent(B, A, 0, 'ㅇㅇ');
  await note('B의 말풍선에 "사전 필터 통과"가 표시됩니다.');
  await sleep(1800);
  const b2 = '미안, 오늘 과제 때문에 너무 바빠서 못 했어. 내일 하면 안 될까?';
  await type(B, b2);
  await settle(B);
  await click(btn(B, '메시지 보내기'), 500);
  await sent(B, A, 1, b2);

  // ── 4. 언어 순화 ───────────────────────────────────────────────────────
  await cap('언어 순화 · 대안 3개 (A)', '날 선 초안을 감지하면 "대안 보기"가 뜹니다. 하고 싶은 말은 유지한 대안 3개를 제안합니다.');
  const a2 = '진짜 대단하시네 ㅋㅋ 맨날 그 모양이지';
  await type(A, a2);
  await settle(A);
  await note('상대 반응 미리보기가 부정적 반응을 예상하고, "더 부드러운 표현이 있어요 · 대안 보기"가 나타납니다.');
  await sleep(1500);
  await click(vis(A.getByText('더 부드러운 표현이 있어요 · 대안 보기 →')), 800);
  await alternativesReady(A);
  await note('순화 시트: 초안의 내용과 화자 입장을 유지한 대안 3개 (부드럽게 / 간결하게 / 요청과 경계를 분명히)');
  await dialogWheel(A, 480);
  await sleep(1500);
  await dialogWheel(A, -480);
  await cap('대안의 상대 반응 비교 (A)', '대안을 보냈을 때의 예상 반응을 원문의 예상 반응과 나란히 비교합니다.');
  await click(inDialog(A, '상대 반응 비교하기').first(), 500);
  await dialog(A).getByText(/전체 반응 강도|표시를 보류했어요/).nth(1).waitFor({ timeout: 45000 }).catch(() => log('비교 반응 대기 초과'));
  await sleep(3500);
  await cap('원문 그대로 보내기 (A)', 'AI는 제안만 합니다. 강제로 막지 않으므로 원문을 그대로 보낼 수도 있습니다.');
  await click(inDialog(A, '원문 그대로 보내기'), 500);
  await sent(A, B, 1, a2);
  await note('A의 감정 온도계 단계가 올라가고("상승 중 ↑"), B의 카드에도 상대(A)의 단계가 표시됩니다.');
  await sleep(3500);

  await cap('보내기를 누르면 열리는 순화 시트 (B)', '감지된 초안에서 ↑를 누르면 전송 전에 순화 시트가 먼저 열립니다.');
  const b3 = '너나 잘해. 지난주에 설거지 쌓아두고 모른 척한 건 너잖아, 뻔뻔하게';
  await type(B, b3);
  await settle(B);
  await click(btn(B, '메시지 보내기'), 800);
  if (await dialog(B).waitFor({ timeout: 30000 }).then(() => true).catch(() => false)) {
    await alternativesReady(B).catch(() => log('B 대안 없음'));
    await dialogWheel(B, 320);
    await note('B도 원문을 그대로 보내기로 합니다. 대화가 격해집니다.');
    await click(inDialog(B, '원문 그대로 보내기'), 500);
  }
  await sent(B, A, 2, b3);
  await note('오른쪽 패널의 "대화 전체 갈등 점수"가 올라갔습니다. 갈등 온도가 높을수록 순화 제안 기준이 낮아집니다.');
  await sleep(3500);

  await cap('이 표현으로 보내기 (A)', '이번에는 A가 대안 중 하나를 골라 그대로 보냅니다.');
  const a3 = '됐어, 너랑은 말이 안 통해. 진짜 짜증나';
  await type(A, a3);
  await settle(A);
  await click(vis(A.getByText(/대안 보기 →|다듬기 제안 받기 →/)), 800);
  await alternativesReady(A);
  await sleep(2500);
  const chosen = (await dialog(A).getByText('대안 1', { exact: true }).locator('xpath=following-sibling::*[1]').innerText()).trim();
  log(`A 선택 대안: ${chosen}`);
  await click(inDialog(A, '이 표현으로 보내기').first(), 500);
  await sent(A, B, 2, chosen);
  await note('순화된 표현이 전송됐고, A의 감정 온도계 추세가 바뀝니다.');
  await sleep(3500);

  await cap('다듬기 제안 받기 · 고쳐서 보내기 (B)', '문제 없는 문장도 다른 표현을 요청할 수 있고, 대안을 입력창에 넣어 직접 고칠 수 있습니다.');
  const b4 = '나도 말이 심했어. 내일 저녁 8시에 꼭 같이 하자';
  await type(B, b4);
  await settle(B);
  await click(vis(B.getByText(/다듬기 제안 받기 →|대안 보기 →/)), 800);
  await alternativesReady(B);
  await sleep(2500);
  await click(inDialog(B, '고쳐서 보내기').first(), 1500);
  await note('대안이 입력창에 들어갔습니다. 한마디 덧붙여 보냅니다.');
  await input(B).press('End');
  await input(B).pressSequentially(' 약속할게.', { delay: 70 });
  const b4final = await input(B).inputValue();
  await settle(B, { reaction: true, review: true });
  await click(btn(B, '메시지 보내기'), 800);
  if (await dialog(B).waitFor({ timeout: 4000 }).then(() => true).catch(() => false)) await click(inDialog(B, '원문 그대로 보내기'), 500);
  await sent(B, A, 3, b4final);
  await sleep(2500);

  // ── 5. 감정 온도계와 토글 ──────────────────────────────────────────────
  await cap('감정 온도계', '전송 때 저장한 분석만 읽어 나와 상대의 표현된 분노 · 긴장을 5단계와 추세로 보여줍니다(추가 AI 호출 없음).');
  await sleep(4500);
  await cap('기능 켜고 끄기 (A)', '채팅 상단 토글로 기능을 바로 끌 수 있습니다. 온도계를 끄면 카드가 사라집니다.');
  const sw = (name) => vis(A.getByRole('switch', { name, exact: true }));
  await click(sw('온도계'), 2200);
  await click(sw('온도계'), 1800);
  await note('언어 순화와 상대 반응을 끄면 날 선 초안을 써도 확인 · 미리보기가 실행되지 않습니다.');
  await click(sw('언어 순화'), 900);
  await click(sw('상대 반응'), 900);
  await type(A, '맨날 말로만 그러지');
  await sleep(4000);
  await note('다시 켜면 같은 초안에 초안 확인과 상대 반응 미리보기가 실행됩니다.');
  await click(sw('언어 순화'), 900);
  await click(sw('상대 반응'), 900);
  await settle(A);
  await sleep(1500);
  await input(A).fill('');
  await sleep(1200);

  // ── 6. 다툼판결 ────────────────────────────────────────────────────────
  await cap('다툼판결 요청 (A)', '검사 · 변호사 · 팩트체크가 토론하고 심사위원 3명이 A와 B의 실제 발화를 채점합니다.');
  await click(btn(A, '다툼판결 요청'), 2000);
  await click(btn(A, 'UFC'), 1500);
  await click(btn(A, 'WWE'), 1500);
  await click(btn(A, '전달할 대화 보기'), 1200);
  await wheel(A, 620);
  await sleep(1200);
  const ctx = vis(A.getByLabel('판결에 참고할 추가 상황'));
  await click(ctx, 200);
  await ctx.pressSequentially('이번 주 청소 당번은 B였어요.', { delay: 55 });
  await note('추가 상황은 사실이 아니라 "요청자의 주장"으로 전달됩니다. WWE 모드로 요청합니다.');
  await click(btn(A, '이 대화로 다툼판결 요청하기'), 1500);
  await wheel(A, -900);
  const resultTitle = (f) => vis(f.getByText('서로의 입장을 살펴봤어요', { exact: true }));
  async function awaitVerdict(f) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const outcome = await Promise.race([
        resultTitle(f).waitFor({ timeout: 300000 }).then(() => 'ok'),
        btn(f, '같은 요청으로 다시 시도').waitFor({ timeout: 300000 }).then(() => 'error'),
      ]);
      if (outcome === 'ok') return;
      log('판결 오류, 다시 시도');
      await sleep(2500);
      await click(btn(f, '같은 요청으로 다시 시도'), 1000);
    }
    throw new Error('판결을 받지 못했습니다.');
  }
  const pending = awaitVerdict(A);
  await cap('판결 중에도 채팅 가능 (B)', '판결은 요청 시점의 대화를 고정해서 보며, 40~60초 동안 상대는 채팅을 계속할 수 있습니다.');
  const b5 = '판결 결과 나오면 같이 보자';
  await type(B, b5);
  await settle(B);
  await click(btn(B, '메시지 보내기'), 800);
  if (await dialog(B).waitFor({ timeout: 4000 }).then(() => true).catch(() => false)) await click(inDialog(B, '원문 그대로 보내기'), 500);
  await bubbles(B).nth(4).waitFor({ timeout: 60000 }).catch(() => log('B 전송 대기 초과'));
  await note('토론 3회(검사 → 변호사 → 팩트체크)와 심사 3회를 라운드마다 반복하고, 판단이 안정되면 일찍 끝납니다.');
  await pending;
  await sleep(1500);

  await cap('판결 결과 · 점수 카드', '논리력 · 감정 조절 · 근거 점수(심사위원 3명 평균)와 항목별 근거 문장, 잘 전달한 점 · 더 나아질 점');
  await sleep(3000);
  await wheel(A, 420, 60);
  await sleep(3500);
  await wheel(A, 420, 60);
  await sleep(3500);
  await cap('라운드별 판단 변화 · 쟁점 정리', '라운드 수 · 종료 이유 · 모델 호출 수 · 토큰 · 소요 시간과 "A 쪽으로 N%" 변화를 보여줍니다.');
  await btn(A, '정리된 쟁점과 입장 보기').scrollIntoViewIfNeeded();
  await sleep(3500);
  await click(btn(A, '정리된 쟁점과 입장 보기'), 1500);
  await wheel(A, 520, 60);
  await sleep(4000);
  await cap('판결 요약 · 제안 · 한 줄 돌아보기', '승패 선언이 아니라 요약과 다음 대화를 위한 제안을 줍니다. WWE 모드는 유머 한 줄이 붙습니다.');
  await txt(A, '판결 요약').scrollIntoViewIfNeeded();
  await wheel(A, 260, 60);
  await sleep(5000);
  await cap('토론 과정 보기', '역할별 발언과 근거 메시지 번호, 발언 전 비공개 전략까지 확인할 수 있습니다.');
  await click(btn(A, '토론 과정 보기'), 1500);
  await click(btn(A, '발언 전 전략도 보기'), 1500);
  await wheel(A, 1500, 70);
  await sleep(2500);

  await cap('저장된 판결 다시 보기 (B)', '판결은 방에 저장되어 B도 "저장된 판결"에서 같은 결과를 "나 (B)" 기준으로 볼 수 있습니다.');
  await click(btn(B, '다툼판결 요청'), 2000);
  const saved = vis(B.getByRole('button', { name: /^WWE · / }));
  await saved.scrollIntoViewIfNeeded();
  await sleep(1500);
  await click(saved, 1500);
  await resultTitle(B).waitFor({ timeout: 20000 });
  await wheel(B, -1200);
  await sleep(1500);
  await wheel(B, 500, 60);
  await sleep(3000);

  await cap('점수 기준 (B)', '점수마다 속한 구간의 뜻과 인용 근거가 붙고, 심사위원별 원점수와 채점 기준표를 볼 수 있습니다.');
  await click(btn(B, '채점 기준 보기'), 1500);
  await sleep(3500);
  await txt(B, '판결 요약').scrollIntoViewIfNeeded();
  await wheel(B, 200, 60);
  await sleep(4500);

  // ── 7. 마무리 ──────────────────────────────────────────────────────────
  await cap('대화 이어가기 · 저장된 방', '판결 뒤 채팅으로 돌아갑니다. 방과 대화는 서버(SQLite)에 저장되어 목록에서 다시 열 수 있습니다.');
  await click(btn(B, '대화 이어가기'), 2200);
  await click(btn(A, '대화 이어가기'), 2200);
  await click(btn(A, '목록'), 2500);
  await click(btn(A, '대화 이어가기'), 3000);
  await page.evaluate(() => window.card('시연 끝', '대화방 · 메시지 분석과 갈등 온도 · 언어 순화 · 감정 온도계<br>상대 반응 미리보기 · 다툼판결 · 대화방 설정'));
  await sleep(4000);

  const video = page.video();
  await context.close();
  const file = await video.path();
  await browser.close();
  log(`VIDEO ${file}`);
})().catch(async (error) => { console.error('FAILED', error); try { await pageRef.screenshot({ path: path.join(__dirname, 'fail.png') }); await contextRef.close(); } catch {} process.exit(1); });
