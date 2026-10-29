/* 朗读（听书）：课程重点、AI 对话边听边看（2026-10-21）。
   用户要求和他的 Obsidian 插件「听书」用法一样（D:\猛人obsidian编辑插件开发 的 src/speech.mjs、speech-text.mjs），
   切句、跳过时间、跟随滚动、播放条都照它来：
   - 按句子读，正在读的句子高亮（CSS Highlight，不改页面内容）；快读到看得见的区域底部时自动往下翻（停在约三分之一高处）；
   - 自己用滚轮滚动后暂停跟随，停手 6 秒自动恢复，「定位」马上回到朗读处；双击任意一句从那里开始读，双击正在读的句子暂停 / 继续；
   - 视频时间（显示成链接的 [01:50]、[01:42]-[02:23]）、插图、流程图代码不读；
   - 来源：Edge 在线（自然，联网）、百度朗读（联网，句子发给百度翻译）——这两种由后台 core/tts.py 取声音；
     系统声音（Windows 自带的慧慧、康康、瑶瑶，不联网）用浏览器自带的朗读；
   - 声音只在内存里，最多 8 句（提前准备后面 4 句），停止就清空；每节课读到哪里记在本机（最多 200 条、180 天）。 */
const TTS = (() => {
  const RATES = [0.75, 1, 1.25, 1.5, 1.75, 2];
  const ENGINES = { edge: 'Edge 在线朗读（推荐，声音自然）', baidu: '百度朗读（联网）', system: '系统声音（离线）' };
  const HL = { yellow: ['黄色', 'rgba(255, 208, 0, .45)'], green: ['绿色', 'rgba(46, 204, 113, .42)'], blue: ['蓝色', 'rgba(64, 156, 255, .42)'],
    orange: ['橙色', 'rgba(255, 140, 0, .45)'], purple: ['紫色', 'rgba(170, 102, 255, .45)'] };
  const ENDERS = /[。！？!?；;…]/, CLOSERS = /[”’」』）)】》〉"'\]]/, SOFT = /[，,、：:—]/, SPEAKABLE = /[\p{L}\p{N}]/u, MAX = 80;
  const CACHE_LIMIT = 8, PREFETCH = 4, RESUME_FOLLOW = 6000, RETRY = [600, 1500], BOTTOM_GAP = 60;
  const PROGRESS_KEY = 'tts-progress', PROGRESS_LIMIT = 200, PROGRESS_DAYS = 180;
  const BLOCK = /^(P|LI|H[1-6]|TD|TH|BLOCKQUOTE|DIV|DT|DD|TR|UL|OL|TABLE|SECTION|BR|HR)$/;
  const SKIP = 'a.tslink, a.cite, a.pglink, pre, img, svg, button, script, style, .msg.user, .doubt-tag, .msg-actions, .tts-skip';
  const ICON = {
    prev: '<path d="M19 20L9 12l10-8z"/><path d="M5 19V5"/>', next: '<path d="M5 4l10 8-10 8z"/><path d="M19 5v14"/>',
    play: '<path d="M7 4l13 8-13 8z"/>', pause: '<path d="M7 4h4v16H7zM14 4h4v16h-4z"/>', again: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    voice: '<path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
    locate: '<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/><circle cx="12" cy="12" r="8"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>', close: '<path d="M6 6l12 12M18 6L6 18"/>',
  };
  const svg = k => `<svg viewBox="0 0 24 24">${ICON[k]}</svg>`;
  const cache = new Map();
  let s = null, token = 0;

  const pref = (k, d) => (S.settings && S.settings[k] != null && S.settings[k] !== '' ? S.settings[k] : d);
  const engine = () => (ENGINES[pref('tts_engine', 'edge')] ? pref('tts_engine', 'edge') : 'edge');
  const rate = () => +pref('tts_rate', 1) || 1;
  const hasHighlight = () => !!(window.CSS && CSS.highlights && window.Highlight);

  /* ---------- 切句（照 speech-text.mjs：按句末标点切，超过 80 字在逗号等处切开；每个字记住它在页面上的位置） ---------- */
  const isTs = n => !!n && n.nodeType === 1 && n.matches('a.tslink, a.cite, a.pglink');  // 书的页码（P12、PDF 第 3 页）也不读
  function segments(root) {
    const out = [];
    let chars = [];
    const flush = () => { if (chars.length) out.push(...split(chars)); chars = []; };
    const walk = node => {
      if (node.nodeType === 3) { const t = node.nodeValue; for (let k = 0; k < t.length; k++) chars.push([t[k], node, k]); return; }
      if (node.nodeType !== 1 || node.matches(SKIP) || node.hidden) return;
      const block = BLOCK.test(node.tagName);
      if (block) flush();
      for (const c of node.childNodes) {
        // [01:42]-[02:23]：两个时间中间的横杠也不读
        if (c.nodeType === 3 && /^\s*[-–—~～至到]\s*$/.test(c.nodeValue) && isTs(c.previousSibling) && isTs(c.nextSibling)) continue;
        walk(c);
      }
      if (block) flush();
    };
    walk(root);
    flush();
    return out;
  }
  function split(chars) {
    const pieces = [];
    let cur = [];
    const close = () => { if (cur.length) pieces.push(cur); cur = []; };
    for (let k = 0; k < chars.length; k++) {
      cur.push(chars[k]);
      const ch = chars[k][0];
      const english = ch === '.' && (k + 1 === chars.length || /\s/.test(chars[k + 1][0])) && /[A-Za-z]/.test((chars[k - 1] || [''])[0]);
      if (ENDERS.test(ch) || english) {
        while (k + 1 < chars.length && (CLOSERS.test(chars[k + 1][0]) || ENDERS.test(chars[k + 1][0]))) cur.push(chars[++k]);
        close();
      }
    }
    close();
    const res = [];
    for (const piece of pieces) for (const part of limit(piece)) {
      let a = 0, b = part.length;
      while (a < b && /[\s，]/.test(part[a][0])) a++;
      while (b > a && /[\s，]/.test(part[b - 1][0])) b--;
      const kept = part.slice(a, b);
      const text = kept.map(c => c[0]).join('').replace(/\s+/g, ' ');
      if (!SPEAKABLE.test(text)) continue;
      res.push({ text, a: [kept[0][1], kept[0][2]], b: [kept[kept.length - 1][1], kept[kept.length - 1][2] + 1] });
    }
    return res;
  }
  function limit(piece) {
    const parts = [];
    while (piece.length > MAX) {
      let cut = -1;
      for (let k = MAX - 1; k >= MAX / 3; k--) if (SOFT.test(piece[k][0]) || piece[k][0] === ' ') { cut = k + 1; break; }
      if (cut < 0) cut = MAX;
      parts.push(piece.slice(0, cut)); piece = piece.slice(cut);
    }
    if (piece.length) parts.push(piece);
    return parts;
  }
  function rangeOf(seg) {
    if (!seg || !seg.a[0].isConnected || !seg.b[0].isConnected) return null;
    const r = document.createRange();
    try { r.setStart(seg.a[0], seg.a[1]); r.setEnd(seg.b[0], seg.b[1]); } catch (_) { return null; }
    return r;
  }

  /* ---------- 读哪里：课程重点页读课程重点，AI 对话页读 AI 的回答（不读你自己的提问） ---------- */
  function source() {
    if (S.view === 'mentor') {  // 老师工作台：读老师的回答（mentor.js）
      const cid = typeof M !== 'undefined' && M.chat ? M.chat.id : 'new';
      return { root: $('#mentorMsgs'), pane: 'mentor:' + cid, name: '老师的回答', anchor: $('.mentor-head') };
    }
    if (S.mode === 'book') {  // 书：读右边正在看的那一章 / 全书总览（2026-10-29 用户要求，和课程重点一样）
      const i = S.bookSel ? S.bookSel.i : -1;
      return { root: $('#bkView'), pane: 'book:' + i, name: i === -2 ? '全书总览' : '这一章', anchor: $('#bkView').previousElementSibling };
    }
    const tab = ($('.ai-tabs .tab.active') || {}).dataset || {};
    if (tab.tab === 'chat') return { root: $('#chatBox'), pane: 'chat', name: 'AI 对话' };
    return { root: $('#summaryBox'), pane: 'summary', name: '课程重点' };
  }

  /* ---------- 读到哪里（本机记住，最多 200 条、180 天） ---------- */
  function loadProgress() {
    try {
      const all = JSON.parse(localStorage.getItem(PROGRESS_KEY) || '{}'), cut = Date.now() - PROGRESS_DAYS * 86400000;
      const kept = Object.entries(all).filter(([, v]) => v && v.at > cut).sort((x, y) => y[1].at - x[1].at).slice(0, PROGRESS_LIMIT);
      return Object.fromEntries(kept);
    } catch (_) { return {}; }
  }
  function saveProgress(done) {
    if (!s) return;
    try {
      const all = loadProgress(), seg = s.segs[s.i];
      all[s.key] = { i: s.i, t: seg ? seg.text.slice(0, 30) : '', d: !!done, at: Date.now() };
      localStorage.setItem(PROGRESS_KEY, JSON.stringify(all));
    } catch (_) { /* 本机存不了就不记 */ }
  }
  function resumeIndex(key, segs) {
    const p = loadProgress()[key];
    if (!p || p.d) return 0;
    let best = -1;
    segs.forEach((x, k) => { if (x.text.startsWith(p.t) && (best < 0 || Math.abs(k - p.i) < Math.abs(best - p.i))) best = k; });
    return best >= 0 ? best : Math.min(p.i, segs.length - 1);
  }

  /* ---------- 开始、暂停、停止 ---------- */
  // 某一块是谁在读：原书的文字版由 app.js 的 bookSrcSource 给（读原文），其他按当前所在的区域
  function sourceFor(root) {
    if (root === $('#bkSrcText') && typeof bookSrcSource === 'function') return bookSrcSource();
    const src = source();
    return src.root === root ? src : null;
  }
  function start(mode, src = source()) {
    if (!src.root || (!S.path && S.view !== 'mentor')) return toast('先打开一节课');
    const segs = segments(src.root);
    if (!segs.length) return toast(`${src.name}里还没有可以朗读的文字`);
    stop(true);
    const key = (S.view === 'mentor' ? '' : S.path) + '|' + src.pane;
    let i = 0;
    if (typeof mode === 'number') i = Math.max(0, Math.min(segs.length - 1, mode));  // 双击的那一句
    else if (mode === 'here') i = firstVisible(segs);
    else if (mode !== 'start') i = resumeIndex(key, segs);
    s = { root: src.root, anchor: src.anchor, key, name: src.name, segs, i, state: 'playing', follow: true, handScroll: 0, programmatic: 0, cleanup: [], onClose: src.onClose, src };
    const v = $('#video'); if (v && !v.paused) v.pause();  // 边听边看：先停下视频
    mount();
    listen();
    speak();
  }
  function firstVisible(segs) {
    const sc = scrollParent(segs[0].a[0].parentElement);
    const top = sc ? sc.getBoundingClientRect().top : 0;
    const k = segs.findIndex(x => { const r = rangeOf(x); return r && r.getBoundingClientRect().bottom > top + 4; });
    return k < 0 ? 0 : k;
  }
  function toggle() {
    if (!s) return start();
    if (s.state === 'playing') {
      s.state = 'paused';
      if (engine() === 'system') { token++; speechSynthesis.cancel(); }  // 系统声音的暂停 = 取消，继续时重读这一句（各平台 pause() 不可靠）
      else if (s.audio) s.audio.pause();
      saveProgress(); render();
    } else if (s.state === 'ended') { s.i = 0; speak(); }
    else if (engine() !== 'system' && s.audio && s.audioI === s.i) { s.state = 'playing'; s.audio.play().catch(() => speak()); render(); }
    else speak();
  }
  function step(d) { if (!s) return; s.i = Math.max(0, Math.min(s.segs.length - 1, s.i + d)); speak(); }
  function stop(quiet) {
    token++;
    if (s) { saveProgress(s.state === 'ended'); s.cleanup.forEach(f => f()); if (s.bar) s.bar.remove(); }
    stopAudio();
    try { speechSynthesis.cancel(); } catch (_) { /* 没有系统朗读 */ }
    if (hasHighlight()) CSS.highlights.delete('tts-speaking');
    for (const p of cache.values()) p.then(u => URL.revokeObjectURL(u)).catch(() => {});
    cache.clear();
    s = null;
    if (!quiet) updateBtn();
  }
  function stopAudio() {
    if (s && s.audio) { s.audio.onended = s.audio.onerror = null; s.audio.pause(); s.audio = null; }
  }
  function finish() {
    s.state = 'ended'; saveProgress(true);
    if (hasHighlight()) CSS.highlights.delete('tts-speaking');
    render();
  }

  /* ---------- 播放 ---------- */
  function speak() {
    if (!s) return;
    const seg = s.segs[s.i];
    if (!seg) return finish();
    if (!rangeOf(seg)) { stop(); return toast('内容已经更新，朗读停止了'); }  // 例如重新生成了课程重点
    const my = ++token;
    s.state = 'playing';
    stopAudio();
    highlight(); render(); saveProgress();
    if (engine() === 'system') speakSystem(seg, my);
    else { speakOnline(seg, my); prefetch(); }
  }
  function next() { if (!s) return; s.i++; speak(); }
  async function speakOnline(seg, my) {
    let url;
    try { url = await fetchAudio(seg.text); } catch (e) {
      if (my !== token || !s) return;
      s.state = 'paused'; render();
      toast(`${engine() === 'edge' ? 'Edge 在线朗读' : '百度朗读'}这一句没取到声音（已自动重试 2 次），已暂停在这一句。网络恢复后点播放继续；一直不行可以在「声音」里改用别的来源。（${e.message}）`, { error: true, ms: 12000 });
      return;
    }
    if (my !== token || !s) return;
    const a = new Audio(url);
    a.playbackRate = rate();
    s.audio = a; s.audioI = s.i;
    a.onended = () => { if (my === token) next(); };
    a.onerror = () => { if (my === token) next(); };
    a.play().catch(() => {});
  }
  function fetchAudio(text) {
    const voice = engine() === 'edge' ? pref('tts_voice', '') : '', voiceEn = engine() === 'edge' ? pref('tts_voice_en', '') : '';
    const key = engine() + '|' + voice + '|' + voiceEn + '|' + text;
    if (cache.has(key)) return cache.get(key);
    const eng = engine();
    const job = (async () => {
      let last;
      for (let k = 0; k <= RETRY.length; k++) {
        if (k) await sleep(RETRY[k - 1]);
        try {
          const r = await api('tts', { engine: eng, text, voice, voice_en: voiceEn });  // 英文句子后台自动换英文声音
          const bin = atob(r.audio), u8 = new Uint8Array(bin.length);
          for (let j = 0; j < bin.length; j++) u8[j] = bin.charCodeAt(j);
          return URL.createObjectURL(new Blob([u8], { type: r.mime || 'audio/mpeg' }));
        } catch (e) { last = e; }
      }
      throw last;
    })();
    cache.set(key, job);
    job.catch(() => cache.delete(key));
    while (cache.size > CACHE_LIMIT) {  // 只留最近几句，旧的释放掉
      const old = cache.keys().next().value, p = cache.get(old);
      cache.delete(old); p.then(u => URL.revokeObjectURL(u)).catch(() => {});
    }
    return job;
  }
  // 提前准备后面几句：Edge 一句一般要 2 秒左右，偶尔更久（后台还会超时重发）
  function prefetch() { for (let k = 1; k <= PREFETCH; k++) { const x = s.segs[s.i + k]; if (x) fetchAudio(x.text).catch(() => {}); } }
  function sysVoice(english) {
    const all = speechSynthesis.getVoices(), want = pref('tts_sys_voice', '');
    if (english) return all.find(v => /^en-US/i.test(v.lang)) || all.find(v => /^en/i.test(v.lang)) || null;  // 英文句子：系统里的英文声音
    return all.find(v => v.name === want) || all.find(v => /^zh/i.test(v.lang)) || null;
  }
  // 英文句子（没有汉字、有英文单词）换英文声音（2026-10-29：外文书朗读原文，中文声音读英文很难听）；中文句子里夹英文词还是中文声音
  const isEnglish = t => !/[㐀-鿿]/.test(t) && /[A-Za-z]{2,}/.test(t);
  function speakSystem(seg, my) {
    if (!window.speechSynthesis) { s.state = 'paused'; render(); return toast('这台电脑没有系统朗读，请在「声音」里改用 Edge 在线朗读', { error: true }); }
    const go = () => {
      if (my !== token || !s) return;
      const u = new SpeechSynthesisUtterance(seg.text);
      const en = isEnglish(seg.text);
      u.lang = en ? 'en-US' : 'zh-CN'; u.rate = rate();
      const v = sysVoice(en); if (v) u.voice = v;
      u.onend = () => { if (my === token && s && s.state === 'playing') next(); };
      speechSynthesis.speak(u);
    };
    // 取消后马上说，有的系统会把新的一句也吞掉
    if (speechSynthesis.speaking || speechSynthesis.pending) { speechSynthesis.cancel(); setTimeout(go, 60); } else go();
  }

  /* ---------- 高亮和跟随滚动（快读到看得见区域的底部时自动往下翻） ---------- */
  function scrollParent(el) {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const st = getComputedStyle(n);
      if (/(auto|scroll)/.test(st.overflowY) && n.scrollHeight > n.clientHeight + 1) return n;
    }
    return null;
  }
  function highlight() {
    if (!s) return;
    if (!s.follow && s.handScroll && Date.now() - s.handScroll > RESUME_FOLLOW) s.follow = true;
    const r = rangeOf(s.segs[s.i]);
    if (!r) return;
    document.documentElement.style.setProperty('--tts-hl', (HL[pref('tts_hl', 'yellow')] || HL.yellow)[1]);
    if (hasHighlight()) CSS.highlights.set('tts-speaking', new Highlight(r));
    if (s.follow) reveal(r);
  }
  function reveal(r) {
    const sc = scrollParent(r.startContainer.parentElement);
    if (!sc) return;
    const rect = r.getBoundingClientRect(), box = sc.getBoundingClientRect();
    if (rect.top >= box.top + 40 && rect.bottom <= box.bottom - BOTTOM_GAP) return;
    s.programmatic = Date.now();
    sc.scrollBy({ top: rect.top - box.top - box.height / 3, behavior: 'smooth' });
  }
  function listen() {
    const on = (el, type, fn, opt) => { el.addEventListener(type, fn, opt); s.cleanup.push(() => el.removeEventListener(type, fn, opt)); };
    // 只有真的用滚轮滚动才停止跟随；停手 6 秒自动恢复
    on(s.root, 'wheel', () => {
      if (Date.now() - s.programmatic < 500) return;
      s.handScroll = Date.now();
      if (s.follow) { s.follow = false; render(); }
    }, { passive: true });
  }

  /* ---------- 播放条 ---------- */
  function mount() {
    const bar = s.bar = document.createElement('div');
    bar.className = 'tts-bar';
    bar.innerHTML = `<span class="tts-name">朗读${esc(s.name)}</span>
      <button class="btn icon-only small" data-t="prev" title="上一句">${svg('prev')}</button>
      <button class="btn icon-only small tts-main" data-t="play" title="暂停"></button>
      <button class="btn icon-only small" data-t="next" title="下一句">${svg('next')}</button>
      <button class="btn small tts-rate" data-t="rate" title="语速"></button>
      <button class="btn icon-only small" data-t="voice" title="声音">${svg('voice')}</button>
      <button class="btn icon-only small tts-locate" data-t="locate" title="回到朗读位置">${svg('locate')}</button>
      <button class="btn icon-only small" data-t="more" title="更多">${svg('more')}</button>
      <button class="btn icon-only small" data-t="close" title="关闭朗读">${svg('close')}</button>
      <div class="tts-progress"><i></i></div>`;
    (s.anchor || $('.ai-tabs')).after(bar);
    bar.onclick = e => {
      const b = e.target.closest('[data-t]'); if (!b) return;
      e.stopPropagation();  // 不然这次点击传到窗口，会把刚打开的语速 / 更多菜单马上关掉（2026-10-21 用户报告点了没反应）
      const t = b.dataset.t, r = b.getBoundingClientRect();
      if (t === 'prev') step(-1);
      else if (t === 'next') step(1);
      else if (t === 'play') toggle();
      else if (t === 'close') { const f = s.onClose; stop(); if (f) f(); }
      else if (t === 'locate') { s.follow = true; s.handScroll = 0; highlight(); render(); }
      else if (t === 'rate') rateMenu(r);
      else if (t === 'voice') voiceMenu(r);
      else if (t === 'more') moreMenu(r);
    };
  }
  function render() {
    updateBtn();
    if (!s || !s.bar) return;
    const play = $('[data-t=play]', s.bar), icon = s.state === 'playing' ? 'pause' : s.state === 'ended' ? 'again' : 'play';
    if (play.dataset.icon !== icon) { play.innerHTML = svg(icon); play.dataset.icon = icon; }
    play.title = s.state === 'playing' ? '暂停（也可以双击正在读的句子）' : s.state === 'ended' ? '重新朗读' : '继续';
    $('[data-t=rate]', s.bar).textContent = rate() + '×';
    s.bar.dataset.follow = String(s.follow);
    const total = s.segs.reduce((n, x) => n + x.text.length, 0) || 1;
    const read = s.state === 'ended' ? total : s.segs.slice(0, s.i).reduce((n, x) => n + x.text.length, 0);
    $('.tts-progress i', s.bar).style.width = (Math.round(read / total * 1000) / 10) + '%';
  }
  function updateBtn() {
    const b = $('#btnTts'); if (!b) return;
    b.classList.toggle('primary', !!s && s.state === 'playing');
    b.title = s && s.state === 'playing' ? '暂停朗读' : '朗读（边听边看）：课程重点页读课程重点，AI 对话页读 AI 的回答';
    const bk = $('#bkTts');
    if (bk) { bk.classList.toggle('primary', !!s && s.state === 'playing' && s.root === $('#bkView')); bk.title = s && s.state === 'playing' ? '暂停朗读' : '朗读（边听边看）：读这一章整理好的内容；双击任意一句从那里读'; }
    const bs = $('#bkSrcTts');
    if (bs) bs.classList.toggle('primary', !!s && s.state === 'playing' && s.root === $('#bkSrcText'));
  }
  const menuItem = (label, on, attr) => `<button class="menu-item" ${attr}>${on ? '✓ ' : '<span class="tts-pad"></span>'}${esc(label)}</button>`;
  function rateMenu(r) {
    showCtx(r.left, r.bottom + 4, RATES.map(x => menuItem(x + '×', x === rate(), `data-rate="${x}"`)).join(''), m => {
      $$('[data-rate]', m).forEach(el => el.onclick = () => { m.hidden = true; setRate(+el.dataset.rate); });
    });
  }
  async function voiceMenu(r) {
    let edge = [], edgeEn = [];
    try { ({ edge, edge_en: edgeEn = [] } = await api('tts_voices')); } catch (_) { /* 后台没有 */ }
    const eng = engine();
    let html = '<div class="menu-label">朗读来源</div>' + Object.entries(ENGINES).map(([k, v]) => menuItem(v, k === eng, `data-eng="${k}"`)).join('');
    if (eng === 'edge') {
      const cur = pref('tts_voice', (edge[0] || {}).id), curEn = pref('tts_voice_en', (edgeEn[0] || {}).id);
      html += '<div class="menu-label">声音</div>' + edge.map(v => menuItem(v.name, v.id === cur, `data-voice="${esc(v.id)}"`)).join('')
        + (edgeEn.length ? '<div class="menu-label">英文句子用的声音（自动切换）</div>' + edgeEn.map(v => menuItem(v.name, v.id === curEn, `data-voice-en="${esc(v.id)}"`)).join('') : '');
    } else if (eng === 'system' && window.speechSynthesis) {
      const all = speechSynthesis.getVoices(), zh = all.filter(v => /^zh/i.test(v.lang)), cur = pref('tts_sys_voice', '');
      html += '<div class="menu-label">声音（英文句子自动用系统里的英文声音）</div>' + menuItem('自动选中文声音', !cur, 'data-sys=""')
        + (zh.length ? zh : all).map(v => menuItem(v.name.replace(/^Microsoft /, '').replace(/ - .*$/, ''), v.name === cur, `data-sys="${esc(v.name)}"`)).join('');
    } else if (eng === 'baidu') html += '<div class="menu-label">百度朗读只有一种声音（英文句子自动用英文读）；正在读的句子会发给百度翻译</div>';
    showCtx(r.left, r.bottom + 4, html, m => {
      $$('[data-eng]', m).forEach(el => el.onclick = () => { m.hidden = true; change({ tts_engine: el.dataset.eng }); });
      $$('[data-voice]', m).forEach(el => el.onclick = () => { m.hidden = true; change({ tts_voice: el.dataset.voice }); });
      $$('[data-voice-en]', m).forEach(el => el.onclick = () => { m.hidden = true; change({ tts_voice_en: el.dataset.voiceEn }); });
      $$('[data-sys]', m).forEach(el => el.onclick = () => { m.hidden = true; change({ tts_sys_voice: el.dataset.sys }); });
    });
  }
  function moreMenu(r) {
    const cur = pref('tts_hl', 'yellow');
    showCtx(r.left, r.bottom + 4, menuItem('从头朗读', false, 'data-from="start"') + menuItem('从当前屏幕开始朗读', false, 'data-from="here"')
      + '<div class="menu-label">高亮颜色</div>' + Object.entries(HL).map(([k, v]) => menuItem(v[0], k === cur, `data-hl="${k}"`)).join(''), m => {
      $$('[data-from]', m).forEach(el => el.onclick = () => { m.hidden = true; start(el.dataset.from, (s && s.src) || source()); });
      $$('[data-hl]', m).forEach(el => el.onclick = () => { m.hidden = true; savePrefs({ tts_hl: el.dataset.hl }); highlight(); });
    });
  }
  function setRate(x) {
    savePrefs({ tts_rate: x });
    if (!s) return;
    if (s.audio) s.audio.playbackRate = x;  // 在线声音直接调播放速度，不用重新取
    else if (engine() === 'system' && s.state === 'playing') speak();  // 系统声音从这一句重新读
    render();
  }
  function change(patch) {  // 换来源或声音：从这一句重新读
    savePrefs(patch);
    if (!s) return;
    for (const p of cache.values()) p.then(u => URL.revokeObjectURL(u)).catch(() => {});
    cache.clear();
    if (s.state === 'playing') speak(); else render();
  }

  // 朗读按钮：正在读的就是这一块 → 暂停 / 继续；否则从这一块开始读
  function button(src = source()) { if (s && s.root === src.root && s.key.endsWith('|' + src.pane)) toggle(); else start(undefined, src); }
  $('#btnTts').onclick = () => button();
  $('#bkTts').onclick = () => button();
  /* 双击某一句：从这一句开始读（没在朗读也直接开始）；双击正在读的那一句：暂停 / 继续（和插件「听书」一样）。
     单击留给选字、复制、点时间跳视频，所以用双击（2026-10-21 用户要求，阅读状态下没有光标，没法指定从哪读）。 */
  function sentenceAt(segs, x, y) {
    const p = document.caretRangeFromPoint && document.caretRangeFromPoint(x, y);
    if (p) {
      const k = segs.findIndex(seg => { const r = rangeOf(seg); try { return !!r && r.comparePoint(p.startContainer, p.startOffset) === 0; } catch (_) { return false; } });
      if (k >= 0) return k;
    }
    return segs.findIndex(seg => { const r = rangeOf(seg); return !!r && [...r.getClientRects()].some(b => x >= b.left - 2 && x <= b.right + 2 && y >= b.top - 2 && y <= b.bottom + 2); });
  }
  for (const id of ['#summaryBox', '#chatBox', '#bkView', '#bkSrcText']) $(id).addEventListener('dblclick', e => {
    if (e.target.closest('a.tslink, a.pglink, button, img, svg, pre, .msg.user, .tts-skip')) return;
    const root = $(id), src = sourceFor(root);
    if (!src) return;
    const live = s && s.root === root, segs = live ? s.segs : segments(root);
    const k = sentenceAt(segs, e.clientX, e.clientY);
    if (k < 0) return;
    setTimeout(() => { try { getSelection().removeAllRanges(); } catch (_) { /* 没选中 */ } }, 30);  // 双击会顺手选中一个词
    if (!live) return start(k, src);
    if (k === s.i && s.state !== 'ended') return toggle();
    s.i = k; s.follow = true; s.handScroll = 0;
    speak();
  });
  if (window.speechSynthesis) speechSynthesis.getVoices();  // 系统声音列表是异步加载的，先触发一次
  return { start, toggle, stop, button, segments, isEnglish, active: () => s, _state: () => s && { i: s.i, state: s.state, follow: s.follow, n: s.segs.length } };
})();
