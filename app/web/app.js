'use strict';
/* 猛人学习 · 界面 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const sleep = ms => new Promise(r => setTimeout(r, ms));

const S = {
  settings: null, doubaoModels: {}, chatProviders: [], providerNames: {}, teachers: [], defaultTemplate: '',
  path: null, doc: null, info: null,
  dirty: false, saving: null, saveTimer: null, lastSaved: null,
  active: -1, editing: null, userScrollAt: 0, undo: [], job: null,
  hits: [], hitPos: -1, chatAbort: null, lib: [], resumeAt: 0, resumePending: false, loopIdx: null, frameUrls: {},
};
const LAYOUT_KEYS = ['font_size', 'line_height', 'cue_gap', 'cue_width', 'font_family', 'split', 'sidebar', 'lib_w', 'ai_dock', 'ai_hidden', 'ai_w', 'video_h'];

/* ---------------------------------------------------------------- 工具 ---- */
async function api(name, body = {}) {
  const bridge = window.pywebview && window.pywebview.api;
  if (!bridge) throw new Error('请从「猛人学习」程序里打开这个界面');
  let d;
  try { d = await bridge.call(name, body); } catch (e) { throw new Error('后台出错：' + (e.message || e)); }
  if (!d || !d.ok) throw new Error((d && d.error) || '出错了');
  return d;
}
function bridgeReady() {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.call) return Promise.resolve();
  return new Promise(r => window.addEventListener('pywebviewready', () => r(), { once: true }));
}
function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  const p = n => String(n).padStart(2, '0');
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}
function parseTs(str) {
  const parts = str.split(':').map(Number);
  if (parts.some(isNaN)) return null;
  return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const mb = n => n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(1) + ' GB' : (n / 1024 ** 2).toFixed(0) + ' MB';

function toast(msg, opts = {}) {
  const el = document.createElement('div');
  el.className = 'toast' + (opts.error ? ' error' : '');
  el.innerHTML = `<span>${esc(msg)}</span>`;
  if (opts.action) {
    const b = document.createElement('button');
    b.className = 'btn small'; b.textContent = opts.action.label;
    b.onclick = () => { opts.action.run(); el.remove(); };
    el.appendChild(b);
  }
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), opts.ms || (opts.error ? 8000 : 3500));
}
const fail = e => { toast(e.message || String(e), { error: true }); logToFile('warn', '提示错误：' + (e.message || e)); };
function logToFile(level, message) {
  try { if (window.pywebview && window.pywebview.api) window.pywebview.api.call('log', { level, message }); } catch (_) { /* 记日志失败不影响使用 */ }
}
window.addEventListener('error', e => logToFile('error', `${e.message} @ ${e.filename}:${e.lineno}:${e.colno}
${e.error && e.error.stack || ''}`));
window.addEventListener('unhandledrejection', e => logToFile('error', '未处理的异步错误：' + (e.reason && (e.reason.stack || e.reason.message) || e.reason)));

/* 简单 Markdown（AI 回答用），时间点可点击跳转 */
function md(src) {
  const blocks = [];
  src = String(src || '').replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, c) => { blocks.push(c); return `\u0000${blocks.length - 1}\u0000`; });
  const inline = t => esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
    .replace(/\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?/g, (m, ts) => {
      const t = parseTs(ts); return t == null ? m : `<a class="tslink" data-t="${t}">[${ts}]</a>`;
    });
  const out = []; let list = null, para = [], table = null;
  const flushPara = () => { if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; } };
  const cells = line => line.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table;
    out.push('<table><thead><tr>' + head.map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
      rows.map(r => '<tr>' + r.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
    table = null;
  };
  const lines = src.split('\n');
  const flushList = () => { if (list) { out.push(`<${list.tag}>` + list.items.map(i => `<li>${inline(i)}</li>`).join('') + `</${list.tag}>`); list = null; } };
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li].trimEnd();
    let m;
    if (table && /^\s*\|/.test(line)) { table.push(cells(line)); continue; }
    if (table) flushTable();
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{2,}/.test(lines[li + 1] || '')) { flushPara(); flushList(); table = [cells(line)]; li++; continue; }
    if (/^\u0000\d+\u0000$/.test(line.trim())) { flushPara(); flushList(); out.push(`<pre><code>${esc(blocks[+line.trim().slice(1, -1)])}</code></pre>`); }
    else if ((m = line.trim().match(/^!\[\[\s*画面\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\]$/))) { flushPara(); flushList(); const t = parseTs(m[1]); out.push(`<figure class="shot-fig" data-ft="${t}"><img alt=""><figcaption><a class="tslink" data-t="${t}">[${m[1]}]</a> 画面</figcaption></figure>`); }
    else if ((m = line.match(/^(#{1,6})\s+(.*)/))) { flushPara(); flushList(); const n = Math.min(4, m[1].length); out.push(`<h${n}>${inline(m[2])}</h${n}>`); }
    else if ((m = line.match(/^\s*[-*+]\s+(.*)/))) { flushPara(); if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; } list.items.push(m[1]); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; } list.items.push(m[1]); }
    else if ((m = line.match(/^>\s?(.*)/))) { flushPara(); flushList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
    else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList(); flushTable();
  return out.join('');
}

/* --------------------------------------------------------------- 外观 ---- */
const THEMES = [
  { id: 'dark', name: '墨黑', bg: '#0e0e10', fg: '#ebe8e1' },
  { id: 'purple', name: '帝王紫', bg: '#170f25', fg: '#efe9f7' },
  { id: 'dim', name: '深灰', bg: '#24252a', fg: '#ecebe7' },
  { id: 'light', name: '浅色', bg: '#fbf9f5', fg: '#22201c' },
];
const ACCENTS = ['#e8b04a', '#ff8a3d', '#ff5a5a', '#b48cff', '#4c9bff', '#3ccf91', '#ece8df'];
const FONTS = {
  yahei: '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif',
  hei: 'SimHei, "Heiti SC", sans-serif',
  deng: 'DengXian, "Microsoft YaHei", sans-serif',
  song: 'SimSun, "Songti SC", serif',
  kai: 'KaiTi, STKaiti, serif',
  fang: 'FangSong, STFangsong, serif',
};

function applyLook() {
  const s = S.settings, root = document.documentElement.style;
  document.documentElement.dataset.theme = s.theme || 'dark';
  root.setProperty('--accent', s.accent || '#e8b04a');
  root.setProperty('--cue-size', (s.font_size || 17) + 'px');
  root.setProperty('--cue-line', s.line_height || 1.6);
  root.setProperty('--cue-gap', (s.cue_gap ?? 9) + 'px');
  root.setProperty('--cue-width', s.cue_width ? s.cue_width + 'px' : 'none');
  root.setProperty('--cue-font', FONTS[s.font_family] || FONTS.yahei);
  $('#txSize').textContent = s.font_size;
  $('#txLine').textContent = (+s.line_height).toFixed(1);
  $('#txGap').textContent = s.cue_gap;
  $('#txWidth').textContent = s.cue_width ? s.cue_width : '全宽';
  $('#txFont').value = s.font_family || 'yahei';
  $('#txTheme').value = s.theme;
  $('#optFollow').checked = !!s.follow;
  $('#optTr').checked = !!s.show_tr;
  $('#optOverlay').checked = !!s.overlay;
  $('#cues').classList.toggle('hide-tr', !s.show_tr);
  $('#overlay').hidden = !s.overlay;
  const cp = s.chat;
  $('#aiModel').textContent = `${S.providerNames[cp] || cp} · ${(s.providers[cp] || {}).chat_model || '未选模型'}`;
  applyLayout();
}

function applyLayout() {
  const s = S.settings;
  $('#library').hidden = !s.sidebar; $('#libSplit').hidden = !s.sidebar;
  $('#library').style.width = (s.lib_w || 270) + 'px';
  $('#left').style.width = Math.round((s.split || 0.5) * 100) + '%';
  const main = $('#stageMain'), panel = $('#aiPanel');
  const side = s.ai_dock === 'side', hidden = !!s.ai_hidden;
  main.classList.toggle('ai-side', side && !hidden);
  main.classList.toggle('ai-hidden', hidden);
  const target = hidden ? null : (side ? $('#aiDockSide') : $('#aiDockBottom'));
  if (target && panel.parentElement !== target) target.appendChild(panel);
  if (!target && panel.parentElement !== document.body) document.body.appendChild(panel);
  panel.classList.toggle('docked', !!target);
  const stageW = $('#stage').getBoundingClientRect().width || 1200;
  const aiW = clamp(s.ai_w || 440, 280, Math.max(300, stageW * 0.65));
  $('#aiDockSide').style.width = aiW + 'px';
  // AI 在右侧时，视频和字幕按剩下的宽度分配比例
  if (side && !hidden) $('#left').style.width = `calc((100% - ${Math.round(aiW + 20)}px) * ${(s.split || 0.5).toFixed(3)})`;
  $('#playerArea').style.flex = (!side && !hidden && !$('#playerWrap').classList.contains('audio')) ? `0 0 ${Math.round((s.video_h || 0.55) * 100)}%` : '';
  $('#btnShowAi').hidden = !hidden;
}

let prefTimer = null, prefPatch = {};
function savePrefs(patch) {
  Object.assign(S.settings, patch); Object.assign(prefPatch, patch);
  applyLook();
  clearTimeout(prefTimer);
  prefTimer = setTimeout(() => { const p = prefPatch; prefPatch = {}; api('settings', { settings: p }).catch(() => {}); }, 400);
}
function keepLayout(fresh) {
  for (const k of LAYOUT_KEYS) fresh[k] = S.settings[k];
  return fresh;
}

async function loadSettings() {
  const d = await api('settings', {});
  S.settings = d.settings; S.doubaoModels = d.doubao_models; S.chatProviders = d.chat_providers;
  S.providerNames = d.provider_names; S.teachers = d.teachers; S.defaultTemplate = d.default_template;
  applyLook(); renderTeacherSelect();
}
const teacherById = id => S.teachers.find(t => t.id === id) || null;
function teacherOptions(selected, none = '未指定老师（不用术语表）') {
  return `<option value="">${none}</option>` + S.teachers.map(t => `<option value="${t.id}" ${t.id === selected ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
}
function renderTeacherSelect() {
  $('#teacherSelect').innerHTML = teacherOptions(S.doc && S.doc.teacher, '未指定老师') + '<option value="__manage">＋ 管理老师和术语表…</option>';
  $('#teacherChip').hidden = !S.doc;
}

/* ------------------------------------------------------------ 媒体库 ---- */
async function loadLibrary(groups) {
  try { S.lib = groups || (await api('library')).groups; } catch (e) { fail(e); }
  renderLibrary();
}
function itemHTML(it) {
  const tags = [];
  if (it.book) tags.push(`<span class="tag ${it.book.done ? 'ok' : ''}">整理 ${it.book.done}/${it.book.total}</span>`);
  if (it.subs) tags.push(`<span class="tag ok">字幕 ${it.subs}</span>`);
  if (it.summary) tags.push('<span class="tag acc">重点</span>');
  if (it.reviewed) tags.push('<span class="tag ok">已校对</span>');
  if (it.clipped) tags.push('<span class="tag">已剪藏</span>');
  if (it.pos && it.duration) tags.push(`<span>看到 ${fmt(it.pos)}</span>`);
  const pct = it.pos && it.duration ? Math.min(100, it.pos / it.duration * 100) : 0;
  const dot = it.clipped ? 'done' : (it.subs || (it.book && it.book.done) ? 'has' : '');
  return `<div class="lib-item ${S.path && it.path.toLowerCase() === S.path.toLowerCase() ? 'active' : ''}" data-open="${esc(it.path)}" title="${esc(it.path)}">
    <i class="dot ${dot}"></i><div class="lib-name">${esc(it.name)}</div>
    ${tags.length ? `<div class="lib-meta">${tags.join('')}</div>` : ''}
    ${pct ? `<div class="lib-prog"><i style="width:${pct.toFixed(1)}%"></i></div>` : ''}</div>`;
}
function renderLibrary() {
  const q = $('#libSearch').value.trim().toLowerCase();
  const body = $('#libBody');
  if (!S.lib.length) {
    body.innerHTML = '<div class="lib-empty">还没有添加文件。<br>点上面的 <b>＋文件夹</b> 一次加入整个文件夹的视频和音频，以后点哪个看哪个。</div>';
    return;
  }
  const scroll = body.scrollTop;
  body.innerHTML = S.lib.map((g, gi) => {
    const items = g.items.filter(it => !q || it.name.toLowerCase().includes(q) || (it.sub || '').toLowerCase().includes(q));
    let lastSub = null;
    const list = items.map(it => {
      const head = it.sub && it.sub !== lastSub ? `<div class="lib-sub">${esc(it.sub)}</div>` : '';
      lastSub = it.sub;
      return head + itemHTML(it);
    }).join('');
    const t = g.teacher && teacherById(g.teacher);
    return `<div class="lib-group ${g.collapsed && !q ? 'collapsed' : ''}">
      <div class="lib-ghead" data-toggle="${gi}">
        <svg class="caret" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
        <span class="lib-gname" title="${esc(g.path)}">${esc(g.name)}</span>
        ${t ? `<span class="lib-gteacher" title="这个文件夹的视频默认用这位老师的术语表">${esc(t.name)}</span>` : ''}
        <span class="lib-gcount">${items.length}</span>
        <button class="lib-more" data-more="${gi}" title="更多">⋯</button>
      </div>
      ${g.missing ? '<div class="lib-missing">找不到这个文件夹，可能被移动或删除了</div>' : ''}
      <div class="lib-items">${list || (g.missing ? '' : '<div class="lib-missing muted">没有音视频文件</div>')}</div>
    </div>`;
  }).join('');
  body.scrollTop = scroll;
}
function patchLib(path, patch, later) {
  if (!path) return;
  const p = path.toLowerCase();
  for (const g of S.lib) for (const it of g.items) if (it.path.toLowerCase() === p) Object.assign(it, patch);
  clearTimeout(patchLib.timer);
  if (later) patchLib.timer = setTimeout(renderLibrary, 800); else renderLibrary();
}
function syncLibFromDoc() {
  if (!S.doc) return;
  if (S.doc.kind === 'book') {
    const chs = S.doc.chapters || [];
    patchLib(S.path, { book: { done: chs.filter(c => (c.md || '').trim()).length, total: chs.length }, clipped: !!S.doc.clip });
    return;
  }
  patchLib(S.path, { subs: S.doc.segments.length, summary: !!(S.doc.summary || '').trim(), clipped: !!S.doc.clip, reviewed: !!S.doc.reviewed, duration: S.doc.duration });
}
function showCtx(x, y, html, bindings) {
  const m = $('#ctxMenu');
  m.innerHTML = html; m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 8)) + 'px';
  m.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 8)) + 'px';
  bindings(m);
}
function groupMenu(gi, x, y) {
  const g = S.lib[gi];
  if (g.type === 'files') {
    showCtx(x, y, `<button class="menu-item danger" data-a="clear">清空单个文件列表（不删除文件）</button>`, m => {
      $('[data-a=clear]', m).onclick = async () => { m.hidden = true; let r; for (const it of g.items) r = await api('library_remove', { kind: 'file', path: it.path }); if (r) loadLibrary(r.groups); };
    });
    return;
  }
  showCtx(x, y, `<div class="menu-label">这个文件夹的老师 / 术语表</div>
    <select data-a="teacher">${teacherOptions(g.teacher, '不指定')}</select>
    <button class="menu-item" data-a="rescan">重新扫描</button>
    <button class="menu-item" data-a="reveal">在资源管理器中打开</button>
    <button class="menu-item danger" data-a="remove">从媒体库移除（不删除文件）</button>`, m => {
    $('[data-a=teacher]', m).onchange = async e => {
      g.teacher = e.target.value; m.hidden = true;
      await api('library_set', { path: g.path, teacher: g.teacher }); renderLibrary();
      toast(g.teacher ? '已设置，这个文件夹里没指定老师的视频会用这位老师的术语表' : '已取消绑定');
    };
    $('[data-a=rescan]', m).onclick = () => { m.hidden = true; loadLibrary(); };
    $('[data-a=reveal]', m).onclick = () => { m.hidden = true; api('reveal', { target: g.path }); };
    $('[data-a=remove]', m).onclick = async () => { m.hidden = true; if (confirm(`从媒体库移除「${g.name}」？\n不会删除电脑上的文件。`)) loadLibrary((await api('library_remove', { kind: 'folder', path: g.path })).groups); };
  });
}
function itemMenu(path, x, y) {
  const inFiles = S.lib.some(g => g.type === 'files' && g.items.some(it => it.path === path));
  showCtx(x, y, `<button class="menu-item" data-a="open">打开</button>
    <button class="menu-item" data-a="reveal">在资源管理器中显示</button>
    ${inFiles ? '<button class="menu-item danger" data-a="remove">从列表移除</button>' : ''}`, m => {
    $('[data-a=open]', m).onclick = () => { m.hidden = true; openPath(path); };
    $('[data-a=reveal]', m).onclick = () => { m.hidden = true; api('reveal', { target: path }); };
    const rm = $('[data-a=remove]', m);
    if (rm) rm.onclick = async () => { m.hidden = true; loadLibrary((await api('library_remove', { kind: 'file', path })).groups); };
  });
}
async function addFolder() {
  try {
    const r = await api('library_add_folder');
    if (r.groups) { loadLibrary(r.groups); toast(`已添加文件夹，找到 ${r.added} 个音视频文件`); if (!S.settings.sidebar) savePrefs({ sidebar: true }); }
  } catch (e) { fail(e); }
}
async function addFiles() {
  try {
    const r = await api('library_add_files');
    if (r.added) { loadLibrary(r.groups); toast(`已添加 ${r.added} 个文件`); if (!S.settings.sidebar) savePrefs({ sidebar: true }); }
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------ 打开文件 ---- */
async function pickAndOpen() {
  try { const { path } = await api('pick_media'); if (path) openPath(path); } catch (e) { fail(e); }
}
window.openPath = async function (path) {
  if (S.job) return toast('还有任务在进行，请等它完成或取消后再切换视频', { error: true });
  try {
    await flushSave();
    const d = await api('open', { path });
    if (d.kind === 'book') { openBook(d); return; }
    setMode('media');
    S.resumeAt = d.position || 0;
    S.path = d.path; S.doc = d.doc; S.info = d.info; S.undo = []; S.active = -1; S.dirty = false; S.loopIdx = null;
    S.lastSaved = d.doc.updated || null;
    $('#btnUndo').disabled = true;
    $('#empty').hidden = true; $('#stageMain').hidden = false;
    $('#fileTitle').textContent = d.doc.title; $('#fileTitle').title = d.path; document.title = `${d.doc.title} - 猛人学习`;
    if (summaryEditing) toggleSummaryEdit(false);
    setupPlayer(d);
    renderTeacherSelect(); renderCues(); renderSummary(); renderChat(); renderFrames(); updateButtons(); updateSaveLabel();
    $('#optReviewed').checked = !!S.doc.reviewed;
    showPendingBanner(); renderLibrary();
    if (d.convert_job) runJob(d.convert_job, '正在准备播放…', { onDone: job => setSource(job.result.src) });
  } catch (e) { fail(e); }
};
function setupPlayer(d) {
  const v = $('#video');
  const audio = !d.info.has_video;
  $('#playerWrap').classList.toggle('audio', audio);
  $('#audioCover').hidden = !audio;
  $('#audioName').textContent = d.doc.title;
  v.removeAttribute('src'); v.load();
  applyLayout();
  if (d.src) setSource(d.src);
}
function setSource(src) {
  const v = $('#video');
  S.resumePending = S.resumeAt > 3;
  v.src = src; v.playbackRate = +$('#speed').value;
  v.addEventListener('loadedmetadata', () => {
    const t = S.resumeAt;
    if (S.resumePending && t < (v.duration || 0) - 3) {
      v.currentTime = t;
      toast(`从上次的 ${fmt(t)} 继续`, { action: { label: '从头播放', run: () => { v.currentTime = 0; savePosition(true); } }, ms: 6000 });
    }
    S.resumePending = false;
  }, { once: true });
}

/* ------------------------------------------------------------ 书籍和文档 ---- */
function setMode(mode) {
  S.mode = mode;
  document.body.classList.toggle('mode-book', mode === 'book');
  $('#bookMain').hidden = mode !== 'book';
  if (mode === 'book') {  // 停掉视频，免得把视频的播放进度记到书上
    $('#stageMain').hidden = true;
    const v = $('#video'); v.pause(); v.removeAttribute('src'); v.load();
  }
}
function bookShift() { const s = S.doc.shift; return s === null || s === undefined || s === '' ? null : +s; }
function pageRef(n) { const s = bookShift(); return s !== null && n - s >= 1 ? `P${n - s}` : `PDF 第 ${n} 页`; }
function openBook(d) {
  S.path = d.path; S.doc = d.doc; S.info = { has_video: false, book: d.book }; S.book = d.book; S.viewer = d.viewer;
  S.undo = []; S.dirty = false; S.lastSaved = d.doc.updated || null; S.bookSel = S.bookSel && S.bookSel.path === d.path ? S.bookSel : { path: d.path, i: -1 };
  S.bookChecked = new Set();
  setMode('book');
  $('#empty').hidden = true;
  $('#fileTitle').textContent = d.doc.title; $('#fileTitle').title = d.path; document.title = `${d.doc.title} - 猛人学习`;
  $('#bkTitle').value = d.doc.title || ''; $('#bkAuthor').value = d.doc.author || '';
  const s = bookShift();
  $('#bkFirst').value = s === null ? '' : s + 1;
  $('#bkFirst').closest('.field').hidden = d.book.kind !== 'pdf';
  const kinds = { pdf: d.book.scan ? '扫描 PDF（OCR 文字层）' : 'PDF', docx: 'Word 文档', text: '文本文档' };
  $('#bkInfo').textContent = `${kinds[d.book.kind]}，${d.book.pages} ${d.book.kind === 'pdf' ? '页' : '段'}，约 ${Math.round(d.book.chars / 1000)} 千字，${d.book.figures} 页有图表` +
    (d.book.kind === 'pdf' && d.book.detected_shift === null ? '。没认出书上的页码，请填上面的「书上第 1 页是 PDF 第几页」，否则锚点写成「PDF 第 X 页」' : '') +
    (d.book.scan && d.book.chars < d.book.pages * 50 ? '。⚠ 这个 PDF 几乎没有文字层，请先用 WPS 做 OCR' : '') +
    (d.book.companion ? `。同时参考同名 Word「${d.book.companion}」的正文` : '') +
    (!d.book.toc && (d.doc.chapters || []).every(c => /^第 \d+–\d+ 页$|^全文$/.test(c.title)) ? '。⚠ 没认出章节，现在是按页数分段；建议点「AI 读目录分章」' : '');
  updateShiftHint();
  if (d.viewer) { $('#bookPdf').hidden = false; $('#bookText').hidden = true; $('#bookPdf').src = d.viewer; }
  else {
    $('#bookPdf').hidden = true; $('#bookText').hidden = false; $('#bookText').innerHTML = '<div class="muted">正在读取…</div>';
    api('book_pages', { path: d.path }).then(r => {
      $('#bookText').innerHTML = r.pages.map(p => `<div class="bpage" id="bp-${p.n}"><span class="bref">第 ${p.n} 段</span>${esc(p.text)}${p.images.map(u => `<img src="${esc(u)}" alt="">`).join('')}</div>`).join('');
    }).catch(fail);
  }
  renderTeacherSelect(); renderBook(); updateButtons(); updateSaveLabel(); renderLibrary();
}
function updateShiftHint() {
  const s = bookShift();
  $('#bkShiftHint').textContent = s === null ? '不填就写成「PDF 第 X 页」' : `锚点：PDF 第 ${s + 1} 页 = P1`;
}
function bookJump(n) {
  if (!S.doc || S.mode !== 'book') return;
  n = clamp(Math.round(n), 1, (S.book && S.book.pages) || 1);
  if (S.viewer) $('#bookPdf').src = S.viewer + '#page=' + n;
  else { const el = $('#bp-' + n); if (el) el.scrollIntoView({ block: 'start' }); }
}
function bookMd(src) {
  return md(src).replace(/PDF\s*第\s*(\d{1,4})(?:\s*[-–—]\s*\d{1,4})?\s*页/g, m => `<a class="pglink" data-n="${m.match(/\d+/)[0]}">${m}</a>`)
    .replace(/(^|[^A-Za-z0-9_"=])P(\d{1,4})(?!\d)/g, (m, pre, p) => `${pre}<a class="pglink" data-p="${p}">P${p}</a>`);
}
function chapterState(c) {
  return (c.md || '').trim() ? '<span class="ch-state ok">✓ 已整理</span>' : '<span class="ch-state">未整理</span>';
}
function renderBook() {
  const d = S.doc, list = $('#bkChapters');
  const chs = d.chapters || [];
  list.innerHTML = `<div class="ch-row overview ${S.bookSel.i === -2 ? 'active' : ''}" data-ch="-2"><span>📘</span><span>00、全书总览</span>${(d.overview || '').trim() ? '<span class="ch-state ok">✓ 已生成</span>' : '<span class="ch-state">未生成</span>'}</div>` +
    chs.map((c, i) => `<div class="ch-row ${S.bookSel.i === i ? 'active' : ''}" data-ch="${i}">
      <input type="checkbox" data-chk ${S.bookChecked.has(i) ? 'checked' : ''} title="勾选后点「整理选中的章节」">
      <input type="text" data-f="title" value="${esc(c.title)}" title="章名">
      <input type="number" data-f="start" value="${c.start}" min="1" title="从 PDF 第几页（${esc(pageRef(c.start))}）">
      <input type="number" data-f="end" value="${c.end}" min="1" title="到 PDF 第几页（${esc(pageRef(c.end))}）">
      <span>${chapterState(c)}<button data-delch title="删除这一章（不影响原文）">✕</button></span></div>`).join('');
  renderChapterView();
  updateButtons();
}
function renderChapterView() {
  const d = S.doc, i = S.bookSel.i;
  const c = i >= 0 ? (d.chapters || [])[i] : null;
  const text = i === -2 ? d.overview : (c ? c.md : '');
  $('#bkViewTitle').textContent = i === -2 ? '00、全书总览' : (c ? `${String(i + 1).padStart(2, '0')}、${c.title}（${pageRef(c.start)}–${pageRef(c.end)}）` : '');
  $('#bkView').innerHTML = (text || '').trim() ? bookMd(text)
    : `<div class="ai-empty">${i === -2 ? '章节整理好以后，点「全书总览」生成。' : c ? '这一章还没整理。点「整理选中的章节」开始。' : '先核对上面的书名、作者和页码，再看看章节分得对不对（可以直接改章名和起止页），然后整理。<br>点左边原文里的页码会跳过去。'}</div>`;
  $('#bkEdit').disabled = !(i === -2 || c); $('#bkCopy').disabled = !(text || '').trim();
  $('#bkViewHint').textContent = c && c.note ? c.note : '';
  if (bookEditing) toggleBookEdit(false);
}
let bookEditing = false;
function toggleBookEdit(on) {
  bookEditing = on ?? !bookEditing;
  $('#bkEditor').hidden = !bookEditing; $('#bkView').hidden = bookEditing;
  $('#bkEdit').textContent = bookEditing ? '完成编辑' : '编辑';
  const i = S.bookSel.i;
  if (bookEditing) $('#bkEditor').value = i === -2 ? (S.doc.overview || '') : ((S.doc.chapters[i] || {}).md || '');
  else renderChapterViewOnly();
}
function renderChapterViewOnly() { const keep = bookEditing; bookEditing = false; renderChapterView(); bookEditing = keep; }
async function bookOrganize(indexes) {
  if (!indexes.length) return toast('没有要整理的章节', { error: true });
  const doc = S.doc;
  try {
    await flushSave();
    const r = await api('book_organize', { path: S.path, doc, indexes });
    let n = 0, adds = 0;
    const ok = await runJob(r.job, '正在整理…', {
      onItems: items => {
        for (const x of items) {
          const c = doc.chapters[x.i]; if (!c) continue;
          c.md = x.md; c.updated = new Date().toLocaleString(); n++; adds += x.added;
          c.note = (x.missing ? `查漏：${x.missing} 页没被引用，补了 ${x.added} 条` : '每一页都有引用') + (x.verified ? `；回页面核对确认了 ${x.verified} 处` : '');
          if (S.doc === doc) { S.bookSel.i = x.i; renderBook(); }
        }
        markDirty(); save();
      },
    });
    if (ok) toast(`整理好了 ${n} 章${adds ? `，查漏补了 ${adds} 条` : ''}。核对后可以点「全书总览」和「剪藏」`, { ms: 8000 });
    syncLibFromDoc();
    return ok;
  } catch (e) { fail(e); return false; }
}
async function bookOverview() {
  const doc = S.doc;
  if (!(doc.chapters || []).some(c => (c.md || '').trim())) return toast('还没有整理好的章节', { error: true });
  try {
    await flushSave();
    const r = await api('book_overview', { doc });
    await runJob(r.job, 'AI 正在写全书总览…', {
      onDone: job => { doc.overview = job.result.overview; markDirty(); save(); if (S.doc === doc) { S.bookSel.i = -2; renderBook(); } toast('全书总览写好了'); },
    });
  } catch (e) { fail(e); }
}
async function bookAiToc() {
  if ((S.doc.chapters || []).some(c => (c.md || '').trim()) && !confirm('重新分章会清掉已经整理的章节内容。继续吗？')) return;
  try {
    const r = await api('book_chapters', { path: S.path, ai: true, shift: bookShift() });
    await runJob(r.job, 'AI 正在读目录…', {
      onDone: job => {
        const chs = job.result.chapters || [];
        if (!chs.length) return toast('AI 没有从目录里认出章节，可以手动改章名和起止页', { error: true });
        S.doc.chapters = chs.map(c => ({ ...c, md: '' })); S.bookSel.i = 0; S.bookChecked = new Set();
        renderBook(); markDirty(); toast(`分成了 ${chs.length} 章，请核对起止页`);
      },
    });
  } catch (e) { fail(e); }
}
async function bookClip() {
  try {
    await flushSave();
    const r = await api('book_clip', { doc: S.doc });
    S.doc.clip = { dir: r.dir, time: r.time }; markDirty(); save();
    toast(`已剪藏 ${r.files.length} 篇笔记到 Obsidian`, { action: { label: '在 Obsidian 中打开', run: () => api('reveal', { target: r.uri }) }, ms: 8000 });
    return true;
  } catch (e) { fail(e); return false; }
}
function bindBook() {
  $('#bkTitle').addEventListener('input', e => { if (S.doc) { S.doc.title = e.target.value.trim(); $('#fileTitle').textContent = S.doc.title; markDirty(); } });
  $('#bkAuthor').addEventListener('input', e => { if (S.doc) { S.doc.author = e.target.value.trim(); markDirty(); } });
  $('#bkFirst').addEventListener('change', e => {
    const v = e.target.value.trim();
    S.doc.shift = v === '' ? null : Math.max(0, +v - 1);
    updateShiftHint(); renderBook(); markDirty();
  });
  $('#bkChapters').addEventListener('click', e => {
    const row = e.target.closest('[data-ch]'); if (!row) return;
    const i = +row.dataset.ch;
    if (e.target.matches('[data-chk]')) { e.target.checked ? S.bookChecked.add(i) : S.bookChecked.delete(i); return; }
    if (e.target.matches('[data-delch]')) {
      const c = S.doc.chapters[i];
      if ((c.md || '').trim() && !confirm(`删除「${c.title}」？已整理的内容也会删掉。`)) return;
      S.doc.chapters.splice(i, 1); S.bookChecked = new Set(); S.bookSel.i = -1; renderBook(); markDirty(); return;
    }
    if (S.bookSel.i !== i) { S.bookSel.i = i; $$('.ch-row', $('#bkChapters')).forEach(r => r.classList.toggle('active', r === row)); renderChapterView(); }
    if (i >= 0 && !e.target.matches('input')) bookJump(S.doc.chapters[i].start);
  });
  $('#bkChapters').addEventListener('change', e => {
    const row = e.target.closest('[data-ch]'); const f = e.target.dataset.f; if (!row || !f) return;
    const c = S.doc.chapters[+row.dataset.ch];
    c[f] = f === 'title' ? e.target.value.trim() : clamp(Math.round(+e.target.value || 1), 1, (S.book && S.book.pages) || 1);
    if (c.end < c.start) c.end = c.start;
    markDirty(); renderChapterView();
  });
  $('#bkRun').onclick = () => { if (S.job) return; const sel = [...S.bookChecked].sort((a, b) => a - b); bookOrganize(sel.length ? sel : (S.bookSel.i >= 0 ? [S.bookSel.i] : [])); };
  $('#bkRunAll').onclick = () => { if (!S.job) bookOrganize(S.doc.chapters.map((c, i) => (c.md || '').trim() ? -1 : i).filter(i => i >= 0)); };
  $('#bkOverview').onclick = () => { if (!S.job) bookOverview(); };
  $('#bkAiToc').onclick = () => { if (!S.job) bookAiToc(); };
  $('#bkAddCh').onclick = () => {
    const last = S.doc.chapters[S.doc.chapters.length - 1];
    const start = last ? Math.min(last.end + 1, S.book.pages) : 1;
    S.doc.chapters.push({ title: '新的一章', start, end: S.book.pages, md: '' });
    S.bookSel.i = S.doc.chapters.length - 1; renderBook(); markDirty();
  };
  $('#bkEdit').onclick = () => toggleBookEdit();
  $('#bkEditor').addEventListener('input', e => {
    const i = S.bookSel.i;
    if (i === -2) S.doc.overview = e.target.value; else if (S.doc.chapters[i]) S.doc.chapters[i].md = e.target.value;
    markDirty();
  });
  $('#bkCopy').onclick = () => { const i = S.bookSel.i; navigator.clipboard.writeText(i === -2 ? S.doc.overview : S.doc.chapters[i].md).then(() => toast('已复制')); };
  $('#bkView').addEventListener('click', e => {
    const a = e.target.closest('.pglink'); if (!a) return;
    if (a.dataset.n) bookJump(+a.dataset.n);
    else { const s = bookShift(); bookJump(+a.dataset.p + (s === null ? 0 : s)); }
  });
  dragger($('#bookSplit'), 'x', ev => {
    const L = $('#bookMain').getBoundingClientRect();
    $('#bookLeft').style.width = clamp((ev.clientX - L.left) / L.width, 0.2, 0.75) * 100 + '%';
  }, () => {});
}

/* 播放进度：播放中每 5 秒、暂停、关闭软件时记下来 */
let posAt = 0;
function savePosition(force) {
  const v = $('#video');
  if (!S.path || S.resumePending || !v.getAttribute('src') || !isFinite(v.duration) || !v.duration) return Promise.resolve();
  const now = Date.now();
  if (!force && now - posAt < 5000) return Promise.resolve();
  posAt = now;
  const t = v.currentTime > v.duration - 3 ? 0 : v.currentTime;
  patchLib(S.path, { pos: t, duration: v.duration }, true);
  return api('position', { path: S.path, t, d: v.duration }).catch(() => {});
}

/* ------------------------------------------------------------- 字幕列表 ---- */
const ICONS = {
  align: '<svg viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2M9 2h6"/></svg>',
  split: '<svg viewBox="0 0 24 24"><path d="M4 12h16M12 4v4m0 8v4"/></svg>',
  merge: '<svg viewBox="0 0 24 24"><path d="M7 4v6a5 5 0 0 0 10 0V4M12 15v5"/></svg>',
  del: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
};
function doubtOf(seg) {
  const ds = S.doc && S.doc.doubts;
  return ds && seg ? ds.find(d => Math.abs(d.t - seg.start) < 0.02) || null : null;
}
function cueHTML(seg, i) {
  const d = doubtOf(seg);
  return `<div class="cue${d ? ' doubt' : ''}" data-i="${i}">
    <button class="ts" tabindex="-1" title="跳到这里">${fmt(seg.start)}</button>
    <div class="cue-body">
      <div class="src" contenteditable="plaintext-only" spellcheck="false">${esc(seg.text)}</div>
      <div class="tr" contenteditable="plaintext-only" spellcheck="false" data-ph="${S.doc.lang === 'en' ? '（还没有译文，点「翻译」或在这里手动输入）' : ''}">${esc(seg.tr || '')}</div>
      ${d ? `<div class="doubt-tag"><span class="why" title="${esc(d.reason)}">存疑：${esc(d.reason)}</span><button data-doubt="play" title="单句循环播放这一句">回听</button><button data-doubt="ok" title="已经回听确认（或改好了），去掉标记">已确认</button></div>` : ''}
    </div>
    <div class="cue-tools">
      <button data-act="align" title="把这一句的开始时间设为当前播放位置">${ICONS.align}</button>
      <button data-act="split" title="在光标处拆成两句">${ICONS.split}</button>
      <button data-act="merge" title="与下一句合并">${ICONS.merge}</button>
      <button data-act="del" title="删除这一句">${ICONS.del}</button>
    </div>
  </div>`;
}
function renderCues() {
  const box = $('#cues');
  const segs = S.doc ? S.doc.segments : [];
  box.innerHTML = segs.length ? `<div class="cues-inner">${segs.map(cueHTML).join('')}</div>`
    : `<div class="cues-empty">这个文件还没有字幕。<br>点上方的「生成字幕」或「一键整理」开始。</div>`;
  S.active = -1;
  $('#cueCount').textContent = segs.length ? `${segs.length} 句` : '';
  updateDoubtBtn();
  runSearch(false);
  updateActive(true);
}
function cuesInner() {
  let inner = $('.cues-inner', $('#cues'));
  if (!inner) { $('#cues').innerHTML = '<div class="cues-inner"></div>'; inner = $('.cues-inner', $('#cues')); }
  return inner;
}
function cueEl(i) { return $(`.cue[data-i="${i}"]`, $('#cues')); }
function findIndex(t) {
  const segs = S.doc ? S.doc.segments : [];
  let lo = 0, hi = segs.length - 1, ans = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (segs[mid].start <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return ans;
}
function updateActive(force) {
  if (!S.doc) return;
  const v = $('#video'), t = v.currentTime || 0;
  const segs = S.doc.segments;
  let i = findIndex(t);
  if ($('#optLoop').checked && S.loopIdx != null && segs[S.loopIdx] && !v.paused) {
    const seg = segs[S.loopIdx];
    if (t >= seg.end - 0.03 || t < seg.start - 0.5) { v.currentTime = seg.start; return; }
    i = S.loopIdx;
  }
  if (i !== S.active || force) {
    const old = cueEl(S.active); if (old) old.classList.remove('active');
    S.active = i;
    const el = cueEl(i);
    if (el) {
      el.classList.add('active');
      const recentlyScrolled = Date.now() - S.userScrollAt < 2500;
      if (S.settings.follow && !S.editing && !recentlyScrolled && !v.paused) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }
  if (S.settings.overlay) {
    const seg = segs[i];
    const show = seg && t <= seg.end + 0.6;
    $('#overlay').innerHTML = show ? esc(seg.text) + (S.settings.show_tr && seg.tr ? `<span class="o-tr">${esc(seg.tr)}</span>` : '') : '';
  }
}
function seek(t, play) {
  const v = $('#video');
  if (!v.getAttribute('src')) return;
  v.currentTime = Math.max(0, t + 0.01);
  if (play) v.play().catch(() => {});
  setTimeout(() => updateActive(true), 30);
}
function doubtIndexes() {
  if (!S.doc || !(S.doc.doubts || []).length) return [];
  return S.doc.segments.map((seg, i) => doubtOf(seg) ? i : -1).filter(i => i >= 0);
}
function updateDoubtBtn() {
  const n = doubtIndexes().length;
  $('#btnDoubts').hidden = !n;
  $('#btnDoubts').textContent = `存疑 ${n}`;
}
function playLoop(i) {
  const seg = S.doc.segments[i]; if (!seg) return;
  $('#optLoop').checked = true; S.loopIdx = i;
  seek(seg.start, true);
  const el = cueEl(i); if (el) { el.scrollIntoView({ block: 'center' }); S.userScrollAt = Date.now(); }
}
function nextDoubt() {
  const list = doubtIndexes(); if (!list.length) return;
  const i = list.find(x => x > S.active) ?? list[0];
  playLoop(i);
}
function resolveDoubt(i) {
  const seg = S.doc.segments[i], d = doubtOf(seg); if (!d) return;
  S.doc.doubts = S.doc.doubts.filter(x => x !== d);
  const el = cueEl(i);
  if (el) { el.classList.remove('doubt'); const tag = $('.doubt-tag', el); if (tag) tag.remove(); }
  updateDoubtBtn(); markDirty();
}
function onCueInput(e) {
  const el = e.target.closest('.src, .tr'); if (!el) return;
  const i = +el.closest('.cue').dataset.i;
  const seg = S.doc.segments[i]; if (!seg) return;
  if (el.classList.contains('src')) seg.text = el.textContent; else seg.tr = el.textContent;
  markDirty();
}
function snapshot() {
  S.undo.push(JSON.stringify(S.doc.segments));
  if (S.undo.length > 60) S.undo.shift();
  $('#btnUndo').disabled = false;
}
function undo() {
  if (!S.undo.length) return;
  S.doc.segments = JSON.parse(S.undo.pop());
  $('#btnUndo').disabled = !S.undo.length;
  renderCues(); markDirty(); toast('已撤销');
}
function caretOffset(el) {
  const sel = getSelection();
  if (!sel.rangeCount || !el.contains(sel.anchorNode)) return null;
  const r = sel.getRangeAt(0).cloneRange();
  r.selectNodeContents(el); r.setEnd(sel.anchorNode, sel.anchorOffset);
  return r.toString().length;
}
let lastCaret = { i: -1, field: 'src', pos: null };
function cueAction(act, i) {
  const segs = S.doc.segments, seg = segs[i]; if (!seg) return;
  const v = $('#video');
  if (act === 'align') {
    snapshot();
    const t = +v.currentTime.toFixed(2);
    seg.start = t;
    if (segs[i - 1]) segs[i - 1].end = Math.min(segs[i - 1].end, t);
    if (seg.end < t) seg.end = segs[i + 1] ? segs[i + 1].start : t + 2;
    segs.sort((a, b) => a.start - b.start);
  } else if (act === 'split') {
    const pos = lastCaret.i === i && lastCaret.field === 'src' && lastCaret.pos != null ? lastCaret.pos : null;
    const text = seg.text;
    const at = pos != null && pos > 0 && pos < text.length ? pos : Math.round(text.length / 2);
    snapshot();
    const mid = +(seg.start + (seg.end - seg.start) * at / Math.max(1, text.length)).toFixed(2);
    segs.splice(i + 1, 0, { start: mid, end: seg.end, text: text.slice(at).trim(), tr: '' });
    seg.text = text.slice(0, at).trim(); seg.end = mid;
  } else if (act === 'merge') {
    const next = segs[i + 1]; if (!next) return toast('这已经是最后一句了');
    snapshot();
    const sep = /[A-Za-z0-9,.!?]$/.test(seg.text) && /^[A-Za-z0-9]/.test(next.text) ? ' ' : '';
    seg.text = seg.text + sep + next.text;
    seg.tr = [seg.tr, next.tr].filter(Boolean).join('');
    seg.end = next.end;
    segs.splice(i + 1, 1);
  } else if (act === 'del') {
    snapshot();
    segs.splice(i, 1);
  }
  renderCues(); markDirty();
  const el = cueEl(Math.min(i, segs.length - 1)); if (el) el.scrollIntoView({ block: 'nearest' });
}

/* ---------------------------------------------------------------- 保存 ---- */
function markDirty() {
  if (!S.doc) return;
  S.dirty = true; updateSaveLabel();
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(() => save(), 1200);
}
function updateSaveLabel() {
  const b = $('#btnSave');
  b.classList.toggle('dirty', S.dirty);
  b.title = S.dirty ? '有修改还没保存（会自动保存）' : (S.lastSaved ? `已保存 ${S.lastSaved}` : '保存（Ctrl+S）');
  $('#saveLabel').textContent = S.saving ? '保存中…' : (S.dirty ? '保存' : (S.lastSaved ? '已保存' : '保存'));
}
async function save(quiet = true) {
  if (!S.doc || !S.path) return;
  clearTimeout(S.saveTimer);
  if (S.saving) { await S.saving; if (!S.dirty) return; }
  S.dirty = false;
  const run = (async () => {
    try {
      const r = await api('save', { path: S.path, doc: S.doc });
      S.lastSaved = r.updated;
      syncLibFromDoc();
      if (!quiet) toast('已保存');
    } catch (e) { S.dirty = true; fail(new Error('保存失败：' + e.message)); }
  })();
  S.saving = run; updateSaveLabel();
  await run; S.saving = null; updateSaveLabel();
}
async function flushSave() { await savePosition(true); if (S.dirty) await save(); else if (S.saving) await S.saving; return true; }
window.flushSave = flushSave;
/* 关闭窗口前由后台调用：保存完（最多等 3 秒）再通知后台关窗 */
window.prepareClose = async function () {
  try { await Promise.race([flushSave(), sleep(3000)]); }
  catch (e) { logToFile('error', '关闭前保存失败：' + (e.message || e)); }
  finally { try { await window.pywebview.api.call('quit', {}); } catch (_) { /* 后台会自己超时关窗 */ } }
};
window.addEventListener('beforeunload', () => { if (S.dirty) save(); });

function updateButtons() {
  if (S.mode === 'book') {
    const busy = !!S.job, done = S.doc && (S.doc.chapters || []).some(c => (c.md || '').trim());
    $('#btnSave').disabled = !S.doc;
    $('#btnClip').disabled = !done || busy;
    ['#bkRun', '#bkRunAll', '#bkAiToc', '#bkAddCh'].forEach(s => $(s).disabled = busy);
    $('#bkOverview').disabled = busy || !done;
    return;
  }
  const has = S.doc && S.doc.segments.length > 0, busy = !!S.job;
  $('#btnTranscribe').disabled = !S.doc || busy;
  $('#btnPipeline').disabled = !S.doc || busy;
  $('#btnCorrect').disabled = !has || busy;
  $('#btnTranslate').disabled = !has || busy;
  $('#btnSave').disabled = !S.doc;
  $('#btnExport').disabled = !has;
  $('#btnClip').disabled = !has;
  $('#btnSummary').disabled = !has;
  const vid = !!(S.info && S.info.has_video);
  $('#btnFrames').disabled = !S.doc || busy || !vid;
  $('#btnCapture').disabled = !S.doc || busy || !vid;
  $('#btnDescribe').disabled = !S.doc || busy || !((S.doc && S.doc.frames) || []).some(f => !f.note);
  $('#btnRecheck').disabled = !S.doc || busy || !vid || !doubtIndexes().length;
  $('#btnReview').disabled = !has || busy || !(S.doc && (S.doc.summary || '').trim()) || !!S.chatAbort;
  $('#btnSend').disabled = !has;
}

/* ------------------------------------------------------------ 后台任务 ---- */
/* 返回 true 表示成功完成 */
async function runJob(id, label, { onItems, onDone, onError, onExtra } = {}) {
  S.job = id; updateButtons();
  const bar = $('#jobbar'); bar.hidden = false;
  $('#jobText').textContent = label; $('#jobFill').style.width = '0%';
  let since = 0, ok = false;
  try {
    for (;;) {
      const { job } = await api('job', { id, since });
      since = job.total;
      if (job.items.length && onItems) onItems(job.items);
      if (job.extra && onExtra) onExtra(job.extra);
      $('#jobFill').style.width = Math.round(job.progress * 100) + '%';
      $('#jobText').textContent = (job.message || label) + (job.progress > 0 && job.progress < 1 ? `　${Math.round(job.progress * 100)}%` : '');
      if (job.status === 'done') { ok = true; if (onDone) onDone(job); break; }
      if (job.status === 'error') { onError ? onError(job) : fail(new Error(job.error)); break; }
      if (job.status === 'cancelled') { toast('已取消'); if (onError) onError(job); break; }
      await sleep(600);
    }
  } catch (e) { fail(e); }
  finally { S.job = null; bar.hidden = true; updateButtons(); }
  return ok;
}
$('#jobCancel').onclick = () => { if (S.job) api('cancel', { id: S.job }).catch(() => {}); S.cancelPipeline = true; };

/* ------------------------------------------------------------ 生成字幕 ---- */
const PROVIDER_LABEL = { doubao: '豆包', siliconflow: '硅基流动', openai: 'OpenAI' };
function providerHint(p) {
  const s = S.settings.providers[p];
  const model = p === 'doubao' ? (S.doubaoModels[s.model] || {}).name : s.asr_model;
  const terms = { doubao: '术语会作为热词传给豆包。', openai: '术语会作为提示词传给 Whisper。', siliconflow: '硅基流动读不了术语表，建议识别后用 AI 校对。' }[p];
  return `模型：${model || ''}${s.has_key ? '' : '　⚠ 还没有填写 API Key'}　${terms}`;
}
function teacherHint(id) {
  const t = teacherById(id);
  if (!t) return '不用术语表。可以在「设置 → 老师与术语表」里添加。';
  const n = (t.terms || '').split(/[\r\n,，、;；]+/).filter(x => x.trim()).length;
  return `${t.terms_file ? '关联了术语表文件（自动读最新内容）　' : ''}${n ? `手写术语约 ${n} 个词条` : ''}${t.about ? '　背景：' + t.about.slice(0, 40) : ''}`;
}
function openTranscribe() {
  const dlg = $('#dlgTranscribe');
  const tid = S.doc.teacher || '';
  $('#trTeacher').innerHTML = teacherOptions(tid);
  const lang = S.doc.lang || (teacherById(tid) || {}).lang || S.settings.last_lang || 'zh';
  $$('input[name=lang]', dlg).forEach(r => r.checked = r.value === lang);
  const setProv = () => {
    const l = $('input[name=lang]:checked', dlg).value;
    $('#trProvider').value = l === 'en' ? S.settings.asr_en : S.settings.asr_zh;
    $('#trProviderHint').textContent = providerHint($('#trProvider').value);
  };
  const setTeacher = () => {
    const t = $('#trTeacher').value;
    $('#trTeacherHint').textContent = teacherHint(t);
    $('#trCorrect').checked = !!t && S.settings.auto_correct;
  };
  $$('input[name=lang]', dlg).forEach(r => r.onchange = setProv);
  $('#trProvider').onchange = () => $('#trProviderHint').textContent = providerHint($('#trProvider').value);
  $('#trTeacher').onchange = () => {
    const t = teacherById($('#trTeacher').value);
    if (t && !S.doc.lang) { $$('input[name=lang]', dlg).forEach(r => r.checked = r.value === t.lang); setProv(); }
    setTeacher();
  };
  setProv(); setTeacher();
  $('#trWarn').hidden = !S.doc.segments.length;
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = async () => {
    if (dlg.returnValue !== 'ok') return;
    setDocTeacher($('#trTeacher').value);
    const ok = await startTranscribe($('input[name=lang]:checked', dlg).value, $('#trProvider').value);
    if (ok && $('#trCorrect').checked) await startCorrect(true);
  };
}
function setDocTeacher(id) {
  if (!S.doc || (S.doc.teacher || '') === (id || '')) return;
  S.doc.teacher = id || ''; renderTeacherSelect(); markDirty();
}
async function startTranscribe(lang, provider, pending) {
  try {
    await flushSave();
    const r = await api('transcribe', { path: S.path, lang, provider, pending, teacher: S.doc.teacher, title: S.doc.title });
    const before = S.doc.segments;
    const live = [];
    if (before.length) { snapshot(); pushVersion('重新识别前'); }
    S.doc.segments = live; renderCues();
    return await runJob(r.job, `${PROVIDER_LABEL[r.provider]}识别中…`, {
      onItems: items => {
        for (const c of items) live.push({ start: c.start, end: c.end, text: c.text, tr: '' });
        cuesInner().insertAdjacentHTML('beforeend', items.map((c, k) => cueHTML(c, live.length - items.length + k)).join(''));
        $('#cueCount').textContent = `${live.length} 句`;
      },
      onExtra: extra => {
        if (extra && extra.task_id && (!S.doc.pending_task || S.doc.pending_task.task_id !== extra.task_id)) {
          S.doc.pending_task = { ...extra, lang, provider }; save();
        }
      },
      onDone: job => {
        S.doc.lang = lang; S.doc.provider = `${r.provider}${job.result.model ? ' · ' + job.result.model : ''}`;
        S.doc.corrected = false; S.doc.reviewed = false; $('#optReviewed').checked = false;
        delete S.doc.pending_task;
        renderCues(); markDirty(); save(); showPendingBanner();
        toast(`字幕生成好了，共 ${live.length} 句`);
      },
      onError: job => {
        if (live.length && job.status === 'error') {
          fail(new Error(job.error));
          if (confirm(`识别中途出错，已经识别了 ${live.length} 句。\n要保留这部分吗？选「取消」恢复原来的字幕。`)) { S.doc.lang = lang; renderCues(); markDirty(); return; }
        } else if (job.status === 'error') fail(new Error(job.error));
        S.doc.segments = before; if (before.length) { S.undo.pop(); (S.doc.versions || []).shift(); } renderCues();
      },
    });
  } catch (e) { fail(e); return false; }
}
function showPendingBanner() {
  const b = $('#banner');
  const p = S.doc && S.doc.pending_task;
  if (!p || S.job) { b.hidden = true; return; }
  const when = new Date(p.submitted * 1000).toLocaleString();
  b.innerHTML = `<span>豆包${p.model === 'idle' ? '闲时版' : '标准版'}的识别任务还在处理中（${esc(when)} 提交）。</span><button class="btn small primary" id="resumeTask">继续获取结果</button><button class="btn small" id="dropTask">放弃</button>`;
  b.hidden = false;
  $('#resumeTask').onclick = () => { b.hidden = true; startTranscribe(p.lang, 'doubao', p); };
  $('#dropTask').onclick = () => { delete S.doc.pending_task; markDirty(); b.hidden = true; };
}

/* ------------------------------------------------------------ 字幕版本 ---- */
/* 重新识别、清空、恢复之前，把当前字幕存一份（最多 5 份，存在字幕文件里） */
function pushVersion(reason) {
  const segs = S.doc.segments;
  if (!segs.length) return;
  S.doc.versions = [{ time: new Date().toLocaleString(), reason, provider: S.doc.provider || '', corrected: !!S.doc.corrected,
    count: segs.length, segments: JSON.parse(JSON.stringify(segs)) }, ...(S.doc.versions || [])].slice(0, 5);
}
function renderVersions() {
  const vs = (S.doc && S.doc.versions) || [];
  $('#menuVersions').innerHTML = '<div class="menu-label">历史版本（重新识别、清空、恢复前自动保存，最多 5 份）</div>' +
    (vs.length ? vs.map((v, i) => `<button class="menu-item" data-ver="${i}">恢复：${esc(v.reason)}的字幕<span class="vmeta">${esc(v.time)}　${v.count} 句${v.provider ? '　' + esc(v.provider) : ''}${v.corrected ? '　已校对过' : ''}</span></button>`).join('')
      : '<div class="menu-label">还没有历史版本</div>') +
    `<div class="menu-label">当前字幕</div><button class="menu-item danger" data-ver="clear" ${S.doc && S.doc.segments.length ? '' : 'disabled'}>清空当前字幕（会先存一份历史版本）</button>`;
}
function useVersion(i) {
  closeMenus();
  if (S.job) return toast('还有任务在进行，请稍后再试', { error: true });
  if (i === 'clear') {
    if (!confirm('清空当前字幕？\n清空前会自动存一份历史版本，之后还可以恢复。')) return;
    snapshot(); pushVersion('清空前');
    S.doc.segments = []; S.doc.corrected = false; S.doc.reviewed = false; $('#optReviewed').checked = false;
    renderCues(); markDirty(); save(); updateButtons();
    toast('已清空，可以重新生成字幕');
    return;
  }
  const v = S.doc.versions[+i];
  snapshot(); pushVersion('恢复旧版本前');
  S.doc.versions = S.doc.versions.filter(x => x !== v);
  S.doc.segments = v.segments; S.doc.provider = v.provider; S.doc.corrected = v.corrected;
  renderCues(); markDirty(); save(); updateButtons();
  toast(`已恢复 ${v.time} 的字幕（${v.count} 句）。刚才的字幕也存成了历史版本`);
}

/* ------------------------------------------------------------ AI 校对 ---- */
async function startCorrect(auto) {
  const t = teacherById(S.doc.teacher);
  if (!t && !auto && !confirm('这个视频没有指定老师，没有术语表可参考。\n仍然让 AI 只修正明显的错别字吗？\n（可以在顶栏选择老师）')) return false;
  try {
    await flushSave();
    const refs = S.doc.segments.slice();
    const items = refs.map((s, i) => [i, s.text, s.start]).filter(([, txt]) => txt.trim());
    const r = await api('correct', { items, title: S.doc.title, teacher: S.doc.teacher, frames: (S.doc.frames || []).filter(f => f.note) });
    snapshot();
    const oldDoubts = S.doc.doubts || [];
    const doubts = [];
    let n = 0;
    const ok = await runJob(r.job, 'AI 校对中…', {
      onItems: list => {
        for (const it of list) {
          const seg = refs[it.i]; if (!seg) continue;
          if (it.doubt) { doubts.push({ t: seg.start, reason: it.doubt, text: seg.text }); continue; }
          seg.text = it.text; n++;
          const el = cueEl(S.doc.segments.indexOf(seg));
          if (el) { $('.src', el).textContent = it.text; el.classList.add('fixed'); }
        }
        if (list.length) markDirty();
      },
    });
    if (ok) {
      S.doc.doubts = doubts; S.doc.corrected = true;
      const fixedNow = $$('.cue.fixed', $('#cues')).map(el => +el.dataset.i);
      renderCues(); fixedNow.forEach(i => { const el = cueEl(i); if (el) el.classList.add('fixed'); });
      markDirty(); save();
      toast((n ? `AI 校对完成，改了 ${n} 句（绿色下划线标出），不满意可以按「撤销」恢复` : 'AI 校对完成，没有发现需要改的地方')
        + (doubts.length ? `。另有 ${doubts.length} 句存疑（橙色），点字幕栏上方的「存疑」逐句回听` : ''), { ms: 8000 });
    } else {
      S.doc.doubts = oldDoubts;
      if (!n) { S.undo.pop(); $('#btnUndo').disabled = !S.undo.length; }
    }
    return ok;
  } catch (e) { fail(e); return false; }
}

/* ---------------------------------------------------------------- 翻译 ---- */
function openTranslate() {
  const segs = S.doc.segments;
  const missing = segs.filter(s => s.text.trim() && !s.tr.trim()).length;
  const dlg = $('#dlgTranslate');
  $('#tlInfo').innerHTML = (S.doc.lang === 'zh' ? '这是中文字幕，一般不需要翻译。<br>' : '') +
    `共 ${segs.length} 句，其中 ${missing} 句还没有译文。将使用 <b>${esc(S.providerNames[S.settings.translate])}</b> 翻译。`;
  $('#tlMissing').textContent = missing ? `翻译缺少的 ${missing} 句` : '没有缺少的';
  $('#tlMissing').disabled = !missing;
  $('#tlAll').hidden = missing === segs.length;
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = () => { if (dlg.returnValue === 'missing' || dlg.returnValue === 'all') startTranslate(dlg.returnValue === 'all'); };
}
async function startTranslate(all) {
  try {
    const refs = S.doc.segments.slice();
    const items = refs.map((s, i) => [i, s.text]).filter(([i, t]) => t.trim() && (all || !refs[i].tr.trim()));
    if (!items.length) return true;
    if (all) snapshot();
    const r = await api('translate', { items, title: S.doc.title, teacher: S.doc.teacher });
    let n = 0;
    return await runJob(r.job, '翻译中…', {
      onItems: pairs => {
        for (const [i, zh] of pairs) {
          const seg = refs[i]; if (!seg) continue;
          seg.tr = zh; n++;
          const el = cueEl(S.doc.segments.indexOf(seg)); if (el) $('.tr', el).textContent = zh;
        }
        markDirty();
      },
      onDone: () => { save(); toast(`翻译完成，共 ${n} 句`); if (!S.settings.show_tr) savePrefs({ show_tr: true }); },
    });
  } catch (e) { fail(e); return false; }
}

/* ------------------------------------------------------------ 一键整理 ---- */
function openPipeline() {
  const dlg = $('#dlgPipeline');
  const has = S.doc.segments.length > 0;
  $('#plTeacher').innerHTML = teacherOptions(S.doc.teacher || '');
  $('#plAsr').checked = !has;
  $('#plAsrLabel').textContent = has ? '重新生成字幕（会替换现有字幕）' : '生成字幕';
  const lang = S.doc.lang || (teacherById(S.doc.teacher) || {}).lang || S.settings.last_lang || 'zh';
  $$('input[name=plLang]').forEach(r => r.checked = r.value === lang);
  const sync = () => {
    $('#plCorrect').checked = !!$('#plTeacher').value && !S.doc.corrected;
    const l = $('input[name=plLang]:checked').value;
    $('#plTranslate').checked = l === 'en';
    $('#plTranslate').disabled = l !== 'en';
  };
  $('#plTeacher').onchange = () => {
    const t = teacherById($('#plTeacher').value);
    if (t && !S.doc.lang) $$('input[name=plLang]').forEach(r => r.checked = r.value === t.lang);
    sync();
  };
  $$('input[name=plLang]').forEach(r => r.onchange = sync);
  sync();
  $('#plSummary').checked = true;
  $('#plClip').checked = false;
  const vid = !!(S.info && S.info.has_video), nShots = (S.doc.frames || []).length;
  $('#plFrames').disabled = !vid;
  $('#plFrames').checked = vid && !nShots && providerReady(S.settings.vision);
  $('#plRecheck').disabled = !vid;
  $('#plRecheck').checked = vid && providerReady(S.settings.vision);
  $('#plFramesLabel').textContent = !vid ? '找关键帧（这是音频文件，没有画面）'
    : nShots ? `重新找关键帧（现在有 ${nShots} 张画面记录，会替换自动找的那些）`
      : `找关键帧、写画面记录${providerReady(S.settings.vision) ? '' : '（还没设置画面识别用的 AI）'}`;
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = () => { if (dlg.returnValue === 'ok') runPipeline(); };
}
async function runPipeline() {
  S.cancelPipeline = false;
  setDocTeacher($('#plTeacher').value);
  const lang = $('input[name=plLang]:checked').value;
  const steps = [];
  if ($('#plAsr').checked) steps.push(['生成字幕', () => startTranscribe(lang, lang === 'en' ? S.settings.asr_en : S.settings.asr_zh)]);
  if ($('#plCorrect').checked) steps.push(['AI 校对', () => startCorrect(true)]);
  if ($('#plFrames').checked && !$('#plFrames').disabled) steps.push(['找关键帧', () => findFrames(true)]);
  if ($('#plRecheck').checked && !$('#plRecheck').disabled) steps.push(['用画面复核存疑句', () => recheckDoubts(true)]);
  if ($('#plTranslate').checked && !$('#plTranslate').disabled) steps.push(['翻译', () => startTranslate(false)]);
  if ($('#plSummary').checked) steps.push(['课程重点', () => doSummary()]);
  if ($('#plClip').checked) steps.push(['剪藏', () => doClip()]);
  for (const [name, fn] of steps) {
    if (S.cancelPipeline) { toast('一键整理已停止'); return; }
    if (name !== '生成字幕' && !S.doc.segments.length) { toast('没有字幕，一键整理已停止', { error: true }); return; }
    const ok = await fn();
    if (!ok) { toast(`「${name}」没有完成，一键整理已停止`, { error: true }); return; }
  }
  toast('一键整理完成。建议检查一遍字幕，打开字幕栏上方的「已校对」后再剪藏。', { ms: 9000 });
}

/* ---------------------------------------------------------------- 导出 ---- */
async function doExport(kind) {
  closeMenus();
  const mode = $('input[name=exMode]:checked').value;
  const stamps = $('#exStamps').checked;
  const suffix = { both: '中英', src: '原文', tr: '中文' }[mode];
  const name = `${S.doc.title}-${suffix}.${kind}`;
  try {
    const { path: dest } = await api('save_dialog', { name, ext: kind });
    if (!dest) return;
    await api('export', { doc: S.doc, dest, format: kind, mode, timestamps: stamps });
    toast('已保存：' + dest, { action: { label: '打开文件夹', run: () => api('reveal', { target: dest }) } });
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------- 剪藏 ---- */
async function doClip() {
  try {
    await flushSave();
    const r = await api('clip', { doc: S.doc });
    S.doc.clip = { path: r.path, time: r.time }; markDirty(); save();
    toast(S.doc.reviewed ? '已剪藏到 Obsidian' : '已剪藏到 Obsidian（字幕还没标记为已校对）',
      { action: { label: '在 Obsidian 中打开', run: () => api('reveal', { target: r.uri }) }, ms: 7000 });
    return true;
  } catch (e) { fail(e); return false; }
}

/* -------------------------------------------------------------- 画面 ---- */
/* 关键帧和画面记录：AI 校对、课程重点、对话都会参考；课程重点里的 ![[画面 mm:ss]] 剪藏时换成图片 */
function frameUrl(t, small) {
  const key = `${S.path}|${t}|${small ? 's' : ''}`;
  if (!S.frameUrls[key]) S.frameUrls[key] = api('frame_url', { path: S.path, t, small }).then(r => r.url).catch(() => { delete S.frameUrls[key]; return ''; });
  return S.frameUrls[key];
}
function hydrateFigures(root) {
  if (!S.path) return;
  $$('[data-ft] img', root).forEach(img => {
    const t = +img.closest('[data-ft]').dataset.ft;
    const shot = (S.doc.frames || []).find(f => Math.abs(f.t - t) <= 3);
    frameUrl(shot ? shot.t : t, false).then(u => { if (u) img.src = u; });
  });
}
function shotHTML(f, i) {
  return `<div class="shot" data-k="${i}">
    <div><img alt="" data-st="${f.t}" title="点击看大图"></div>
    <div class="shot-body">
      <div class="shot-head"><a class="tslink" data-t="${f.t}">[${fmt(f.t)}]</a>
        <label class="check" title="课程重点里可以插这张图"><input type="checkbox" data-fig ${f.figure ? 'checked' : ''}> 可插图</label>
        ${f.why ? `<span class="why" title="${esc(f.why)}">${esc(f.manual ? '手动截取' : f.why).slice(0, 24)}</span>` : (f.manual ? '<span class="why">手动截取</span>' : '')}
        <span class="grow"></span>
        <button data-redo title="让 AI 重新写这张的画面记录">重新识别</button><button data-del title="删除这张画面">删除</button></div>
      <textarea data-note spellcheck="false" placeholder="${f.note === undefined ? '还没有画面记录，点「补写画面记录」让 AI 写，或者自己写' : ''}">${esc(f.note || '')}</textarea>
      ${f.caption ? `<div class="cap">画面字幕：「${esc(f.caption)}」</div>` : ''}
    </div></div>`;
}
function renderFrames() {
  const box = $('#framesBox');
  const list = (S.doc && S.doc.frames) || [];
  $('#framesCount').textContent = list.length ? list.length : '';
  if (!S.doc || !list.length) {
    box.innerHTML = `<div class="ai-empty">${S.info && !S.info.has_video ? '这是音频文件，没有画面。' : '点「找关键帧」：软件会挑出课件、板书、老师手绘这类有内容的画面，<br>让能看图的 AI 写成画面记录（照录课件文字和板书、分清哪些是老师现场画的）。<br>AI 校对、课程重点和对话都会参考；课程重点里还可以插图。'}</div>`;
  } else {
    box.innerHTML = list.map(shotHTML).join('');
    $$('img[data-st]', box).forEach(img => frameUrl(+img.dataset.st, true).then(u => { if (u) img.src = u; }));
  }
  const missing = list.filter(f => !f.note).length;
  $('#framesHint').textContent = list.length ? `${list.length} 张${missing ? `，${missing} 张还没有记录` : ''}` : '';
  updateButtons();
}
function sortFrames() { S.doc.frames = (S.doc.frames || []).sort((a, b) => a.t - b.t); }
async function findFrames(auto) {
  if (!S.doc || !S.path) return false;
  const old = S.doc.frames || [];
  const manual = old.filter(f => f.manual);
  if (!auto && old.length > manual.length && !confirm(`重新找关键帧？\n现在自动找到的 ${old.length - manual.length} 张画面和它们的记录会被替换（你手动截取的 ${manual.length} 张保留）。`)) return false;
  if (S.settings.ai_hidden) savePrefs({ ai_hidden: false });
  $$('.ai-tabs .tab')[2].click();
  try {
    await flushSave();
    const doc = S.doc;
    const r = await api('frames_extract', { path: S.path, segments: doc.segments, duration: doc.duration || (S.info && S.info.duration) || 0 });
    const got = [];
    return await runJob(r.job, '正在找关键帧…', {
      onItems: items => { if (S.doc !== doc) return; got.push(...items); doc.frames = manual.concat(got); sortFrames(); renderFrames(); },
      onDone: job => {
        if (S.doc !== doc) return;
        doc.frames = manual.concat(job.result.frames || []); sortFrames();
        renderFrames(); markDirty(); save();
        const n = (job.result.frames || []).length;
        toast(n ? `找到 ${n} 张有内容的画面，已写好画面记录。AI 校对、课程重点和对话会参考它们` : '没有找到有内容的画面（只有老师讲话的视频很正常）', { ms: 7000 });
      },
      onError: job => { if (S.doc === doc) { doc.frames = old; renderFrames(); } if (job.status === 'error') fail(new Error(job.error)); },
    });
  } catch (e) { fail(e); return false; }
}
async function describeFrames(list) {
  const doc = S.doc;
  if (!list.length) return false;
  list.forEach(f => { const el = $(`.shot[data-k="${doc.frames.indexOf(f)}"]`); if (el) el.classList.add('busy'); });
  try {
    await flushSave();
    const r = await api('frames_describe', { path: S.path, frames: list.map(f => ({ t: f.t })), segments: doc.segments });
    const apply = items => {
      for (const x of items) {
        const f = (doc.frames || []).find(y => Math.abs(y.t - x.t) < 0.01);
        if (f) Object.assign(f, { note: x.note || '', caption: x.caption || '', figure: !!x.figure });
      }
      if (S.doc === doc) renderFrames();
    };
    return await runJob(r.job, 'AI 正在写画面记录…', {
      onItems: apply,
      onDone: job => { apply(job.result.frames || []); markDirty(); save(); toast('画面记录写好了'); },
      onError: job => { renderFrames(); if (job.status === 'error') fail(new Error(job.error)); },
    });
  } catch (e) { renderFrames(); fail(e); return false; }
}
async function captureFrame() {
  const v = $('#video');
  if (!S.doc || !v.getAttribute('src')) return;
  v.pause();
  const t = +v.currentTime.toFixed(2);
  S.doc.frames = S.doc.frames || [];
  if (S.doc.frames.some(f => Math.abs(f.t - t) < 0.5)) return toast('这个时间已经有一张了');
  const f = { t, manual: true, why: '手动截取' };
  S.doc.frames.push(f); sortFrames();
  if (S.settings.ai_hidden) savePrefs({ ai_hidden: false });
  $$('.ai-tabs .tab')[2].click();
  renderFrames(); markDirty();
  if (providerReady(S.settings.vision)) await describeFrames([f]);
  else toast('已截取。还没设置「画面识别」用的 AI，可以自己在下面写画面记录', { ms: 6000 });
}
/* 用画面复核存疑句：AI 校对后还拿不准的句子，截下说话时的画面再核对一次 */
async function recheckDoubts(auto) {
  const doc = S.doc, segs = doc.segments;
  const list = doubtIndexes();
  if (!list.length) { if (!auto) toast('没有存疑句'); return true; }
  try {
    await flushSave();
    const items = list.map(i => {
      const d = doubtOf(segs[i]);
      return { i, start: segs[i].start, end: segs[i].end, text: segs[i].text, reason: d ? d.reason : '',
        before: segs.slice(Math.max(0, i - 2), i).map(s => s.text).join(' '), after: segs.slice(i + 1, i + 3).map(s => s.text).join(' ') };
    });
    const r = await api('doubts_recheck', { path: S.path, items, frames: (doc.frames || []).filter(f => f.note) });
    snapshot();
    let fixed = 0;
    const ok = await runJob(r.job, 'AI 正在用画面复核存疑句…', {
      onItems: res => {
        for (const x of res) {
          const seg = segs[x.i]; if (!seg) continue;
          const d = doubtOf(seg);
          if (x.text !== undefined) {
            if (x.text !== seg.text) { seg.text = x.text; fixed++; }
            if (d) doc.doubts = doc.doubts.filter(y => y !== d);
            if (S.doc === doc) { const el = cueEl(x.i); if (el) { $('.src', el).textContent = seg.text; el.classList.add('fixed'); } }
          } else if (d && x.doubt) d.reason = x.doubt;
        }
        markDirty();
      },
    });
    if (S.doc === doc) {
      const fixedNow = $$('.cue.fixed', $('#cues')).map(el => +el.dataset.i);
      renderCues(); fixedNow.forEach(i => { const el = cueEl(i); if (el) el.classList.add('fixed'); });
    }
    save();
    if (ok) {
      const left = doubtIndexes().length;
      toast(`用画面复核了 ${list.length} 句存疑句：确认 ${list.length - left} 句（改了 ${fixed} 句，绿色下划线标出）${left ? `，还有 ${left} 句看不清，留给你回听` : ''}`, { ms: 9000 });
    }
    return ok || auto;  // 一键整理里复核失败不影响后面的步骤
  } catch (e) { fail(e); return !!auto; }
}
function shotOf(el) { const k = el.closest('.shot'); return k && S.doc.frames[+k.dataset.k]; }
function showLightbox(url) {
  if (!url) return;
  const lb = document.createElement('div'); lb.className = 'lightbox';
  lb.innerHTML = `<img src="${esc(url)}" alt="">`; lb.onclick = () => lb.remove();
  document.body.appendChild(lb);
}

/* ------------------------------------------------------------ AI 面板 ---- */
let summaryEditing = false;
function renderSummary() {
  const box = $('#summaryBox');
  const s = S.doc && S.doc.summary;
  box.innerHTML = s ? md(s) : `<div class="ai-empty">生成字幕后，点「生成课程重点」：<br>AI 按老师这节课的知识体系整理重点，每条标出处时间，<br>再自动对照字幕查漏补缺；剪藏时放在笔记的上半部分。</div>`;
  $('#btnSummary').textContent = s ? '重新生成' : '生成课程重点';
  $('#btnCopySummary').disabled = !s;
  $('#btnEditSummary').disabled = !S.doc;
  $('#summaryHint').textContent = s ? '可以编辑；剪藏时放在笔记上半部分' : '';
  updateButtons();
  if (s) refreshCoverage();
  hydrateFigures(box);
}
/* 字幕有多少被课程重点的出处时间引用到（粗略估计，用来判断有没有大段漏掉） */
function refreshCoverage() {
  const doc = S.doc; if (!doc || !(doc.summary || '').trim()) return;
  api('coverage', { doc: { segments: doc.segments, summary: doc.summary } }).then(r => {
    if (S.doc !== doc || S.chatAbort) return;
    const gaps = r.gaps.length ? `，有 ${r.gaps.length} 段没有出处（如 ${r.gaps.slice(0, 3).map(g => fmt(g[0])).join('、')}）` : '';
    $('#summaryHint').textContent = `字幕被引用约 ${Math.round(r.ratio * 100)}%${gaps}；可以编辑，剪藏时放在笔记上半部分`;
  }).catch(() => {});
}
async function runReview() {
  if (!S.doc || !(S.doc.summary || '').trim()) return false;
  try {
    await flushSave();
    const doc = S.doc;
    const r = await api('review', { doc });
    return await runJob(r.job, 'AI 正在对照字幕查漏…', {
      onDone: job => {
        const x = job.result;
        if (S.doc !== doc) return;
        if (x.added) { doc.summary = x.summary; markDirty(); save(); }
        renderSummary();
        toast(x.added ? `查漏补缺：补了 ${x.added} 条（放进了对应的小节）。字幕被引用约 ${Math.round(x.before * 100)}% → ${Math.round(x.after * 100)}%`
          : `查漏补缺：没有发现漏掉的内容（字幕被引用约 ${Math.round(x.after * 100)}%）`, { ms: 8000 });
      },
    });
  } catch (e) { fail(e); return false; }
}
function toggleSummaryEdit(on) {
  summaryEditing = on ?? !summaryEditing;
  $('#summaryEdit').hidden = !summaryEditing; $('#summaryBox').hidden = summaryEditing;
  $('#btnEditSummary').textContent = summaryEditing ? '完成编辑' : '编辑';
  if (summaryEditing) { $('#summaryEdit').value = S.doc.summary || ''; $('#summaryEdit').focus(); }
  else renderSummary();
}
function renderChat() {
  const box = $('#chatBox');
  const msgs = (S.doc && S.doc.chat) || [];
  if (!msgs.length) { box.innerHTML = `<div class="ai-empty">AI 读过这节课的全部字幕和术语表，可以问它任何内容。<br>回答里的时间点可以点击跳转。</div>`; return; }
  box.innerHTML = msgs.map(m => `<div class="msg ${m.role}"><div class="bubble ${m.role === 'assistant' ? 'md' : ''}">${m.role === 'assistant' ? md(m.content) : esc(m.content)}</div></div>`).join('');
  box.scrollTop = box.scrollHeight;
}
async function streamAI(body, onText) {
  const { job: id } = await api('chat', body);
  let stopped = false;
  S.chatAbort = { abort() { stopped = true; api('cancel', { id }).catch(() => {}); } };
  let text = '', since = 0;
  try {
    for (;;) {
      const { job } = await api('job', { id, since });
      since = job.total;
      if (job.items.length) { text += job.items.join(''); onText(text); }
      if (stopped) { const e = new Error('已停止'); e.name = 'AbortError'; throw e; }
      if (job.status === 'error') throw new Error(job.error);
      if (job.status !== 'running') break;
      await sleep(150);
    }
  } finally { S.chatAbort = null; }
  return text;
}
async function doSummary() {
  if (S.chatAbort) { S.chatAbort.abort(); return false; }
  if (summaryEditing) toggleSummaryEdit(false);
  if (S.settings.ai_hidden) savePrefs({ ai_hidden: false });
  $$('.ai-tabs .tab')[0].click();
  const box = $('#summaryBox');
  $('#btnSummary').textContent = '停止';
  box.innerHTML = '<div class="typing muted">AI 正在阅读字幕…</div>';
  let ok = false;
  try {
    const text = await streamAI({ mode: 'summary', doc: S.doc }, t => { box.innerHTML = md(t); if (box.lastElementChild) box.lastElementChild.classList.add('typing'); box.scrollTop = box.scrollHeight; });
    if (text.trim()) { S.doc.summary = text.trim(); markDirty(); save(); ok = true; }
  } catch (e) { if (e.name !== 'AbortError') fail(e); }
  renderSummary();
  if (ok && S.settings.auto_review) await runReview();  // 查漏失败不影响课程重点本身
  return ok;
}
async function sendChat(q) {
  q = (q || '').trim(); if (!q || !S.doc) return;
  if (S.chatAbort) return;
  S.doc.chat = S.doc.chat || [];
  S.doc.chat.push({ role: 'user', content: q });
  $('#chatInput').value = '';
  renderChat();
  const box = $('#chatBox');
  box.insertAdjacentHTML('beforeend', `<div class="msg assistant"><div class="bubble md typing"></div></div>`);
  const bubble = box.lastElementChild.firstElementChild;
  $('#btnSend').textContent = '停止';
  try {
    const text = await streamAI({ mode: 'chat', doc: S.doc, messages: S.doc.chat }, t => { bubble.innerHTML = md(t); box.scrollTop = box.scrollHeight; });
    S.doc.chat.push({ role: 'assistant', content: text.trim() });
  } catch (e) {
    if (e.name === 'AbortError') { const t = bubble.innerText.trim(); if (t) S.doc.chat.push({ role: 'assistant', content: t + '（已停止）' }); }
    else { fail(e); S.doc.chat.pop(); $('#chatInput').value = q; }
  }
  $('#btnSend').textContent = '发送';
  renderChat(); markDirty();
}

/* ------------------------------------------------------------- 搜索 ---- */
function runSearch(jump) {
  const q = $('#search').value.trim().toLowerCase();
  $$('.cue.hit', $('#cues')).forEach(el => el.classList.remove('hit', 'current'));
  S.hits = [];
  if (!q || !S.doc) { $('#searchCount').textContent = ''; return; }
  S.doc.segments.forEach((s, i) => { if ((s.text + ' ' + s.tr).toLowerCase().includes(q)) S.hits.push(i); });
  S.hits.forEach(i => { const el = cueEl(i); if (el) el.classList.add('hit'); });
  $('#searchCount').textContent = S.hits.length ? `${S.hitPos >= 0 ? S.hitPos + 1 : 0}/${S.hits.length}` : '无结果';
  if (jump && S.hits.length) {
    S.hitPos = (S.hitPos + 1) % S.hits.length;
    $$('.cue.current', $('#cues')).forEach(el => el.classList.remove('current'));
    const el = cueEl(S.hits[S.hitPos]);
    if (el) { el.classList.add('current'); el.scrollIntoView({ block: 'center' }); S.userScrollAt = Date.now(); }
    $('#searchCount').textContent = `${S.hitPos + 1}/${S.hits.length}`;
  }
}

/* ------------------------------------------------------------ 设置窗口 ---- */
function getPath(obj, path) { return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj); }
function setPath(obj, path, val) {
  const ks = path.split('.'); let o = obj;
  ks.slice(0, -1).forEach(k => { o[k] = o[k] || {}; o = o[k]; });
  o[ks[ks.length - 1]] = val;
}
function providerReady(name) { const p = S.settings.providers[name] || {}; return !!p.has_key && !!p.chat_model; }
function fillProviderSelects() {
  $$('.provider-select').forEach(sel => {
    const cur = getPath(S.settings, sel.dataset.k);
    sel.innerHTML = S.chatProviders.map(p => `<option value="${p}" ${p === cur ? 'selected' : ''}>${esc(S.providerNames[p])}${providerReady(p) ? '　· ' + esc(S.settings.providers[p].chat_model) : '　（未设置）'}</option>`).join('');
  });
}
function markNav() {
  const P = S.settings.providers;
  const ready = { codex: P.codex.has_key, claude: P.claude.has_key, gemini: P.gemini.has_key, deepseek: P.deepseek.has_key, ark: P.ark.has_key,
    siliconflow: P.siliconflow.has_key, openai: P.openai.has_key, doubao: P.doubao.has_key, teachers: S.teachers.length > 0 };
  $$('#settingsNav button').forEach(b => {
    const st = b.querySelector('.state'); if (st) st.remove();
    if (ready[b.dataset.sec]) b.insertAdjacentHTML('beforeend', `<span class="state">${b.dataset.sec === 'teachers' ? S.teachers.length : '✓'}</span>`);
  });
}
function openSettings(section) {
  const dlg = $('#dlgSettings');
  const s = S.settings;
  dlg._draft = { theme: s.theme, accent: s.accent };
  $('#themePick').innerHTML = THEMES.map(t => `<button data-theme="${t.id}" style="background:${t.bg};color:${t.fg}" class="${s.theme === t.id ? 'active' : ''}">${t.name}</button>`).join('');
  $('#accentPick').innerHTML = ACCENTS.map(c => `<button data-accent="${c}" style="background:${c}" class="${s.accent === c ? 'active' : ''}"></button>`).join('');
  $('#doubaoModel').innerHTML = Object.entries(S.doubaoModels).map(([k, m]) => `<option value="${k}">${esc(m.name)}</option>`).join('');
  fillProviderSelects();
  $$('[data-k]', dlg).forEach(el => {
    if (el.classList.contains('provider-select')) return;
    const v = getPath(s, el.dataset.k);
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.hasAttribute('data-secret')) {
      if (el.dataset.ph0 === undefined) el.dataset.ph0 = el.placeholder;
      el.value = ''; delete el.dataset.cleared;
      el.placeholder = v ? `已保存（${v}），留空表示不修改` : el.dataset.ph0;
    } else el.value = v ?? '';
  });
  $('#summaryTemplate').value = s.summary_template || S.defaultTemplate;
  const p = s.providers.doubao;
  $$('input[name=dbAuth]').forEach(r => r.checked = r.value === (p.app_id && !p.key ? 'old' : 'new'));
  refreshDoubaoFields();
  renderCodexBox(); $('#codexPaste').hidden = true;
  renderTeacherList(S.teachers[0] && S.teachers[0].id);
  $$('.test-result', dlg).forEach(el => { el.textContent = ''; el.className = 'test-result'; });
  markNav();
  showSection(section || 'look');
  api('about').then(a => {
    $('#aboutVersion').textContent = a.version || '开发版';
    $('#aboutLog').textContent = a.log_dir; $('#aboutData').textContent = a.data_dir; $('#aboutCache').textContent = a.cache_dir;
    $('#aboutCacheSize').textContent = mb(a.cache);
    $('#aboutRoot').textContent = a.storage_root + (a.storage_custom ? '' : '（软件所在文件夹）');
    $('#btnStorageReset').disabled = !a.storage_custom;
  }).catch(() => {});
  dlg.showModal();
}
function showHelp(sec) {
  $$('#helpNav [data-h]').forEach(b => b.classList.toggle('active', b.dataset.h === sec));
  $$('#helpBody section').forEach(el => el.hidden = el.dataset.h !== sec);
  $('#helpBody').scrollTop = 0;
}
function showSection(sec) {
  $$('#settingsNav button').forEach(b => b.classList.toggle('active', b.dataset.sec === sec));
  $$('#settingsBody section').forEach(el => el.hidden = el.dataset.sec !== sec);
}
function refreshDoubaoFields() {
  const auth = ($('input[name=dbAuth]:checked') || {}).value || 'new';
  $$('[data-auth]').forEach(el => el.hidden = el.dataset.auth !== auth);
  const m = S.doubaoModels[$('#doubaoModel').value] || {};
  $('#doubaoNote').textContent = m.note || '';
  $$('#dlgSettings [data-kind="stream"], #dlgSettings .tos[data-kind]').forEach(el => el.hidden = el.dataset.kind !== m.kind);
}
function collectSettings() {
  const dlg = $('#dlgSettings');
  const patch = { theme: dlg._draft.theme, accent: dlg._draft.accent };
  $$('[data-k]', dlg).forEach(el => {
    let v;
    if (el.type === 'checkbox') v = el.checked;
    else if (el.type === 'number') v = Number(el.value) || 0;
    else v = el.value.trim();
    if (el.hasAttribute('data-secret')) { if (el.dataset.cleared) v = null; else if (!v) return; }
    setPath(patch, el.dataset.k, v);
  });
  if (patch.summary_template && patch.summary_template.trim() === S.defaultTemplate.trim()) patch.summary_template = '';
  if (($('input[name=dbAuth]:checked') || {}).value === 'old') setPath(patch, 'providers.doubao.key', null);
  return patch;
}
async function saveSettings(close = true) {
  try {
    const r = await api('settings', { settings: collectSettings() });
    S.settings = keepLayout(r.settings); S.teachers = r.teachers;
    $$('#dlgSettings [data-secret]').forEach(el => delete el.dataset.cleared);
    applyLook(); fillProviderSelects(); markNav();
    if (close) { $('#dlgSettings').close(); toast('设置已保存'); }
    return true;
  } catch (e) { fail(e); return false; }
}
async function refreshSettingsOnly() {
  const d = await api('settings', {});
  S.settings = keepLayout(d.settings); S.teachers = d.teachers;
  fillProviderSelects(); markNav(); applyLook();
}

/* ChatGPT 账号登录 */
let codexPoll = null;
function renderCodexBox() {
  const acc = S.settings.providers.codex.account;
  $('#codexBox').innerHTML = acc
    ? `<div class="who">✓ 已登录：<b>${esc(acc.email || 'ChatGPT 账号')}</b>${acc.plan ? `（${esc(acc.plan)}）` : ''}</div><button class="btn small" id="codexLogout">退出登录</button>`
    : `<div class="who muted">还没有登录</div><button class="btn small primary" id="codexLogin">用 ChatGPT 账号登录</button>`;
  const lo = $('#codexLogout'), li = $('#codexLogin');
  if (lo) lo.onclick = async () => { await api('codex_logout'); await refreshSettingsOnly(); renderCodexBox(); toast('已退出 ChatGPT 账号'); };
  if (li) li.onclick = codexLogin;
}
async function codexLogin() {
  try {
    await api('codex_login');
    $('#codexPaste').hidden = false; $('#codexUrl').value = '';
    clearInterval(codexPoll);
    const t0 = Date.now();
    codexPoll = setInterval(async () => {
      if (!$('#dlgSettings').open || Date.now() - t0 > 600000) { clearInterval(codexPoll); return; }
      try { const r = await api('codex_finish', {}); if (r.status === 'done') codexDone(r); } catch (e) { clearInterval(codexPoll); fail(e); }
    }, 2000);
  } catch (e) { fail(e); }
}
async function codexDone(r) {
  clearInterval(codexPoll);
  $('#codexPaste').hidden = true;
  await refreshSettingsOnly(); renderCodexBox();
  toast(`ChatGPT 登录成功：${r.email || ''}`);
  const input = $('[data-k="providers.codex.chat_model"]');
  if (!input.value) {
    try {
      const { models } = await api('models', { provider: 'codex' });
      fillDatalist('codex', models);
      if (models[0]) { input.value = models[0].id; await saveSettings(false); toast('已选择模型 ' + models[0].id + '，可以在下面修改'); }
    } catch (e) { fail(e); }
  }
}
function fillDatalist(name, models) {
  const dl = $('#dl-' + name);
  if (dl) dl.innerHTML = models.map(m => `<option value="${esc(m.id)}">${esc(m.name !== m.id ? m.name : '')}</option>`).join('');
}

/* 老师档案 */
let editingTeacher = null;
function renderTeacherList(selectId) {
  $('#teacherList').innerHTML = S.teachers.map(t => `<button data-tid="${t.id}">${esc(t.name)}</button>`).join('') +
    '<button class="add" data-tid="__new">＋ 添加老师</button>';
  editTeacher(selectId ? teacherById(selectId) : null);
}
function editTeacher(t) {
  editingTeacher = t ? t.id : null;
  $$('#teacherList button').forEach(b => b.classList.toggle('active', b.dataset.tid === (t ? t.id : '__new')));
  $('#tName').value = t ? t.name : '';
  $('#tDisplay').value = t ? (t.display || '') : '';
  setTermsFile(t ? (t.terms_file || '') : '');
  $('#tAbout').value = t ? t.about : '';
  $('#tTerms').value = t ? t.terms : '';
  $('#tNotes').value = t ? (t.notes || '') : '';
  $('#tLang').value = t ? t.lang : 'zh';
  $('#tFolder').value = t ? t.folder : '';
  $('#tOwnTemplate').checked = !!(t && t.template);
  $('#tTemplate').value = (t && t.template) || S.settings.summary_template || S.defaultTemplate;
  $('#tTemplateField').hidden = !$('#tOwnTemplate').checked;
  $('#tDelete').hidden = !t;
  $('#tMsg').textContent = '';
  countTerms();
}
function countTerms() {
  const lines = $('#tTerms').value.split(/\n/).map(x => x.trim()).filter(x => x && !x.startsWith('#'));
  const forced = lines.filter(x => /(=>|->|→)\s*[!！]/.test(x)).length;
  const hints = lines.filter(x => /=>|->|→/.test(x)).length - forced;
  const words = lines.filter(x => !/=>|->|→/.test(x)).join(',').split(/[,，、;；]+/).filter(x => x.trim()).length;
  $('#tTermsHint').textContent = `${words} 个术语，${hints} 条识别错误参考，${forced} 条直接替换。最值得加的是：老师自创的说法、专有名词、人名和容易听错的词。`;
  const n = $('#tNotes').value.length;
  $('#tNotesHint').textContent = n ? `${n} 个字${n > 20000 ? '（超过 20000 字的部分不会发给 AI）' : ''}` : '';
}
/* 关联 Obsidian 里的术语表文件 */
async function setTermsFile(path, preview) {
  const el = $('#tFile');
  el.dataset.path = path || '';
  el.textContent = path || '没有关联';
  el.title = path || '';
  $('#tFileClear').hidden = !path;
  if (!path) { $('#tFileHint').textContent = TERMS_FILE_HINT; return; }
  try {
    const r = preview || await api('terms_preview', { path });
    if (($('#tFile').dataset.path || '') !== path) return;
    $('#tFileHint').textContent = `从文件里读到 ${r.words} 个术语、${r.hints} 条识别错误参考（文件 ${r.chars} 字，整份给 AI 当术语说明）。以后在 Obsidian 里改，软件会自动用最新的。`;
  } catch (e) { $('#tFileHint').textContent = '⚠ ' + e.message; }
}
const TERMS_FILE_HINT = '关联后，软件每次都读这个文件的最新内容：小节里的「正确写法 / 标准写法」和热词当术语，「ASR 常见错误」表当识别错误参考，整份文件给 AI 当术语说明。以后只在 Obsidian 里改术语表就行。';
async function pickTermsFile() {
  try {
    const r = await api('terms_preview', {});
    if (!r.path) return;
    await setTermsFile(r.path, r);
    if ($('#tNotes').value.trim() && confirm('关联文件后，文件内容会自动作为「术语说明」给 AI。\n下面「术语说明」框里已有内容（可能是旧版术语表），要清空吗？\n不清空的话，两份会一起发给 AI。')) {
      $('#tNotes').value = ''; countTerms();
    }
    toast('已选择术语表文件，记得点「保存这位老师」');
  } catch (e) { fail(e); }
}
async function saveTeacher() {
  const t = { id: editingTeacher, name: $('#tName').value, display: $('#tDisplay').value, terms_file: $('#tFile').dataset.path || '',
    about: $('#tAbout').value, terms: $('#tTerms').value, notes: $('#tNotes').value, lang: $('#tLang').value,
    folder: $('#tFolder').value, template: $('#tOwnTemplate').checked ? $('#tTemplate').value : '' };
  if (!t.name.trim()) { $('#tName').focus(); return toast('请填写老师名字', { error: true }); }
  try {
    const r = await api('teacher_save', { teacher: t });
    S.teachers = r.teachers; renderTeacherList(r.teacher.id); renderTeacherSelect(); renderLibrary(); markNav();
    $('#tMsg').textContent = '✓ 已保存'; $('#tMsg').className = 'test-result ok';
  } catch (e) { fail(e); }
}
async function deleteTeacher() {
  const t = teacherById(editingTeacher);
  if (!t || !confirm(`删除「${t.name}」和这份术语表？`)) return;
  const r = await api('teacher_delete', { id: t.id });
  S.teachers = r.teachers; renderTeacherList(S.teachers[0] && S.teachers[0].id); renderTeacherSelect(); markNav();
}

/* ------------------------------------------------------------- 菜单 ---- */
function closeMenus() { $$('.menu.open').forEach(m => m.classList.remove('open')); $('#ctxMenu').hidden = true; }
async function showRecent(target) {
  try {
    const { items } = await api('recent');
    target.innerHTML = items.length ? '<div class="menu-label">最近打开</div>' + items.slice(0, 6).map(r => `<button class="menu-item" data-open="${esc(r.path)}" title="${esc(r.path)}">${esc(r.title)}<small>${esc(r.path)}</small></button>`).join('') : '';
  } catch (e) { fail(e); }
}

/* 拖动分隔条 */
function dragger(el, axis, onMove, onEnd) {
  el.addEventListener('pointerdown', e => {
    e.preventDefault();
    try { el.setPointerCapture(e.pointerId); } catch (_) { /* 模拟事件没有真实指针 */ }
    el.classList.add('dragging');
    document.body.classList.add(axis === 'x' ? 'dragging-col' : 'dragging-row');
    const move = ev => onMove(ev);
    const up = () => { el.removeEventListener('pointermove', move); el.classList.remove('dragging'); document.body.classList.remove('dragging-col', 'dragging-row'); onEnd(); };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', up, { once: true });
  });
}

/* ------------------------------------------------------------ 事件绑定 ---- */
function bind() {
  const v = $('#video');
  bindBook();
  $('#btnOpen').onclick = pickAndOpen;
  $('#btnOpen2').onclick = pickAndOpen;
  $('#btnAddFolder2').onclick = addFolder;
  $('#btnLibrary').onclick = () => savePrefs({ sidebar: !S.settings.sidebar });
  $('#libAddFolder').onclick = addFolder;
  $('#libAddFiles').onclick = addFiles;
  $('#libRefresh').onclick = () => loadLibrary();
  $('#libSearch').oninput = renderLibrary;
  $('#libBody').addEventListener('click', e => {
    const more = e.target.closest('[data-more]');
    if (more) { e.stopPropagation(); const r = more.getBoundingClientRect(); groupMenu(+more.dataset.more, r.left, r.bottom + 4); return; }
    const tg = e.target.closest('[data-toggle]');
    if (tg) {
      const g = S.lib[+tg.dataset.toggle]; g.collapsed = !g.collapsed; renderLibrary();
      api('library_set', { path: g.path, collapsed: g.collapsed }).catch(() => {});
    }
  });
  $('#libBody').addEventListener('contextmenu', e => {
    const it = e.target.closest('[data-open]'); const gh = e.target.closest('.lib-ghead');
    if (it) { e.preventDefault(); itemMenu(it.dataset.open, e.clientX, e.clientY); }
    else if (gh) { e.preventDefault(); groupMenu(+gh.dataset.toggle, e.clientX, e.clientY); }
  });
  document.addEventListener('click', e => {
    const item = e.target.closest('[data-open]');
    if (item && !e.target.closest('#ctxMenu')) { closeMenus(); openPath(item.dataset.open); return; }
    if (!e.target.closest('.menu') && !e.target.closest('#ctxMenu')) closeMenus();
    const ts = e.target.closest('.tslink');
    if (ts) seek(+ts.dataset.t, true);
  });
  $('#teacherSelect').onchange = e => {
    if (e.target.value === '__manage') { renderTeacherSelect(); openSettings('teachers'); return; }
    setDocTeacher(e.target.value);
    toast(e.target.value ? `这个视频用「${teacherById(e.target.value).name}」的术语表` : '这个视频不用术语表');
  };
  $('#optReviewed').onchange = e => { if (S.doc) { S.doc.reviewed = e.target.checked; markDirty(); } };
  $('#btnExport').onclick = e => { e.stopPropagation(); const m = $('#menuExport'); const o = !m.classList.contains('open'); closeMenus(); if (o) m.classList.add('open'); };
  $('#btnVersions').onclick = e => { e.stopPropagation(); const m = $('#menuVersions'); const o = !m.classList.contains('open'); closeMenus(); if (o) { renderVersions(); m.classList.add('open'); } };
  $('#menuVersions').addEventListener('click', e => { e.stopPropagation(); const b = e.target.closest('[data-ver]'); if (b && !b.disabled) useVersion(b.dataset.ver); });
  $('#btnText').onclick = e => { e.stopPropagation(); const m = $('#menuText'); const o = !m.classList.contains('open'); closeMenus(); if (o) m.classList.add('open'); };
  $$('[data-export]').forEach(b => b.onclick = () => doExport(b.dataset.export));
  $('#btnTranscribe').onclick = openTranscribe;
  $('#btnCorrect').onclick = () => startCorrect(false);
  $('#btnTranslate').onclick = openTranslate;
  $('#btnPipeline').onclick = openPipeline;
  $('#btnSave').onclick = () => { S.dirty = true; save(false); };
  $('#btnClip').onclick = () => (S.mode === 'book' ? bookClip() : doClip());
  $('#btnSettings').onclick = () => openSettings();
  $('#btnTheme').onclick = () => {
    const order = THEMES.map(t => t.id);
    const next = order[(order.indexOf(S.settings.theme) + 1) % order.length];
    savePrefs({ theme: next }); toast('主题：' + THEMES.find(t => t.id === next).name, { ms: 1200 });
  };
  $('#btnUndo').onclick = undo;
  const openHelp = sec => { showHelp(sec || 'start'); $('#dlgHelp').showModal(); };
  $('#btnHelp').onclick = () => openHelp('edit');
  $('#btnHelpTop').onclick = () => openHelp(S.mode === 'book' ? 'book' : 'start');
  $('#btnHelp2').onclick = () => openHelp('start');
  $('#helpNav').onclick = e => { const b = e.target.closest('[data-h]'); if (b) showHelp(b.dataset.h); };
  $('#helpClose').onclick = () => $('#dlgHelp').close();

  // 文字设置
  $('#txTheme').innerHTML = THEMES.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
  $$('[data-tx]').forEach(b => b.onclick = () => {
    const [k, d] = b.dataset.tx.split(':'); const delta = +d; const s = S.settings;
    if (k === 'font_size') savePrefs({ font_size: clamp(s.font_size + delta, 12, 36) });
    if (k === 'line_height') savePrefs({ line_height: +clamp(s.line_height + delta, 1.2, 2.6).toFixed(1) });
    if (k === 'cue_gap') savePrefs({ cue_gap: clamp(s.cue_gap + delta, 2, 30) });
    if (k === 'cue_width') {
      const full = $('#cues').clientWidth;
      const next = clamp((s.cue_width || full) + delta, 360, 2400);
      savePrefs({ cue_width: next >= full ? 0 : next });
    }
  });
  $('#txFont').onchange = e => savePrefs({ font_family: e.target.value });
  $('#txTheme').onchange = e => savePrefs({ theme: e.target.value });
  $('#menuText').addEventListener('click', e => e.stopPropagation());

  // 播放器
  v.addEventListener('timeupdate', () => { updateActive(); if (!v.paused) savePosition(false); });
  v.addEventListener('pause', () => savePosition(true));
  v.addEventListener('ended', () => savePosition(true));
  document.addEventListener('visibilitychange', () => { if (document.hidden) savePosition(true); });
  v.addEventListener('seeked', () => updateActive(true));
  v.addEventListener('play', () => { if (S.loopIdx == null) S.loopIdx = S.active >= 0 ? S.active : null; tick(); });
  v.addEventListener('error', () => { if (v.getAttribute('src')) fail(new Error('这个文件无法在软件里播放，字幕功能仍然可以使用')); });
  let raf = null;
  function tick() { cancelAnimationFrame(raf); if (!v.paused) { updateActive(); raf = requestAnimationFrame(tick); } }
  $('#speed').onchange = () => v.playbackRate = +$('#speed').value;
  $('#optFollow').onchange = e => savePrefs({ follow: e.target.checked });
  $('#optTr').onchange = e => savePrefs({ show_tr: e.target.checked });
  $('#optOverlay').onchange = e => { savePrefs({ overlay: e.target.checked }); updateActive(true); };
  $('#optLoop').onchange = e => { S.loopIdx = e.target.checked ? (S.active >= 0 ? S.active : null) : null; };
  $('#playerWrap').addEventListener('dblclick', e => {
    if (e.target === v && S.settings.overlay) { e.preventDefault(); document.fullscreenElement ? document.exitFullscreen() : $('#playerWrap').requestFullscreen(); }
  });

  // 字幕列表
  const box = $('#cues');
  box.addEventListener('input', onCueInput);
  box.addEventListener('wheel', () => S.userScrollAt = Date.now(), { passive: true });
  box.addEventListener('mousedown', () => S.userScrollAt = Date.now());
  box.addEventListener('click', e => {
    const tool = e.target.closest('.cue-tools button');
    const cue = e.target.closest('.cue'); if (!cue) return;
    const i = +cue.dataset.i;
    const db = e.target.closest('[data-doubt]');
    if (db) { if (db.dataset.doubt === 'play') playLoop(i); else resolveDoubt(i); return; }
    if (tool) { cueAction(tool.dataset.act, i); return; }
    if (e.target.closest('.ts')) { seek(S.doc.segments[i].start, true); S.loopIdx = i; return; }
    if (e.target.closest('.src, .tr')) {
      const seg = S.doc.segments[i];
      const t = v.currentTime;
      if (t < seg.start - 0.2 || t > seg.end + 0.2) seek(seg.start, false);
      S.loopIdx = i;
    }
  });
  box.addEventListener('focusin', e => { if (e.target.matches('.src, .tr')) S.editing = e.target; });
  box.addEventListener('focusout', e => {
    if (e.target.matches('.src, .tr')) {
      const cue = e.target.closest('.cue');
      lastCaret = { i: +cue.dataset.i, field: e.target.classList.contains('src') ? 'src' : 'tr', pos: e.target._caret ?? null };
      S.editing = null;
    }
  });
  document.addEventListener('selectionchange', () => { const el = S.editing; if (el) el._caret = caretOffset(el); });
  box.addEventListener('keydown', e => {
    if (!e.target.matches('.src, .tr')) return;
    if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); e.target.blur(); }
  });

  // 搜索
  $('#search').addEventListener('input', () => { S.hitPos = -1; runSearch(false); });
  $('#search').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); runSearch(true); } if (e.key === 'Escape') { e.target.value = ''; runSearch(false); e.target.blur(); } });

  // 分隔条
  dragger($('#splitter'), 'x', ev => {
    const L = $('#stageMain').getBoundingClientRect();
    const sideW = S.settings.ai_dock === 'side' && !S.settings.ai_hidden ? $('#aiDockSide').offsetWidth : 0;
    S.settings.split = clamp((ev.clientX - L.left) / (L.width - sideW - 20), 0.2, 0.8);
    applyLayout();
  }, () => savePrefs({ split: +S.settings.split.toFixed(3) }));
  dragger($('#libSplit'), 'x', ev => {
    const w = clamp(ev.clientX - $('#library').getBoundingClientRect().left, 180, 520);
    $('#library').style.width = w + 'px'; S.settings.lib_w = w;
  }, () => savePrefs({ lib_w: Math.round(S.settings.lib_w) }));
  dragger($('#vSplit'), 'y', ev => {
    const L = $('#left').getBoundingClientRect();
    S.settings.video_h = clamp((ev.clientY - L.top - 12) / (L.height - 24), 0.12, 0.88); applyLayout();
  }, () => savePrefs({ video_h: +S.settings.video_h.toFixed(3) }));
  dragger($('#aiSplit'), 'x', ev => {
    const M = $('#stageMain').getBoundingClientRect();
    S.settings.ai_w = clamp(M.right - ev.clientX - 6, 280, M.width * 0.65);
    applyLayout();
  }, () => savePrefs({ ai_w: Math.round(S.settings.ai_w) }));

  // AI 区
  $$('.ai-tabs .tab').forEach(t => t.onclick = () => {
    $$('.ai-tabs .tab').forEach(x => x.classList.toggle('active', x === t));
    $$('.ai-body').forEach(p => p.hidden = p.dataset.pane !== t.dataset.tab);
  });
  $('#btnAiDock').onclick = () => savePrefs({ ai_dock: S.settings.ai_dock === 'side' ? 'bottom' : 'side' });
  $('#btnAiHide').onclick = () => savePrefs({ ai_hidden: true });
  $('#btnShowAi').onclick = () => savePrefs({ ai_hidden: false });
  $('#btnAiMax').onclick = () => {
    const s = S.settings;
    if (s.ai_dock === 'side') {
      const W = $('#stageMain').getBoundingClientRect().width;
      savePrefs({ ai_w: s.ai_w > W * 0.5 ? 440 : Math.round(W * 0.6) });
    } else savePrefs({ video_h: s.video_h > 0.2 ? 0.14 : 0.55 });
  };
  $('#btnSummary').onclick = doSummary;
  $('#btnEditSummary').onclick = () => toggleSummaryEdit();
  $('#summaryEdit').addEventListener('input', e => { if (S.doc) { S.doc.summary = e.target.value; markDirty(); } });
  $('#btnCopySummary').onclick = () => navigator.clipboard.writeText(S.doc.summary || '').then(() => toast('已复制'));
  $('#btnSend').onclick = () => { if (S.chatAbort) S.chatAbort.abort(); else sendChat($('#chatInput').value); };
  $('#chatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(e.target.value); } });
  $('#btnClearChat').onclick = () => { if (S.doc && S.doc.chat && S.doc.chat.length && confirm('清空这节课的 AI 对话记录？')) { S.doc.chat = []; renderChat(); markDirty(); } };
  $$('#chips button').forEach(b => b.onclick = () => { $$('.ai-tabs .tab')[1].click(); sendChat(b.dataset.q); });

  // 设置
  $('#settingsNav').onclick = e => { const b = e.target.closest('button'); if (b) showSection(b.dataset.sec); };
  $('#themePick').onclick = e => { const b = e.target.closest('button'); if (!b) return; $('#dlgSettings')._draft.theme = b.dataset.theme; $$('#themePick button').forEach(x => x.classList.toggle('active', x === b)); document.documentElement.dataset.theme = b.dataset.theme; };
  $('#accentPick').onclick = e => { const b = e.target.closest('button'); if (!b) return; $('#dlgSettings')._draft.accent = b.dataset.accent; $$('#accentPick button').forEach(x => x.classList.toggle('active', x === b)); document.documentElement.style.setProperty('--accent', b.dataset.accent); };
  $('#doubaoModel').onchange = refreshDoubaoFields;
  $$('input[name=dbAuth]').forEach(r => r.onchange = refreshDoubaoFields);
  $$('[data-clear]').forEach(b => b.onclick = () => { const el = $(`[data-k="${b.dataset.clear}"]`); el.value = ''; el.dataset.cleared = '1'; el.placeholder = '保存后清除'; });
  $('#setCancel').onclick = () => $('#dlgSettings').close();
  $('#dlgSettings').addEventListener('close', () => { clearInterval(codexPoll); applyLook(); });
  $('#setSave').onclick = () => saveSettings(true);
  $('#resetTemplate').onclick = () => { $('#summaryTemplate').value = S.defaultTemplate; };
  $$('[data-dir]').forEach(b => b.onclick = () => api('open_dir', { which: b.dataset.dir }).catch(fail));
  $('#aboutHome').onclick = e => { e.preventDefault(); api('reveal', { target: 'https://github.com/longpeng1413/mengren-learning' }).catch(fail); };
  $$('[data-test]').forEach(b => b.onclick = async () => {
    const out = b.parentElement.querySelector('.test-result');
    out.className = 'test-result'; out.textContent = '先保存设置，再测试…';
    if (!(await saveSettings(false))) return;
    out.textContent = '测试中…';
    try { const r = await api('test', { provider: b.dataset.test, kind: b.dataset.kind }); out.textContent = '✓ ' + r.message; out.classList.add('ok'); }
    catch (e) { out.textContent = '✗ ' + e.message; out.classList.add('bad'); }
  });
  $$('[data-models]').forEach(b => b.onclick = async () => {
    const name = b.dataset.models;
    if (!(await saveSettings(false))) return;
    b.disabled = true; const label = b.textContent; b.textContent = '获取中…';
    try {
      const { models } = await api('models', { provider: name });
      fillDatalist(name, models);
      const input = $(`[data-k="providers.${name}.chat_model"]`);
      toast(`找到 ${models.length} 个模型，点输入框可以选择`);
      if (!input.value && models[0]) input.value = models[0].id;
      input.focus();
    } catch (e) { fail(e); }
    b.disabled = false; b.textContent = label;
  });
  $('#codexDone').onclick = async () => {
    try { const r = await api('codex_finish', { url: $('#codexUrl').value }); if (r.status === 'done') codexDone(r); else toast('还没有拿到登录结果'); }
    catch (e) { fail(e); }
  };
  $('#teacherList').onclick = e => { const b = e.target.closest('button'); if (!b) return; editTeacher(b.dataset.tid === '__new' ? null : teacherById(b.dataset.tid)); if (b.dataset.tid === '__new') $('#tName').focus(); };
  $('#tSave').onclick = saveTeacher;
  $('#tFilePick').onclick = pickTermsFile;
  $('#tFileClear').onclick = () => setTermsFile('');
  $('#btnDoubts').onclick = nextDoubt;
  $('#btnFrames').onclick = () => { if (!S.job) findFrames(false); };
  $('#btnCapture').onclick = () => { if (!S.job) captureFrame(); };
  $('#btnDescribe').onclick = () => { if (!S.job) describeFrames((S.doc.frames || []).filter(f => !f.note)); };
  $('#btnRecheck').onclick = () => { if (!S.job) recheckDoubts(false); };
  const fb = $('#framesBox');
  fb.addEventListener('input', e => { const f = e.target.matches('[data-note]') && shotOf(e.target); if (f) { f.note = e.target.value; markDirty(); } });
  fb.addEventListener('change', e => { const f = e.target.matches('[data-fig]') && shotOf(e.target); if (f) { f.figure = e.target.checked; markDirty(); } });
  fb.addEventListener('click', async e => {
    const f = shotOf(e.target); if (!f) return;
    if (e.target.matches('img')) { showLightbox(await frameUrl(f.t, false)); return; }
    if (e.target.matches('[data-del]')) { S.doc.frames = S.doc.frames.filter(x => x !== f); renderFrames(); markDirty(); return; }
    if (e.target.matches('[data-redo]') && !S.job) describeFrames([f]);
  });
  $('#summaryBox').addEventListener('click', async e => {
    const img = e.target.closest('.shot-fig img'); if (img && img.src) showLightbox(img.src);
  });
  $('#btnReview').onclick = () => { if (!S.job) runReview(); };
  $('#tDelete').onclick = deleteTeacher;
  $('#tTerms').oninput = countTerms;
  $('#tNotes').oninput = countTerms;
  $('#btnExportSettings').onclick = async () => {
    try {
      if ($('#dlgSettings').open) await saveSettings(false);
      const r = await api('export_settings', { keys: $('#exKeys').checked, library: $('#exLibrary').checked });
      if (r.path) { $('#ioMsg').textContent = '✓ 已导出：' + r.path; $('#ioMsg').className = 'test-result ok'; }
    } catch (e) { fail(e); }
  };
  $('#btnImportSettings').onclick = async () => {
    try {
      const r = await api('import_settings');
      if (!r.imported) return;
      await loadSettings(); loadLibrary(); $('#dlgSettings').close(); openSettings('about');
      toast(`已导入设置${r.teachers ? `、${r.teachers} 位老师` : ''}${r.folders ? `、${r.folders} 个媒体库文件夹` : ''}${r.with_keys ? '' : '。这个文件里没有密钥，请重新填写'}`, { ms: 7000 });
    } catch (e) { fail(e); }
  };
  const setStorage = async body => {
    try {
      const r = await api('set_storage', body);
      if (r.changed) { $('#aboutRoot').textContent = r.root + '（重启软件后生效）'; toast('已更改，重启软件后缓存和日志会放到新位置。旧位置的文件夹可以手动删除。', { ms: 7000 }); }
    } catch (e) { fail(e); }
  };
  $('#btnStorage').onclick = () => setStorage({ pick: true });
  $('#btnStorageReset').onclick = () => setStorage({ path: '' });
  $('#tImport').onclick = async () => {
    try {
      const r = await api('pick_text');
      if (r.text == null) return;
      if ($('#tNotes').value.trim() && !confirm('用这个文件替换现在的术语说明？')) return;
      $('#tNotes').value = r.text.trim(); countTerms();
      toast(`已导入「${r.name}」，记得点「保存这位老师」`);
    } catch (e) { fail(e); }
  };
  $('#clearCache').onclick = async () => {
    if (!confirm('清空缓存？\n会删除转换后的视频和抽出的音频，下次打开或识别时会重新生成。字幕、设置和你的视频都不受影响。')) return;
    try { const r = await api('cache_clear'); $('#aboutCacheSize').textContent = mb(r.cache); toast(`已清空，释放 ${mb(r.freed)}`); } catch (e) { fail(e); }
  };
  $('#tOwnTemplate').onchange = e => { $('#tTemplateField').hidden = !e.target.checked; };
  $('#pickBookFolder').onclick = async () => {
    const vault = $('[data-k="obsidian.vault"]').value.replace(/[\\/]+$/, '');
    const { path: p } = await api('pick_folder', { start: vault });
    if (!p) return;
    if (!p.toLowerCase().startsWith(vault.toLowerCase())) return toast('请选择库里面的文件夹', { error: true });
    $('[data-k="obsidian.book_folder"]').value = p.slice(vault.length).replace(/^[\\/]+/, '').replace(/\\/g, '/');
  };
  $('#pickBookRules').onclick = async () => { const { path: p } = await api('pick_file', { kind: 'text' }); if (p) $('[data-k="book_rules_file"]').value = p; };
  $('#pickVault').onclick = async () => { const { path: p } = await api('pick_folder', { start: $('[data-k="obsidian.vault"]').value }); if (p) $('[data-k="obsidian.vault"]').value = p; };
  $('#pickFolder').onclick = async () => {
    const vault = $('[data-k="obsidian.vault"]').value.replace(/[\\/]+$/, '');
    const { path: p } = await api('pick_folder', { start: vault });
    if (!p) return;
    if (!p.toLowerCase().startsWith(vault.toLowerCase())) return toast('请选择库里面的文件夹', { error: true });
    $('[data-k="obsidian.folder"]').value = p.slice(vault.length).replace(/^[\\/]+/, '').replace(/\\/g, '/');
  };

  // 拖放
  const dz = $('#dropZone');
  document.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
  document.addEventListener('dragleave', e => { if (!e.relatedTarget) dz.classList.remove('drag'); });
  document.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag'); });

  // 键盘
  document.addEventListener('keydown', e => {
    const inField = e.target.closest('input, textarea, select, [contenteditable]');
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); if (S.doc) { S.dirty = true; save(false); } return; }
    if (ctrl && e.key.toLowerCase() === 'o') { e.preventDefault(); pickAndOpen(); return; }
    if (!S.doc) return;
    if (ctrl && e.key.toLowerCase() === 'k') { e.preventDefault(); v.paused ? v.play() : v.pause(); return; }
    if (ctrl && e.key === 'ArrowLeft') { e.preventDefault(); v.currentTime = Math.max(0, v.currentTime - 3); return; }
    if (ctrl && e.key === 'ArrowRight') { e.preventDefault(); v.currentTime += 3; return; }
    if (ctrl && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault();
      const i = clamp(S.active + (e.key === 'ArrowUp' ? -1 : 1), 0, S.doc.segments.length - 1);
      if (S.doc.segments[i]) { seek(S.doc.segments[i].start, !v.paused); S.loopIdx = i; const el = cueEl(i); if (el) el.scrollIntoView({ block: 'center' }); }
      return;
    }
    if (ctrl && e.key.toLowerCase() === 'z' && !inField) { e.preventDefault(); undo(); return; }
    if (ctrl && e.key.toLowerCase() === 'f') { e.preventDefault(); $('#search').focus(); return; }
    if (e.key === ' ' && !inField && !e.target.closest('button')) { e.preventDefault(); v.paused ? v.play() : v.pause(); }
  });
  window.addEventListener('resize', () => applyLayout());
}

/* ---------------------------------------------------------------- 启动 ---- */
(async function init() {
  bind();
  await bridgeReady();
  try { await loadSettings(); } catch (e) { fail(e); }
  showRecent($('#recentList'));
  loadLibrary();
  $('#stageMain').hidden = true; $('#empty').hidden = false;
  updateButtons();
})();
