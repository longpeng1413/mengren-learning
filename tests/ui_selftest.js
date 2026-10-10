// 在真实窗口里跑的界面自测（由 main.py --selftest= 执行）
Promise.race([new Promise(r => setTimeout(() => r((window.__R || []).concat(['TIMEOUT'])), 240000)), (async () => {
  const R = window.__R = [];
  const ok = (name, cond, info) => R.push((cond ? 'PASS ' : 'FAIL ') + name + (cond || info === undefined ? '' : ' :: ' + JSON.stringify(info)));
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
    ok('添加文件夹到媒体库', document.querySelectorAll('.lib-item').length === 2, document.querySelectorAll('.lib-item').length);
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
    const reread = await api('open', { path: base + '演示视频.mp4' });
    ok('修改和老师都保存到磁盘', reread.doc.segments[0].text === '改过的第一句' && reread.doc.teacher === tr.teacher.id);
    ok('媒体库里的状态跟着更新', await until(() => [...document.querySelectorAll('.lib-item.active .tag')].length >= 1));

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
    ok('字幕栏上方显示存疑数量', !document.querySelector('#btnDoubts').hidden && document.querySelector('#btnDoubts').textContent === '存疑 1');
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
    ok('设置：可以关联术语表文件、填笔记里的称呼', document.querySelector('#tFile').textContent === '没有关联' && !!document.querySelector('#tFilePick') && !!document.querySelector('#tDisplay'));
    showSection('obsidian');
    ok('设置：笔记文件名格式', document.querySelector('[data-k="obsidian.name_pattern"]').value === '{title}');
    showSection('tasks');
    ok('设置：任务分配可选 7 个 AI 服务', document.querySelectorAll('select[data-k="chat"] option').length === 7);
    showSection('template');
    showSection('about');
    ok('设置：导入导出和存放位置', !!document.querySelector('#btnExportSettings') && await until(() => document.querySelector('#aboutRoot').textContent.length > 3));
    ok('设置：课程重点模板', document.querySelector('#summaryTemplate').value.includes('不要套用固定栏目'));
    document.querySelector('#dlgSettings').close();

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

    await api('teacher_delete', { id: tr.teacher.id });

    // 改完马上关窗（--selftest-close 时会接着走关闭流程，检查这句有没有存下来）
    S.doc.segments[1].text = '关闭前最后一改'; markDirty();
    ok('关闭前有未保存的修改', S.dirty === true);
  } catch (e) {
    R.push('ERROR ' + (e.stack || e));
  }
  return R;
})()])
