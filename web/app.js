// WHOAREYOU 프론트 — pywebview JS 브리지 호출 + 렌더 (목데이터 없음, 전부 실데이터)
const api = () => window.pywebview.api;
const $ = (id) => document.getElementById(id);

// ── 에러 안전망: 브리지 실패해도 UI 안 얼도록 ──
function subMsg(t) { const el = $('subMeta'); if (el) el.innerHTML = t; }   // 서브메타에 짧은 안내
// 추천 탭 상단 배너(있으면 갈아끼움, 없으면 만듦)
function showBanner(html) {
  const box = $('cards'); if (!box) return;
  let b = document.getElementById('errBanner');
  if (!b) { b = document.createElement('div'); b.id = 'errBanner'; b.className = 'banner'; box.parentNode.insertBefore(b, box); }
  b.innerHTML = html;
}
function clearErrBanner() { const b = document.getElementById('errBanner'); if (b) b.remove(); }
// 브리지 호출 공통 래퍼: 실패 시 스피너 끄고 한국어 안내
function onErr(e, where) {
  try { setLoading(false); } catch (_) {}
  const msg = '문제가 생겼어요 — 잠시 후 다시 시도해 주세요';
  subMsg(`<span class="muted">${where ? esc(where) + ' · ' : ''}</span>${msg}`);
  console.error('[WHOAREYOU]', where || '', e);
}
// 전역 안전망 — 잡히지 않은 에러/거부도 스피너 끄고 안내
window.onerror = function (m, src, ln, col, e) { onErr(e || m, ''); return false; };
window.addEventListener('unhandledrejection', ev => onErr(ev.reason, ''));

function esc(s) {
  return (s == null ? '' : String(s)).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function axCls(score) {
  if (score == null) return '';
  if (score <= 20) return 'safe';
  if (score <= 45) return 'warn';
  return 'danger';
}
const NA = '<span class="na-t">표본 부족</span>';   // 신호 결측 = 재정규화로 빼고, 표시만 이렇게
function jotsoVal(score, label) {
  if (score == null) return NA;
  const g = label || (score <= 20 ? '안전' : score <= 45 ? '주의' : '위험');
  return `${score} <span class="grade">${esc(g)}</span>`;
}
function firstTok(s, sep) { return s ? esc(String(s).split(sep)[0]) : '-'; }

function card(r) {
  const done = r.status === 'applied';
  const repOk = !!(r.rep_d && r.rep_d.startsWith('★'));
  const rep = repOk ? esc(r.rep_d.split(' ')[0]) : NA;
  const jotsoNa = r.jotso_score == null;
  const comOk = r.commute != null;                     // 정규화된 통근 점수 있으면 좌표 확보됨
  const com = comOk ? firstTok(r.commute_d, '/') : `<span class="na-t">${esc(r.commute_d || '표본 부족')}</span>`;
  const matchOk = r.match != null;
  return `<div class="card ${done ? 'done' : ''}" data-url="${esc(r.url)}">
    <div class="crow"><div class="cmain"><div class="co">${r.source ? `<span class="site">${esc(r.source)}</span>` : ''}${esc(r.company)} ${r.title ? `<span class="role">· ${esc(r.title)}</span>` : ''}</div></div>
      <div class="fit"><div class="n">${r.total ?? '-'}</div><div class="l">적합도</div></div></div>
    <div class="axes">
      <div class="axis ${comOk ? '' : 'na'}"><div class="ic"><i class="ti ti-home"></i></div><div class="lab">통근</div><div class="val">${com}</div></div>
      <div class="axis ${jotsoNa ? 'na' : axCls(r.jotso_score)}"><div class="ic"><i class="ti ti-shield-half"></i></div><div class="lab">좋소</div><div class="val">${jotsoVal(r.jotso_score, r.jotso_label)}</div></div>
      <div class="axis ${repOk ? '' : 'na'}"><div class="ic"><i class="ti ti-star"></i></div><div class="lab">평판</div><div class="val">${rep}</div></div>
      <div class="axis ${matchOk ? '' : 'na'}"><div class="ic"><i class="ti ti-target"></i></div><div class="lab">매칭</div><div class="val">${matchOk ? r.match + '%' : NA}</div></div>
    </div>
    <div class="meta2">${r.commute_d ? `<span>${esc(r.commute_d)}</span>` : ''}${r.rep_d ? `<span>${esc(r.rep_d)}</span>` : ''}</div>
    <div class="acts">
      <button class="btn" onclick="mark(this,'not_interested')"><i class="ti ti-thumb-down"></i>관심 없음</button>
      <button class="btn" onclick="mark(this,'applied')"><i class="ti ti-check"></i>지원함</button>
      <button class="btn primary" onclick="api().open_url('${esc(r.url)}')">공고 바로가기 <i class="ti ti-external-link"></i></button>
    </div></div>`;
}

let curThreshold = 70;
let lastSettings = null, lastKeys = null;   // §5·§10 셋업 인지용 최신 상태

// 미설정 유저(집주소·이력서·카카오키 전부 없음) 판정 — §5
function isUnconfigured() {
  const s = lastSettings || {}, ks = lastKeys || (s.keys_set || {});
  const noHome = !s.home_address;
  const noResume = !(s.resume_name || s.resume_path);
  const noKakao = !ks.kakao_rest_key;
  return noHome && noResume && noKakao;
}
// 온보딩 체크리스트 — 딥링크(switchTab). 내용만(배너 래퍼는 호출부에서)
const ONBOARD_HTML = `처음이신가요? 3가지만 넣으면 채점이 시작돼요 —
    <b><span class="klink" onclick="switchTab('set')">① 집 주소(설정)</span></b> ·
    <b><span class="klink" onclick="switchTab('set')">② 이력서 PDF(설정)</span></b> ·
    <b><span class="klink" onclick="switchTab('stat')">③ 카카오·ODsay 키(키 관리)</span></b>`;

function render(recos, threshold, opts) {
  opts = opts || {};
  threshold = (threshold != null) ? threshold : curThreshold;
  recos = recos || [];
  clearErrBanner();
  // §4 정직: total===null(미채점/표본부족)과 진짜 낮은 점수를 구분 — null을 0으로 강제하지 않음
  const aboveThreshold = recos.filter(r => r.total != null && r.total >= threshold);
  const scoredBelow = recos.filter(r => r.total != null && r.total < threshold);
  const unscored = recos.filter(r => r.total == null);
  const above = aboveThreshold;
  const below = scoredBelow.concat(unscored);   // 임계점 미만 취급(미채점 포함) — 카드는 다 보여줌
  const box = $('cards');
  const unconf = isUnconfigured();
  let shownN;
  if (above.length) {
    if (unconf) showBanner(ONBOARD_HTML);   // 미설정이면 온보딩도 같이
    box.innerHTML = above.map(card).join('');
    shownN = above.length;
  } else if (below.length) {
    // §4 배너: '최고 N점'은 채점된(scoredBelow) 것만으로 계산 — null을 0으로 만들지 않음
    let banner;
    if (scoredBelow.length) {
      const top = Math.max(...scoredBelow.map(r => r.total));
      banner = `<div class="banner">임계점 <b>${threshold}점</b> 이상 추천이 없어요 (최고 ${top}점). 아래는 <b>기준 미만</b> — 설정에서 임계점을 낮추거나 조건(경력·지역)을 넓혀보세요.</div>`;
    } else if (unconf) {
      banner = `<div class="banner" id="errBanner">${ONBOARD_HTML}</div>`;   // id로 §10 통근배너 중복 억제
    } else {
      banner = `<div class="banner">아직 점수를 못 냈어요 — 집 주소·이력서·키를 넣으면 채점됩니다 <b><span class="klink" onclick="switchTab('set')">설정</span></b> · <b><span class="klink" onclick="switchTab('stat')">키 관리</span></b> 탭</div>`;
    }
    box.innerHTML = banner + below.map(card).join('');
    shownN = below.length;
  } else {
    box.innerHTML = '';
    shownN = 0;
    if (unconf) showBanner(ONBOARD_HTML);
  }
  $('empty').style.display = shownN ? 'none' : 'block';
  // 서브메타 = 적용조건 + 퍼널 + 임계점 안내
  const s = opts.stats || {}, q = opts.query;
  let sub = '';
  if (opts.note) sub = opts.note + ' · ';
  else if (q && q.keyword) {
    const bits = [q.career, ...(q.regions || [])].filter(Boolean).join('·');
    sub = `「${esc(q.keyword)}」${bits ? ` <span class="muted">${esc(bits)}</span>` : ''} · `;
  }
  if (s.crawled != null) sub += `<span class="muted">수집 ${s.crawled}→조건 ${s.filtered}→</span> `;
  sub += `추천 <b>${above.length}</b>건`;
  if (above.length && below.length) sub += ` <span class="muted">· 임계점 미만 ${below.length} 숨김</span>`;
  $('subMeta').innerHTML = sub;
  // §10 통근 전부 표본부족 + 카카오키 없음 → 키/집주소 안내 배너 (온보딩 배너 없을 때만)
  const ks = lastKeys || ((lastSettings || {}).keys_set || {});
  const noCommute = recos.length && recos.every(r => r.commute == null);
  if (noCommute && !ks.kakao_rest_key && !document.getElementById('errBanner')) {
    showBanner('통근 점수를 보려면 <b><span class="klink" onclick="switchTab(\'stat\')">키 관리</span></b> 탭에서 카카오 REST 키와 집 주소를 넣어주세요');
  }
}

// §6 경과초 티커 — 수집/검색은 1~3분 걸림, 멈춘 것처럼 안 보이게
let _tick = null;
const _loadTxt = () => $('loading') && $('loading').querySelector('div');
function startTick() {
  stopTick();
  const el = _loadTxt(); if (!el) return;
  const t0 = Date.now();
  const upd = () => { el.textContent = `수집·채점 중… (${Math.round((Date.now() - t0) / 1000)}초) · 1~3분 걸릴 수 있어요`; };
  upd(); _tick = setInterval(upd, 1000);
}
function stopTick() {
  if (_tick) { clearInterval(_tick); _tick = null; }
  const el = _loadTxt(); if (el) el.textContent = '수집·채점 중… (좋소는 매너있게 천천히)';
}
function setLoading(on) {
  $('loading').classList.toggle('on', on);
  $('collectBtn').disabled = on; $('searchBtn').disabled = on;
  if (on) { clearErrBanner(); $('cards').innerHTML = ''; $('empty').style.display = 'none'; startTick(); }
  else stopTick();
}

async function loadRecos() {
  try {
    const r = await api().recommendations();
    if (r.threshold != null) curThreshold = r.threshold;
    render(r.recos, r.threshold);
  } catch (e) { onErr(e, '추천 불러오기'); }
}

async function collect() {
  setLoading(true);
  try {
    const res = await api().collect();
    if (res && res.busy === true) { subMsg('이미 수집 중이에요 — 끝나면 갱신돼요'); return; }   // §9
    if (res.threshold != null) curThreshold = res.threshold;
    render(res.recos, res.threshold, { stats: res.stats, query: res.query });
  } catch (e) { onErr(e, '지금 검색'); }
  finally { setLoading(false); }
}
async function search() {
  const q = $('q').value.trim();
  setLoading(true);
  try {
    const res = await api().search(q);
    if (res && res.busy === true) { subMsg('이미 수집 중이에요 — 끝나면 갱신돼요'); return; }   // §9
    if (res.threshold != null) curThreshold = res.threshold;
    render(res.recos, res.threshold, { stats: res.stats, note: `"${esc(q || '기본')}"` });
  } catch (e) { onErr(e, '검색'); }
  finally { setLoading(false); }
}
async function resetSearch() {
  if (!confirm('저장된 추천을 모두 지우고 초기화할까요?')) return;
  try {
    const r = await api().reset();
    render([], curThreshold);
    $('subMeta').innerHTML = `초기화됨 (${r.cleared || 0}건 삭제) · <b>[지금 검색]</b>으로 다시 수집`;
  } catch (e) { onErr(e, '초기화'); }
}

async function mark(btn, status) {
  const c = btn.closest('.card');
  const url = c.dataset.url;
  // §7 성공했을 때만 카드 변경 — 실패 시 원복
  const wasDone = c.classList.contains('done');
  try {
    await api().set_status(url, status);
    if (status === 'not_interested') c.remove(); else c.classList.add('done');
  } catch (e) {
    if (!wasDone) c.classList.remove('done');   // 낙관적 변경 없었지만 방어적 원복
    subMsg('처리 실패 — 다시 눌러주세요');
    console.error('[WHOAREYOU] mark', e);
  }
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
  ['rec', 'set', 'stat'].forEach(p => $(p).classList.toggle('hidden', p !== name));
  if (name === 'stat') loadKeys();
}

// ── 키 관리(별도 탭) ──
async function loadKeys() {
  try {
    const ks = (await api().get_settings()).keys_set || {};
    lastKeys = ks;   // §5·§10 상태 캐시
    kset('k_kakao_s', ks.kakao_rest_key); kset('k_odsay_s', ks.odsay_key);
    // §11 사용자가 입력 중인(포커스·이미 값 있는) 칸은 지우지 않음
    ['k_kakao', 'k_odsay'].forEach(id => {
      const el = $(id);
      if (document.activeElement === el || el.value.trim()) return;
      el.value = '';
    });
  } catch (e) { onErr(e, '키 불러오기'); }
}
async function saveKeys() {
  const patch = {
    kakao_rest_key: $('k_kakao').value.trim(),
    odsay_key: $('k_odsay').value.trim(),
  };
  $('keysMsg').textContent = '저장 중…';
  try {
    await api().save_settings(patch);
    ['k_kakao', 'k_odsay'].forEach(id => $(id).value = '');   // 저장됐으니 입력칸 비움(원문 미노출)
    $('keysMsg').textContent = '저장됨';
    setTimeout(() => { $('keysMsg').textContent = ''; }, 3000);
    await loadKeys();
  } catch (e) { $('keysMsg').textContent = '저장 실패 — 다시 시도해 주세요'; onErr(e, '키 저장'); }
}

// ── 설정 ──
function chipVals(group) {
  return [...document.querySelectorAll(`[data-group="${group}"] .fchip.on`)].map(c => c.dataset.val);
}
function setChips(group, vals) {
  document.querySelectorAll(`[data-group="${group}"] .fchip`).forEach(c =>
    c.classList.toggle('on', (vals || []).includes(c.dataset.val)));
}
function bindChips() {
  document.querySelectorAll('[data-group]').forEach(g => {
    const single = g.dataset.single === '1';
    g.querySelectorAll('.fchip').forEach(c => c.onclick = () => {
      if (single) g.querySelectorAll('.fchip').forEach(x => x.classList.remove('on'));
      c.classList.toggle('on');
    });
  });
}
function intervalLbl() {
  const m = Math.max(1, (+$('s_interval_n').value || 1) * (+$('s_interval_u').value || 60));
  const t = m % 1440 === 0 ? `${m / 1440}일` : m % 60 === 0 ? `${m / 60}시간` : `${m}분`;
  $('s_interval_lbl').textContent = `= ${t}마다`;
}
function wSum() {
  const s = ['w_commute', 'w_jotso', 'w_rep', 'w_match'].reduce((a, id) => a + (+$(id).value), 0);
  const el = $('w_sum'); el.innerHTML = `합계 <b>${s}</b> / 100`; el.classList.toggle('bad', s !== 100);
}
function bindControls() {
  ['w_commute', 'w_jotso', 'w_rep', 'w_match'].forEach(id =>
    $(id).oninput = () => { $(id + '_v').textContent = $(id).value; wSum(); });
  $('s_commute').oninput = () => $('s_commute_v').textContent = $('s_commute').value + '분';
  $('s_threshold').oninput = () => $('s_threshold_v').textContent = $('s_threshold').value + '점↑';
  ['s_noti_desktop', 's_noti_kakao'].forEach(id => $(id).onclick = () => $(id).classList.toggle('on'));
  $('s_interval_n').oninput = intervalLbl; $('s_interval_u').onchange = intervalLbl;
  $('saveBtn').onclick = saveSettings;
}
function kset(id, on) {
  const el = $(id); el.textContent = on ? '● 설정됨' : '미설정';
  el.classList.toggle('no', !on);
}
async function loadSettings() {
  let s;
  try { s = await api().get_settings(); }
  catch (e) { onErr(e, '설정 불러오기'); return; }
  lastSettings = s; lastKeys = s.keys_set || lastKeys;   // §5·§10 상태 캐시
  $('s_keyword').value = s.keyword || '';
  setChips('regions', s.regions); setChips('career', [s.career]);
  setChips('edu', s.edu); setChips('emp_types', s.emp_types); setChips('comp_types', s.comp_types);
  $('s_salary').value = s.salary_min;
  $('s_exclude').value = (s.exclude || []).join(', ');
  $('s_home').value = s.home_address || '';
  $('s_commute').value = s.max_commute; $('s_commute_v').textContent = s.max_commute + '분';
  const w = s.weights || {};
  const wmap = { w_commute: w.commute, w_jotso: w.jotso, w_rep: w.reputation, w_match: w.match };
  for (const [id, v] of Object.entries(wmap)) { $(id).value = v; $(id + '_v').textContent = v; }
  wSum();
  $('s_threshold').value = s.threshold; $('s_threshold_v').textContent = s.threshold + '점↑';
  $('s_per').value = String(s.per_site);
  $('s_noti_desktop').classList.toggle('on', !!s.noti_desktop);
  $('s_noti_kakao').classList.toggle('on', !!s.noti_kakao);
  const iv = s.schedule_interval || 1440;
  if (iv % 60 === 0) { $('s_interval_n').value = iv / 60; $('s_interval_u').value = '60'; }
  else { $('s_interval_n').value = iv; $('s_interval_u').value = '1'; }
  intervalLbl();
  $('s_quiet').value = s.quiet_hours || '';
  $('resumeName').textContent = s.resume_name || '선택 안 됨';
  setKakaoState((s.keys_set || {}).kakao_refresh_token);
  kset('k_kakao_secret_s', (s.keys_set || {}).kakao_client_secret);
  $('k_kakao_secret').value = '';
}

// ── 이력서 · 카카오톡 ──
async function pickResume() {
  try {
    const r = await api().pick_resume();
    if (r.ok) $('resumeName').textContent = r.name;
    else if (!r.cancelled) alert('이력서 불러오기 실패: ' + (r.error || ''));
  } catch (e) { onErr(e, '이력서 선택'); }
}
function setKakaoState(on) {
  const el = $('kakaoState'); el.textContent = on ? '● 연결됨' : '미연결';
  el.classList.toggle('no', !on);
}
async function kakaoConnect() {
  const btn = $('kakaoConnectBtn'); btn.disabled = true;
  try {
    const sec = $('k_kakao_secret').value.trim();
    if (sec) await api().save_settings({ kakao_client_secret: sec });   // 시크릿 먼저 저장
    $('kakaoState').textContent = '브라우저에서 로그인…';
    const r = await api().kakao_connect();
    if (r.ok) { setKakaoState(true); alert('카카오톡 연결 완료!'); }
    else { setKakaoState(false); alert('연결 실패: ' + (r.error || '')); }
  } catch (e) { setKakaoState(false); onErr(e, '카카오 연결'); }
  finally { btn.disabled = false; }
}
async function kakaoTest() {
  try {
    const r = await api().kakao_test();
    alert(r.ok ? '테스트 전송됨 — 카톡을 확인하세요.' : '전송 실패: ' + (r.error || ''));
  } catch (e) { onErr(e, '카카오 테스트'); }
}
async function checkHome() {
  const el = $('homeCheck');
  el.textContent = '확인 중…'; el.classList.add('no');
  try {
    const r = await api().check_home($('s_home').value.trim());
    if (r.ok) { el.innerHTML = '✅ ' + esc(r.address) + ' <span class="na-t">(' + (+r.lat).toFixed(4) + ', ' + (+r.lng).toFixed(4) + ')</span>'; el.classList.remove('no'); }
    else { el.textContent = '❌ ' + (r.error || '못 찾음'); el.classList.add('no'); }
  } catch (e) { el.textContent = '❌ 확인 실패 — 다시 시도해 주세요'; el.classList.add('no'); onErr(e, '집주소 확인'); }
}
// §8 가중치 합 100 아님 → 저장 시 자동 정규화(막지 않음·깨진 값 저장 안 함)
function normWeights(w) {
  const sum = w.commute + w.jotso + w.reputation + w.match;
  if (sum === 100 || sum <= 0) return w;
  const k = 100 / sum;
  const out = { commute: Math.round(w.commute * k), jotso: Math.round(w.jotso * k), reputation: Math.round(w.reputation * k), match: Math.round(w.match * k) };
  const drift = 100 - (out.commute + out.jotso + out.reputation + out.match);   // 반올림 오차는 최대축에 흡수
  if (drift) { const mx = ['commute', 'jotso', 'reputation', 'match'].reduce((a, b) => out[a] >= out[b] ? a : b); out[mx] += drift; }
  return out;
}
async function saveSettings() {
  const weights = normWeights({ commute: +$('w_commute').value, jotso: +$('w_jotso').value, reputation: +$('w_rep').value, match: +$('w_match').value });
  const patch = {
    keyword: $('s_keyword').value.trim(),
    regions: chipVals('regions'), career: chipVals('career')[0] || '신입',
    edu: chipVals('edu'), emp_types: chipVals('emp_types'), comp_types: chipVals('comp_types'),
    salary_min: $('s_salary').value,
    exclude: $('s_exclude').value.split(',').map(x => x.trim()).filter(Boolean),
    home_address: $('s_home').value.trim(), max_commute: +$('s_commute').value,
    weights: weights,
    threshold: +$('s_threshold').value, per_site: +$('s_per').value,
    noti_desktop: $('s_noti_desktop').classList.contains('on'),
    noti_kakao: $('s_noti_kakao').classList.contains('on'),
    schedule_interval: Math.max(1, (+$('s_interval_n').value || 1) * (+$('s_interval_u').value || 60)),
    quiet_hours: $('s_quiet').value.trim(),
  };
  $('saveMsg').textContent = '저장 중…';
  try {
    await api().save_settings(patch);
    $('saveMsg').textContent = '저장됨 · 새 조건으로 검색합니다';
    setTimeout(() => { $('saveMsg').textContent = ''; }, 3000);
    await loadSettings();
    switchTab('rec');
    collect();          // 저장 즉시 새 설정으로 재검색(= "저장했는데 안 바뀜" 해결)
  } catch (e) { $('saveMsg').textContent = '저장 실패 — 다시 시도해 주세요'; onErr(e, '설정 저장'); }
}

// 바인딩
document.querySelectorAll('.tab').forEach(t => t.onclick = () => switchTab(t.dataset.tab));
$('collectBtn').onclick = collect;
$('resetBtn').onclick = resetSearch;
$('searchBtn').onclick = search;
$('saveKeysBtn').onclick = saveKeys;
$('pickResumeBtn').onclick = pickResume;
$('kakaoConnectBtn').onclick = kakaoConnect;
$('kakaoTestBtn').onclick = kakaoTest;
$('checkHomeBtn').onclick = checkHome;
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
bindChips();
bindControls();

// §3 견고한 초기화 — pywebviewready가 이미 떴거나(타이밍) 영영 안 와도 안 멈추게
let _booted = false;
async function boot() {
  if (_booted) return; _booted = true;
  // 순서: 설정·키 먼저(셋업 상태 캐시) → 추천 렌더가 온보딩/통근배너 판단에 그 상태 사용
  await loadSettings();
  await loadKeys();
  await loadRecos();
}
if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener('pywebviewready', boot);
// ~8초 지나도 초기화 안 됐으면 '불러오는 중…'을 실패 안내로 교체
setTimeout(() => {
  if (_booted) return;
  const el = $('subMeta');
  if (el && /불러오는 중/.test(el.textContent)) el.textContent = '앱 준비 실패 — 프로그램을 다시 시작해 주세요';
}, 8000);
