/* 圆桌讨论和主题蒸馏（2026-10-24）。后台在 app/core/roundtable.py，规则见知识库《AI知识蒸馏规范》V2.0 第六节。
   - 左栏老师下面两组：「圆桌讨论」「主题蒸馏」，点组名新建；右键条目改名、删除、重新蒸馏。
   - 圆桌讨论：每位老师各自回答（边写边显示，出处按是哪位老师说的来认，点了跳到他那节课那一秒）→ 自动总结；
     「让老师们互相回应」再来一轮；「收藏到 Obsidian」整场存进「02、主题蒸馏资料」的一个主题文件夹。
   - 主题蒸馏：后台每位老师整理一次再合并，写进 Obsidian；软件里也能看，出处 ［老师名 S02-21］ 可点。
   - 「提示词」按钮：显示软件实际交给 AI 的提示词。
   和 mentor.js 共用对话区：R.mode 不为空时，mentor.js 的 renderMentorHead / renderMentorMsgs / mentorAsk 交给这里。 */
const R = { mode: null, round: null, topic: null, rounds: [], topics: [], folders: [], job: null, tjobs: {}, tpolling: false };

/* ---------- 左栏 ---------- */
async function loadRounds() {
  try { const r = await api('round_list'); R.rounds = r.rounds; R.topics = r.topics; R.folders = r.folders; } catch (_) { /* 下次再刷新 */ }
  for (const t of R.topics) if (t.running && !R.tjobs[t.id]) R.tjobs[t.id] = { job: t.running, progress: 0, message: '正在蒸馏…' };
  if (Object.keys(R.tjobs).length) pollTopics();
}
async function refreshRounds() { await loadRounds(); renderMentorList(); }
function roundListHTML(q) {
  const hit = s => !q || String(s).toLowerCase().includes(q.toLowerCase());
  const names = ps => ps.map(p => p.name).join('、');
  const rs = R.rounds.filter(r => hit(r.title + r.names.join('')));
  const ts = R.topics.filter(t => hit(t.topic + names(t.people)));
  return '<div class="mt-sep"></div>'
    + '<div class="mt-section" data-new="round" title="就一个问题请几位老师一起讨论（点一下新建）"><span>圆桌讨论</span><span class="grow"></span><span class="muted">＋ 新建</span></div>'
    + rs.map(r => `<div class="mt-chat rt-item ${R.mode === 'round' && R.round && R.round.id === r.id ? 'active' : ''}" data-rid="${r.id}" title="${esc(r.names.join('、'))}（${esc(r.updated)}）。右键：改名、删除">${esc(r.title)}<small>${esc(r.names.join('、'))}</small></div>`).join('')
    + '<div class="mt-section" data-new="topic" title="选一个主题和几位老师，比较他们分别怎么说（点一下新建）"><span>主题蒸馏</span><span class="grow"></span><span class="muted">＋ 新建</span></div>'
    + ts.map(t => {
      const d = R.tjobs[t.id];
      return `<div class="mt-chat tp-item ${R.mode === 'topic' && R.topic && R.topic.topic.id === t.id ? 'active' : ''}" data-tid="${t.id}" title="${esc(names(t.people))}。右键：重新蒸馏、删除记录">${esc(t.topic)}`
        + `<small class="tp-tag">${d ? `蒸馏中 ${Math.round((d.progress || 0) * 100)}%` : t.done ? esc(names(t.people)) : '没做完'}</small></div>`;
    }).join('');
}
async function ensureOv(mids) {  // 出处要按每位老师的来源、观点编号来认
  for (const mid of mids) {
    if (!M.ov[mid]) { try { M.ov[mid] = await api('mentor_open', { id: mid }); } catch (_) { /* 老师被移除了：他的出处标红 */ } }
  }
}
function roundNames() {
  const o = {};
  (R.mode === 'round' && R.round ? R.round.people : R.topic ? R.topic.topic.people : []).forEach(p => { o[p.name] = p.mid; });
  return o;
}
$('#mentorList').addEventListener('click', e => {
  const n = e.target.closest('.mt-section[data-new]');
  if (n) return openMulti(n.dataset.new);
  const r = e.target.closest('.rt-item');
  if (r) return openRound(r.dataset.rid);
  const t = e.target.closest('.tp-item');
  if (t) openTopic(t.dataset.tid);
});
$('#mentorList').addEventListener('contextmenu', e => {
  const r = e.target.closest('.rt-item'), t = e.target.closest('.tp-item');
  if (!r && !t) return;
  e.preventDefault();
  if (r) {
    showCtx(e.clientX, e.clientY, '<button class="menu-item" data-a="rename">改名…</button><button class="menu-item danger" data-a="del">删除这个圆桌讨论</button>', m => {
      $('[data-a=rename]', m).onclick = async () => {
        m.hidden = true;
        const now = (R.rounds.find(x => x.id === r.dataset.rid) || {}).title || '';
        const name = (window.prompt('圆桌讨论改名：', now) || '').trim();
        if (!name) return;
        try { const x = await api('round_rename', { cid: r.dataset.rid, title: name }); if (R.round && R.round.id === x.chat.id) { R.round = x.chat; renderMentorHead(); } refreshRounds(); } catch (err) { fail(err); }
      };
      $('[data-a=del]', m).onclick = async () => {
        m.hidden = true;
        if (!confirm('删除这个圆桌讨论？删除后找不回来（已经收藏到 Obsidian 的不受影响）。')) return;
        try { await api('round_delete', { cid: r.dataset.rid }); if (R.round && R.round.id === r.dataset.rid) leaveRound(); refreshRounds(); } catch (err) { fail(err); }
      };
    });
    return;
  }
  const tp = R.topics.find(x => x.id === t.dataset.tid) || {};
  showCtx(e.clientX, e.clientY, `<button class="menu-item" data-a="redo">重新蒸馏…</button>
    ${tp.file ? '<button class="menu-item" data-a="reveal">在资源管理器中显示文件</button>' : ''}
    <button class="menu-item danger" data-a="del">删除记录（不删 Obsidian 里的文件）</button>`, m => {
    $('[data-a=redo]', m).onclick = () => { m.hidden = true; redoTopic(tp); };
    const rv = $('[data-a=reveal]', m);
    if (rv) rv.onclick = () => { m.hidden = true; api('reveal', { target: tp.file }); };
    $('[data-a=del]', m).onclick = async () => {
      m.hidden = true;
      if (!confirm(`删除「${tp.topic}」这条主题蒸馏的记录？Obsidian 里写好的文件不动。`)) return;
      try { await api('topic_delete', { id: tp.id }); if (R.topic && R.topic.topic.id === tp.id) leaveRound(); refreshRounds(); } catch (err) { fail(err); }
    };
  });
});
function leaveRound() {
  R.mode = null; R.round = null; R.topic = null;
  renderMentorHead(); renderMentorMsgs();
}

/* ---------- 新建：选几位老师 ---------- */
function readyMentors() { return M.list.filter(m => !(m.distilled && !m.distill_done)); }
function openMulti(kind, preset = {}) {
  const ms = readyMentors(), topic = kind === 'topic';
  if (ms.length < 2) return toast(`至少要有两位蒸馏好的老师，才能${topic ? '做主题蒸馏' : '开圆桌讨论'}`, { error: true });
  const dlg = $('#dlgMulti');
  $('#mlTitle').textContent = topic ? (preset.id ? `重新蒸馏「${preset.topic}」` : '新建主题蒸馏') : '新建圆桌讨论';
  $('#mlInfo').textContent = topic
    ? 'AI 从每位老师的观点库里找出和这个主题相关的内容，比较他们分别怎么说（共识、分歧和原因、条件和边界），写进 Obsidian「04、外部知识转化库/02、主题蒸馏资料」。写书时参考。'
    : '就一个问题请几位老师一起讨论：每位老师只用自己的课程各自回答，然后 AI 总结共识和分歧；还可以让老师们互相回应。';
  $$('.topic-only', dlg).forEach(el => { el.hidden = !topic; });
  $('#mlTopic').value = preset.topic || '';
  $('#mlNote').value = preset.note || '';
  $('#mlFolder').innerHTML = R.folders.map(f => `<option>${esc(f)}</option>`).join('');
  if (preset.folder) $('#mlFolder').value = preset.folder;
  const sel = new Set(preset.mids || []);
  $('#mlList').innerHTML = ms.map(m => `<label class="ds-item"><input type="checkbox" data-mid="${m.id}" ${sel.has(m.id) || (!preset.mids && ms.length <= 4) ? 'checked' : ''}>`
    + `<span class="ds-name">${esc(m.name)}</span><small class="muted">${(m.sources || []).length} 节课</small></label>`).join('');
  const picked = () => $$('#mlList input:checked').map(b => b.dataset.mid);
  const count = () => {
    const n = picked().length;
    $('#mlCount').textContent = `已选 ${n} 位`;
    $('#mlCost').textContent = topic ? `会调用「对话」的 AI ${n + 1} 次（每位老师一次，最后合并一次），在后台做，可以照常看课。`
      : `每提一个问题调用「对话」的 AI ${n + 1} 次（每位老师一次，最后总结一次）；「让老师们互相回应」再 ${n + 1} 次。`;
  };
  $('#mlList').onchange = count;
  count();
  $('#mlOk').textContent = topic ? '开始蒸馏' : '开始讨论';
  $('#mlOk').onclick = e => {
    if (picked().length < 2) { e.preventDefault(); toast('请至少选两位老师', { error: true }); return; }
    if (topic && !$('#mlTopic').value.trim()) { e.preventDefault(); toast('请填主题', { error: true }); }
  };
  dlg.returnValue = '';
  dlg.showModal();
  dlg.onclose = async () => {
    if (dlg.returnValue !== 'ok') return;
    try {
      if (topic) {
        const r = await api('topic_start', { id: preset.id, topic: $('#mlTopic').value.trim(), note: $('#mlNote').value.trim(), folder: $('#mlFolder').value, mentors: picked() });
        R.tjobs[r.topic.id] = { job: r.job, progress: 0, message: '准备…' };
        await refreshRounds();
        await openTopic(r.topic.id);
        pollTopics();
        toast('开始主题蒸馏：在后台做，可以照常看课，做完会提示你', { ms: 6000 });
      } else {
        const r = await api('round_new', { mentors: picked() });
        await refreshRounds();
        await openRound(r.chat.id);
        $('#mentorInput').focus();
      }
    } catch (err) { fail(err); }
  };
}

/* ---------- 圆桌讨论 ---------- */
async function openRound(cid) {
  M.seq++;  // 还没完成的「打开老师 / 对话」作废
  try {
    const r = await api('round_load', { cid });
    await ensureOv(r.chat.people.map(p => p.mid));
    R.mode = 'round'; R.round = r.chat; R.topic = null;
    renderMentorHead(); renderMentorMsgs(); renderMentorList();
    if (r.running && !R.job) followRound(r.running);  // 后台还在讨论（界面重新打开过）：接上
  } catch (e) { fail(e); }
}
function renderRoundHead() {
  const round = R.mode === 'round';
  $('#roundTools').hidden = !round;
  $('#topicTools').hidden = round;
  $('#mentorDistill').hidden = true;
  $('#mentorModel').textContent = $('#aiModel').textContent || '';
  if (round) {
    const c = R.round, names = c.people.map(p => p.name).join('、');
    const answered = c.messages.some(x => x.role === 'teacher');
    $('#mentorName').textContent = '圆桌讨论';
    $('#mentorChatTitle').textContent = names + (c.title && c.title !== '新的圆桌讨论' ? '　' + c.title : '');
    $('#rtReply').disabled = !!R.job || !answered;
    $('#rtSummary').disabled = !!R.job || !answered;
    $('#rtSave').disabled = !answered;
    $('#rtSave').textContent = c.saved ? '★ 更新 Obsidian 里的' : '★ 收藏到 Obsidian';
    $('#mentorInput').disabled = false; $('#mentorSend').disabled = false;
    $('#mentorInput').placeholder = `向${names}提问，几位老师各自回答…（Enter 发送，Shift+Enter 换行）`;
  } else {
    const t = R.topic.topic;
    $('#mentorName').textContent = '主题蒸馏';
    $('#mentorChatTitle').textContent = `${t.topic}　${t.people.map(p => p.name).join('、')}`;
    $('#tpOpen').disabled = !R.topic.text;
    $('#tpRedo').disabled = !!R.tjobs[t.id];
    $('#mentorInput').disabled = true; $('#mentorSend').disabled = true;
    $('#mentorInput').placeholder = '主题蒸馏是一份文档，不能在这里对话；想和几位老师讨论，可以新建圆桌讨论';
  }
}
function roundMsgHTML(x, i) {
  if (x.role === 'user') return `<div class="msg user"><div class="bubble">${esc(x.content)}</div></div>`;
  const sum = x.role === 'summary';
  const who = sum ? 'AI 总结<small>不是任何一位老师的原话，是 AI 归纳的</small>' : esc(x.name) + (x.round === 2 ? '<small>回应其他老师</small>' : '');
  return `<div class="msg assistant ${sum ? 'rt-summary' : 'rt-teacher'}" data-i="${i}"><div class="rt-who">${who}</div>`
    + `<div class="bubble md">${answerHTML(x.content, sum ? { names: roundNames() } : { mid: x.mid })}</div>`
    + `<div class="msg-actions"><span class="msg-meta">${esc((x.model || '') + (x.time ? '　' + x.time.slice(5, 16) : ''))}</span>`
    + (i >= 0 ? `<button class="btn small" data-rcopy="${i}">复制</button>` : '') + '</div></div>';
}
function renderRoundMsgs() {
  if (R.mode === 'topic') return renderTopic();
  const box = $('#mentorMsgs'), c = R.round;
  if (!c.messages.length) {
    const names = c.people.map(p => p.name).join('、');
    const egs = ['交易系统应该包括哪些部分？', '单笔风险应该怎么定？', '连续亏损的时候该怎么办？'];
    box.innerHTML = `<div class="mentor-intro"><h3>圆桌讨论：${esc(names)}</h3>
      <p>提一个问题，几位老师各自<b>只用自己的课程</b>回答（出处可点，跳到那位老师那节课那一秒），然后 AI 总结共识、分歧和各自的条件。还可以点上方「让老师们互相回应」，每位老师看了别人的回答再回应一轮。</p>
      <p>觉得有价值的讨论点「★ 收藏到 Obsidian」，整场存进「02、主题蒸馏资料」，写书时参考。</p>
      <p>可以这样问：</p>${egs.map(q => `<button class="btn small ask-eg" data-reg="${esc(q)}">${esc(q)}</button>`).join('')}</div>`;
    return;
  }
  box.innerHTML = c.messages.map(roundMsgHTML).join('');
  markLabels(box);
  box.scrollTop = box.scrollHeight;
}
async function roundAsk(q) {
  if (R.mode !== 'round') return;
  if (R.job) { api('cancel', { id: R.job }).catch(() => {}); return; }  // 「停止」
  q = String(q == null ? $('#mentorInput').value : q).trim();
  if (!q) return;
  try {
    const r = await api('round_ask', { cid: R.round.id, question: q });
    $('#mentorInput').value = '';
    R.round = r.chat;
    renderRoundMsgs();
    await followRound(r.job);
  } catch (e) { fail(e); }
}
async function roundAction(name) {  // 互相回应 / 重新总结
  if (!R.round || R.job) return;
  try { const r = await api(name, { cid: R.round.id }); await followRound(r.job); } catch (e) { fail(e); }
}
async function followRound(jobId) {
  const cid = R.round.id, live = {};
  R.job = jobId;
  renderMentorHead();
  let since = 0;
  try {
    for (;;) {
      const { job } = await api('job', { id: jobId, since });
      since = job.total;
      const here = R.mode === 'round' && R.round && R.round.id === cid, box = $('#mentorMsgs');
      for (const it of job.items) {
        if (!here || !it || typeof it !== 'object') continue;
        if (it.kind === 'start') {
          box.insertAdjacentHTML('beforeend', roundMsgHTML({ role: it.role, name: it.name, mid: it.who, round: it.round, content: '' }, -1));
          const el = box.lastElementChild, b = el.querySelector('.bubble');
          b.classList.add('typing');
          b.textContent = it.role === 'summary' ? 'AI 正在总结…' : `${it.name}正在翻课程资料…`;
          live[it.who] = { el, text: '' };
        } else if ((it.kind === 'text' || it.kind === 'reset') && live[it.who]) {
          const l = live[it.who], b = l.el.querySelector('.bubble');
          l.text = it.kind === 'reset' ? '' : l.text + it.text;
          b.classList.remove('typing');
          b.innerHTML = answerHTML(l.text, it.who === 'summary' ? { names: roundNames() } : { mid: it.who });
          markLabels(b);
        }
        box.scrollTop = box.scrollHeight;
      }
      if (job.status === 'error') throw new Error(job.error);
      if (job.status !== 'running') break;
      await sleep(200);
    }
  } catch (e) { fail(e); } finally {
    R.job = null;
    try {
      const r = await api('round_load', { cid });
      if (R.mode === 'round' && R.round && R.round.id === cid) R.round = r.chat;
    } catch (_) { /* 用界面上的 */ }
    renderMentorHead();
    if (R.mode === 'round' && R.round && R.round.id === cid) renderRoundMsgs();
    refreshRounds();
  }
}
$('#rtReply').onclick = () => roundAction('round_reply');
$('#rtSummary').onclick = () => roundAction('round_summary');
$('#rtSave').onclick = e => {
  e.stopPropagation();  // 不然窗口的 click 会马上关掉刚打开的菜单
  const c = R.round, rc = e.currentTarget.getBoundingClientRect();
  showCtx(rc.left, rc.bottom + 4, '<div class="menu-label">存进「02、主题蒸馏资料」的哪个主题文件夹</div>'
    + R.folders.map(f => `<button class="menu-item" data-f="${esc(f)}">${esc(f)}${c.saved && c.saved.folder === f ? '（已经存在这里，更新）' : ''}</button>`).join(''), m => {
    $$('[data-f]', m).forEach(b => {
      b.onclick = async () => {
        m.hidden = true;
        try {
          const s = await api('round_save', { cid: c.id, folder: b.dataset.f });
          R.round = s.chat; renderRoundHead(); refreshRounds();
          favToast(`已存进 Obsidian：02、主题蒸馏资料/${b.dataset.f}`, { uri: s.uri, file: s.file, exists: true });
        } catch (err) { fail(err); }
      };
    });
  });
};

/* ---------- 主题蒸馏 ---------- */
async function openTopic(tid) {
  M.seq++;
  try {
    const r = await api('topic_get', { id: tid });
    await ensureOv(r.topic.people.map(p => p.mid));
    R.mode = 'topic'; R.topic = r; R.round = null;
    renderMentorHead(); renderMentorMsgs(); renderMentorList();
  } catch (e) { fail(e); }
}
function renderTopic() {
  const box = $('#mentorMsgs'), t = R.topic.topic, d = R.tjobs[t.id];
  if (d) {
    box.innerHTML = `<div class="mentor-intro"><h3>主题蒸馏：${esc(t.topic)}</h3>
      <p>正在后台蒸馏 ${Math.round((d.progress || 0) * 100)}%：${esc(d.message || '')}</p>
      <p>可以切到别的地方，做完会提示你。</p><button class="btn" data-tstop>停止</button></div>`;
    return;
  }
  if (!R.topic.text) {
    box.innerHTML = `<div class="mentor-intro"><h3>主题蒸馏：${esc(t.topic)}</h3><p>还没做完（可能停下了或出错了），也可能 Obsidian 里的文件被挪走了。</p>
      <button class="btn primary" data-tredo>重新蒸馏…</button></div>`;
    return;
  }
  const body = R.topic.text.replace(/\r\n/g, '\n').replace(/^---\n[\s\S]*?\n---\n/, '')
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2').replace(/\[\[([^\]]+)\]\]/g, '$1');
  box.innerHTML = `<div class="mentor-doc md">${answerHTML(body, { names: R.topic.names })}</div>`;
  markLabels(box);
  box.scrollTop = 0;
}
function redoTopic(t = R.topic && R.topic.topic) {
  if (!t) return;
  openMulti('topic', { id: t.id, topic: t.topic, note: t.note, folder: t.folder, mids: t.people.map(p => p.mid) });
}
async function pollTopics() {
  if (R.tpolling) return;
  R.tpolling = true;
  try {
    while (Object.keys(R.tjobs).length) {
      for (const [tid, d] of Object.entries(R.tjobs)) {
        let job;
        try { job = (await api('job', { id: d.job, since: 1e9 })).job; } catch (e) { job = { status: 'error', error: e.message || String(e) }; }
        if (!job) job = { status: 'error', error: '后台任务不见了' };
        d.progress = job.progress; d.message = job.message;
        $$(`.tp-item[data-tid="${tid}"] .tp-tag`).forEach(el => { el.textContent = `蒸馏中 ${Math.round((d.progress || 0) * 100)}%`; });
        const here = R.mode === 'topic' && R.topic && R.topic.topic.id === tid;
        if (job.status === 'running') { if (here) renderTopic(); continue; }
        delete R.tjobs[tid];
        const t = R.topics.find(x => x.id === tid) || {};
        if (job.status === 'done') {
          toast(`主题蒸馏「${t.topic || ''}」做好了` + (job.result.bad_refs ? `；有 ${job.result.bad_refs} 处出处对不上，文末标出来了` : ''),
            { ms: 12000, action: { label: '在 Obsidian 里打开', run: () => openFav({ uri: job.result.uri, file: job.result.file, exists: true }) } });
        } else if (job.status === 'cancelled') toast(`主题蒸馏「${t.topic || ''}」停下了。右键它「重新蒸馏」可以再做`, { ms: 8000 });
        else toast(`主题蒸馏「${t.topic || ''}」没做完：${job.error}`, { error: true, ms: 12000 });
        await refreshRounds();
        if (here) await openTopic(tid);
      }
      await sleep(1000);
    }
  } finally { R.tpolling = false; }
}
$('#tpOpen').onclick = () => { if (R.topic) openFav({ uri: R.topic.uri, file: R.topic.topic.file, exists: !!R.topic.text }); };
$('#tpRedo').onclick = () => redoTopic();

/* ---------- 对话区里的按钮（圆桌、主题蒸馏） ---------- */
$('#mentorMsgs').addEventListener('click', async e => {
  if (!roundOn() || e.target.closest('a.cite')) return;
  const eg = e.target.closest('[data-reg]');
  if (eg) return roundAsk(eg.dataset.reg);
  const cp = e.target.closest('[data-rcopy]');
  if (cp && R.round) { try { await navigator.clipboard.writeText(R.round.messages[+cp.dataset.rcopy].content); toast('已复制'); } catch (err) { fail(err); } return; }
  if (e.target.closest('[data-tstop]') && R.topic) { const d = R.tjobs[R.topic.topic.id]; if (d) api('cancel', { id: d.job }).catch(() => {}); return; }
  if (e.target.closest('[data-tredo]')) redoTopic();
});

/* ---------- 查看提示词 ---------- */
$('#mentorPrompts').onclick = async () => {
  try {
    const r = await api('prompts');
    $('#prList').innerHTML = r.prompts.map((p, k) => `<details ${k === 0 ? 'open' : ''}><summary>${k + 1}、${esc(p.title)}</summary>`
      + `<p class="muted">什么时候用、还附上什么：${esc(p.when)}</p><pre>${esc(p.text)}</pre></details>`).join('');
    $('#dlgPrompts').showModal();
  } catch (e) { fail(e); }
};
