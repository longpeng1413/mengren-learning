"""界面与后台之间的桥：界面通过 window.pywebview.api.call(名称, 参数) 调用这里的函数。
不开本地网络端口（有的电脑上安全软件或代理会干扰 127.0.0.1 连接），视频直接用 file:// 播放。"""
import os
import subprocess
import threading
import time
from pathlib import Path

from core import asr, book, cache, codex_auth, config, doc as docs, frames, glossary, jobs, library, llm, media
from core.log import LOG_DIR, log

MEDIA_FILTER = ('音视频和文档 (*.mp4;*.m4v;*.mov;*.mkv;*.webm;*.avi;*.flv;*.ts;*.wmv;*.3gp;*.mpg;*.mpeg;'
                '*.mp3;*.m4a;*.aac;*.wav;*.flac;*.ogg;*.opus;*.wma;*.amr;*.ape;*.pdf;*.docx;*.txt;*.md)',
                '书籍和文档 (*.pdf;*.docx;*.txt;*.md)', '所有文件 (*.*)')


def file_url(path) -> str:
    return Path(path).resolve().as_uri()


class Api:
    def __init__(self):
        self._window = None
        self._quit = threading.Event()
        self._version = ''
        self._bundled = None

    # pywebview 会把 Api 的公开方法暴露给界面；统一从 call 进来，出错时返回 {'ok': False, 'error': …}
    def call(self, name, body=None):
        fn = getattr(self, 'do_' + str(name), None)
        if fn is None:
            return {'ok': False, 'error': '未知操作：' + str(name)}
        t0 = time.time()
        try:
            out = fn(body or {})
            out.setdefault('ok', True)
            return out
        except (asr.AsrError, llm.LlmError, media.MediaError, book.BookError, OSError, ValueError, codex_auth.AuthError) as e:
            log.warning('%s 失败：%s', name, e)
            return {'ok': False, 'error': str(e)}
        except Exception as e:
            log.exception('%s 出错', name)
            return {'ok': False, 'error': f'出错了：{e}（详情已写入日志）'}
        finally:
            spent = time.time() - t0
            if spent > 8 and name not in ('pick_media', 'pick_media_multi', 'pick_folder', 'save_dialog', 'library_add_folder', 'library_add_files'):
                log.warning('%s 用了 %.1f 秒', name, spent)
            elif name not in ('job', 'position'):
                log.debug('%s %.2fs', name, spent)

    # ---------------------------------------------------------- 文件对话框 ----
    def do_pick_media(self, b):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=MEDIA_FILTER)
        return {'path': r[0] if r else None}

    def do_pick_media_multi(self, b):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=MEDIA_FILTER)
        return {'paths': list(r or [])}

    def do_pick_text(self, b):
        """选一个 md / txt 文件，读出文字（导入术语说明用）。"""
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=('文本文件 (*.md;*.txt)', '所有文件 (*.*)'))
        if not r:
            return {'text': None}
        raw = Path(r[0]).read_bytes()
        for enc in ('utf-8-sig', 'gb18030'):
            try:
                return {'text': raw.decode(enc), 'name': Path(r[0]).name}
            except UnicodeDecodeError:
                continue
        raise ValueError('读不出这个文件的文字，请用 UTF-8 编码保存后再试')

    def do_pick_file(self, b):
        """选一个文件，只返回路径。"""
        import webview
        types = ('Markdown / 文本 (*.md;*.txt)', '所有文件 (*.*)') if b.get('kind') == 'text' else ('所有文件 (*.*)',)
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=types)
        return {'path': r[0] if r else None}

    def do_pick_folder(self, b):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=b.get('start') or '')
        return {'path': r[0] if r else None}

    def do_save_dialog(self, b):
        import webview
        ext = 'srt' if b.get('ext') == 'srt' else 'txt'
        types = ('文本文件 (*.txt)',) if ext == 'txt' else ('字幕文件 (*.srt)',)
        r = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=b.get('name') or '', file_types=types)
        if not r:
            return {'path': None}
        path = r if isinstance(r, str) else r[0]
        return {'path': path if path.lower().endswith('.' + ext) else path + '.' + ext}

    # -------------------------------------------------------------- 设置 ----
    def do_settings(self, b):
        if b.get('settings'):
            config.update(b['settings'])
        s = config.load()
        return {'settings': config.public(s), 'provider_names': config.PROVIDER_NAMES, 'chat_providers': config.CHAT_PROVIDERS,
                'teachers': config.load_teachers(), 'default_template': llm.DEFAULT_TEMPLATE,
                'doubao_models': {k: {'name': v['name'], 'note': v['note'], 'kind': v['kind']} for k, v in asr.DOUBAO_MODELS.items()}}

    def do_recent(self, b):
        return {'items': [r for r in config.load().get('recent', []) if os.path.isfile(r.get('path', ''))]}

    # -------------------------------------------------------------- 文件 ----
    def do_open(self, b):
        path = os.path.abspath(str(b.get('path') or '').strip().strip('"'))
        if not os.path.isfile(path):
            raise ValueError('找不到这个文件：' + path)
        if Path(path).suffix.lower() in book.DOC_EXT:
            return self._open_book(path)
        if Path(path).suffix.lower() not in media.MEDIA_EXT:
            raise ValueError('不支持这种文件。支持常见的视频（mp4、mkv、mov、webm…）、音频（mp3、m4a、wav…）和文档（pdf、docx、txt、md）')
        info = media.probe(path)
        d = docs.load(path) or docs.new(path, info)
        d['duration'] = info['duration'] or d.get('duration', 0)
        config.add_recent(path, d.get('title') or Path(path).stem)
        plan = media.play_plan(path, info)
        if not d.get('teacher'):
            d['teacher'] = library.folder_teacher(path)
        out = {'path': path, 'info': info, 'doc': d, 'plan': plan, 'position': config.get_position(path)}
        cached = media.playable_path(path, info)
        if plan == 'direct':
            out['src'] = file_url(path)
        elif cached.is_file():
            cache.touch(cached)
            out['src'] = file_url(cached)
        else:
            def convert(job):
                job.report(0, '正在转换成可播放的格式…' if plan == 'transcode' else '正在整理视频格式…')
                p = media.make_playable(path, info, plan, lambda f: job.report(f), job.cancel)
                return {'src': file_url(p)}
            out['convert_job'] = jobs.start('convert', convert).id
        return out

    def do_save(self, b):
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了，无法保存字幕')
        return docs.save(path, b.get('doc') or {})

    def do_position(self, b):
        path = b.get('path')
        if path and os.path.isfile(path):
            config.set_position(path, float(b.get('t') or 0), float(b.get('d') or 0))
        return {}

    # -------------------------------------------------------------- 任务 ----
    def do_job(self, b):
        job = jobs.get(b.get('id'))
        if not job:
            raise ValueError('任务不存在')
        return {'job': job.snapshot(int(b.get('since') or 0))}

    def do_cancel(self, b):
        job = jobs.get(b.get('id'))
        if job:
            job.cancel.set()
        return {}

    def do_transcribe(self, b):
        path, lang = b.get('path'), b.get('lang') if b.get('lang') in ('zh', 'en') else 'zh'
        s = config.load()
        provider = b.get('provider') or (s['asr_en'] if lang == 'en' else s['asr_zh'])
        if provider not in ('openai', 'siliconflow', 'doubao'):
            raise ValueError('未知的识别服务')
        pending = b.get('pending') if isinstance(b.get('pending'), dict) else None
        if pending and pending.get('model'):
            s['providers']['doubao']['model'] = pending['model']
        asr.check_ready(s, provider)
        info = media.probe(path)
        teacher = config.get_teacher(b.get('teacher'))
        config.update({'last_lang': lang, 'last_teacher': b.get('teacher') or ''})

        def run(job):
            cues = asr.recognise(path, info['duration'], provider, lang, s, job.cancel, job.report, job.push, pending,
                                 teacher, b.get('title') or Path(path).stem)
            model = s['providers']['doubao']['model'] if provider == 'doubao' else s['providers'][provider].get('asr_model')
            return {'count': len(cues), 'provider': provider, 'model': model, 'lang': lang}
        return {'job': jobs.start('transcribe', run).id, 'provider': provider}

    def do_translate(self, b):
        s = config.load()
        name = s['translate']
        items = [(int(i), str(t)) for i, t in (b.get('items') or []) if str(t).strip()]
        if not items:
            raise ValueError('没有需要翻译的字幕')
        llm.check(s, name)  # 先检查密钥
        teacher = config.get_teacher(b.get('teacher'))

        def run(job):
            llm.translate_all(s, name, items, b.get('title') or '', teacher, job.cancel, job.report,
                              lambda part: job.push([[k, v] for k, v in sorted(part.items())]))
            return {'provider': name}
        return {'job': jobs.start('translate', run).id}

    def do_chat(self, b):
        """AI 回答以任务形式逐段产出，界面轮询拼接。"""
        s = config.load()
        name = s['chat']
        llm.check(s, name)
        d = b.get('doc') or {}
        teacher = config.get_teacher(d.get('teacher'))
        messages = [{'role': 'system', 'content': llm.system_prompt(d, teacher)}]
        if b.get('mode') == 'summary':
            template = (teacher or {}).get('template') or s.get('summary_template') or llm.DEFAULT_TEMPLATE
            boundary = docs.source_boundary(d, media.tail_problem(d['media']) if d.get('media') and os.path.isfile(d['media']) else '')
            messages = llm.summary_messages(d, teacher, template, boundary)
        else:
            for m in (b.get('messages') or [])[-20:]:
                if m.get('role') in ('user', 'assistant') and m.get('content'):
                    messages.append({'role': m['role'], 'content': str(m['content'])})

        def run(job):
            for piece in llm.stream(s, name, messages, job.cancel):
                job.push([piece])
            return {}
        return {'job': jobs.start('chat', run).id}

    def do_review(self, b):
        """查漏补缺：对照完整字幕，把课程重点漏掉的内容补进对应小节。"""
        s = config.load()
        name = s['chat']
        llm.check(s, name)
        d = b.get('doc') or {}
        summary = str(b.get('summary') or d.get('summary') or '')
        if not summary.strip():
            raise ValueError('还没有课程重点')
        teacher = config.get_teacher(d.get('teacher'))

        def run(job):
            job.report(0.05, 'AI 正在对照字幕查漏…')
            out, n, before, after = llm.review_summary(s, name, d, teacher, summary)
            return {'summary': out, 'added': n, 'before': round(before, 3), 'after': round(after, 3)}
        return {'job': jobs.start('review', run).id}

    def do_terms_suggest(self, b):
        """从这节课里找新术语和识别错误（校对改动 + 完整字幕），去掉术语表里已有的，交给用户勾选。"""
        d = b.get('doc') or {}
        teacher = config.get_teacher(d.get('teacher'))
        if not teacher:
            raise ValueError('先在顶栏选择老师，才知道和哪份术语表比对、加进哪份术语表')
        if not d.get('segments'):
            raise ValueError('还没有字幕')
        s = config.load()
        name = s['chat']
        llm.check(s, name)

        def run(job):
            job.report(0.05, 'AI 正在找这节课的新术语…')
            return llm.suggest_terms(s, name, d, teacher)
        return {'job': jobs.start('terms', run).id}

    def do_terms_add(self, b):
        """把勾选的新术语和识别错误加进老师的术语表（关联了 Obsidian 文件就写进文件，先备份）。"""
        return glossary.add(b.get('teacher'), b.get('terms'), b.get('errors'), str(b.get('title') or ''))

    def do_coverage(self, b):
        d = b.get('doc') or {}
        ratio, gaps = llm.coverage(d.get('segments'), d.get('summary') or '')
        return {'ratio': round(ratio, 3), 'gaps': [[a, e] for a, e, _ in gaps]}

    def do_terms_preview(self, b):
        """预览从术语表文件里读出来的术语和识别错误对照。"""
        path = str(b.get('path') or '')
        if not path:
            import webview
            r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=('Markdown / 文本 (*.md;*.txt)', '所有文件 (*.*)'))
            if not r:
                return {'path': None}
            path = r[0]
        text = config.read_text_file(path)
        terms = llm.terms_from_markdown(text)
        words, hints, forced = llm.parse_terms(terms)
        return {'path': path, 'terms': terms, 'words': len(words), 'hints': len(hints), 'chars': len(text)}

    def do_test(self, b):
        s = config.load()
        name = b.get('provider')
        if name not in s['providers']:
            raise ValueError('未知的服务')
        if b.get('kind') == 'chat':
            reply = llm.complete(s, name, [{'role': 'user', 'content': '只回复两个字：你好'}])
            return {'message': '连接正常：' + reply.strip()[:20]}
        return {'message': asr.test_provider(s, name)}

    # -------------------------------------------------------- 导出与剪藏 ----
    def do_export(self, b):
        dest = str(b.get('dest') or '')
        if not dest:
            raise ValueError('没有选择保存位置')
        fmt_name = 'srt' if b.get('format') == 'srt' else 'txt'
        docs.export(b.get('doc') or {}, dest, fmt_name, b.get('mode') or 'both', bool(b.get('timestamps', True)))
        return {'path': dest}

    def do_text(self, b):
        d = b.get('doc') or {}
        mode = b.get('mode') or 'both'
        text = docs.to_srt(d, mode) if b.get('format') == 'srt' else docs.to_txt(d, mode, bool(b.get('timestamps', True)))
        return {'text': text}

    def do_clip(self, b):
        ob = config.load()['obsidian']
        d = b.get('doc') or {}
        teacher = config.get_teacher(d.get('teacher'))
        series = ''
        media_path = os.path.normcase(d.get('media') or '')
        for f in config.load_library()['folders']:
            if media_path.startswith(os.path.normcase(f['path']).rstrip('\\/') + os.sep):
                series = f.get('name') or Path(f['path']).name
        mp = d.get('media') or ''
        boundary = docs.source_boundary(d, media.tail_problem(mp) if mp and os.path.isfile(mp) else '')
        shot = (lambda t: frames.grab(mp, t)) if mp and os.path.isfile(mp) else None
        return docs.clip(d, ob.get('vault'), ob.get('folder'), teacher, series, ob.get('teacher_subfolder', True),
                         ob.get('name_pattern') or '{title}', boundary, shot, bool(b.get('force')))

    def do_reveal(self, b):
        """在资源管理器里显示文件，或用 Obsidian 打开笔记。"""
        target = str(b.get('target') or '')
        if target.startswith('obsidian://') or target.startswith('https://github.com/longpeng1413/'):
            os.startfile(target)
        elif os.path.exists(target):
            subprocess.Popen(['explorer', '/select,', os.path.normpath(target)])
        return {}

    # ------------------------------------------------------------ AI 校对 ----
    def do_correct(self, b):
        s = config.load()
        name = s['correct']
        llm.check(s, name)
        raw = [x for x in (b.get('items') or []) if len(x) >= 2 and str(x[1]).strip()]
        items = [(int(x[0]), str(x[1])) for x in raw]
        times = {int(x[0]): float(x[2]) for x in raw if len(x) >= 3}
        if not items:
            raise ValueError('没有需要校对的字幕')
        teacher = config.get_teacher(b.get('teacher'))
        shots = [f for f in (b.get('frames') or []) if isinstance(f, dict)]

        def run(job):
            def push(part):
                fixed, doubts = part
                job.push([{'i': k, 'text': v} for k, v in sorted(fixed.items())] +
                         [{'i': k, 'doubt': v} for k, v in sorted(doubts.items())])
            llm.correct_all(s, name, items, b.get('title') or '', teacher, job.cancel, job.report, push, times, shots)
            return {'provider': name}
        return {'job': jobs.start('correct', run).id}

    # ------------------------------------------------------------ 书籍 ----
    @staticmethod
    def _book_info(info, d=None):
        shift = (d or {}).get('shift', info.get('shift')) if d and 'shift' in d else info.get('shift')
        return {'kind': info['kind'], 'pages': len(info['pages']), 'scan': info['scan'], 'shift': shift, 'detected_shift': info.get('shift'),
                'chars': info['chars'], 'toc': len(info.get('toc') or []), 'figures': sum(1 for p in info['pages'] if p['figure']),
                'companion': Path(info['companion']).name if info.get('companion') else ''}

    def _open_book(self, path):
        info = book.read(path)
        d = docs.load(path)
        if not d or d.get('kind') != 'book':
            d = book.new_doc(path, info)
        d['pages'] = len(info['pages'])
        if not d.get('teacher'):
            d['teacher'] = library.folder_teacher(path)
        config.add_recent(path, d.get('title') or Path(path).stem)
        return {'path': path, 'kind': 'book', 'doc': d, 'book': self._book_info(info, d),
                'viewer': file_url(path) if info['kind'] == 'pdf' else None}

    def do_book_pages(self, b):
        """Word / 文本在左边显示：每「页」的文字和图片。"""
        info = book.read(b.get('path'))
        shift = b.get('shift') if 'shift' in b else info.get('shift')
        return {'pages': [{'n': p['n'], 'ref': book.page_ref(p['n'], shift), 'text': p['text'],
                           'images': [file_url(x) for x in p.get('images') or []]} for p in info['pages']]}

    def do_book_chapters(self, b):
        """重新识别章节。ai=True 时让 AI 读目录页（后台任务）。"""
        path = b.get('path')
        info = book.read(path)
        if 'shift' in b:
            info = dict(info, shift=b['shift'])
        if not b.get('ai'):
            return {'chapters': book.detect_chapters(info)}
        s = config.load()
        name = s['chat']
        llm.check(s, name)

        def run(job):
            job.report(0.1, 'AI 正在读目录…')
            return {'chapters': book.ai_chapters(s, name, info)}
        return {'job': jobs.start('book', run).id}

    def do_book_organize(self, b):
        """整理选中的章节（一章一章来，每章整理完再查漏）。"""
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('文件不见了')
        d = b.get('doc') or {}
        todo = [int(i) for i in b.get('indexes') or []]
        chapters = d.get('chapters') or []
        if not todo:
            raise ValueError('没有选择要整理的章节')
        s = config.load()
        name = s['chat']
        llm.check(s, name)
        vision = name not in llm.VISION_NOTE
        info = book.read(path)
        if 'shift' in d:
            info = dict(info, shift=d['shift'])
        teacher = config.get_teacher(d.get('teacher'))

        def run(job):
            for k, i in enumerate(todo):
                if job.cancel.is_set():
                    break
                c = chapters[i]
                lo, hi = k / len(todo), (k + 1) / len(todo)

                def prog(f, m=None):
                    job.report(lo + (hi - lo) * f, (m or '') + (f'（第 {k + 1}/{len(todo)} 章）' if len(todo) > 1 else ''))
                md = book.organize_chapter(s, name, path, info, c, d, teacher, vision, prog, job.cancel, chapters)
                prog(0.92, f'正在查漏「{c["title"]}」…')
                md, added, missing = book.review_chapter(s, name, info, c, md, d, teacher)
                prog(0.96, f'正在回页面核对「{c["title"]}」里需要确认的地方…')
                md, verified = book.verify_doubts(s, name, path, info, c, md, d, teacher, vision)
                job.push([{'i': i, 'md': md, 'added': added, 'missing': missing, 'verified': verified}])
            return {'vision': vision}
        return {'job': jobs.start('book', run).id}

    def do_book_overview(self, b):
        d = b.get('doc') or {}
        s = config.load()
        name = s['chat']
        llm.check(s, name)
        chapters = d.get('chapters') or []

        def run(job):
            job.report(0.1, 'AI 正在写全书总览…')
            files = book.chapter_files(chapters)
            info = book.read(d['media'])
            shift = d.get('shift', info.get('shift'))
            for c in chapters:
                c['range'] = book.range_ref(c['start'], c['end'], shift)
            return {'overview': book.overview(s, name, d, chapters, files)}
        return {'job': jobs.start('book', run).id}

    def do_book_clip(self, b):
        d = b.get('doc') or {}
        ob = config.load()['obsidian']
        info = book.read(d['media'])
        return book.clip_book(d, info, ob.get('vault'), ob.get('book_folder') or '', ob.get('book_dir_pattern') or '《{title}》{author}')

    # ------------------------------------------------------------ 画面 ----
    def _vision(self, s, need=True):
        name = s.get('vision') or ''
        if name and name in config.CHAT_PROVIDERS:
            try:
                if name in llm.VISION_NOTE:
                    raise llm.LlmError(llm.VISION_NOTE[name] + '，请到「设置 → 任务用哪个 AI」给「画面识别」换一个能看图的服务')
                llm.check(s, name)
                return name
            except llm.LlmError:
                if need:
                    raise
        if need:
            raise llm.LlmError('还没有设置「画面识别」用哪个 AI，请到「设置 → 任务用哪个 AI」选择')
        return ''

    def do_frames_extract(self, b):
        """找关键帧并写画面记录。没有能看图的 AI 时，只截候选画面。"""
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了')
        s = config.load()
        name = self._vision(s, need=not b.get('no_ai'))
        segs = b.get('segments') or []
        duration = float(b.get('duration') or 0)

        def run(job):
            try:
                out = frames.extract(s, name, path, duration, segs, job.cancel, job.report,
                                     lambda part: job.push(part))
            except frames.Cancelled:
                return {'frames': []}
            return {'frames': out, 'ai': bool(name)}
        return {'job': jobs.start('frames', run).id}

    def do_frames_describe(self, b):
        """给指定的画面写（或重写）画面记录。"""
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了')
        s = config.load()
        name = self._vision(s)
        todo = [{'t': float(f['t']), **{k: v for k, v in f.items() if k != 't'}} for f in (b.get('frames') or []) if isinstance(f, dict) and 't' in f]
        if not todo:
            raise ValueError('没有要识别的画面')

        def run(job):
            try:
                out = frames.describe(s, name, path, todo, b.get('segments') or [], job.cancel, job.report,
                                      on_part=lambda part: job.push(part))
            except frames.Cancelled:
                return {'frames': []}
            return {'frames': out}
        return {'job': jobs.start('frames', run).id}

    def do_doubts_recheck(self, b):
        """用画面复核存疑句：截下每句说话时的画面（常有烧录字幕），能确认的改正，看不清的继续存疑。"""
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了')
        s = config.load()
        name = self._vision(s)
        items = [x for x in (b.get('items') or []) if isinstance(x, dict) and 'i' in x and x.get('text')]
        if not items:
            raise ValueError('没有存疑句')
        shots = [f for f in (b.get('frames') or []) if isinstance(f, dict)]

        def run(job):
            try:
                out = frames.recheck_doubts(s, name, path, items[:60], shots, job.cancel, job.report, on_part=lambda part: job.push(part))
            except frames.Cancelled:
                return {'n': 0}
            return {'n': len(out)}
        return {'job': jobs.start('recheck', run).id}

    def do_frame_url(self, b):
        path = b.get('path')
        if not path or not os.path.isfile(path):
            raise ValueError('视频文件不见了')
        return {'url': file_url(frames.grab(path, float(b.get('t') or 0), small=bool(b.get('small'))))}

    def do_models(self, b):
        name = b.get('provider')
        if name not in config.CHAT_PROVIDERS:
            raise ValueError('未知的服务')
        return {'models': llm.list_models(config.load(), name)}

    # ------------------------------------------------------- ChatGPT 账号 ----
    def do_codex_login(self, b):
        return {'url': codex_auth.start()}

    def do_codex_finish(self, b):
        try:
            return codex_auth.finish(b.get('url') or '')
        except codex_auth.AuthError as e:
            raise ValueError(str(e))

    def do_codex_logout(self, b):
        codex_auth.logout()
        return {}

    # ------------------------------------------------------------ 老师 ----
    def do_teacher_save(self, b):
        t = config.save_teacher(b.get('teacher') or {})
        return {'teacher': t, 'teachers': config.load_teachers()}

    def do_teacher_delete(self, b):
        config.delete_teacher(b.get('id'))
        return {'teachers': config.load_teachers()}

    # ---------------------------------------------------------- 媒体库 ----
    def do_library(self, b):
        return {'groups': library.view()}

    def do_library_add_folder(self, b):
        path = b.get('path')
        if not path:
            import webview
            r = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            path = r[0] if r else None
        if not path:
            return {'added': 0, 'groups': None}
        n = library.add_folder(path)
        return {'added': n, 'groups': library.view()}

    def do_library_add_files(self, b):
        paths = b.get('paths')
        if not paths:
            paths = self.do_pick_media_multi({})['paths']
        n = library.add_files(paths or [])
        return {'added': n, 'groups': library.view()}

    def do_library_remove(self, b):
        library.remove(b.get('kind'), b.get('path') or '')
        return {'groups': library.view()}

    def do_library_set(self, b):
        library.set_folder(b.get('path') or '', **{k: b[k] for k in ('teacher', 'collapsed', 'name') if k in b})
        return {'groups': library.view() if b.get('refresh') else None}

    # ------------------------------------------------------------ 日志与退出 ----
    def do_log(self, b):
        """界面上的脚本错误也记进日志。"""
        level = {'error': 40, 'warn': 30}.get(b.get('level'), 20)
        log.log(level, '[界面] %s', str(b.get('message') or '')[:4000])
        return {}

    def do_about(self, b):
        s = config.load()
        bundled, self._bundled = self._bundled, None  # 只提示一次
        return {'version': self._version, 'bundled': bool(bundled), 'log_dir': str(LOG_DIR), 'data_dir': str(config.DATA_DIR),
                'cache_dir': str(config.CACHE_DIR), 'storage_root': str(config.STORAGE_ROOT),
                'storage_custom': s.get('storage_dir') or '', 'storage_default': str(config.default_storage_root()), **cache.info()}

    def do_set_storage(self, b):
        """更改缓存和日志的位置（空 = 软件所在文件夹），重启后生效。"""
        path = str(b.get('path') or '').strip()
        if b.get('pick'):
            import webview
            r = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(config.STORAGE_ROOT))
            if not r:
                return {'changed': False}
            path = r[0]
        if path:
            probe = Path(path) / config.CACHE_NAME
            try:
                probe.mkdir(parents=True, exist_ok=True)
                (probe / '.write-test').write_text('ok', encoding='utf-8')
                (probe / '.write-test').unlink()
            except OSError:
                raise ValueError('这个位置不能写入，请换一个文件夹')
        config.update({'storage_dir': path})
        log.info('缓存和日志位置改为：%s', path or '软件所在文件夹')
        return {'changed': True, 'root': path or str(config.default_storage_root())}

    # ------------------------------------------------------------ 设置导入导出 ----
    def do_export_settings(self, b):
        import json as _json
        import webview
        r = b.get('dest') or self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename='猛人学习设置.json', file_types=('设置文件 (*.json)',))
        if not r:
            return {'path': None}
        dest = r if isinstance(r, str) else r[0]
        if not dest.lower().endswith('.json'):
            dest += '.json'
        s = config.load()
        s.pop('storage_dir', None)
        s.pop('recent', None)
        if not b.get('keys'):
            for name, p in s['providers'].items():
                for k in config.SECRETS:
                    if k in p:
                        p[k] = ''
                if 'oauth' in p:
                    p['oauth'] = {}
                if name == 'doubao':
                    p['tos_ak'] = ''
        data = {'app': config.APP_NAME, 'kind': 'settings-export', 'version': self._version,
                'exported': time.strftime('%Y-%m-%d %H:%M:%S'), 'with_keys': bool(b.get('keys')),
                'settings': s, 'teachers': config.load_teachers()}
        if b.get('library'):
            data['library'] = config.load_library()
        Path(dest).write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        log.info('导出设置到 %s（%s密钥）', dest, '含' if b.get('keys') else '不含')
        return {'path': dest}

    def do_import_settings(self, b):
        import json as _json
        import webview
        r = [b['path']] if b.get('path') else self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=('设置文件 (*.json)', '所有文件 (*.*)'))
        if not r:
            return {'imported': False}
        try:
            data = _json.loads(Path(r[0]).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            raise ValueError('读不出这个文件，请选择从猛人学习（猛人字幕）导出的设置文件')
        if not isinstance(data, dict) or data.get('app') not in (config.APP_NAME, config.OLD_APP_NAME) or not isinstance(data.get('settings'), dict):
            raise ValueError('这不是猛人学习（猛人字幕）导出的设置文件')
        cur = config.load()
        new = config._merge(config.DEFAULTS, data['settings'])
        new['storage_dir'] = cur.get('storage_dir', '')  # 位置跟电脑有关，保留这台电脑的
        new['recent'] = cur.get('recent', [])
        for name, p in new['providers'].items():  # 文件里没带密钥时，保留这台电脑上已有的
            for k in list(config.SECRETS) + ['oauth', 'tos_ak']:
                if k in p and not p[k] and cur['providers'].get(name, {}).get(k):
                    p[k] = cur['providers'][name][k]
        config.save(new)
        n_t = 0
        for t in data.get('teachers') or []:
            if isinstance(t, dict) and t.get('id'):
                config.save_teacher(t)
                n_t += 1
        n_f = 0
        if isinstance(data.get('library'), dict):
            lib = config.load_library()
            known = {os.path.normcase(f['path']) for f in lib['folders']}
            for f in data['library'].get('folders') or []:
                if isinstance(f, dict) and f.get('path') and os.path.normcase(f['path']) not in known:
                    lib['folders'].append(f)
                    n_f += 1
            for p in data['library'].get('files') or []:
                if p not in lib['files']:
                    lib['files'].append(p)
            config.save_library(lib)
        log.info('从 %s 导入设置：%d 位老师，%d 个媒体库文件夹', r[0], n_t, n_f)
        return {'imported': True, 'teachers': n_t, 'folders': n_f, 'with_keys': bool(data.get('with_keys'))}

    def do_cache_clear(self, b):
        return {'freed': cache.cleanup(clear_all=True), **cache.info()}

    def do_open_dir(self, b):
        target = {'log': LOG_DIR, 'data': config.DATA_DIR, 'cache': config.CACHE_DIR}.get(b.get('which'), LOG_DIR)
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))
        return {}

    def do_quit(self, b):
        """界面保存完毕，可以关窗了。"""
        log.info('界面已保存，准备退出')
        self._quit.set()
        return {}
