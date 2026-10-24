/* 老师工作台（2026-10-24）：和蒸馏好的老师对话。后台在 app/core/mentor.py。
   - 左栏顶上「课程 / 老师」切换；老师这边列出导入的老师和他们的对话（多个对话，保存在软件里，可搜索、改名、删除）。
   - 回答里的出处 ［S02 19:54］ / ［S02-21］ 变成可点的小标签：点了切到「课程」、打开那节课、跳到那一秒播放；
     鼠标停在上面显示那一秒前后的原话（或观点库那一条）。观点库里找不到的出处标红（AI 编的）。
   - 【AI 推测…】【AI 自己的看法…】用不同颜色的框。
   - 「☆ 收藏」把这条问答追加到老师文件夹里的「对话精选.md」；「★ 收藏整个对话」一次收藏还没收藏的。
   - 蒸馏（后台在 app/core/distill.py）：从媒体库选课 → 后台逐节提取、合并、写画像 → 写进 Obsidian；进度在对话区上方和左栏。 */
const M = { list: [], cur: null, chat: null, job: null, searchHits: null, quotes: {}, seq: 0,  // seq：每次打开老师 / 对话编号，晚到的旧结果作废
  distill: {}, polling: false };  // distill：正在蒸馏的老师 {老师id: {job, progress, message}}
S.view = 'course';

/* ---------- 课程 / 老师 切换 ---------- */
function switchView(v) {
  if (S.view === v) return;
  S.view = v;
  $$('#libTabs .lib-tab').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  $$('.course-only').forEach(el => { el.hidden = v !== 'course'; });
  $$('.mentor-only').forEach(el => { el.hidden = v !== 'mentor'; });
  if (typeof TTS !== 'undefined') TTS.stop();
  const mentor = v === 'mentor';
  $('#mentorMain').hidden = !mentor;
  if (mentor) {
    $('#empty').hidden = true; $('#stageMain').hidden = true; $('#bookMain').hidden = true;
    const vid = $('#video'); if (vid && !vid.paused) vid.pause();
    loadMentors();
  } else {
    const has = !!S.doc;
    $('#empty').hidden = has;
    $('#stageMain').hidden = !has || S.mode === 'book';
    $('#bookMain').hidden = !has || S.mode !== 'book';
  }
}
$('#libTabs').onclick = e => { const b = e.target.closest('.lib-tab'); if (b) switchView(b.dataset.view); };

/* ---------- 老师和对话列表 ---------- */
async function loadMentors() {
  try { M.list = (await api('mentor_list')).mentors; } catch (e) { fail(e); M.list = []; }
  if (M.cur && !M.list.some(m => m.id === M.cur.mentor.id)) { M.cur = null; M.chat = null; }
  for (const m of M.list) {  // 界面重新打开时，后台可能还在蒸馏：接上进度
    if (m.distilled && !m.distill_done && !M.distill[m.id]) {
      try { const st = await api('distill_status', { id: m.id }); if (st.running) M.distill[m.id] = { job: st.running, progress: 0, message: '正在蒸馏…' }; } catch (_) { /* 下次再看 */ }
    }
  }
  if (Object.keys(M.distill).length) pollDistill();
  renderMentorList();
  if (!M.cur && M.list.length) await openMentor(M.list[0].id, (M.list[0].chats[0] || {}).id);
  else renderMentorHead(), renderMentorMsgs();
}
function renderMentorList() {
  const box = $('#mentorList'), q = $('#mentorSearch').value.trim();
  if (!M.list.length) {
    box.innerHTML = '<div class="mt-empty">还没有老师。<br>点上方的人像加号按钮：从媒体库的课蒸馏一位新老师，或导入 Obsidian「04、外部知识转化库」里已经蒸馏好的老师文件夹。</div>';
    return;
  }
  box.innerHTML = M.list.map(m => {
    const chats = (M.searchHits && M.searchHits[m.id]) || m.chats || [];
    const miss = (m.sources || []).filter(s => !s.found).length;
    return `<div class="mt-teacher" data-mid="${m.id}" title="点一下开一个新对话；右键：${m.distilled ? '更新蒸馏、' : ''}重新查找课程、清空对话、移除"><span>${esc(m.name)}</span><span class="grow"></span>`
      + `<span class="mt-tag">${distillTag(m)}</span>`
      + `<span class="muted">${(m.sources || []).length ? `${m.sources.length} 节课` : ''}${miss ? `，${miss} 节找不到视频` : ''}</span></div>`
      + chats.map(c => `<div class="mt-chat ${M.chat && M.chat.id === c.id ? 'active' : ''}" data-mid="${m.id}" data-cid="${c.id}" title="${esc(c.title)}（${esc(c.updated)}，${c.count} 条）。右键：改名、删除">${esc(c.title)}<small>${esc((c.updated || '').slice(5, 10))}</small></div>`).join('')
      + (q && !chats.length ? '<div class="mt-empty">没有找到</div>' : '');
  }).join('');
}
async function openMentor(mid, cid) {
  const my = ++M.seq;
  try {
    const cur = !M.cur || M.cur.mentor.id !== mid ? await api('mentor_open', { id: mid }) : M.cur;
    if (cur.mentor.distilled && cur !== M.cur) cur.distill = await api('distill_status', { id: mid });  // 蒸馏到哪了（没蒸馏完时显示每节课的状态）
    const chat = cid ? (await api('mentor_chat', { id: mid, cid })).chat : null;  // 新对话：发第一句时才建
    if (my !== M.seq) return;  // 这期间又打开了别的老师 / 对话，或者已经开始提问：这次的结果不要了
    if (cur !== M.cur) { M.cur = cur; M.quotes = {}; }
    M.chat = chat;
  } catch (e) { if (my === M.seq) fail(e); }
  if (my !== M.seq) return;
  renderMentorHead(); renderMentorMsgs(); renderMentorList();
}
function renderMentorHead() {
  const name = M.cur ? M.cur.mentor.name : '老师工作台';
  $('#mentorName').textContent = name;
  $('#mentorChatTitle').textContent = M.cur ? (M.chat ? M.chat.title : '新对话') : '';
  $('#mentorModel').textContent = $('#aiModel').textContent || '';
  const ready = !!M.cur && !!M.cur.files.atoms && !!M.cur.files.persona;  // 还没蒸馏完的老师没有观点库和画像，不能对话
  $('#mentorInput').placeholder = !M.cur ? '先在左边加一位老师' : ready ? `向${name}提问…（Enter 发送，Shift+Enter 换行）` : `${name}还没蒸馏完，蒸馏好以后就能对话`;
  $('#mentorInput').disabled = !ready;
  $('#mentorSend').disabled = !ready;
  $('#mentorNew').disabled = !ready;
  $('#mentorFav').disabled = !ready;
  $('#mentorSaveAll').disabled = !M.chat || !M.chat.messages.some(x => x.role === 'assistant' && !x.saved);
  renderDistillBar();
}
async function refreshMentorList() {
  try { M.list = (await api('mentor_list')).mentors; } catch (_) { /* 下次再刷新 */ }
  renderMentorList();
}

/* ---------- 回答的显示：出处变成可点的小标签，AI 推测换颜色 ---------- */
function citeHTML(inner) {
  return inner.split(/[、,，;；]\s*/).map(part => {
    const p = part.trim();
    let m, sid, t = null, ok = false, label = p, aid = '';
    if ((m = p.match(/^(S\d{2,3})\s+(\d{1,2}:\d{2}(?::\d{2})?)/))) {
      sid = m[1]; t = parseTs(m[2]); ok = !!M.cur && M.cur.mentor.sources.some(s => s.sid === sid);
    } else if ((m = p.match(/^(S\d{2,3}-[A-Za-z0-9]+)/))) {
      aid = m[1]; sid = aid.split('-')[0];
      const a = M.cur && M.cur.atoms[aid];
      ok = !!a; t = a ? a.t : null;
    } else return esc(p);
    return `<a class="cite${ok ? '' : ' bad'}" data-sid="${sid}" data-t="${t == null ? '' : t}" data-aid="${aid}"${ok ? '' : ' title="观点库和课程里找不到这个出处，可能是 AI 编的"'}>${esc(label)}</a>`;
  }).join(' ');
}
function answerHTML(text) {
  const marks = [];
  const pre = String(text || '').replace(/[［\[]\s*(S\d{2,3}[^］\]\n]{0,80})\s*[］\]]/g, (all, inner) => `\u0001${marks.push(citeHTML(inner)) - 1}\u0002`);
  return md(pre).replace(/\u0001(\d+)\u0002/g, (_, k) => marks[+k]);
}
function markLabels(root) {
  $$('blockquote, p, li', root).forEach(n => {
    const t = n.textContent.trim();
    if (/^【\s*AI\s*推测/.test(t)) n.classList.add('ai-guess');
    else if (/^【\s*AI\s*自己/.test(t)) n.classList.add('ai-own');
  });
}
function renderMentorMsgs() {
  const box = $('#mentorMsgs');
  if (!M.cur) {
    box.innerHTML = '<div class="mentor-intro"><h3>老师工作台</h3><p>先点左上方的人像加号按钮：从媒体库的课蒸馏一位新老师（AI 逐节提取观点库、合并成课程蒸馏、写老师画像），或导入 Obsidian「04、外部知识转化库」里已经蒸馏好的老师文件夹。之后就能按老师画像和他对话：课程里讲过的都带出处，点出处跳到那节课那一秒；超出课程的会标「AI 推测」。</p></div>';
    return;
  }
  const msgs = (M.chat && M.chat.messages) || [];
  if (!msgs.length && M.cur.distill && !(M.cur.files.atoms && M.cur.files.persona)) {
    const st = M.cur.distill, run = M.distill[M.cur.mentor.id];
    const label = { done: x => `已提取 ${x.points} 条`, todo: () => '待提取', changed: () => '字幕改过，要重新提取', missing: () => '<b style="color:var(--danger)">找不到字幕</b>' };
    box.innerHTML = `<div class="mentor-intro"><h3>蒸馏「${esc(M.cur.mentor.name)}」</h3>
      <p>${run ? '正在后台蒸馏，进度在上方。可以切到「课程」照常看课，做完会提示你。' : '还没蒸馏完（可能停下了，或者 AI 额度用完了）。做完的课都存着，点「继续蒸馏」接着做，不会重复花钱。'}
      蒸馏好以后，就能在这里和他对话。</p>
      <ul class="ds-status">${st.lessons.map(x => `<li><span>${x.sid}　${esc(x.title)}</span><span class="muted">${label[x.state](x)}</span>`
        + (run ? '' : `<button class="btn small" data-rm-sid="${x.sid}" title="这节课不用了（编号空着，不给别的课用）">去掉</button>`) + '</li>').join('')}</ul>
      ${run ? '' : '<button class="btn primary" data-distill-go>继续蒸馏</button> <button class="btn" data-distill-edit>加课…</button>'}</div>`;
    return;
  }
  if (!msgs.length) {
    const src = M.cur.mentor.sources.map(s => `<li>${s.sid}　${esc(s.title)}${s.found ? '' : '　<b style="color:var(--danger)">（找不到视频，右键老师「重新查找课程」）</b>'}</li>`).join('');
    const egs = ['交易系统包括哪些方面？', '我的单笔风险应该怎么定？', '连续亏了好几笔，要不要改系统？', '你的课里有没有讲止盈怎么做？'];
    box.innerHTML = `<div class="mentor-intro"><h3>和${esc(M.cur.mentor.name)}对话</h3>
      <p>回答按「老师画像」和他的课程资料来写：课程里讲过的都带出处（如 <a class="cite">S02 19:54</a>），点出处跳到那节课那一秒，鼠标停在上面能看到原话；超出课程的会标<span class="ai-guess" style="display:inline;padding:1px 6px;margin:0">【AI 推测】</span>。觉得好的回答点「☆ 收藏」，存进 Obsidian 的「对话精选」。</p>
      <p>他的课程：</p><ul>${src}</ul><p>可以这样问：</p>${egs.map(q => `<button class="btn small ask-eg" data-eg="${esc(q)}">${esc(q)}</button>`).join('')}</div>`;
    return;
  }
  box.innerHTML = msgs.map((m, i) => m.role === 'user'
    ? `<div class="msg user"><div class="bubble">${esc(m.content)}</div></div>`
    : `<div class="msg assistant" data-i="${i}"><div class="bubble md">${answerHTML(m.content)}</div><div class="msg-actions">
        <span class="msg-meta">${esc((m.model || '') + (m.time ? '　' + m.time.slice(5, 16) : ''))}</span>
        <button class="btn small fav-btn${m.saved ? ' saved' : ''}" data-save="${i}" ${m.saved ? 'disabled' : ''} title="${m.saved ? '已经收藏到 Obsidian 的「对话精选」' : '连同问题一起收藏到 Obsidian 的「对话精选」'}">${m.saved ? '★ 已收藏' : '☆ 收藏'}</button>
        <button class="btn small" data-copy="${i}">复制</button></div></div>`).join('');
  markLabels(box);
  box.scrollTop = box.scrollHeight;
}

/* ---------- 提问 ---------- */
async function mentorAsk(q) {
  if (M.job) { api('cancel', { id: M.job }).catch(() => {}); return; }  // 「停止」
  q = String(q == null ? $('#mentorInput').value : q).trim();
  if (!q || !M.cur) return;
  const mid = M.cur.mentor.id;
  M.seq++;  // 还没完成的「打开老师 / 对话」作废，免得晚到时把正在问的对话清掉
  let cid = null;
  try {
    if (!M.chat) M.chat = (await api('mentor_chat', { id: mid })).chat;
    cid = M.chat.id;
    M.chat.messages.push({ role: 'user', content: q });
    $('#mentorInput').value = '';
    renderMentorMsgs(); renderMentorHead();
    const box = $('#mentorMsgs');
    box.insertAdjacentHTML('beforeend', '<div class="msg assistant"><div class="bubble md typing">老师正在翻课程资料…</div></div>');
    const bubble = box.lastElementChild.firstElementChild;
    const r = await api('mentor_ask', { id: mid, cid, question: q });
    M.job = r.job; $('#mentorSend').textContent = '停止';
    let text = '', since = 0;
    for (;;) {
      const { job } = await api('job', { id: r.job, since });
      since = job.total;
      if (job.items.length && M.chat && M.chat.id === cid) {
        text += job.items.join('');
        bubble.classList.remove('typing'); bubble.innerHTML = answerHTML(text); markLabels(bubble);
        box.scrollTop = box.scrollHeight;
      }
      if (job.status === 'error') throw new Error(job.error);
      if (job.status !== 'running') break;
      await sleep(150);
    }
  } catch (e) { fail(e); }
  finally { M.job = null; $('#mentorSend').textContent = '发送'; }
  if (M.chat && M.chat.id === cid) {  // 以存下来的为准（中途停止的也保存了已写的部分）
    try { M.chat = (await api('mentor_chat', { id: mid, cid })).chat; } catch (_) { /* 用界面上的 */ }
    renderMentorMsgs(); renderMentorHead();
  }
  refreshMentorList();
}
$('#mentorSend').onclick = () => mentorAsk();
$('#mentorInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); mentorAsk(); }
});
$('#mentorNew').onclick = () => { if (M.cur) { M.chat = null; renderMentorHead(); renderMentorMsgs(); renderMentorList(); $('#mentorInput').focus(); } };
$('#mentorTts').onclick = () => $('#btnTts').click();  // 朗读按当前所在的区域读（tts.js 的 source）

/* ---------- 出处：点了跳视频，停在上面看原话 ---------- */
async function mentorJump(sid, t) {
  const s = M.cur && M.cur.mentor.sources.find(x => x.sid === sid);
  if (!s || !s.found) return toast(`找不到 ${sid} 的视频。可以右键老师「重新查找课程」`, { error: true });
  t = +t || 0;
  switchView('course');
  if (S.path && S.path.toLowerCase() === s.path.toLowerCase() && S.mode !== 'book') seek(t, true);
  else { S.jumpTo = { path: s.path.toLowerCase(), t }; await window.openPath(s.path); }
  toast(`${sid}《${s.title}》${fmt(t)}。点左上角「老师」回到对话`, { ms: 7000 });
}
$('#mentorMsgs').addEventListener('click', async e => {
  const c = e.target.closest('a.cite');
  if (c) { if (!c.classList.contains('bad') && c.dataset.sid) mentorJump(c.dataset.sid, c.dataset.t); return; }
  const eg = e.target.closest('[data-eg]');
  if (eg) return mentorAsk(eg.dataset.eg);
  const sv = e.target.closest('[data-save]');
  if (sv && M.chat) {
    try {
      const r = await api('mentor_save', { id: M.cur.mentor.id, cid: M.chat.id, index: +sv.dataset.save });
      M.chat = r.chat; renderMentorMsgs(); renderMentorHead();
      favToast('已收藏到 Obsidian 的「对话精选」', r);
    } catch (err) { fail(err); }
    return;
  }
  if (e.target.closest('[data-distill-go]')) return startDistill(M.cur.mentor.id);
  if (e.target.closest('[data-distill-edit]')) return openDistill({ mid: M.cur.mentor.id });
  const rm = e.target.closest('[data-rm-sid]');
  if (rm) {
    if (!confirm(`不用 ${rm.dataset.rmSid} 这节课了？它的编号空着，不给别的课用。`)) return;
    try { M.cur.distill = await api('distill_remove_lesson', { id: M.cur.mentor.id, sid: rm.dataset.rmSid }); renderMentorMsgs(); refreshMentorList(); } catch (err) { fail(err); }
    return;
  }
  const cp = e.target.closest('[data-copy]');
  if (cp && M.chat) { try { await navigator.clipboard.writeText(M.chat.messages[+cp.dataset.copy].content); toast('已复制'); } catch (err) { fail(err); } }
});
$('#mentorMsgs').addEventListener('mouseover', async e => {
  const c = e.target.closest('a.cite');
  if (!c || c.title || c.classList.contains('bad') || !M.cur || !c.dataset.sid) return;
  const a = c.dataset.aid && M.cur.atoms[c.dataset.aid];
  if (a) { c.title = `${c.dataset.aid}（${a.src}）\n${a.text}\n\n点一下跳到视频`; return; }
  const key = c.dataset.sid + '@' + c.dataset.t;
  if (!M.quotes[key]) {
    try { M.quotes[key] = await api('mentor_quote', { id: M.cur.mentor.id, sid: c.dataset.sid, t: +c.dataset.t || 0 }); } catch (_) { return; }
  }
  const q = M.quotes[key];
  c.title = `《${q.title}》\n${q.text || '（这一处没有字幕）'}\n\n点一下跳到视频`;
});

/* ---------- 导入、右键菜单、搜索 ---------- */
$('#mentorAdd').onclick = e => {
  e.stopPropagation();  // 不然窗口的 click 会马上关掉刚打开的菜单
  const r = e.currentTarget.getBoundingClientRect();
  showCtx(r.left, r.bottom + 4, `<button class="menu-item" data-a="distill" title="AI 逐节提取观点库，再合并成课程蒸馏、写老师画像，写进 Obsidian">从媒体库的课蒸馏一位新老师…</button>
    <button class="menu-item" data-a="import" title="Obsidian「04、外部知识转化库」里已经有观点库和老师画像的老师文件夹">导入已经蒸馏好的老师文件夹…</button>`, m => {
    $('[data-a=distill]', m).onclick = () => { m.hidden = true; openDistill({}); };
    $('[data-a=import]', m).onclick = () => { m.hidden = true; importMentor(); };
  });
};
async function importMentor() {
  let folder;
  try { folder = (await api('mentor_pick', {})).folder; } catch (e) { return fail(e); }
  if (!folder) return;
  const btn = $('#mentorAdd');
  btn.disabled = true;
  const wait = toast('正在查找课程笔记…（要在 Obsidian 库里找每节课的笔记，可能要几秒）', { ms: 120000 });
  try {
    const r = await api('mentor_add', { folder });
    M.list = r.mentors;
    const miss = r.mentor.sources.filter(s => !s.found).length;
    toast(`已导入「${r.mentor.name}」：${r.mentor.sources.length} 节课` + (miss ? `，其中 ${miss} 节找不到视频（出处没法跳转）` : ''), { ms: 8000 });
    M.cur = null;
    await openMentor(r.mentor.id);
  } catch (e) { fail(e); } finally { wait.remove(); btn.disabled = false; }
}

/* ---------- 收藏：一键收藏整个对话、在 Obsidian 里打开对话精选 ---------- */
function openFav(info) {
  if (info.uri) api('reveal', { target: info.uri });
  else if (info.exists) api('reveal', { target: info.file });
  else toast('还没有收藏过。觉得好的回答点它下面的「☆ 收藏」');
}
function favToast(msg, info) { toast(msg, { ms: 6000, action: { label: '在 Obsidian 里打开', run: () => openFav(info) } }); }
$('#mentorSaveAll').onclick = async () => {
  if (!M.cur || !M.chat) return;
  try {
    const r = await api('mentor_save_all', { id: M.cur.mentor.id, cid: M.chat.id });
    M.chat = r.chat; renderMentorMsgs(); renderMentorHead();
    if (r.count) favToast(`收藏了 ${r.count} 条回答到 Obsidian 的「对话精选」`, r);
    else toast('这个对话里的回答都已经收藏过了');
  } catch (e) { fail(e); }
};
$('#mentorFav').onclick = async () => {
  if (!M.cur) return;
  try { openFav(await api('mentor_favorites', { id: M.cur.mentor.id })); } catch (e) { fail(e); }
};

/* ---------- 蒸馏：从媒体库选课，后台逐节提取、合并、写画像 ---------- */
const BOOK_EXT = ['pdf', 'docx', 'doc', 'txt', 'md', 'epub', 'mobi', 'azw3', 'azw', 'pptx', 'ppt'];
function libFolders(nodes = S.lib, depth = 0, out = []) {
  for (const n of nodes || []) if (n.type === 'folder') { out.push({ path: n.path, name: n.name, depth }); libFolders(n.children, depth + 1, out); }
  return out;
}
function lessonsUnder(node, out = []) {
  for (const c of node.children || []) {
    if (c.type === 'folder') lessonsUnder(c, out);
    else if (!c.book && !BOOK_EXT.includes(c.ext)) out.push(c);
  }
  return out;
}
function distillTag(m) {
  const d = M.distill[m.id];
  if (d) return `蒸馏中 ${Math.round((d.progress || 0) * 100)}%`;
  return m.distilled && !m.distill_done ? '未蒸馏完' : '';
}
async function openDistill(o) {
  // o：{folder, name} 新建；{mid} 给已有的老师加课 / 重新蒸馏
  const upd = o.mid ? M.list.find(x => x.id === o.mid) : null;
  let st = null;
  if (upd) { try { st = await api('distill_status', { id: o.mid }); } catch (e) { return fail(e); } }
  const folders = libFolders();
  if (!folders.length) return toast('媒体库里还没有文件夹。先切到「课程」，添加课程所在的文件夹', { error: true });
  const have = new Set((st ? st.lessons : []).map(x => x.path.toLowerCase()));
  const all = st ? folders.filter(f => st.lessons.length && st.lessons.every(x => insideDir(x.path, f.path))) : [];
  const dlg = $('#dlgDistill');
  $('#dsTitle').textContent = upd ? `更新蒸馏：${upd.name}` : '从媒体库的课蒸馏一位老师';
  $('#dsOk').textContent = upd ? '更新蒸馏' : '开始蒸馏';
  $('#dsName').value = upd ? upd.name : (o.name || '');
  $('#dsName').disabled = !!upd;
  $('#dsFolder').innerHTML = folders.map(f => `<option value="${esc(f.path)}">${'　'.repeat(f.depth)}${esc(f.name)}</option>`).join('');
  $('#dsFolder').value = o.folder || (all.length ? all[all.length - 1].path : folders[0].path);
  const boxes = () => $$('#dsList input[type=checkbox]');
  const picked = () => boxes().filter(b => b.checked && !b.disabled).map(b => b.dataset.path);
  const count = () => {
    const n = picked().length;
    $('#dsCount').textContent = upd ? `已经有 ${have.size} 节，这次加 ${n} 节` : `已选 ${n} 节`;
  };
  const fill = () => {
    const node = libNode($('#dsFolder').value);
    const ls = node ? lessonsUnder(node) : [];
    $('#dsList').innerHTML = ls.length ? ls.map(n => {
      const inside = have.has(n.path.toLowerCase()), ok = n.subs > 0;
      const why = inside ? '已经在这位老师里' : ok ? (n.summary ? '有字幕和课程重点' : '有字幕（还没有课程重点）') : '还没有字幕，先生成字幕';
      return `<label class="ds-item${ok || inside ? '' : ' off'}"><input type="checkbox" data-path="${esc(n.path)}" ${inside || ok ? 'checked' : ''} ${inside || !ok ? 'disabled' : ''}>`
        + `<span class="ds-name">${esc(n.name)}</span><small class="muted">${why}</small></label>`;
    }).join('') : '<div class="muted">这个文件夹里没有音视频课程</div>';
    count();
  };
  let tTimer = null;
  const target = () => {
    clearTimeout(tTimer);
    tTimer = setTimeout(async () => {
      const name = $('#dsName').value.trim();
      if (!name) { $('#dsTarget').textContent = ''; return; }
      try {
        const r = await api('distill_target', { name });
        $('#dsTarget').textContent = `结果写到：${r.folder}` + (r.files.length
          ? `（那里已经有 ${r.files.length} 份文件。蒸馏完会先备份，再覆盖观点库、课程蒸馏、老师画像这三份，别的文件不动）` : '');
      } catch (e) { $('#dsTarget').textContent = e.message || String(e); }
    }, 250);
  };
  $('#dsFolder').onchange = fill;
  $('#dsList').onchange = count;
  $('#dsName').oninput = target;
  $('#dsAll').onclick = () => { boxes().forEach(b => { if (!b.disabled) b.checked = true; }); count(); };
  $('#dsNone').onclick = () => { boxes().forEach(b => { if (!b.disabled) b.checked = false; }); count(); };
  $('#dsOk').onclick = e => {
    if (!upd && !$('#dsName').value.trim()) { e.preventDefault(); toast('请填老师的名字', { error: true }); return; }
    if (!upd && !picked().length) { e.preventDefault(); toast('请至少勾选一节有字幕的课', { error: true }); }
  };
  fill(); target();
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = async () => {
    if (dlg.returnValue !== 'ok') return;
    const paths = picked();
    try {
      let mid;
      if (upd) {
        mid = upd.id;
        if (paths.length) M.list = (await api('distill_add', { id: mid, paths })).mentors;
      } else {
        const r = await api('distill_create', { name: $('#dsName').value.trim(), paths });
        M.list = r.mentors; mid = r.mentor.id;
      }
      M.cur = null;
      await openMentor(mid);
      await startDistill(mid);
    } catch (err) { fail(err); }
  };
}
async function startDistill(mid, force = false) {
  try {
    const r = await api('distill_start', { id: mid, force });
    if (r.conflict) {
      if (!confirm(`这几份文件在上次蒸馏之后，在 Obsidian 里改过（或者不是软件写的）：\n\n${r.conflict.join('\n')}\n\n`
        + '继续会先把它们备份到软件的备份文件夹，再用新的蒸馏结果覆盖（你在里面改的会被替换，备份里还有）。继续吗？')) return;
      return startDistill(mid, true);
    }
    M.distill[mid] = { job: r.job, progress: 0, message: '准备蒸馏…' };
    renderMentorList(); renderMentorHead(); renderMentorMsgs();
    toast('开始蒸馏：在后台做，可以切到「课程」照常看课。进度在对话区上方和左栏', { ms: 6000 });
    pollDistill();
  } catch (e) { fail(e); }
}
function renderDistillBar() {
  const d = M.cur && M.distill[M.cur.mentor.id];
  $('#mentorDistill').hidden = !d;
  if (!d) return;
  $('#mdMsg').textContent = `蒸馏中 ${Math.round((d.progress || 0) * 100)}%：${d.message || ''}`;
  $('#mdBar').style.width = Math.round((d.progress || 0) * 100) + '%';
}
$('#mdStop').onclick = () => { const d = M.cur && M.distill[M.cur.mentor.id]; if (d) api('cancel', { id: d.job }).catch(() => {}); };
async function pollDistill() {
  if (M.polling) return;
  M.polling = true;
  try {
    while (Object.keys(M.distill).length) {
      for (const [mid, d] of Object.entries(M.distill)) {
        let job;
        try { job = (await api('job', { id: d.job, since: 1e9 })).job; } catch (e) { job = { status: 'error', error: e.message || String(e) }; }
        if (!job) job = { status: 'error', error: '后台任务不见了' };
        d.progress = job.progress; d.message = job.message;
        $$(`.mt-teacher[data-mid="${mid}"] .mt-tag`).forEach(el => { el.textContent = `蒸馏中 ${Math.round((d.progress || 0) * 100)}%`; });
        if (job.status === 'running') continue;
        delete M.distill[mid];
        const name = (M.list.find(x => x.id === mid) || {}).name || '';
        if (job.status === 'done') {
          const r = job.result;
          toast(`「${name}」蒸馏好了：${r.lessons} 节课、${r.points} 条观点（这次提取了 ${r.extracted} 节）`
            + (r.bad_refs ? `；AI 写了 ${r.bad_refs} 处观点库里没有的编号，文末标出来了` : '') + (r.bad_times ? `；${r.bad_times} 处时间超出课程长度，标了「时间待核对」` : ''),
          { ms: 15000, action: { label: '打开文件夹', run: () => api('reveal', { target: r.files[0] }) } });
        } else if (job.status === 'cancelled') toast(`「${name}」的蒸馏停下了。做完的课都存着，右键老师「继续蒸馏」接着做`, { ms: 8000 });
        else toast(`「${name}」蒸馏没做完：${job.error}。做完的课都存着，右键老师「继续蒸馏」接着做`, { error: true, ms: 15000 });
        await refreshMentorList();
        if (M.cur && M.cur.mentor.id === mid) { const cid = M.chat && M.chat.id; M.cur = null; await openMentor(mid, cid); }
      }
      renderDistillBar();
      await sleep(1000);
    }
  } finally { M.polling = false; renderDistillBar(); }
}
$('#mentorList').addEventListener('click', e => {
  const c = e.target.closest('.mt-chat');
  if (c) return openMentor(c.dataset.mid, c.dataset.cid);
  const t = e.target.closest('.mt-teacher');
  if (t) openMentor(t.dataset.mid);
});
$('#mentorList').addEventListener('contextmenu', e => {
  const c = e.target.closest('.mt-chat'), t = e.target.closest('.mt-teacher');
  if (!c && !t) return;
  e.preventDefault();
  if (c) {
    showCtx(e.clientX, e.clientY, '<button class="menu-item" data-a="rename">改名…</button><button class="menu-item danger" data-a="del">删除这个对话</button>', m => {
      $('[data-a=rename]', m).onclick = async () => {
        m.hidden = true;
        const now = (M.list.find(x => x.id === c.dataset.mid).chats.find(x => x.id === c.dataset.cid) || {}).title || '';
        const name = (window.prompt('对话改名：', now) || '').trim();
        if (!name) return;
        try { await api('mentor_chat_rename', { id: c.dataset.mid, cid: c.dataset.cid, title: name }); if (M.chat && M.chat.id === c.dataset.cid) M.chat.title = name; renderMentorHead(); refreshMentorList(); } catch (err) { fail(err); }
      };
      $('[data-a=del]', m).onclick = async () => {
        m.hidden = true;
        if (!confirm('删除这个对话？删除后找不回来（已经收藏到 Obsidian 的不受影响）。')) return;
        try {
          await api('mentor_chat_delete', { id: c.dataset.mid, cid: c.dataset.cid });
          if (M.chat && M.chat.id === c.dataset.cid) { M.chat = null; renderMentorHead(); renderMentorMsgs(); }
          refreshMentorList();
        } catch (err) { fail(err); }
      };
    });
    return;
  }
  const mid = t.dataset.mid, me = M.list.find(x => x.id === mid) || {}, name = me.name || '';
  const run = !!M.distill[mid];
  showCtx(e.clientX, e.clientY, (me.distilled ? (run ? '<button class="menu-item" data-a="dstop">停止蒸馏（做完的课保存着）</button>'
    : (me.distill_done ? '' : '<button class="menu-item" data-a="dgo">继续蒸馏</button>')
      + '<button class="menu-item" data-a="dupd" title="只提取新课和字幕改过的课，再重新合并、写画像">更新蒸馏（加新课、字幕改过的重新提取）…</button>') : '')
    + `<button class="menu-item" data-a="refresh">重新查找课程（笔记或视频挪了位置后）</button>
    <button class="menu-item" data-a="reveal">在资源管理器中打开老师文件夹</button>
    <button class="menu-item danger" data-a="clear">清空全部对话</button>
    <button class="menu-item danger" data-a="remove">移除这位老师（不删 Obsidian 里的文件）</button>`, m => {
    const on = (a, fn) => { const b = $(`[data-a=${a}]`, m); if (b) b.onclick = () => { m.hidden = true; fn(); }; };
    on('dstop', () => api('cancel', { id: M.distill[mid].job }).catch(() => {}));
    on('dgo', () => startDistill(mid));
    on('dupd', () => openDistill({ mid }));
    $('[data-a=refresh]', m).onclick = async () => {
      m.hidden = true;
      try { const r = await api('mentor_refresh', { id: mid }); if (M.cur && M.cur.mentor.id === mid) M.cur = r; await refreshMentorList(); renderMentorMsgs(); toast(`找到 ${r.mentor.sources.filter(s => s.found).length}/${r.mentor.sources.length} 节课的视频`); } catch (err) { fail(err); }
    };
    $('[data-a=reveal]', m).onclick = () => { m.hidden = true; const x = M.list.find(y => y.id === mid); if (x) api('reveal', { target: x.folder }); };
    $('[data-a=clear]', m).onclick = async () => {
      m.hidden = true;
      if (!confirm(`清空和「${name}」的全部对话？删除后找不回来（已经收藏到 Obsidian 的不受影响）。`)) return;
      try { const r = await api('mentor_chat_delete', { id: mid, all: true }); if (M.cur && M.cur.mentor.id === mid) { M.chat = null; renderMentorHead(); renderMentorMsgs(); } refreshMentorList(); toast(`删除了 ${r.deleted} 个对话`); } catch (err) { fail(err); }
    };
    $('[data-a=remove]', m).onclick = async () => {
      m.hidden = true;
      if (run) { toast('这位老师正在蒸馏，先停止蒸馏再移除', { error: true }); return; }
      if (!confirm(`从老师工作台移除「${name}」？他的对话记录会一起删掉${me.distilled ? '，软件里的蒸馏记录（每节课提取的结果）也删掉，以后要更新就得从头蒸馏' : ''}；`
        + 'Obsidian 里的观点库、老师画像、对话精选都不动，以后可以重新导入。')) return;
      try { const r = await api('mentor_remove', { id: mid }); M.list = r.mentors; if (M.cur && M.cur.mentor.id === mid) { M.cur = null; M.chat = null; } renderMentorList(); renderMentorHead(); renderMentorMsgs(); } catch (err) { fail(err); }
    };
  });
});
let mentorSearchTimer = null;
$('#mentorSearch').addEventListener('input', () => {
  clearTimeout(mentorSearchTimer);
  mentorSearchTimer = setTimeout(async () => {
    const q = $('#mentorSearch').value.trim();
    if (!q) { M.searchHits = null; renderMentorList(); return; }
    const hits = {};
    for (const m of M.list) { try { hits[m.id] = (await api('mentor_chats', { id: m.id, q })).chats; } catch (_) { hits[m.id] = []; } }
    M.searchHits = hits; renderMentorList();
  }, 300);
});
