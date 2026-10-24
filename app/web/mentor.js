/* 老师工作台（2026-10-24）：和蒸馏好的老师对话。后台在 app/core/mentor.py。
   - 左栏顶上「课程 / 老师」切换；老师这边列出导入的老师和他们的对话（多个对话，保存在软件里，可搜索、改名、删除）。
   - 回答里的出处 ［S02 19:54］ / ［S02-21］ 变成可点的小标签：点了切到「课程」、打开那节课、跳到那一秒播放；
     鼠标停在上面显示那一秒前后的原话（或观点库那一条）。观点库里找不到的出处标红（AI 编的）。
   - 【AI 推测…】【AI 自己的看法…】用不同颜色的框。
   - 「收藏到 Obsidian」把这条问答追加到老师文件夹里的「对话精选.md」。 */
const M = { list: [], cur: null, chat: null, job: null, searchHits: null, quotes: {}, seq: 0 };  // seq：每次打开老师 / 对话编号，晚到的旧结果作废
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
  renderMentorList();
  if (!M.cur && M.list.length) await openMentor(M.list[0].id, (M.list[0].chats[0] || {}).id);
  else renderMentorHead(), renderMentorMsgs();
}
function renderMentorList() {
  const box = $('#mentorList'), q = $('#mentorSearch').value.trim();
  if (!M.list.length) {
    box.innerHTML = '<div class="mt-empty">还没有老师。<br>点上方的「导入老师」按钮，选择 Obsidian「04、外部知识转化库」里蒸馏好的老师文件夹（里面要有「观点库」和「老师画像」两份文件）。</div>';
    return;
  }
  box.innerHTML = M.list.map(m => {
    const chats = (M.searchHits && M.searchHits[m.id]) || m.chats || [];
    const miss = (m.sources || []).filter(s => !s.found).length;
    return `<div class="mt-teacher" data-mid="${m.id}" title="点一下开一个新对话；右键：重新查找课程、清空对话、移除"><span>${esc(m.name)}</span><span class="grow"></span>`
      + `<span class="muted">${(m.sources || []).length} 节课${miss ? `，${miss} 节找不到视频` : ''}</span></div>`
      + chats.map(c => `<div class="mt-chat ${M.chat && M.chat.id === c.id ? 'active' : ''}" data-mid="${m.id}" data-cid="${c.id}" title="${esc(c.title)}（${esc(c.updated)}，${c.count} 条）。右键：改名、删除">${esc(c.title)}<small>${esc((c.updated || '').slice(5, 10))}</small></div>`).join('')
      + (q && !chats.length ? '<div class="mt-empty">没有找到</div>' : '');
  }).join('');
}
async function openMentor(mid, cid) {
  const my = ++M.seq;
  try {
    const cur = !M.cur || M.cur.mentor.id !== mid ? await api('mentor_open', { id: mid }) : M.cur;
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
  $('#mentorInput').placeholder = M.cur ? `向${name}提问…（Enter 发送，Shift+Enter 换行）` : '先在左边导入一位老师';
  $('#mentorInput').disabled = !M.cur;
  $('#mentorSend').disabled = !M.cur;
  $('#mentorNew').disabled = !M.cur;
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
    if ((m = p.match(/^(S\d{2})\s+(\d{1,2}:\d{2}(?::\d{2})?)/))) {
      sid = m[1]; t = parseTs(m[2]); ok = !!M.cur && M.cur.mentor.sources.some(s => s.sid === sid);
    } else if ((m = p.match(/^(S\d{2}-[A-Za-z0-9]+)/))) {
      aid = m[1]; sid = aid.slice(0, 3);
      const a = M.cur && M.cur.atoms[aid];
      ok = !!a; t = a ? a.t : null;
    } else return esc(p);
    return `<a class="cite${ok ? '' : ' bad'}" data-sid="${sid}" data-t="${t == null ? '' : t}" data-aid="${aid}"${ok ? '' : ' title="观点库和课程里找不到这个出处，可能是 AI 编的"'}>${esc(label)}</a>`;
  }).join(' ');
}
function answerHTML(text) {
  const marks = [];
  const pre = String(text || '').replace(/[［\[]\s*(S\d{2}[^］\]\n]{0,80})\s*[］\]]/g, (all, inner) => `\u0001${marks.push(citeHTML(inner)) - 1}\u0002`);
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
    box.innerHTML = '<div class="mentor-intro"><h3>老师工作台</h3><p>先在左边点「导入老师」，选择 Obsidian「04、外部知识转化库」里蒸馏好的老师文件夹。导入以后就能按老师画像和他对话：课程里讲过的都带出处，点出处跳到那节课那一秒；超出课程的会标「AI 推测」。</p></div>';
    return;
  }
  const msgs = (M.chat && M.chat.messages) || [];
  if (!msgs.length) {
    const src = M.cur.mentor.sources.map(s => `<li>${s.sid}　${esc(s.title)}${s.found ? '' : '　<b style="color:var(--danger)">（找不到视频，右键老师「重新查找课程」）</b>'}</li>`).join('');
    const egs = ['交易系统包括哪些方面？', '我的单笔风险应该怎么定？', '连续亏了好几笔，要不要改系统？', '你的课里有没有讲止盈怎么做？'];
    box.innerHTML = `<div class="mentor-intro"><h3>和${esc(M.cur.mentor.name)}对话</h3>
      <p>回答按「老师画像」和他的课程资料来写：课程里讲过的都带出处（如 <a class="cite">S02 19:54</a>），点出处跳到那节课那一秒，鼠标停在上面能看到原话；超出课程的会标<span class="ai-guess" style="display:inline;padding:1px 6px;margin:0">【AI 推测】</span>。觉得好的回答点「收藏到 Obsidian」。</p>
      <p>他的课程：</p><ul>${src}</ul><p>可以这样问：</p>${egs.map(q => `<button class="btn small ask-eg" data-eg="${esc(q)}">${esc(q)}</button>`).join('')}</div>`;
    return;
  }
  box.innerHTML = msgs.map((m, i) => m.role === 'user'
    ? `<div class="msg user"><div class="bubble">${esc(m.content)}</div></div>`
    : `<div class="msg assistant" data-i="${i}"><div class="bubble md">${answerHTML(m.content)}</div><div class="msg-actions">
        <span class="msg-meta">${esc((m.model || '') + (m.time ? '　' + m.time.slice(5, 16) : ''))}</span>
        <button class="btn small" data-save="${i}" ${m.saved ? 'disabled' : ''}>${m.saved ? '已收藏到 Obsidian' : '收藏到 Obsidian'}</button>
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
      M.chat = r.chat; renderMentorMsgs();
      toast('已收藏到 Obsidian：' + r.file.split(/[\\/]/).slice(-2).join('/'));
    } catch (err) { fail(err); }
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
$('#mentorAdd').onclick = async () => {
  try {
    const r = await api('mentor_add', {});
    if (!r.mentor) return;
    M.list = r.mentors;
    const miss = r.mentor.sources.filter(s => !s.found).length;
    toast(`已导入「${r.mentor.name}」：${r.mentor.sources.length} 节课` + (miss ? `，其中 ${miss} 节找不到视频（出处没法跳转）` : ''), { ms: 8000 });
    M.cur = null;
    await openMentor(r.mentor.id);
  } catch (e) { fail(e); }
};
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
  const mid = t.dataset.mid, name = (M.list.find(x => x.id === mid) || {}).name || '';
  showCtx(e.clientX, e.clientY, `<button class="menu-item" data-a="refresh">重新查找课程（笔记或视频挪了位置后）</button>
    <button class="menu-item" data-a="reveal">在资源管理器中打开老师文件夹</button>
    <button class="menu-item danger" data-a="clear">清空全部对话</button>
    <button class="menu-item danger" data-a="remove">移除这位老师（不删 Obsidian 里的文件）</button>`, m => {
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
      if (!confirm(`从老师工作台移除「${name}」？他的对话记录会一起删掉；Obsidian 里的观点库、老师画像、对话精选都不动，以后可以重新导入。`)) return;
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
