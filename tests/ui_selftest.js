// 在真实窗口里跑的界面自测（由 main.py --selftest= 执行）
Promise.race([new Promise(r => setTimeout(() => r((window.__R || []).concat(['TIMEOUT'])), 240000)), (async () => {
  const R = window.__R = [];
  const ok = (name, cond, info) => {
    const line = (cond ? 'PASS ' : 'FAIL ') + name + (cond || info === undefined ? '' : ' :: ' + JSON.stringify(info));
    R.push(line);
    api('log', { level: 'info', message: '自测 ' + line }).catch(() => {});  // 程序中途退出时，日志里能看到测到哪一步
  };
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const until = async (fn, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (await fn()) return true; } catch (e) {} await wait(100); } return false; };
  const v = document.querySelector('#video');
  const toasts = () => [...document.querySelectorAll('.toast')].map(t => t.innerText);
  const base = window.__TEST_BASE;  // 项目里的 test-media\ui\（由 main.py 传进来）
  try {
    await until(() => S.settings);
    ok('后台已连接', !!(window.pywebview && window.pywebview.api));
    ok('空状态页', !document.querySelector('#empty').hidden);
    ok('媒体库默认显示', !document.querySelector('#library').hidden);

    // 媒体库
    const lib = await api('library_add_folder', { path: base });
    await loadLibrary(lib.groups);
    ok('添加文件夹到媒体库（视频、音频和书籍）', document.querySelectorAll('.lib-item').length === 3, document.querySelectorAll('.lib-item').length);
    const item = [...document.querySelectorAll('.lib-item')].find(el => el.textContent.includes('演示视频'));
    ok('媒体库显示字幕数', item && item.textContent.includes('字幕 8'), item && item.textContent);
    item.click();
    ok('点媒体库里的视频就打开', await until(() => S.doc && S.doc.title === '演示视频' && document.querySelectorAll('.cue').length === 8));
    ok('当前视频在列表里高亮', await until(() => document.querySelector('.lib-item.active')));
    ok('视频可以加载', await until(() => v.readyState >= 1 && v.duration > 39), { rs: v.readyState, d: v.duration });

    // 老师与术语表
    const tr = await api('teacher_save', { teacher: { name: '测试老师', terms: '底层逻辑\n风级 => 峰级', lang: 'en' } });
    S.teachers = tr.teachers; renderTeacherSelect();
    ok('顶栏可以选老师', [...document.querySelectorAll('#teacherSelect option')].some(o => o.textContent === '测试老师'));
    const sel = document.querySelector('#teacherSelect');
    sel.value = tr.teacher.id; sel.dispatchEvent(new Event('change'));
    ok('选了老师写进字幕文件', S.doc.teacher === tr.teacher.id);

    // 点字幕跳转、编辑和自动保存
    const cue3 = document.querySelectorAll('.cue')[3];
    cue3.querySelector('.src').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    ok('点字幕跳到对应时间', await until(() => Math.abs(v.currentTime - S.doc.segments[3].start) < 0.3), v.currentTime);
    const src0 = document.querySelectorAll('.cue')[0].querySelector('.src');
    src0.focus(); document.getSelection().selectAllChildren(src0);
    document.execCommand('insertText', false, '改过的第一句');
    src0.blur();
    ok('自动保存', await until(() => !S.dirty && !S.saving, 5000));
    ok('手动改字幕记下改动（用来积累术语表）', (S.doc.fixes || []).some(f => f.after === '改过的第一句' && f.by === 'manual' && f.before && f.before !== f.after), S.doc.fixes);
    const reread = await api('open', { path: base + '演示视频.mp4' });
    ok('修改和老师都保存到磁盘', reread.doc.segments[0].text === '改过的第一句' && reread.doc.teacher === tr.teacher.id);
    ok('媒体库里的状态跟着更新', await until(() => [...document.querySelectorAll('.lib-item.active .tag')].length >= 1));

    // 后台的 Agent 自动整理改过这节课：界面保存时不直接覆盖，先问
    ok('打开时记下字幕文件的版本号', S.rev > 0 && S.rev === reread.doc.rev, [S.rev, reread.doc.rev]);
    const origConfirm = window.confirm;
    const bg1 = (await api('open', { path: S.path })).doc;
    bg1.summary = '后台整理的课程重点'; await api('save', { path: S.path, doc: bg1 });  // 模拟 Agent 写入（不带版本号）
    window.confirm = () => true;  // 选「重新打开，看自动整理的结果」
    S.doc.segments[2].text = '界面里改的第三句'; markDirty(); await save();
    ok('后台改过：选「重新打开」就看到后台的结果，不覆盖', await until(() => S.doc.summary === '后台整理的课程重点' && !S.dirty) && S.doc.segments[2].text !== '界面里改的第三句', S.doc.summary);
    const bg2 = (await api('open', { path: S.path })).doc;
    bg2.summary = '后台又改了一次'; await api('save', { path: S.path, doc: bg2 });
    window.confirm = () => false;  // 选「保留我的版本」
    S.doc.segments[2].text = '界面里改的第三句'; markDirty(); await save();
    const kept = (await api('open', { path: S.path })).doc;
    ok('后台改过：选「保留我的」就用界面的版本覆盖', kept.segments[2].text === '界面里改的第三句' && kept.summary === '后台整理的课程重点' && !S.dirty, kept.summary);
    window.confirm = origConfirm;
    S.doc.summary = ''; markDirty(); await save();

    // 工具按钮：正在编辑的一行，下排的「合并」按钮超出这一行时，也要点得到它自己（不能被下一句盖住）
    const cue1 = document.querySelectorAll('.cue')[1];
    cue1.scrollIntoView({ block: 'center' });
    cue1.querySelector('.src').focus();
    const mb = cue1.querySelector('[data-act="merge"]').getBoundingClientRect();
    const hitEl = document.elementFromPoint(mb.left + mb.width / 2, mb.top + mb.height / 2);
    const mouseOnCues = !!document.querySelector('.cues-inner:hover');  // 真实鼠标正好停在字幕上时，悬停状态会干扰这项检查
    if (mouseOnCues) console.log('鼠标停在字幕上，跳过「合并」按钮的点击位置检查');
    ok('工具按钮排成两列，「合并」点得到、属于这一句', mouseOnCues || (mb.width > 0 && hitEl && hitEl.closest('[data-act="merge"]') && hitEl.closest('.cue') === cue1),
       [mb.top, mb.height, cue1.getBoundingClientRect().bottom, hitEl && hitEl.outerHTML.slice(0, 80)]);
    cue1.querySelector('.src').blur();

    // 拆分、合并、撤销
    const n0 = S.doc.segments.length;
    cueAction('split', 1); cueAction('merge', 1); cueAction('del', 7);
    ok('拆分合并删除', S.doc.segments.length === n0 - 1);
    undo(); undo(); undo();
    ok('撤销回到原样', S.doc.segments.length === n0);

    // 文字设置（Aa）
    document.querySelector('#btnText').click();
    ok('文字设置面板打开', document.querySelector('#menuText').classList.contains('open'));
    const cs = () => getComputedStyle(document.documentElement);
    const fs0 = S.settings.font_size, lh0 = S.settings.line_height;
    document.querySelector('[data-tx="font_size:1"]').click();
    document.querySelector('[data-tx="line_height:0.1"]').click();
    document.querySelector('[data-tx="cue_gap:2"]').click();
    document.querySelector('[data-tx="cue_width:-60"]').click();
    const f = document.querySelector('#txFont'); f.value = 'kai'; f.dispatchEvent(new Event('change'));
    ok('字号', cs().getPropertyValue('--cue-size').trim() === (fs0 + 1) + 'px');
    ok('行距', Math.abs(parseFloat(cs().getPropertyValue('--cue-line')) - (lh0 + 0.1)) < 0.01);
    ok('句间距', cs().getPropertyValue('--cue-gap').trim() === '11px');
    ok('字幕栏宽度', S.settings.cue_width > 0 && document.querySelector('.cues-inner').getBoundingClientRect().width <= S.settings.cue_width + 1);
    ok('字体', getComputedStyle(document.querySelector('.src')).fontFamily.includes('KaiTi'));
    savePrefs({ font_size: fs0, line_height: lh0, cue_gap: 9, cue_width: 0, font_family: 'yahei' });
    closeMenus();

    // AI 区：上下拖动、放大、移到右侧、隐藏
    savePrefs({ ai_dock: 'bottom', ai_hidden: false, video_h: 0.3 });
    await wait(100);
    const ph = document.querySelector('#playerArea').getBoundingClientRect().height;
    const lh = document.querySelector('#left').getBoundingClientRect().height;
    ok('视频高度可以调', Math.abs(ph / lh - 0.3) < 0.06, { ph, lh });
    const aiH = document.querySelector('#aiPanel').getBoundingClientRect().height;
    ok('AI 区跟着变高', aiH > lh * 0.55, { aiH, lh });
    document.querySelector('#btnAiMax').click();
    ok('放大 AI 区', S.settings.video_h < 0.2);
    document.querySelector('#btnAiDock').click(); await wait(100);
    ok('AI 区移到右侧', document.querySelector('#aiPanel').parentElement.id === 'aiDockSide' && document.querySelector('#aiDockSide').getBoundingClientRect().width > 250);
    const sp = document.querySelector('#aiSplit');
    const M = document.querySelector('#stageMain').getBoundingClientRect();
    ok('右侧 AI 区的拖动条可见', sp.getBoundingClientRect().width >= 10 && getComputedStyle(sp).display !== 'none');
    for (const target of [600, 380]) {
      sp.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, clientX: M.right - 450 }));
      sp.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX: M.right - target }));
      sp.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
      await wait(50);
      const w = document.querySelector('#aiDockSide').getBoundingClientRect().width;
      ok(`拖动把右侧 AI 区调到约 ${target - 6} 像素`, Math.abs(w - (target - 6)) < 12, w);
    }
    const subW = document.querySelector('#right').getBoundingClientRect().width;
    ok('字幕栏没有被挤掉', subW >= 250, subW);
    document.querySelector('#btnAiHide').click(); await wait(50);
    ok('隐藏 AI 区', !document.querySelector('#aiPanel').classList.contains('docked') && !document.querySelector('#btnShowAi').hidden);
    document.querySelector('#btnShowAi').click();
    savePrefs({ ai_dock: 'bottom', video_h: 0.55 });
    ok('移回视频下方', document.querySelector('#aiPanel').parentElement.id === 'aiDockBottom');

    // 课程重点可以编辑
    document.querySelector('#btnEditSummary').click();
    const ta = document.querySelector('#summaryEdit');
    ta.value = '### 一句话主题\n测试重点 [00:10]'; ta.dispatchEvent(new Event('input'));
    document.querySelector('#btnEditSummary').click();
    ok('编辑课程重点', S.doc.summary.includes('测试重点') && document.querySelector('#summaryBox .tslink'));
    const tb = md('前文\n\n| 模型 | 比例 |\n|---|---|\n| 金字塔 | 40% [01:02] |\n\n后文');
    ok('Markdown 表格显示成表格', tb.includes('<table>') && tb.includes('<td>金字塔</td>') && tb.includes('tslink') && tb.includes('<p>后文</p>'), tb);

    // 字幕历史版本、清空、恢复
    window.confirm = () => true;
    const before = S.doc.segments.length;
    useVersion('clear');
    ok('清空字幕', S.doc.segments.length === 0 && document.querySelector('.cues-empty'));
    ok('清空前自动存了历史版本', S.doc.versions.length === 1 && S.doc.versions[0].count === before);
    document.querySelector('#btnVersions').click();
    ok('历史版本菜单', document.querySelectorAll('#menuVersions [data-ver]').length === 2, document.querySelector('#menuVersions').innerText);
    document.querySelector('#menuVersions [data-ver="0"]').click();
    ok('恢复历史版本', S.doc.segments.length === before && document.querySelectorAll('.cue').length === before);
    await until(() => !S.dirty && !S.saving, 5000);
    const v2 = await api('open', { path: base + '演示视频.mp4' });
    ok('历史版本存进字幕文件', v2.doc.segments.length === before);

    // 已校对
    document.querySelector('#optReviewed').click();
    ok('标记已校对', S.doc.reviewed === true);

    // 存疑句（AI 校对时标出、需要回听的句子）
    S.doc.doubts = [{ t: S.doc.segments[2].start, reason: '测试存疑原因' }];
    renderCues();
    ok('存疑句用橙色标出并写明原因', document.querySelectorAll('.cue.doubt').length === 1 && document.querySelector('.cue.doubt .doubt-tag').textContent.includes('测试存疑原因'));
    updateButtons();
    ok('有存疑句时可以用画面复核', !document.querySelector('#btnRecheck').disabled);
    const plOrder = [...document.querySelectorAll('#dlgPipeline input[type=checkbox]')].map(x => x.id);
    ok('一键整理：最后一步找新术语', plOrder[plOrder.length - 1] === 'plTerms', plOrder);
    ok('一键整理：先校对，再找关键帧、用画面复核存疑句', plOrder.indexOf('plCorrect') < plOrder.indexOf('plFrames') && plOrder.indexOf('plFrames') < plOrder.indexOf('plRecheck'), plOrder);
    ok('字幕栏上方显示存疑数量',!document.querySelector('#btnDoubts').hidden && document.querySelector('#btnDoubts').textContent === '存疑 1');
    document.querySelector('#btnDoubts').click();
    ok('点「存疑」跳到那一句并单句循环', S.loopIdx === 2 && document.querySelector('#optLoop').checked);
    const loopHint = document.querySelector('#loopHint');
    ok('单句循环时开关旁边写明在循环哪一句', await until(() => !loopHint.hidden && loopHint.textContent === `正在循环 ${fmt(S.doc.segments[2].start)} 这一句`), loopHint.textContent);
    v.pause();
    document.querySelector('.cue.doubt [data-doubt="ok"]').click();
    ok('「已确认」去掉存疑标记', !S.doc.doubts.length && !document.querySelector('.cue.doubt') && document.querySelector('#btnDoubts').hidden);
    loopHint.click();
    ok('点循环提示关掉单句循环', !document.querySelector('#optLoop').checked && S.loopIdx === null && loopHint.hidden);

    // 画面：关键帧和画面记录、课程重点里的插图
    S.doc.frames = [{ t: 5, note: '课件标题：交易心理学', caption: '欢迎来到', figure: true }, { t: 20, note: '' }];
    renderFrames();
    document.querySelectorAll('.ai-tabs .tab')[2].click();
    ok('画面页列出画面和记录', document.querySelectorAll('.shot').length === 2 && document.querySelector('.shot textarea').value.includes('交易心理学') && document.querySelector('#framesCount').textContent === '2');
    ok('画面缩略图能显示', await until(() => { const im = document.querySelector('.shot img'); return im && im.src.startsWith('file:') && im.complete && im.naturalWidth > 0; }, 15000));
    ok('有没写记录的画面时可以补写', !document.querySelector('#btnDescribe').disabled && document.querySelector('#framesHint').textContent.includes('1 张还没有记录'));
    const ta2 = document.querySelectorAll('.shot textarea')[1]; ta2.value = '我自己写的画面记录'; ta2.dispatchEvent(new Event('input', { bubbles: true }));
    ok('可以自己写画面记录', S.doc.frames[1].note === '我自己写的画面记录' && S.dirty);
    document.querySelectorAll('.shot [data-del]')[1].click();
    ok('删除画面', S.doc.frames.length === 1);
    v.currentTime = 12; await wait(300);
    await captureFrame();
    ok('截取当前画面（没设置画面识别 AI 时先留空）', S.doc.frames.length === 2 && S.doc.frames.some(f => f.manual && Math.abs(f.t - 12) < 0.5));
    S.doc.summary = '## 一、开头\n- 要点 [00:05]\n![[画面 00:05]]';
    renderSummary();
    ok('课程重点里的插图能显示', await until(() => { const im = document.querySelector('#summaryBox .shot-fig img'); return im && im.complete && im.naturalWidth > 0; }, 15000));
    document.querySelectorAll('.ai-tabs .tab')[0].click();

    // 查漏补缺按钮和覆盖率
    renderSummary();
    ok('有课程重点时可以查漏补缺', !document.querySelector('#btnReview').disabled);
    ok('显示字幕被引用的比例', await until(() => document.querySelector('#summaryHint').textContent.includes('字幕被引用约')), document.querySelector('#summaryHint').textContent);

    // 新术语：勾选确认后加进术语表
    ok('「新术语」按钮可用', !document.querySelector('#btnTerms').disabled);
    S.doc.terms_pending = { terms: [{ term: '测试新术语', t: 1, quote: '原话' }, { term: '不要的词', t: 2, quote: '' }], errors: [{ wrong: '风机', right: '峰级', t: 3, quote: '' }] };
    updateButtons();
    ok('有待确认的新术语时按钮显示数量', document.querySelector('#btnTerms').textContent === '新术语 3');
    const termsShown = openTerms();
    ok('新术语对话框：列出新术语和识别错误', document.querySelector('#dlgTerms').open && document.querySelectorAll('#tmList .tm-row').length === 3);
    document.querySelectorAll('#tmList .tm-row')[1].querySelector('input[type=checkbox]').checked = false;
    document.querySelector('#dlgTerms').close('add');
    await termsShown;
    const tTerms = (teacherById(S.doc.teacher) || {}).terms || '';
    ok('勾选的加进老师术语表，没勾的不加', tTerms.includes('测试新术语') && !tTerms.includes('不要的词') && tTerms.includes('风机 => 峰级') && !S.doc.terms_pending, tTerms);

    // 媒体库读不出来时显示原因，不显示“还没有添加文件”
    const libSaved = S.lib; S.lib = []; S.libError = '读不出「library.json」'; renderLibrary();
    ok('媒体库读不出来：显示原因和重试，不显示“还没有添加文件”', $('#libBody').textContent.includes('读不出') && !$('#libBody').textContent.includes('还没有添加文件'));
    S.lib = libSaved; S.libError = ''; renderLibrary();
    // 收起媒体库
    document.querySelector('#btnLibrary').click();
    ok('收起媒体库', document.querySelector('#library').hidden);
    document.querySelector('#btnLibrary').click();

    // 没设置 AI 时的提示
    await startCorrect(true);
    ok('AI 校对没设置时提示', toasts().some(t => t.includes('还没有填写')), toasts());

    // 整理按钮和一键整理都交给后台任务层（和命令行 / Agent 同一套规则）。识别和 AI 是假的（--fake-ai），不联网
    const aiBase = base.replace(/ui\\$/, 'ui-ai\\');
    const keepPath = S.path;
    await api('settings', { settings: { providers: { deepseek: { key: 'sk-fake' }, openai: { key: 'sk-fake' }, doubao: { key: 'fake' } },
      vision: 'openai', chat: 'deepseek', correct: 'deepseek', translate: 'deepseek', asr_zh: 'doubao', asr_en: 'openai', auto_review: true } });
    await loadSettings();
    await window.openPath(aiBase + 'AI整理测试.mp4');
    ok('整理：打开还没有字幕的视频', await until(() => S.doc && S.doc.title === 'AI整理测试' && !S.doc.segments.length));
    setDocTeacher(tr.teacher.id);
    ok('生成字幕：后台识别并写进字幕文件，界面重新读取', await startTranscribe('zh', 'doubao') && S.doc.segments.length === 4 && S.doc.lang === 'zh'
      && S.rev > 0 && S.doc.steps && S.doc.steps.transcribe && S.doc.teacher === tr.teacher.id && document.querySelectorAll('.cue').length === 4, S.doc);
    ok('AI 校对：改的句子绿色下划线、存疑标橙色', await startCorrect(true) && S.doc.segments[0].text.startsWith('（AI 校对过）')
      && !!document.querySelector('.cue.fixed') && document.querySelectorAll('.cue.doubt').length === 1 && S.doc.corrected, S.doc.segments);
    ok('AI 校对：校对前存了历史版本，改动记进 fixes（积累术语表用）', S.doc.versions[0].reason === 'AI 校对前' && S.doc.fixes.some(f => f.by === 'ai'));
    undo();
    ok('AI 校对：可以撤销', S.doc.segments[0].text === '大家好，今天讲峰级。' && await until(() => !S.dirty && !S.saving));
    await startCorrect(true);
    ok('画面复核存疑句：确认后去掉存疑标记', await recheckDoubts(false) && !document.querySelector('.cue.doubt') && S.doc.segments[1].text.endsWith('（画面确认）'), S.doc.segments);
    ok('找关键帧', await findFrames(true) && S.doc.frames.length === 1 && S.doc.frames[0].note === '假 AI 挑的图');
    {  // AI辅助理解图：画面看不清时 AI 重画
      ok('AI辅助理解图：画面页标「AI 重画」，显示重画的流程图代码', !!document.querySelector('.shot .redraw-tag')
        && document.querySelector('.shot .redraw-code').textContent.includes('破级') && !!document.querySelector('[data-unredraw]'));
      const keep = S.doc.summary;
      S.doc.summary = '## 一、开头\n- 要点 [00:03]\n![[画面 00:03]]'; renderSummary();
      ok('AI辅助理解图：课程重点预览显示重画的图和标注，不显示截图', await until(() => {
        const fig = document.querySelector('#summaryBox .shot-fig');
        return fig && fig.querySelector('.redraw-code') && fig.textContent.includes('AI辅助理解图｜根据老师讲解和画面整理，非原画面');
      }));
      document.querySelector('[data-unredraw]').click();
      ok('改用原截图：去掉重画的图', !S.doc.frames[0].redraw && !!document.querySelector('[data-redraw]') && !document.querySelector('.shot .redraw-tag'));
      document.querySelector('[data-redraw]').click();
      ok('点「AI 重画」：重画一张（假 AI 画 SVG），画面页和课程重点预览都显示', await until(() => S.doc.frames[0].redraw && S.doc.frames[0].redraw.kind === 'svg'
        && document.querySelector('.shot .redraw-svg') && document.querySelector('#summaryBox .redraw-svg'), 15000));
      S.doc.summary = keep; renderSummary();
    }
    ok('课程重点：后台写好，自动查漏补缺', await doSummary() && S.doc.summary.includes('## 一、风险') && S.doc.summary.includes('查漏补上的一条')
      && $('#summaryBox').textContent.includes('截断亏损') && $('#btnSummary').textContent === '重新生成', S.doc.summary);
    ok('课程重点刚生成：不提醒重新生成', document.querySelector('#summaryStale').hidden && S.summaryChanges === 0);
    S.doc.segments.slice(0, 3).forEach(s => { s.text += '（人工改）'; }); markDirty(); await save();
    ok('生成后字幕改了 3 句：课程重点上方提醒重新生成', !document.querySelector('#summaryStale').hidden && S.summaryChanges === 3
      && document.querySelector('#summaryStaleText').textContent.includes('改了 3 句'));
    document.querySelector('#btnSummaryStale').click();
    ok('点提醒里的「重新生成」：用改好的字幕重新写，提醒消失', await until(() => !S.job && S.summaryChanges === 0 && document.querySelector('#summaryStale').hidden, 15000));
    ok('媒体库标「重点待更新」', itemHTML({ path: 'x.mp4', name: 'x', summary: true, summary_changes: 5 }).includes('重点待更新')
      && !itemHTML({ path: 'x.mp4', name: 'x', summary: true, summary_changes: 2 }).includes('重点待更新'));
    ok('翻译：中文字幕自己选了翻译也照样翻', await startTranslate(false) && S.doc.segments.every(s => s.tr.startsWith('中文：')));
    const termsP = findTerms(false);
    ok('新术语：放进待勾选并打开对话框', await until(() => document.querySelector('#dlgTerms').open) && S.doc.terms_pending.terms[0].term === '假AI新术语');
    document.querySelector('#dlgTerms').close('drop'); await termsP;
    ok('新术语：不要就清掉', !S.doc.terms_pending);
    openPipeline();
    ['plAsr', 'plCorrect', 'plFrames', 'plRecheck', 'plSummary', 'plTerms'].forEach(id => { document.getElementById(id).checked = true; });
    document.getElementById('plClip').checked = false;
    document.querySelector('#dlgPipeline').close('ok');
    ok('一键整理：一次交给后台，做完打开新术语对话框', await until(() => document.querySelector('#dlgTerms').open, 30000));
    document.querySelector('#dlgTerms').close('drop');
    ok('一键整理：完成，重新识别前的字幕存了历史版本', await until(() => toasts().some(t => t.includes('一键整理完成')), 10000)
      && S.doc.versions.some(v => v.reason === '重新识别前') && S.doc.corrected && S.doc.summary.includes('## 一、风险') && !S.job, toasts());
    const tasksNow = await api('doc', { path: S.path });
    ok('一键整理：整理结果和磁盘上的一致', tasksNow.doc.rev === S.rev && tasksNow.doc.summary === S.doc.summary);
    await api('settings', { settings: { chat: 'claude' } });  // Claude 没填 Key：课程重点会失败
    await loadSettings();
    const sumBefore = S.doc.summary;
    ok('失败时：提示原因，课程重点保持原样', !(await doSummary()) && toasts().some(t => t.includes('Claude')) && S.doc.summary === sumBefore, toasts());
    await api('settings', { settings: { chat: 'deepseek' } });
    await loadSettings();
    await window.openPath(aiBase + '《AI整理测试书》.pdf');
    ok('书：打开', await until(() => S.mode === 'book' && (S.doc.chapters || []).length === 2));
    ok('书：整理选中的章节', await bookOrganize([0]) && S.doc.chapters[0].md.includes('假 AI 整理') && !S.doc.chapters[1].md);
    await bookOverview();
    ok('书：章节没全部整理完也可以先写全书总览', S.doc.overview.includes('假 AI 写的总览'));
    await window.openPath(keepPath);
    ok('回到原来的视频', await until(() => S.path === keepPath && S.mode === 'media'));

    // 媒体库树形显示、展开收起、拖动排序、改名、新格式（test-media\ui-lib：1、第一讲\{2、乙,1、甲}.avi、资料\补充.epub、预告.avi）
    const libBase = base.replace(/ui\\$/, 'ui-lib\\');
    const rl = await api('library_add_folder', { path: libBase });
    await loadLibrary(rl.groups);
    const top = S.lib.find(n => n.path.toLowerCase() === libBase.replace(/\\$/, '').toLowerCase());
    ok('媒体库按实际层级显示：带序号的在前，子文件夹里也按序号', top && top.children.map(c => c.name).join('|') === '1、第一讲|资料|预告'
      && top.children[0].children.map(c => c.name).join('|') === '1、甲|2、乙', top && top.children.map(c => c.name));
    const headOf = name => [...document.querySelectorAll('#libBody [data-folder]')].find(h => h.querySelector('.lib-gname').textContent === name);
    ok('子文件夹显示成可以展开的一层，往里缩进', !!headOf('资料') && getComputedStyle(headOf('资料')).getPropertyValue('--depth').trim() === '1');
    headOf('资料').click();
    ok('点文件夹收起，记住', headOf('资料').parentElement.classList.contains('collapsed') && libNode(top.path + '\\资料').collapsed);
    headOf('资料').click();
    const sib = () => siblings0(top.path).map(el => el.dataset.fname || el.dataset.drag.split('\\').pop());
    const elOf = name => siblings0(top.path).find(el => (el.dataset.fname || '') === name);
    let list = siblings0(top.path).filter(el => el !== elOf('预告.avi'));
    list.splice(list.indexOf(elOf('资料')), 0, elOf('预告.avi'));
    await libReorder({ el: elOf('预告.avi'), path: elOf('预告.avi').dataset.drag, parent: top.path }, list);
    ok('拖动没序号的：放到哪里就排在哪里', await until(() => sib().join('|') === '1、第一讲|预告.avi|资料'), sib());
    const origPrompt = window.prompt;
    window.prompt = () => null;  // 提示改名时不改
    const inner = top.path + '\\1、第一讲';
    const elsIn = () => siblings0(inner);
    list = elsIn().slice().reverse();
    await libReorder({ el: list[0], path: list[0].dataset.drag, parent: inner }, list);
    ok('拖动带序号的打乱序号：提示改名，不改名就不动', toasts().some(t => t.includes('按序号排列')) && elsIn().map(el => el.dataset.fname).join('|') === '1、甲.avi|2、乙.avi');
    window.prompt = () => '预告（改过名）';
    ok('改名：电脑上的文件和媒体库一起改', await renameNode(libNode(top.path + '\\预告.avi')) && !!libNode(top.path + '\\预告（改过名）.avi')
      && !libNode(top.path + '\\预告.avi'));
    window.prompt = origPrompt;
    const isShut = name => headOf(name).parentElement.classList.contains('collapsed');
    const topName = top.name;
    headOf('资料').click();  // 先收起，再打开里面的书
    await window.openPath(top.path + '\\资料\\补充.epub');
    ok('打开电子书（EPUB）：分出章节，显示类型', await until(() => S.mode === 'book' && (S.doc.chapters || []).length === 2) && $('#bkInfo').textContent.includes('电子书（EPUB）'), $('#bkInfo').textContent);
    ok('打开的课所在的文件夹自动展开', !isShut('资料') && !libNode(top.path + '\\资料').collapsed);
    headOf('资料').click();
    ok('正在看的课所在的文件夹也能收起', isShut('资料'));
    headOf(topName).click();
    ok('正在看的课所在的最上层文件夹也能收起', isShut(topName));
    headOf(topName).click(); headOf('资料').click();
    ok('再点又展开', !isShut(topName) && !isShut('资料'));
    await window.openPath(keepPath);
    await loadLibrary((await api('library_remove', { kind: 'folder', path: top.path })).groups);

    // 设置窗口
    openSettings('codex');
    ok('设置：ChatGPT 账号登录', document.querySelector('#codexBox').textContent.includes('用 ChatGPT 账号登录'));
    showSection('teachers');
    ok('设置：老师列表', document.querySelectorAll('#teacherList button').length === 2);
    document.querySelectorAll('#teacherList button')[0].click();
    const tEdit = teacherById(editingTeacher), tOld = tEdit.name;
    document.querySelector('#tName').value = tOld + '改名';
    ok('改了名称还没点「保存这位老师」：认得出有改动', teacherFormDirty());
    await saveSettings(false);
    ok('点「保存设置」时把改名一起保存，顶栏下拉框跟着变', teacherById(tEdit.id).name === tOld + '改名'
       && [...document.querySelectorAll('#teacherSelect option')].some(o => o.textContent.includes(tOld + '改名')) && !teacherFormDirty());
    document.querySelector('#tName').value = tOld; await saveTeacher();
    ok('设置：可以关联术语表文件；没有「笔记里的称呼」（笔记不写老师）', document.querySelector('#tFile').textContent === '没有关联' && !!document.querySelector('#tFilePick') && !document.querySelector('#tDisplay'));
    showSection('doubao');
    ok('设置：豆包只列四种模型、按顺序自动换；没有模型下拉框和对象存储', document.querySelectorAll('#doubaoOrder li').length === 4
       && document.querySelector('#doubaoOrder li').textContent.includes('流式识别 2.0') && !document.querySelector('#doubaoModel')
       && !document.querySelector('[data-k="providers.doubao.tos_ak"]'));
    document.querySelector('#dbAdd').click();
    const dbRow = document.querySelector('#dbMore .db-acct:last-child');
    dbRow.querySelector('.db-name').value = '小号'; dbRow.querySelector('.db-key').value = 'ui-test-key-98765';
    await saveSettings(false);
    const dbMk = S.settings.providers.doubao.more_keys;
    renderDoubaoAccounts();
    ok('设置：可以添加别的豆包账号，保存后密钥只显示后四位', dbMk.length === 1 && dbMk[0].name === '小号' && dbMk[0].key === '••••8765'
       && document.querySelector('#dbMore .db-key').placeholder.includes('••••8765') && document.querySelector('#dbMore .db-key').value === '', dbMk);
    await api('settings', { settings: { providers: { doubao: { spent: { [dbMk[0].id + '|stream2']: '2026-10-14' } } } } });
    await refreshSettingsOnly(); renderDoubaoAccounts();
    ok('设置：列出哪个账号的哪种模型额度用完了', document.querySelector('#dbSpent').textContent.includes('小号 · 流式识别 2.0：2026-10-14 用完'),
       document.querySelector('#dbSpent').textContent);
    document.querySelector('[data-db-revive]').click();
    ok('点「重新启用」去掉用完的记录', await until(() => !Object.keys(S.settings.providers.doubao.spent || {}).length && !document.querySelector('[data-db-revive]')));
    document.querySelector('#dbMore [data-db-del]').click();
    await saveSettings(false);
    ok('设置：删掉豆包账号', !S.settings.providers.doubao.more_keys.length);
    showSection('obsidian');
    ok('设置：笔记文件名格式', document.querySelector('[data-k="obsidian.name_pattern"]').value === '{title}');
    showSection('tasks');
    ok('设置：任务分配可选 7 个 AI 服务', document.querySelectorAll('select[data-k="chat"] option').length === 7);
    showSection('template');
    showSection('about');
    ok('设置：导入导出和存放位置', !!document.querySelector('#btnExportSettings') && await until(() => document.querySelector('#aboutRoot').textContent.length > 3));
    ok('设置：课程重点模板', document.querySelector('#summaryTemplate').value.includes('不要套用固定栏目'));
    ok('关于：开源地址已改名', document.querySelector('#aboutHome').textContent.includes('mengren-learning'));
    ok('关于：点 × 时收到右下角，默认打开，可以关掉', document.querySelector('[data-k="close_to_tray"]').checked === true);
    document.querySelector('#dlgSettings').close();

    // 识别中途豆包额度用完或没开通、换了账号 / 模型从头识别：已经显示的半截字幕清掉
    const segs0 = S.doc.segments, live0 = liveView();
    live0.onItems([{ kind: 'cue', start: 0, end: 1, text: '半截一' }, { kind: 'cue', start: 1, end: 2, text: '半截二' }]);
    live0.onItems([{ kind: 'reset' }, { kind: 'cue', start: 0, end: 1, text: '重新识别' }]);
    ok('识别中途换了账号或模型：先清掉已经显示的半截字幕', S.doc.segments.length === 1 && S.doc.segments[0].text === '重新识别'
       && document.querySelectorAll('#cues .cue').length === 1, S.doc.segments.length);
    S.doc.segments = segs0; renderCues();

    // 使用说明
    document.querySelector('#btnHelpTop').click();
    ok('顶栏的帮助按钮打开使用说明', document.querySelector('#dlgHelp').open && !document.querySelector('#helpBody section[data-h="start"]').hidden);
    document.querySelector('#helpNav [data-h="book"]').click();
    ok('使用说明可以切换章节', !document.querySelector('#helpBody section[data-h="book"]').hidden && document.querySelector('#helpBody section[data-h="start"]').hidden
      && document.querySelectorAll('#helpNav [data-h]').length >= 17);
    document.querySelector('#helpClose').click();
    ok('关闭使用说明', !document.querySelector('#dlgHelp').open);

    // 书籍和文档
    await flushSave();
    await window.openPath(base + '测试书籍.pdf');
    ok('打开 PDF 进入书籍界面', await until(() => S.mode === 'book' && !document.querySelector('#bookMain').hidden && document.querySelector('#stageMain').hidden && document.body.classList.contains('mode-book')));
    ok('左边显示 PDF 原文', document.querySelector('#bookPdf').src.startsWith('file:') && !document.querySelector('#bookPdf').hidden);
    ok('视频相关按钮在书籍界面隐藏', getComputedStyle(document.querySelector('#btnTranscribe')).display === 'none');
    ok('列出章节和全书总览', document.querySelectorAll('#bkChapters .ch-row').length === 3 && document.querySelector('#bkChapters').textContent.includes('00、全书总览'));
    ok('认出「书上第 1 页是 PDF 第 3 页」', document.querySelector('#bkFirst').value === '3' && document.querySelector('#bkTitle').value === '测试书籍');
    document.querySelector('#bkChapters .ch-row[data-ch="0"] span').click();
    ok('点章节：选中并跳到这一章的第一页', S.bookSel.i === 0 && document.querySelector('#bookPdf').src.endsWith('#page=3'));
    S.doc.chapters[0].md = '## 一、本章主题\n\n讲趋势。（P2）（PDF 第 5 页）';
    renderBook();
    ok('整理稿里的页码可以点', document.querySelectorAll('#bkView .pglink').length === 2);
    document.querySelector('#bkView .pglink[data-p]').click();
    ok('点 P2 跳到 PDF 第 4 页', document.querySelector('#bookPdf').src.endsWith('#page=4'));
    const ti = document.querySelector('#bkChapters .ch-row[data-ch="0"] input[data-f="title"]');
    ti.value = '第一章 改过的名字'; ti.dispatchEvent(new Event('change', { bubbles: true }));
    ok('可以改章名', S.doc.chapters[0].title === '第一章 改过的名字');
    const fp = document.querySelector('#bkFirst'); fp.value = '4'; fp.dispatchEvent(new Event('change'));
    ok('可以改页码对应关系', S.doc.shift === 3 && document.querySelector('#bkShiftHint').textContent.includes('PDF 第 4 页 = P1'));
    ok('有整理好的章节时可以剪藏', !document.querySelector('#btnClip').disabled && !document.querySelector('#bkOverview').disabled);
    await flushSave();
    const reb = await api('open', { path: base + '测试书籍.pdf' });
    ok('书籍整理结果保存到磁盘', reb.doc.chapters[0].title === '第一章 改过的名字' && reb.doc.shift === 3 && reb.doc.chapters[0].md.includes('讲趋势'));
    await window.openPath(base + '演示视频.mp4');
    ok('再打开视频回到视频界面', await until(() => S.mode === 'media' && !document.querySelector('#stageMain').hidden && document.querySelector('#bookMain').hidden && v.readyState >= 1 && !S.resumePending));

    // 播放进度续播
    v.currentTime = 21.3; await wait(300);
    await flushSave();
    document.querySelector('#optLoop').click();
    ok('打开单句循环开关时显示提示', document.querySelector('#optLoop').checked && !document.querySelector('#loopHint').hidden);
    await window.openPath(base + '旧格式.avi');
    ok('换一节课时自动关掉单句循环', !document.querySelector('#optLoop').checked && S.loopIdx === null && document.querySelector('#loopHint').hidden);
    ok('avi 自动转换后可播放', await until(() => v.readyState >= 1 && v.duration > 19, 60000));
    ok('打开 avi 时自动用文件夹没有绑定的老师（空）', S.doc.teacher === '');
    await window.openPath(base + '演示视频.mp4');
    ok('再次打开从上次进度继续', await until(() => Math.abs(v.currentTime - 21.3) < 0.6, 5000), v.currentTime);

    // 剪藏
    await api('settings', { settings: { obsidian: { vault: base, folder: 'clip-test', teacher_subfolder: true } } });
    const okClip = await doClip();
    ok('剪藏到老师子文件夹', okClip && S.doc.clip.path.includes('clip-test\\测试老师\\'), S.doc.clip);
    const libTag = () => (document.querySelector('#libBody .lib-item.active .lib-meta') || {}).textContent || '';
    ok('剪藏后媒体库显示「已剪藏」', await until(() => libTag().includes('已剪藏') && !libTag().includes('待更新')), libTag());
    S.doc.summary = (S.doc.summary || '') + '\n- 剪藏后又加的一条 [00:01]'; markDirty(); await save();
    ok('剪藏后又改了课程重点：显示「待更新剪藏」', await until(() => libTag().includes('待更新剪藏')), libTag());
    ok('字幕标签写明是几句', /字幕 \d+ 句/.test(libTag()), libTag());
    await doClip();
    ok('重新剪藏后恢复「已剪藏」', await until(() => libTag().includes('已剪藏') && !libTag().includes('待更新')), libTag());
    ok('剪藏记下笔记文件的指纹（用来判断 Obsidian 里改过没有）', !!S.doc.clip.file_hash, S.doc.clip);

    await api('teacher_delete', { id: tr.teacher.id });

    // 改完马上关窗（--selftest-close 时会接着走关闭流程，检查这句有没有存下来）
    S.doc.segments[1].text = '关闭前最后一改'; markDirty();
    ok('关闭前有未保存的修改', S.dirty === true);
  } catch (e) {
    R.push('ERROR ' + (e.stack || e));
  }
  return R;
})()])
