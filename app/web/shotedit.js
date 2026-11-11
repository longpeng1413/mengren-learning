/* 编辑截图（2026-11-11 用户要求：把猛人截图（D:\mengren-desktop，猛人桌面助手 F1）的图片编辑搬进来，代替以前的「AI 重画」）。
   字幕栏、课程重点里插的截图下面「✏ 编辑」打开：裁剪、镜像、画笔、箭头、直线、圆形、方框、实心框、文字、橡皮、粗细、字号、颜色、
   撤销 / 恢复 / 清除、翻译（PP-OCRv6 认字 + 有道 / AI 翻译，盖在原处）、复制、保存。按钮做小（用户：按钮大了图上空间不够），
   放在图片下面那一行正中间（同一天用户要求；颜色、粗细、字号菜单往上弹）。
   快捷键照猛人截图：右键撤销、Ctrl+右键恢复、Ctrl+Z / Ctrl+Y、Delete 删选中的文字（没选中时清除全部）、写字时 Enter 写完 / Esc 取消；
   另外 Ctrl+C 复制、Ctrl+S 另存。

   openShotEdit(截图时间, {crop, marks}) → Promise：点「完成」给 {crop, marks}，取消给 null。原截图从来不改：
   crop = [左, 上, 右, 下]（比例），marks = {wh: [宽, 高], flip, items: [...]}（坐标是镜像后整张截图的像素，裁剪最后做）。
   条目格式、画法和后台 core/shotedit.py 一样（显示、打印、剪藏都由后台按数据画）：**改了这里的画法（线宽、箭头、文字行距和基线）
   要一起改 shotedit.py 的 draw_marks**。线宽、字号按截图宽度换算（seK：1280 宽 = 1 倍），大图小图看起来差不多粗。 */
const SE_LH = 1.3;  // 文字行距 = 字号 × 1.3（core/shotedit.py 的 TEXT_LH）
const SE_WIDTHS = [['细', 2], ['默认', 3], ['中', 5], ['粗', 8], ['特粗', 12]];
const SE_SIZES = [['小号', 16], ['中号', 24], ['大号', 36], ['特大', 52]];
const SE_COLORS = ['#ff3b30', '#ff9500', '#ffcc00', '#34c759', '#00a2ff', '#5856d6', '#ff2d92', '#000000', '#ffffff', '#8e8e93'];
const SE_SHAPES = ['arrow', 'line', 'ellipse', 'rect', 'fill'];
const SE_UNDO_MAX = 100;
const SE = { tool: '', color: '#ff3b30', width: 5, size: 24, crop: [0, 0, 1, 1], flip: false, items: [], undo: [], redo: [], sel: -1, edit: null,
  draw: null, img: null, W: 0, H: 0, scale: 1, t: 0, resolve: null, escAt: 0 };

/* 标注的短指纹：界面缓存截图地址时区分「同一张图、不同标注」 */
function marksKey(m) {
  if (!m) return '';
  const s = JSON.stringify(m);
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(36);
}
const seK = () => Math.max(0.6, SE.W / 1280);
const seLineW = () => +(SE.width * seK()).toFixed(1);
const seEraseW = () => +(Math.max(12, SE.width * 4) * seK()).toFixed(1);
const seTextS = () => +(SE.size * seK()).toFixed(1);
const seFull = c => c[0] <= 0.001 && c[1] <= 0.001 && c[2] >= 0.999 && c[3] >= 0.999;
const seCropOrNull = () => seFull(SE.crop) ? null : SE.crop.map(x => Math.round(x * 10000) / 10000);
const seMarks = () => (SE.items.length || SE.flip) ? Object.assign({ wh: [SE.W, SE.H], items: JSON.parse(JSON.stringify(SE.items)) }, SE.flip ? { flip: true } : {}) : null;
const seSnap = () => JSON.stringify({ items: SE.items, crop: SE.crop, flip: SE.flip });
function seRestore(s) { const o = JSON.parse(s); SE.items = o.items; SE.crop = o.crop; SE.flip = o.flip; SE.sel = -1; }
function seCommit(snap) {  // 改之前的样子存进撤销
  SE.undo.push(snap); if (SE.undo.length > SE_UNDO_MAX) SE.undo.shift();
  SE.redo = []; seSyncBar();
}
function seInfo(text, bad) { const el = $('#seInfo'); el.textContent = text || ''; el.title = text || ''; el.classList.toggle('bad', !!bad); }
function sePrefs(save) {
  try {
    if (save) localStorage.setItem('mr-shotedit', JSON.stringify({ tool: SE.tool === 'crop' ? '' : SE.tool, color: SE.color, width: SE.width, size: SE.size }));
    else Object.assign(SE, JSON.parse(localStorage.getItem('mr-shotedit') || '{}'));
  } catch (_) { /* 浏览器存储用不了：用默认的 */ }
}

function openShotEdit(t, edit) {
  return new Promise(resolve => {
    const dlg = $('#dlgShotEdit');
    sePrefs(false);
    const marks = edit && edit.marks;
    Object.assign(SE, { t, resolve, crop: edit && edit.crop ? edit.crop.slice() : [0, 0, 1, 1], flip: !!(marks && marks.flip),
      items: marks ? JSON.parse(JSON.stringify(marks.items || [])) : [], undo: [], redo: [], sel: -1, edit: null, draw: null, img: null, W: 0, H: 0 });
    $('#seText').hidden = true; $('#sePop').hidden = true;
    seInfo('正在取截图…'); seSyncBar();
    const cv = $('#seCanvas'); cv.width = 320; cv.height = 180; cv.style.width = '320px'; cv.style.height = '180px';
    const img = new Image();
    img.onload = () => {
      if (SE.resolve !== resolve) return;
      SE.img = img; SE.W = img.naturalWidth; SE.H = img.naturalHeight;
      if (marks && marks.wh && (marks.wh[0] !== SE.W || marks.wh[1] !== SE.H)) {  // 截图大小变了（一般不会）：按比例换算
        const kx = SE.W / marks.wh[0], ky = SE.H / marks.wh[1];
        SE.items.forEach(it => { if (it.k === 'text') { it.x *= kx; it.y *= ky; it.s *= (kx + ky) / 2; } else { it.p = it.p.map(([x, y]) => [x * kx, y * ky]); it.w *= (kx + ky) / 2; } });
      }
      seInfo(''); seLayout(); seRender();
    };
    img.onerror = () => seInfo('截图取不到', true);
    frameUrl(t, false).then(u => { if (u) img.src = u; else seInfo('截图取不到', true); });
    dlg.showModal();
  });
}
function seClose(ok) {
  const dlg = $('#dlgShotEdit');
  seFinishText(true);
  if (!ok && SE.undo.length && !confirm('放弃这次的修改？')) return;
  const res = ok ? { crop: seCropOrNull(), marks: seMarks() } : null;
  $('#sePop').hidden = true;
  dlg.close();
  const r = SE.resolve; SE.resolve = null; SE.img = null; SE.draw = null;
  sePrefs(true);
  if (r) r(res);
}

/* ---- 画 ---- */
function seLayout() {
  const dlg = $('#dlgShotEdit'), cv = $('#seCanvas');
  if (!SE.W) return;
  const footH = $('.se-foot', dlg).offsetHeight;  // 工具栏在底部这一行里
  const availW = Math.max(200, window.innerWidth * 0.98 - 40), availH = Math.max(150, window.innerHeight * 0.98 - footH - 50);
  SE.scale = Math.min(availW / SE.W, availH / SE.H, 2.5);
  cv.width = SE.W; cv.height = SE.H;
  cv.style.width = Math.round(SE.W * SE.scale) + 'px'; cv.style.height = Math.round(SE.H * SE.scale) + 'px';
  seBoxDraw();
}
function seBoxDraw() {
  const [l, t, r, b] = SE.crop, box = $('#seBox');
  Object.assign(box.style, { left: l * 100 + '%', top: t * 100 + '%', width: (r - l) * 100 + '%', height: (b - t) * 100 + '%' });
  box.classList.toggle('full', seFull(SE.crop));
}
const seFont = s => `${s}px "Microsoft YaHei", sans-serif`;
function seAscent(ctx, s) { ctx.font = seFont(s); const m = ctx.measureText('国Ag'); return m.fontBoundingBoxAscent || s * 1.06; }
function seDrawShape(c, it) {
  const p = it.p; if (!p || !p.length) return;
  c.save();
  c.lineCap = 'round'; c.lineJoin = 'round'; c.lineWidth = it.w;
  c.strokeStyle = c.fillStyle = it.c || '#000';
  if (it.k === 'erase') c.globalCompositeOperation = 'destination-out';
  if (it.k === 'pen' || it.k === 'erase') {
    c.beginPath();
    if (p.length === 1) { c.arc(p[0][0], p[0][1], it.w / 2, 0, Math.PI * 2); c.fill(); }
    else { c.moveTo(p[0][0], p[0][1]); for (let i = 1; i < p.length; i++) c.lineTo(p[i][0], p[i][1]); c.stroke(); }
  } else {
    const [a, b] = [p[0], p[p.length - 1]];
    const x = Math.min(a[0], b[0]), y = Math.min(a[1], b[1]), w = Math.abs(b[0] - a[0]), h = Math.abs(b[1] - a[1]);
    c.beginPath();
    if (it.k === 'line' || it.k === 'arrow') {
      c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]);
      const dx = b[0] - a[0], dy = b[1] - a[1];
      if (it.k === 'arrow' && dx * dx + dy * dy >= 16) {  // 箭头两边：长 max(线宽×4, 12)，张开 0.55 弧度（和猛人截图一样）
        const ang = Math.atan2(dy, dx), L = Math.max(it.w * 4, 12);
        for (const s of [-0.55, 0.55]) { c.moveTo(b[0], b[1]); c.lineTo(b[0] - L * Math.cos(ang + s), b[1] - L * Math.sin(ang + s)); }
      }
      c.stroke();
    } else if (it.k === 'ellipse') { c.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2); c.stroke(); }
    else if (it.k === 'rect') { c.lineJoin = 'miter'; c.strokeRect(x, y, w, h); }
    else if (it.k === 'fill') c.fillRect(x, y, w, h);
  }
  c.restore();
}
function seDrawText(ctx, it) {
  ctx.save();
  const asc = seAscent(ctx, it.s);
  ctx.fillStyle = it.c; ctx.textBaseline = 'alphabetic';
  it.t.split('\n').forEach((ln, n) => ctx.fillText(ln, it.x, it.y + asc + n * it.s * SE_LH));
  ctx.restore();
}
function seTextBox(it) {
  const ctx = $('#seCanvas').getContext('2d'), lines = it.t.split('\n');
  ctx.save(); ctx.font = seFont(it.s);
  const w = Math.max(it.s * 0.5, ...lines.map(l => ctx.measureText(l).width));
  ctx.restore();
  return [it.x, it.y, it.x + w, it.y + lines.length * it.s * SE_LH];
}
function seRender() {
  const cv = $('#seCanvas'), ctx = cv.getContext('2d'), W = SE.W, H = SE.H;
  if (!SE.img) return;
  ctx.clearRect(0, 0, W, H);
  ctx.save(); if (SE.flip) { ctx.translate(W, 0); ctx.scale(-1, 1); } ctx.drawImage(SE.img, 0, 0, W, H); ctx.restore();
  const L = SE.layer || (SE.layer = document.createElement('canvas'));
  L.width = W; L.height = H;  // 改大小 = 清空
  const lc = L.getContext('2d');
  SE.items.forEach(it => { if (it.k !== 'text') seDrawShape(lc, it); });
  if (SE.draw && SE.draw.item) seDrawShape(lc, SE.draw.item);
  ctx.drawImage(L, 0, 0);
  SE.items.forEach((it, i) => { if (it.k === 'text' && !(SE.edit && SE.edit.i === i)) seDrawText(ctx, it); });
  const sel = SE.items[SE.sel];
  if (sel && sel.k === 'text' && !SE.edit) {  // 选中的文字：蓝色虚线框
    const [l, t, r, b] = seTextBox(sel), m = 3 / SE.scale;
    ctx.save(); ctx.strokeStyle = '#2da9ff'; ctx.lineWidth = 1 / SE.scale; ctx.setLineDash([4 / SE.scale, 3 / SE.scale]);
    ctx.strokeRect(l - m, t - m, r - l + 2 * m, b - t + 2 * m); ctx.restore();
  }
  seBoxDraw();
}

/* ---- 文字 ---- */
function seHitText(p) {
  const m = 4 / SE.scale;
  for (let i = SE.items.length - 1; i >= 0; i--) {
    const it = SE.items[i]; if (it.k !== 'text') continue;
    const [l, t, r, b] = seTextBox(it);
    if (p[0] >= l - m && p[0] <= r + m && p[1] >= t - m && p[1] <= b + m) return i;
  }
  return -1;
}
function seStyleTa() {
  const ta = $('#seText'), it = SE.edit.it, sc = SE.scale;
  Object.assign(ta.style, { left: it.x * sc - 1 + 'px', top: it.y * sc - 1 + 'px', fontSize: it.s * sc + 'px', color: it.c });
  ta.style.width = '1px'; ta.style.width = ta.scrollWidth + it.s * sc * 0.6 + 'px';
  ta.style.height = '1px'; ta.style.height = ta.scrollHeight + 'px';
}
function seBeginText(i, p) {
  const s = seTextS();
  const it = i != null ? SE.items[i] : { k: 'text', c: SE.color, s, x: p[0], y: p[1] - s * SE_LH / 2, t: '' };  // 单击处是第一行的中间
  SE.edit = { i, it: Object.assign({}, it), snap: seSnap() };
  SE.sel = -1;
  const ta = $('#seText'); ta.hidden = false; ta.value = it.t;
  seStyleTa(); seRender();
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
}
function seFinishText(commit) {
  const e = SE.edit; if (!e) return;
  SE.edit = null;
  const ta = $('#seText'), t = ta.value.replace(/\s+$/, '');
  ta.hidden = true;
  if (commit) {
    if (e.i == null) { if (t.trim()) { seCommit(e.snap); SE.items.push(Object.assign(e.it, { t })); SE.sel = SE.items.length - 1; } }
    else if (!t.trim()) { seCommit(e.snap); SE.items.splice(e.i, 1); SE.sel = -1; }  // 字删光了 = 删掉这段字
    else if (t !== SE.items[e.i].t) { seCommit(e.snap); SE.items[e.i].t = t; SE.sel = e.i; }
    else SE.sel = e.i;
  }
  seRender();
}
function seEraseTexts(a, b, w) {  // 橡皮碰到的文字整段擦掉
  const m = w / 2, l = Math.min(a[0], b[0]) - m, r = Math.max(a[0], b[0]) + m, t = Math.min(a[1], b[1]) - m, bt = Math.max(a[1], b[1]) + m;
  const before = SE.items.length;
  SE.items = SE.items.filter(it => { if (it.k !== 'text') return true; const [x0, y0, x1, y1] = seTextBox(it); return x1 < l || x0 > r || y1 < t || y0 > bt; });
  if (SE.items.length !== before) SE.sel = -1;
}

/* ---- 按钮 ---- */
function seSyncBar() {
  $$('#seBar [data-tool]').forEach(b => b.classList.toggle('on', b.dataset.tool === SE.tool));
  $('#seStage').dataset.tool = SE.tool;
  $('#seBar [data-act="undo"]').disabled = !SE.undo.length;
  $('#seBar [data-act="redo"]').disabled = !SE.redo.length;
  $('#seSwatch').style.background = SE.color;
  const wl = SE_WIDTHS.find(x => x[1] === SE.width), sl = SE_SIZES.find(x => x[1] === SE.size);
  $('#seBar [data-menu="width"]').title = `线条粗细（现在：${wl ? wl[0] : SE.width}）`;
  $('#seBar [data-menu="size"]').title = `文字大小（现在：${sl ? sl[0] : SE.size}）`;
}
function seUndo() { seFinishText(true); if (!SE.undo.length) return; SE.redo.push(seSnap()); seRestore(SE.undo.pop()); seSyncBar(); seRender(); }
function seRedo() { seFinishText(true); if (!SE.redo.length) return; SE.undo.push(seSnap()); seRestore(SE.redo.pop()); seSyncBar(); seRender(); }
function seClear() {
  seFinishText(true);
  if (!SE.items.length) return seInfo('还没有画任何东西');
  seCommit(seSnap()); SE.items = []; SE.sel = -1; seRender(); seInfo('已清除全部标注（Ctrl+Z 可以撤销）');
}
function seMirror() {
  seFinishText(true); seCommit(seSnap());
  const W = SE.W;
  SE.items.forEach(it => {
    if (it.k === 'text') { const [l, , r] = seTextBox(it); it.x = +(W - it.x - (r - l)).toFixed(1); }  // 文字不翻转，只换到另一边
    else it.p = it.p.map(([x, y]) => [+(W - x).toFixed(1), y]);
  });
  const [l, t, r, b] = SE.crop; SE.crop = [1 - r, t, 1 - l, b];
  SE.flip = !SE.flip; seRender();
}
function seReset() {
  seFinishText(true);
  if (!SE.items.length && !SE.flip && seFull(SE.crop)) return seInfo('现在就是原图');
  seCommit(seSnap()); SE.items = []; SE.flip = false; SE.crop = [0, 0, 1, 1]; SE.sel = -1; seRender();
  seInfo('已恢复原图（Ctrl+Z 可以撤销）');
}
async function seTranslate(btn) {
  seFinishText(true);
  if (!SE.img || btn.disabled) return;
  btn.disabled = true; btn.classList.add('busy'); seInfo('正在认字、翻译…');
  const resolve = SE.resolve;
  try {
    const r = await api('shot_translate', { path: S.path, t: SE.t, flip: SE.flip, crop: seCropOrNull(), wh: [SE.W, SE.H] });
    if (SE.resolve !== resolve) return;  // 等的时候窗口关了
    seCommit(seSnap()); SE.items.push(...r.items); SE.sel = -1; seRender();
    seInfo(`已翻译 ${r.blocks} 段（${r.service}）：译文可以拖动、单击修改，Ctrl+Z 撤销`);
  } catch (e) { if (SE.resolve === resolve) seInfo(e.message || String(e), true); }
  finally { btn.disabled = false; btn.classList.remove('busy'); }
}
async function seExport(kind) {
  seFinishText(true);
  if (!SE.img) return;
  const body = { path: S.path, t: SE.t, crop: seCropOrNull(), marks: seMarks() };
  try {
    if (kind === 'copy') { seInfo('正在复制…'); await api('shot_copy', body); seInfo('已复制到剪贴板，可以直接粘贴到微信、Word、Obsidian'); }
    else {
      const r = await api('shot_save', Object.assign(body, { name: `${(S.doc && S.doc.title) || '截图'}_${fmt(SE.t).replace(/:/g, '-')}` }));
      if (r.path) seInfo('已保存：' + r.path);
    }
  } catch (e) { seInfo(e.message || String(e), true); }
}
function sePop(btn, kind) {
  const pop = $('#sePop');
  if (!pop.hidden && pop.dataset.kind === kind) { pop.hidden = true; return; }
  const sel = SE.items[SE.sel], selText = sel && sel.k === 'text';
  let html = '';
  if (kind === 'width') html = SE_WIDTHS.map(([n, v]) => `<button data-v="${v}" class="${v === SE.width ? 'on' : ''}"><svg width="28" height="12"><line x1="2" y1="6" x2="26" y2="6" stroke="currentColor" stroke-width="${Math.min(10, v)}" stroke-linecap="round"/></svg>${n} ${v}</button>`).join('');
  else if (kind === 'size') html = SE_SIZES.map(([n, v]) => `<button data-v="${v}" class="${v === SE.size ? 'on' : ''}">${n} ${v}</button>`).join('') + (selText ? '<label>选中的文字会一起改</label>' : '');
  else html = `<div class="se-colors">${SE_COLORS.map(c => `<button data-c="${c}" class="${c === SE.color ? 'on' : ''}" style="background:${c}" title="${c}"></button>`).join('')}</div>
    <label>其他颜色 <input type="color" value="${SE.color}"></label>${selText ? '<label>选中的文字会一起改</label>' : ''}`;
  pop.innerHTML = html; pop.dataset.kind = kind; pop.hidden = false;
  const dr = $('#dlgShotEdit').getBoundingClientRect(), br = btn.getBoundingClientRect();
  pop.style.left = Math.max(4, Math.min(br.left - dr.left, dr.width - pop.offsetWidth - 4)) + 'px';
  pop.style.top = Math.max(4, br.top - dr.top - pop.offsetHeight - 4) + 'px';  // 工具栏在底部：菜单往上弹
  const applyText = (k, v) => { const it = SE.items[SE.sel]; if (it && it.k === 'text' && it[k] !== v) { seCommit(seSnap()); it[k] = v; seRender(); } };
  const pickColor = c => { SE.color = c; applyText('c', c); seSyncBar(); $$('.se-colors button', pop).forEach(b => b.classList.toggle('on', b.dataset.c === c)); };
  $$('[data-v]', pop).forEach(b => b.onclick = () => {
    const v = +b.dataset.v;
    if (kind === 'width') SE.width = v; else { SE.size = v; applyText('s', +(v * seK()).toFixed(1)); }
    pop.hidden = true; seSyncBar();
  });
  $$('[data-c]', pop).forEach(b => b.onclick = () => { pickColor(b.dataset.c); pop.hidden = true; });
  const ci = $('input[type=color]', pop); if (ci) ci.onchange = () => pickColor(ci.value);
}
function seSetTool(t) {
  seFinishText(true);
  SE.tool = SE.tool === t ? '' : t;  // 再点一次同一个工具：放下工具（这时可以拖动文字）
  $('#sePop').hidden = true; seSyncBar(); seRender();
}

/* ---- 鼠标 ---- */
function bindShotEditor() {
  const dlg = $('#dlgShotEdit'), stage = $('#seStage'), cv = $('#seCanvas'), ta = $('#seText'), MIN = 0.03;
  const pt = e => { const r = cv.getBoundingClientRect(); return [+clamp((e.clientX - r.left) / r.width * SE.W, 0, SE.W).toFixed(1), +clamp((e.clientY - r.top) / r.height * SE.H, 0, SE.H).toFixed(1)]; };
  const pos = e => { const r = cv.getBoundingClientRect(); return [clamp((e.clientX - r.left) / r.width, 0, 1), clamp((e.clientY - r.top) / r.height, 0, 1)]; };
  $('#seBar').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b || b.disabled) return;
    if (b.dataset.tool) return seSetTool(b.dataset.tool);
    if (b.dataset.menu) { e.stopPropagation(); seFinishText(true); return sePop(b, b.dataset.menu); }
    $('#sePop').hidden = true;
    ({ mirror: seMirror, undo: seUndo, redo: seRedo, clear: seClear, translate: () => seTranslate(b), copy: () => seExport('copy'), save: () => seExport('save') })[b.dataset.act]();
  });
  dlg.addEventListener('pointerdown', e => { if (!e.target.closest('#sePop, [data-menu]')) $('#sePop').hidden = true; });
  stage.addEventListener('contextmenu', e => e.preventDefault());
  stage.addEventListener('pointerdown', e => {
    if (!SE.img || e.target === ta) return;
    if (e.button === 2) { e.preventDefault(); SE.draw = null; (e.ctrlKey ? seRedo : seUndo)(); return; }  // 右键撤销，Ctrl+右键恢复
    if (e.button !== 0) return;
    e.preventDefault();
    if (SE.edit) { seFinishText(true); return; }  // 正在写字时，点别处只是写完
    try { stage.setPointerCapture(e.pointerId); } catch (_) { /* 自测里的假事件没有真的指针 */ }
    const p = pt(e);
    if (SE.tool === 'crop') {
      const full = SE.crop[2] - SE.crop[0] > 0.98 && SE.crop[3] - SE.crop[1] > 0.98;  // 框还是整张图时，拖动是画新框
      SE.draw = { kind: 'crop', h: e.target.dataset.h || (e.target.closest('#seBox') && !full ? 'move' : 'new'), p: pos(e), c: SE.crop.slice(), snap: seSnap() };
      return;
    }
    if (SE.tool === 'text' || !SE.tool) {
      const i = seHitText(p);
      if (i >= 0) { SE.sel = i; SE.draw = { kind: 'textdrag', i, p0: p, start: [SE.items[i].x, SE.items[i].y], moved: false, snap: seSnap() }; seRender(); return; }
      if (SE.sel >= 0) { SE.sel = -1; seRender(); }
      if (SE.tool === 'text') seBeginText(null, p);
      return;
    }
    SE.sel = -1;
    if (SE.tool === 'pen' || SE.tool === 'erase') {
      const item = SE.tool === 'pen' ? { k: 'pen', c: SE.color, w: seLineW(), p: [p] } : { k: 'erase', w: seEraseW(), p: [p] };
      SE.draw = { kind: 'stroke', item, snap: seSnap() };
      if (item.k === 'erase') seEraseTexts(p, p, item.w);
    } else if (SE_SHAPES.includes(SE.tool)) SE.draw = { kind: 'shape', item: { k: SE.tool, c: SE.color, w: seLineW(), p: [p, p] }, snap: seSnap() };
    seRender();
  });
  stage.addEventListener('pointermove', e => {
    const d = SE.draw;
    if (!d) { if (SE.img && (!SE.tool || SE.tool === 'text')) stage.classList.toggle('over-text', seHitText(pt(e)) >= 0); return; }
    if (d.kind === 'crop') {
      const [x, y] = pos(e), dx = x - d.p[0], dy = y - d.p[1];
      let [l, t, r, b] = d.c;
      if (d.h === 'new') SE.crop = [Math.min(d.p[0], x), Math.min(d.p[1], y), Math.max(d.p[0], x), Math.max(d.p[1], y)];
      else if (d.h === 'move') { const w = r - l, h = b - t; l = clamp(l + dx, 0, 1 - w); t = clamp(t + dy, 0, 1 - h); SE.crop = [l, t, l + w, t + h]; }
      else {
        if (d.h.includes('w')) l = clamp(l + dx, 0, r - MIN);
        if (d.h.includes('e')) r = clamp(r + dx, l + MIN, 1);
        if (d.h.includes('n')) t = clamp(t + dy, 0, b - MIN);
        if (d.h.includes('s')) b = clamp(b + dy, t + MIN, 1);
        SE.crop = [l, t, r, b];
      }
      return seBoxDraw();
    }
    const p = pt(e);
    if (d.kind === 'textdrag') {
      const dx = p[0] - d.p0[0], dy = p[1] - d.p0[1];
      if (!d.moved && Math.hypot(dx, dy) * SE.scale < 3) return;
      d.moved = true;
      Object.assign(SE.items[d.i], { x: +(d.start[0] + dx).toFixed(1), y: +(d.start[1] + dy).toFixed(1) });
    } else if (d.kind === 'stroke') {
      const pts = d.item.p, last = pts[pts.length - 1];
      if (Math.hypot(p[0] - last[0], p[1] - last[1]) * SE.scale < 1.5) return;
      if (d.item.k === 'erase') seEraseTexts(last, p, d.item.w);
      pts.push(p);
    } else if (d.kind === 'shape') d.item.p[1] = p;
    seRender();
  });
  const end = e => {
    const d = SE.draw; if (!d) return;
    SE.draw = null;
    if (d.kind === 'crop') {
      const [l, t, r, b] = SE.crop;
      if (r - l < MIN || b - t < MIN) SE.crop = d.c;  // 只是点了一下：不改
      else if (SE.crop.join() !== d.c.join()) seCommit(d.snap);
      return seBoxDraw();
    }
    if (d.kind === 'textdrag') { if (d.moved) seCommit(d.snap); else if (e.type === 'pointerup') return seBeginText(d.i); }  // 单击（没拖动）= 修改这段字
    else if (d.kind === 'stroke') { seCommit(d.snap); SE.items.push(d.item); }
    else if (d.kind === 'shape') {
      const [a, b] = d.item.p;
      if (Math.hypot(b[0] - a[0], b[1] - a[1]) * SE.scale >= 3) { seCommit(d.snap); SE.items.push(d.item); }
    }
    seRender();
  };
  stage.addEventListener('pointerup', end);
  stage.addEventListener('pointercancel', end);
  // 写字：Enter 写完，Shift+Enter 换行，Esc 取消；点别处也算写完
  ta.addEventListener('input', () => { if (SE.edit) seStyleTa(); });
  ta.addEventListener('keydown', e => {
    e.stopPropagation();
    if (e.isComposing || e.keyCode === 229) return;  // 输入法正在选字
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); seFinishText(true); }
    else if (e.key === 'Escape') { e.preventDefault(); SE.escAt = Date.now(); seFinishText(false); }
  });
  ta.addEventListener('blur', () => { if (SE.edit) seFinishText(true); });
  // 快捷键（写字时由上面的文本框自己处理）
  dlg.addEventListener('keydown', e => {
    if (e.target.closest('input')) return;
    e.stopPropagation();
    const ctrl = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (ctrl && k === 'z' && !e.shiftKey) { e.preventDefault(); seUndo(); }
    else if (ctrl && (k === 'y' || (k === 'z' && e.shiftKey))) { e.preventDefault(); seRedo(); }
    else if (e.key === 'Delete') {
      e.preventDefault();
      const it = SE.items[SE.sel];
      if (it && it.k === 'text') { seCommit(seSnap()); SE.items.splice(SE.sel, 1); SE.sel = -1; seRender(); } else seClear();
    }
    else if (ctrl && k === 'c') { e.preventDefault(); seExport('copy'); }
    else if (ctrl && k === 's') { e.preventDefault(); seExport('save'); }
  });
  dlg.addEventListener('cancel', e => {  // Esc：写字时只取消这段字，否则关窗口（改过会先问）
    e.preventDefault();
    if (SE.edit || Date.now() - SE.escAt < 400) { seFinishText(false); return; }
    if (!$('#sePop').hidden) { $('#sePop').hidden = true; return; }
    seClose(false);
  });
  $('#seOk').onclick = () => seClose(true);
  $('#seCancel').onclick = () => seClose(false);
  $('#seReset').onclick = seReset;
  window.addEventListener('resize', () => { if (dlg.open && SE.img) { seLayout(); seRender(); if (SE.edit) seStyleTa(); } });
}
bindShotEditor();
