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
    v.pause();
    document.querySelector('.cue.doubt [data-doubt="ok"]').click();
    ok('「已确认」去掉存疑标记', !S.doc.doubts.length && !document.querySelector('.cue.doubt') && document.querySelector('#btnDoubts').hidden);
    document.querySelector('#optLoop').checked = false; S.loopIdx = null;

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

    // 收起媒体库
    document.querySelector('#btnLibrary').click();
    ok('收起媒体库', document.querySelector('#library').hidden);
    document.querySelector('#btnLibrary').click();

    // 没设置 AI 时的提示
    await startCorrect(true);
    ok('AI 校对没设置时提示', toasts().some(t => t.includes('还没有填写')), toasts());

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
    ok('设置：可以关联术语表文件、填笔记里的称呼', document.querySelector('#tFile').textContent === '没有关联' && !!document.querySelector('#tFilePick') && !!document.querySelector('#tDisplay'));
    showSection('doubao');
    ok('设置：对象存储写清楚存储桶名称和 AK 从哪里来', document.querySelector('section[data-sec="doubao"] .tos').textContent.includes('小写英文字母') && document.querySelector('[data-k="providers.doubao.tos_ak"]').placeholder === 'AKLT…');
    showSection('obsidian');
    ok('设置：笔记文件名格式', document.querySelector('[data-k="obsidian.name_pattern"]').value === '{title}');
    showSection('tasks');
    ok('设置：任务分配可选 7 个 AI 服务', document.querySelectorAll('select[data-k="chat"] option').length === 7);
    showSection('template');
    showSection('about');
    ok('设置：导入导出和存放位置', !!document.querySelector('#btnExportSettings') && await until(() => document.querySelector('#aboutRoot').textContent.length > 3));
    ok('设置：课程重点模板', document.querySelector('#summaryTemplate').value.includes('不要套用固定栏目'));
    ok('关于：开源地址已改名', document.querySelector('#aboutHome').textContent.includes('mengren-learning'));
    document.querySelector('#dlgSettings').close();

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
    await window.openPath(base + '旧格式.avi');
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
