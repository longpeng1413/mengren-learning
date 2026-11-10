"""本机识别（免费，不联网）：sherpa-onnx + SenseVoice Small int8（2026-11-10 加入）。

和猛人输入法、猛人快传同一份模型（k2-fsa 转换的 2024-07-17 官方原版；2025-09-09 那版是粤语模型转来的，普通话漏字多，不用）。
2026-11-09 用四节课实测（docs/本地语音识别评估（2026-11-09）.md）：术语多的课（峰级）错字是豆包的两倍多、术语基本认不出，
AI 校对后能把术语改回来；术语少的课（人间落叶）AI 校对后已经接近豆包；不丢句首的字、断句和人工稿接近（豆包常丢「你看」「这」「不知道」）。
用户定：默认用本机识别，术语多的文件夹（峰级）在媒体库里设成豆包。SenseVoice 不能用热词（sherpa-onnx 的热词只给 transducer 模型）。

- 模型：先找自己的（%LOCALAPPDATA%\\猛人学习\\本机识别模型\\），再找猛人快传下好的（核对过的直接用，不复制）；都没有时下载
  （猛人快传 GitHub Release 上的镜像，.part 断点续传，核对 SHA-256，7 天没续上的半截下载由 cache.cleanup 删掉）。
- 长录音：每段不超过 20 秒，在第 10～20 秒里最安静的 0.1 秒处切开（猛人输入法的做法）；按句末标点和每个字的时间切句；
  切开处停顿很短（< 0.3 秒，多半是一句话中间）时，把前后两半接回一句（实测以前会出现「它一定是分。」「之后连接着极……」）。
- 去掉单独的「呃」「嗯」（豆包和人工稿都不写；规则只针对单独成词、前后是标点的）。
- 模型在进程里只加载一份，整理完 3 分钟没再用就释放内存。"""
import hashlib
import os
import re
import threading
import time
import wave
from pathlib import Path

from .log import log

FOLDER = 'sensevoice-int8-2024-07-17'
FILES = [('model.int8.onnx', 239233841, 'c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51'),
         ('tokens.txt', 315894, 'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc')]
URL = 'https://github.com/longpeng1413/mengren-kuaichuan/releases/download/asr-model-sensevoice-2024-07-17/'
OWN_ROOT = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / '猛人学习' / '本机识别模型'
KUAICHUAN = Path(os.environ.get('APPDATA') or Path.home()) / 'com.personal.lantransfer' / 'lan_transfer' / 'asr_model' / FOLDER
VERIFIED = '.verified'
PART_KEEP = 7 * 86400
SR = 16000
MAX_S, LO_S, WIN = 20.0, 10.0, 0.1
JOIN_GAP = 0.3   # 切开处停顿短于这么多秒，算一句话中间
LONG = 60        # 一句超过这么多字，在逗号处再切
IDLE = 180       # 模型 3 分钟没用就释放内存
FILLER = re.compile(r'(^|[，。？！、；：])\s*(?:嗯|额)+\s*[，。、]?')  # 「嗯」「额」只去单独成词的（「额度」不能动）
UH = re.compile(r'呃+[，、]?')  # 「呃」不会是有意义的字，哪里都去掉
# 模型在说话中间的停顿处也会打句号（实测「所以。」「其实我们聊的就是一个比较。」后面才接「清晰的一个顺序框架」）：
# 以这些明显没说完的词结尾、和下一句挨得近（< 1.5 秒）、合起来不太长的，接回一句。
# 单字的（的 / 是 / 就 / 在 / 和）不算：「其实我好讨厌的。」「我不适合一夜暴富的。」这种以「的」结尾的多半是完整的一句。
UNFINISHED = ('所以', '然后', '但是', '而且', '因为', '如果', '就是', '还是', '比较', '一个', '这个', '那个', '一些', '叫做')
JOIN_NEAR = 1.5


class LocalAsrError(Exception):
    pass


class Cancelled(LocalAsrError):
    pass


def own_dir() -> Path:
    return OWN_ROOT / FOLDER


def _sizes_ok(d: Path) -> bool:
    return all((d / n).is_file() and (d / n).stat().st_size == size for n, size, _ in FILES)


def _sha_ok(d: Path) -> bool:
    for n, _size, sha in FILES:
        h = hashlib.sha256()
        with open(d / n, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        if h.hexdigest() != sha:
            return False
    return True


_found = {}  # 这次运行里已经核对过的文件夹 → 能不能用（核对猛人快传那份要一秒多，不用每次都算）


def find_model():
    """能用的模型文件夹（自己的优先，其次猛人快传的）；没有返回 None。
    自己的核对过后留 .verified 标记；猛人快传的文件夹不写东西，这次运行里核对一次记住结果。
    MENGREN_ASR_MODEL_DIR：界面自测用（测试把 APPDATA 指到了临时文件夹，借这台电脑上现成的模型，只读）。"""
    extra = [Path(os.environ['MENGREN_ASR_MODEL_DIR'])] if os.environ.get('MENGREN_ASR_MODEL_DIR') else []
    for d in [own_dir(), KUAICHUAN] + extra:
        if _check(d):
            return d
    return None


def _check(d: Path) -> bool:
    try:
        if not _sizes_ok(d):
            _found.pop(str(d), None)
            return False
        if _found.get(str(d)) or (d / VERIFIED).exists():
            return True
        ok = _sha_ok(d)
        _found[str(d)] = ok
        if ok and d == own_dir():
            (d / VERIFIED).write_text('1', encoding='utf-8')
        return ok
    except OSError:
        return False


def status() -> dict:
    """设置里显示：模型在哪、占多大（只算自己的）、正在不在内存里。"""
    d = find_model()
    own = own_dir()
    size = sum(f.stat().st_size for f in own.glob('*') if f.is_file()) if own.exists() else 0
    return {'ready': bool(d), 'where': ('own' if d == own else 'kuaichuan') if d else '', 'path': str(d or own),
            'own_size': size, 'download_mb': round(sum(s for _n, s, _h in FILES) / 1048576), 'loaded': _rec['r'] is not None}


def download(progress=lambda f, m=None: None, cancel=None, proxy=''):
    """下载模型到自己的文件夹：断点续传、核对 SHA-256。已经有能用的就直接返回。"""
    if find_model():
        return find_model()
    import requests
    d = own_dir()
    d.mkdir(parents=True, exist_ok=True)
    total = sum(size for _n, size, _h in FILES)
    done_before = 0
    for name, size, sha in FILES:
        dest, part = d / name, d / (name + '.part')
        if dest.is_file() and dest.stat().st_size == size:
            done_before += size
            continue
        last = None
        for px in [None] + ([{'http': proxy, 'https': proxy}] if proxy else []):
            have = part.stat().st_size if part.exists() else 0
            try:
                headers = {'User-Agent': 'mengren-learning', 'Range': f'bytes={have}-'} if have else {'User-Agent': 'mengren-learning'}
                with requests.get(URL + name, stream=True, timeout=(10, 60), proxies=px, headers=headers) as r:
                    if r.status_code == 416:  # 已经下完了
                        break
                    if r.status_code not in (200, 206):
                        last = f'HTTP {r.status_code}'
                        continue
                    if r.status_code == 200:
                        have = 0
                    got = have
                    with open(part, 'ab' if have else 'wb') as f:
                        for chunk in r.iter_content(512 * 1024):
                            if cancel is not None and cancel.is_set():
                                raise Cancelled('已停止下载')
                            f.write(chunk)
                            got += len(chunk)
                            progress((done_before + got) / total, f'正在下载本机识别模型 {(done_before + got) / 1048576:.0f} / {total / 1048576:.0f} MB')
                break
            except requests.RequestException as e:
                last = e
        else:
            raise LocalAsrError(f'下载本机识别模型失败（{str(last)[:80]}）。可以稍后再试（会接着下），或在「设置 → 网络代理」里填上代理地址')
        h = hashlib.sha256()
        with open(part, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        if part.stat().st_size != size or h.hexdigest() != sha:
            part.unlink(missing_ok=True)
            raise LocalAsrError('下载的本机识别模型校验不对（可能下载时坏了），已经删掉，请再试一次')
        os.replace(part, dest)
        done_before += size
    (d / VERIFIED).write_text('1', encoding='utf-8')
    log.info('本机识别模型下载好了：%s', d)
    return d


def delete():
    """删掉自己下载的模型（不动猛人快传的）。先放掉内存里那份。"""
    release()
    d = own_dir()
    if d.exists():
        for f in d.glob('*'):
            f.unlink(missing_ok=True)
        d.rmdir()


def selftest(tmp_dir: Path) -> dict:
    """「测试本机识别」：生成 2 秒的测试音，真识别一次（看引擎、运行库、模型都能用）。返回 {seconds}。"""
    from . import media
    d = find_model()
    if not d:
        raise LocalAsrError('还没有本机识别模型，先点「现在下载」')
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav = tmp_dir / f'local-asr-test-{os.getpid()}.wav'
    try:
        media.run([media.tool('ffmpeg'), '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=300:duration=2', '-ac', '1',
                   '-ar', str(SR), '-c:a', 'pcm_s16le', str(wav)], timeout=60)
        t0 = time.time()
        transcribe(wav, 'zh', model_dir=d)
        return {'seconds': round(time.time() - t0, 1)}
    finally:
        wav.unlink(missing_ok=True)


def cleanup(now=None):
    """删掉 7 天没续上的半截下载（软件启动、命令行整理前由 cache.cleanup 调用）。"""
    now = now or time.time()
    n = 0
    for f in OWN_ROOT.glob('*/*.part') if OWN_ROOT.exists() else []:
        try:
            if now - f.stat().st_mtime > PART_KEEP:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


# ------------------------------------------------------------------ 识别 ----
_rec = {'r': None, 'lang': '', 'used': 0.0}
_rec_lock = threading.Lock()


def _recognizer(model_dir: Path, language: str):
    """按课程语言加载（实测都是指定中文跑的：中文课指定 zh，英文课 en，其他自动判断）。换了语言才重新加载。"""
    import sherpa_onnx
    lang = language if language in ('zh', 'en') else 'auto'
    with _rec_lock:
        if _rec['r'] is None or _rec['lang'] != lang:
            threads = max(1, min(4, (os.cpu_count() or 2) // 2))
            _rec['r'] = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(model_dir / 'model.int8.onnx'), tokens=str(model_dir / 'tokens.txt'), num_threads=threads,
                use_itn=True, language=lang, debug=False)
            _rec['lang'] = lang
            log.info('本机识别模型加载好了（%s，%d 个线程）', lang, threads)
        _rec['used'] = time.time()
        return _rec['r']


def release():
    with _rec_lock:
        if _rec['r'] is not None:
            _rec['r'] = None
            log.info('本机识别模型已从内存释放')


def _idle_release():
    """整理完 3 分钟没再用就释放内存（一批课连着识别时不反复加载）。"""
    def check():
        if _rec['r'] is not None and time.time() - _rec['used'] >= IDLE:
            release()
    t = threading.Timer(IDLE + 1, check)
    t.daemon = True
    t.start()


def _read_wav(path: Path):
    import numpy as np
    with wave.open(str(path)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise LocalAsrError('本机识别要 16k 单声道音频')
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def chunks(x):
    """切段：每段不超过 20 秒，在第 10～20 秒里最安静的 0.1 秒处切开。返回 [(开始, 结束, 切口停顿秒数)]。"""
    import numpy as np
    out, pos, n = [], 0, int(WIN * SR)
    while pos < len(x):
        if len(x) - pos <= MAX_S * SR:
            out.append((pos, len(x), 9.0))
            break
        a, b = pos + int(LO_S * SR), pos + int(MAX_S * SR)
        seg = x[a:b]
        k = len(seg) // n
        energy = (seg[:k * n].reshape(k, n) ** 2).mean(axis=1)
        i = int(energy.argmin())
        # 「安静」以这一段最安静处为准（不超过两成分位的 1.5 倍），算出切口所在那段停顿有多长
        quiet = min(max(float(energy[i]) * 4, 1e-7), float(np.percentile(energy, 20)) * 1.5)
        left = right = i
        while left > 0 and energy[left - 1] <= quiet:
            left -= 1
        while right < k - 1 and energy[right + 1] <= quiet:
            right += 1
        cut = a + ((left + right) // 2) * n + n // 2  # 切在停顿正中间，前后的字都不容易被切掉
        out.append((pos, cut, (right - left + 1) * WIN))
        pos = cut
    return out


def _sentences(toks, ts, offset, end):
    """一段里的字 → [[开始, 结束, 文字]]：按句末标点切，过长的在逗号处再切。"""
    out, cur, st = [], '', None
    for i, (tk, t) in enumerate(zip(toks, ts)):
        st = offset + t if st is None else st
        cur += tk
        stop = bool(tk) and tk[-1] in '。！？!?'
        if not stop and len(cur) >= LONG and tk and tk[-1] in '，,；;':
            stop = True
        if stop:
            nxt = offset + ts[i + 1] if i + 1 < len(ts) else end
            out.append([round(st, 2), round(max(nxt, st), 2), cur.strip()])
            cur, st = '', None
    if cur.strip():
        out.append([round(st, 2), round(end, 2), cur.strip()])
    return out


def clean(text: str) -> str:
    """去掉「呃」和单独的「嗯」「额」，修正数字转换的「十0」，整理空格。"""
    t = UH.sub('', text.strip())
    t = FILLER.sub(lambda m: m.group(1), t)
    t = re.sub(r'(?<![\d一二三四五六七八九])十0(?!\d)', '十', t)  # 模型的数字转换偶尔写出「十0年」
    t = re.sub(r'\s+', ' ', t).strip()
    return t.lstrip('，、')


def join_unfinished(sents):
    """[[开始, 结束, 文字]]：以没说完的词结尾的句子接上下一句（见 UNFINISHED）。"""
    out = []
    for s in sents:
        if out:
            prev = out[-1]
            body = prev[2].rstrip('。')
            if (prev[2].endswith('。') and body.endswith(UNFINISHED) and s[0] - prev[1] < JOIN_NEAR
                    and len(body) + len(s[2]) <= LONG + 20):
                out[-1] = [prev[0], s[1], body + s[2]]
                continue
        out.append(list(s))
    return out


def transcribe(wav: Path, language='zh', cancel=None, progress=lambda f, m=None: None, on_cues=lambda cues: None, model_dir=None):
    """识别 16k 单声道 wav，边识别边 on_cues(这批字幕)。返回全部字幕 [{start, end, text}]。"""
    d = model_dir or find_model()
    if not d:
        raise LocalAsrError('本机识别模型还没有准备好')
    x = _read_wav(wav)
    progress(0.0, '正在加载本机识别模型…')
    rec = _recognizer(d, language)
    plan = chunks(x)
    out, held, held_kind = [], None, ''  # held：上一段最后一句，看下一段开头要不要接回去（gap = 切口停顿短；unfinished = 没说完）

    def emit(cues):
        cues = [dict(start=a, end=b, text=clean(t)) for a, b, t in cues]
        cues = [c for c in cues if c['text'] and re.search(r'\w', c['text'])]
        if cues:
            out.extend(cues)
            on_cues(cues)
    try:
        for k, (a, b, gap) in enumerate(plan):
            if cancel is not None and cancel.is_set():
                raise Cancelled('已取消')
            s = rec.create_stream()
            s.accept_waveform(SR, x[a:b])
            rec.decode_stream(s)
            r = s.result
            toks, ts = list(r.tokens), list(r.timestamps)
            if toks and len(ts) == len(toks):
                sents = _sentences(toks, ts, a / SR, b / SR)
            else:
                sents = [[round(a / SR, 2), round(b / SR, 2), r.text.strip()]] if r.text.strip() else []
            if held and sents and (held_kind == 'gap' or sents[0][0] - held[1] < JOIN_NEAR):
                # 上一段的最后一句被切口切成了两半（或者没说完）：接回去
                first = sents.pop(0)
                body = held[2].rstrip('。')
                sep = '，' if held_kind == 'gap' and held[2].endswith('。') else ''
                sents.insert(0, [held[0], first[1], body + sep + first[2]])
            elif held:
                emit([held])
            held, held_kind = None, ''
            sents = join_unfinished(sents)
            if sents and k + 1 < len(plan):
                last = sents[-1]
                if gap < JOIN_GAP and not last[2].endswith(('？', '?', '！', '!')):
                    held, held_kind = sents.pop(), 'gap'
                elif last[2].endswith('。') and last[2].rstrip('。').endswith(UNFINISHED):
                    held, held_kind = sents.pop(), 'unfinished'
            emit(sents)
            progress((k + 1) / len(plan), f'本机识别中… {k + 1}/{len(plan)} 段')
        if held:
            emit([held])
    finally:
        _rec['used'] = time.time()
        _idle_release()
    return out


# ------------------------------------------------------- 拆分一句：找准时间 ----
# 2026-11-10 用户：拆分后下一句的开头还是上一句的「方方面面的」。以前按字数比例估拆分时间，老师语速不匀、
# 中间有停顿时会差一两秒。现在把这一句的声音再识别一遍，拿到每个字的时间，按光标前的字数对上后半句的第一个字，
# 拆在它前面那段停顿的正中间；模型不能用时退一步，在估计时间附近找最近的停顿。
SPLIT_PAD = 0.3      # 前后多取一点声音
SPLIT_MAX = 60.0     # 超过这么长的句子不重新识别（模型一次听太长认不准），只找停顿
QUIET_DB = 6.0       # 比这一段最安静的两成高不过这么多分贝，算停顿


def _clip(media_path: str, a: float, b: float):
    """取 [a, b] 秒的声音，16k 单声道。"""
    import subprocess
    import numpy as np
    from . import media
    r = subprocess.run([media.tool('ffmpeg'), '-v', 'error', '-ss', f'{a:.3f}', '-t', f'{b - a:.3f}', '-i', media_path,
                        '-vn', '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                       capture_output=True, timeout=60, creationflags=media.NO_WINDOW)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def quiet_runs(x, min_len=0.05):
    """每 10 毫秒算一次音量，返回停顿 [(开始秒, 结束秒)]（相对这段声音的开头）。"""
    import numpy as np
    hop = SR // 100
    n = len(x) // hop
    if n < 3:
        return []
    db = 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-5)
    # 停顿：比最安静的两成高不过 6 分贝，而且比响的地方（九成分位）至少低 15 分贝（整段一样响时没有停顿）
    quiet = db < min(np.percentile(db, 20) + QUIET_DB, np.percentile(db, 90) - 15)
    runs, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) / 100 >= min_len:
                runs.append((i / 100, j / 100))
            i = j
        else:
            i += 1
    return runs


def _chars(s: str):
    return [c for c in (s or '').lower() if c.isalnum()]  # 汉字、字母、数字（标点、空格不算）


def align_split(before: str, after: str, toks, ts):
    """光标前的文字 before、后的文字 after，对上识别出的字 → (前一个字的时间, 后半句第一个字的时间)，对不上返回 None。"""
    import difflib
    rec, rt = [], []
    for tk, t in zip(toks, ts):
        for c in str(tk).lower():
            if c.isalnum():
                rec.append(c)
                rt.append(float(t))
    user, nb = _chars(before) + _chars(after), len(_chars(before))
    if not rec or nb == 0 or nb >= len(user):
        return None
    blocks = [bl for bl in difflib.SequenceMatcher(None, user, rec, autojunk=False).get_matching_blocks() if bl.size]
    k = None
    for bl in blocks:
        if bl.a <= nb < bl.a + bl.size:
            k = bl.b + nb - bl.a
            break
    if k is None:  # 光标正好在对不上的几个字里：用后面最近的那一块往前推
        nxt = next((bl for bl in blocks if bl.a > nb), None)
        prev = [bl for bl in blocks if bl.a + bl.size <= nb]
        lo = prev[-1].b + prev[-1].size if prev else 0
        if nxt:
            k = max(lo, nxt.b - (nxt.a - nb))
        elif prev:
            k = lo + (nb - prev[-1].a - prev[-1].size)
    if k is None or k <= 0 or k >= len(rec):
        return None
    return rt[k - 1], rt[k]


def split_point(media_path: str, start: float, end: float, before: str, after: str, guess: float, language='zh') -> dict:
    """拆分 [start, end] 这一句：返回 {'t': 拆分时间, 'how': 'asr' 按字的时间 | 'quiet' 估计附近的停顿}，都找不到时 t 为 None。"""
    a, b = max(0.0, start - SPLIT_PAD), end + SPLIT_PAD
    x = _clip(media_path, a, b)
    if len(x) < SR // 10:
        return {'t': None, 'how': ''}
    runs = quiet_runs(x)
    lo, hi = start + 0.05, end - 0.05

    def inside(t):
        return t if t is not None and lo < t < hi else None
    d = find_model() if end - start <= SPLIT_MAX else None
    if d:
        try:
            rec = _recognizer(d, language)
            s = rec.create_stream()
            s.accept_waveform(SR, x)
            rec.decode_stream(s)
            hit = align_split(before, after, list(s.result.tokens), list(s.result.timestamps))
        except Exception as e:  # 模型出问题不影响拆分，退回找停顿
            log.warning('拆分时重新识别失败：%s', e)
            hit = None
        finally:
            _rec['used'] = time.time()
            _idle_release()
        if hit:
            p, q = hit
            gaps = [(max(s0, p + 0.1), min(e0, q + 0.15)) for s0, e0 in runs]
            gaps = [g for g in gaps if g[1] - g[0] >= 0.03]
            t = (sum(max(gaps, key=lambda g: g[1] - g[0])) / 2) if gaps else q - 0.05
            t = inside(round(a + t, 2))
            if t is not None:
                return {'t': t, 'how': 'asr'}
    near = [(abs(a + (s0 + e0) / 2 - guess), a + (s0 + e0) / 2) for s0, e0 in runs
            if e0 - s0 >= 0.15 and abs(a + (s0 + e0) / 2 - guess) <= 3]
    near = [(dist, t) for dist, t in near if inside(t) is not None]
    return {'t': round(min(near)[1], 2), 'how': 'quiet'} if near else {'t': None, 'how': ''}