"""设置与目录。
- 设置、老师档案、媒体库、播放进度（都很小）：%APPDATA%\\猛人学习\\（1.6 以前叫「猛人字幕」，第一次启动时自动搬过来）
- 缓存和日志（会变大，可以随时删除）：默认放在软件所在文件夹，
  「缓存（可随时删除）」和「日志（可随时删除）」；也可以在设置里换到别的位置（重启后生效）。"""
import copy
import json
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

APP_NAME = '猛人学习'
OLD_APP_NAME = '猛人字幕'  # 1.6 以前的名字
_APPDATA = Path(os.environ.get('APPDATA') or Path.home())
DATA_DIR = _APPDATA / APP_NAME
OLD_DATA_DIR = _APPDATA / OLD_APP_NAME


def _migrate_data_dir():
    """改名后第一次启动：把旧名字下的设置、老师档案、媒体库、播放进度整个搬过来。"""
    if DATA_DIR.exists() or not OLD_DATA_DIR.is_dir():
        return
    try:
        os.replace(OLD_DATA_DIR, DATA_DIR)
    except OSError:
        try:
            shutil.copytree(OLD_DATA_DIR, DATA_DIR)
        except OSError:
            pass


_migrate_data_dir()
SETTINGS_FILE = DATA_DIR / 'settings.json'
POSITIONS_FILE = DATA_DIR / 'positions.json'
TEACHERS_FILE = DATA_DIR / 'teachers.json'
LIBRARY_FILE = DATA_DIR / 'library.json'
CACHE_NAME = '缓存（可随时删除）'
LOG_NAME = '日志（可随时删除）'
# 旧版本放在 C 盘的位置（启动时会清掉）
OLD_CACHE_DIR = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / OLD_APP_NAME / 'cache'
OLD_LOG_DIR = DATA_DIR / 'logs'
OLD_WEBVIEW_DIR = DATA_DIR / 'webview'


def app_dir() -> Path:
    """软件所在文件夹（打包后是 exe 所在文件夹，开发时是项目文件夹）。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / '.write-test'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        return True
    except OSError:
        return False


def default_storage_root() -> Path:
    return app_dir()


def _storage_root() -> Path:
    try:
        custom = (json.loads(SETTINGS_FILE.read_text(encoding='utf-8')) or {}).get('storage_dir')
    except (OSError, ValueError, AttributeError):
        custom = None
    for root in ([Path(custom)] if custom else []) + [default_storage_root()]:
        if _writable(root / CACHE_NAME):
            return root
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / APP_NAME  # 软件装在不能写的地方时


STORAGE_ROOT = _storage_root()
CACHE_DIR = STORAGE_ROOT / CACHE_NAME
LOG_DIR = STORAGE_ROOT / LOG_NAME


def resource_dir() -> Path:
    """打包成 exe 后，web 等资源在 PyInstaller 的解包目录里。"""
    base = getattr(sys, '_MEIPASS', None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


# 能做翻译、校对、总结和对话的 AI 服务（顺序即界面里的顺序）
CHAT_PROVIDERS = ['codex', 'claude', 'gemini', 'deepseek', 'ark', 'siliconflow', 'openai']

DEFAULTS = {
    'providers': {
        'codex': {'oauth': {}, 'chat_model': '', 'use_proxy': True},
        'claude': {'key': '', 'chat_model': 'claude-opus-5-5', 'use_proxy': True},
        'gemini': {'key': '', 'chat_model': 'gemini-flash-latest', 'use_proxy': True},
        'deepseek': {'key': '', 'base_url': 'https://api.deepseek.com/v1', 'chat_model': 'deepseek-flash', 'use_proxy': False},
        'ark': {'key': '', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3', 'chat_model': '', 'use_proxy': False},
        'siliconflow': {'key': '', 'base_url': 'https://api.siliconflow.cn/v1', 'asr_model': 'Qwen/Qwen3-ASR-1.7B',
                        'chat_model': '', 'use_proxy': False},
        'openai': {'key': '', 'base_url': 'https://api.openai.com/v1', 'asr_model': 'whisper-1', 'chat_model': 'gpt-5-mini', 'use_proxy': True},
        # 豆包可以填几个账号（2026-10-14）：key / app_id / access_token 是主账号（name 是它的名字），
        # more_keys 是备用账号 [{id, name, key}]；spent 记哪个账号的哪种模型额度用完了 {"账号id|模型": "日期"}。
        # 模型不让选了：按 asr.DOUBAO_ORDER 自动换，model 只是识别时临时填进去的。对象存储（TOS）不再需要。
        'doubao': {'key': '', 'app_id': '', 'access_token': '', 'name': '', 'more_keys': [], 'spent': {},
                   'base_url': 'https://openspeech.bytedance.com/api/v3',
                   'model': 'stream2', 'pick': '', 'pick_account': '', 'enable_ddc': False, 'stream_speed': 8, 'use_proxy': False},
    },
    'proxy': '',
    'asr_en': 'openai',
    'asr_zh': 'doubao',
    'translate': 'deepseek',
    'correct': 'deepseek',
    'chat': 'deepseek',
    'vision': 'codex',
    'summary_template': '',
    'auto_correct': True,
    'theme': 'dark',
    'accent': '#e8b04a',
    'font_size': 17,
    'line_height': 1.6,
    'cue_gap': 9,
    'cue_width': 0,
    'font_family': 'yahei',
    # 书的整理笔记、原文文字版的文字设置（2026-10-29 用户要求「像字幕一样能设」；和字幕分开记）
    'book_font_size': 15,
    'book_line_height': 1.8,
    'book_gap': 8,
    'book_width': 0,
    'book_font_family': 'yahei',
    'split': 0.5,
    'sidebar': True,
    'lib_w': 270,
    'ai_dock': 'bottom',
    'ai_hidden': False,
    'ai_w': 440,
    'video_h': 0.55,
    'follow': True,
    'show_tr': True,
    'tts_engine': 'edge',  # 朗读：edge 在线 / baidu 百度 / system 系统声音（2026-10-21）
    'tts_voice': 'zh-CN-XiaoxiaoNeural',
    'tts_voice_en': 'en-US-AriaNeural',  # 英文句子用的 Edge 声音（2026-10-29，外文书朗读原文）
    'tts_sys_voice': '',
    'tts_rate': 1.0,
    'tts_hl': 'yellow',
    'overlay': False,
    'last_lang': 'zh',
    'last_teacher': '',
    'storage_dir': '',
    'obsidian': {'vault': '', 'folder': '猛人学习', 'teacher_subfolder': True, 'name_pattern': '{title}',
                 'book_folder': '猛人学习/书籍', 'book_dir_pattern': '《{title}》{author}'},
    'book_rules_file': '',
    'auto_review': True,
    'close_to_tray': True,  # 点右上角 × 时收到右下角托盘，不退出
    'recent': [],
}

PROVIDER_NAMES = {'codex': 'ChatGPT（账号登录）', 'claude': 'Claude', 'gemini': 'Gemini', 'deepseek': 'DeepSeek',
                  'ark': '豆包大模型（火山方舟）', 'siliconflow': '硅基流动', 'openai': 'OpenAI', 'doubao': '豆包语音（火山引擎）'}
_lock = threading.Lock()


def _merge(base, extra):
    out = copy.deepcopy(base)
    if not isinstance(extra, dict):
        return out
    for k, v in extra.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = copy.deepcopy(v) if not out[k] else _merge(out[k], v)  # 空字典（如登录凭证）整体保留
        elif k in out:
            out[k] = v
    return out


class DataError(Exception):
    """设置、老师档案、媒体库文件读不出来。不能当成空的继续用，否则一保存就把真数据冲掉。"""


_log = logging.getLogger('mengren')
SNAPSHOT_DIR = DATA_DIR / 'backups' / '数据'
SNAPSHOT_KEEP = 30  # 每个文件留最近 30 天的每日快照


def _read_json(path, default):
    """文件不存在才返回 default。读失败（被杀毒软件或另一个窗口占用、写到一半）先重试，再用上一版副本 .bak，
    都不行就报错，绝不悄悄当成空的。"""
    path = Path(path)
    bak = path.with_name(path.name + '.bak')
    if not path.exists():
        if bak.exists():
            _log.warning('%s 不见了，改用上一版副本', path.name)
            path = bak
        else:
            return default
    err = None
    for i in range(8):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            err = e
            time.sleep(0.15 * (i + 1))
    _log.warning('读不出 %s：%r，改用上一版副本', path.name, err)
    try:
        return json.loads(bak.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    raise DataError(f'读不出「{path.name}」（{err}）。请关闭其他打开的猛人学习后重试；数据没有丢，每日备份在 {SNAPSHOT_DIR}')


def _snapshot(path: Path):
    """每天第一次写某个文件前，把旧内容另存一份快照。"""
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        dest = SNAPSHOT_DIR / f'{path.stem}-{time.strftime("%Y%m%d")}{path.suffix}'
        if not dest.exists():
            shutil.copy2(path, dest)
            for old in sorted(SNAPSHOT_DIR.glob(f'{path.stem}-*{path.suffix}'))[:-SNAPSHOT_KEEP]:
                old.unlink(missing_ok=True)
    except OSError:
        _log.warning('保存 %s 的每日快照失败', path.name, exc_info=True)


def _write_json(path, data, indent=2):
    """先写临时文件再替换；替换前把上一版留成 .bak，并留每日快照。被占用时重试。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding='utf-8')
    if path.exists() and path != POSITIONS_FILE:  # 播放进度写得很频繁，不留副本
        _snapshot(path)
        try:
            shutil.copy2(path, path.with_name(path.name + '.bak'))
        except OSError:
            pass
    for i in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.15 * (i + 1))
    os.replace(tmp, path)


def load() -> dict:
    with _lock:
        return _merge(DEFAULTS, _read_json(SETTINGS_FILE, {}))


def save(settings: dict) -> None:
    """保存整份设置。只想改几项请用 update()。
    保险：传进来的设置里没有的项，从现有文件补回（两层），防止只存一项就把密钥、登录、Obsidian 位置全部冲掉。"""
    with _lock:
        out = copy.deepcopy(settings)
        cur = _read_json(SETTINGS_FILE, {})
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k not in out:
                    out[k] = v
                elif isinstance(v, dict) and isinstance(out[k], dict):
                    for kk, vv in v.items():
                        out[k].setdefault(kk, vv)
        _write_json(SETTINGS_FILE, out)


def mask(key: str) -> str:
    return ('••••' + key[-4:]) if key and len(key) > 8 else ('••••' if key else '')


SECRETS = ('key', 'access_token')  # 豆包备用账号的密钥在 more_keys 里，public() / update() 单独处理


def public(settings: dict) -> dict:
    """给界面看的设置：密钥只显示后四位，登录凭证只给账号信息。"""
    out = copy.deepcopy(settings)
    for name, p in out['providers'].items():
        if name == 'codex':
            o = p.pop('oauth', {}) or {}
            p['has_key'] = bool(o.get('refresh'))
            p['account'] = {'email': o.get('email', ''), 'plan': o.get('plan', '')} if o.get('refresh') else None
            continue
        p['has_key'] = bool(p.get('key') or (p.get('app_id') and p.get('access_token'))
                            or any(isinstance(a, dict) and a.get('key') for a in p.get('more_keys') or []))
        for k in SECRETS:
            if k in p:
                p[k] = mask(p.get(k, ''))
        if 'more_keys' in p:
            p['more_keys'] = [dict(a, key=mask(a.get('key', ''))) for a in p['more_keys'] if isinstance(a, dict)]
    return out


def _merge_more_keys(old, new):
    """豆包备用账号：界面交回 [{id, name, key}]，key 是掩码或空表示不改，没交回的就是删掉了。"""
    before = {a.get('id'): a for a in old or [] if isinstance(a, dict)}
    out = []
    for a in new or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get('id') or '').strip() or uuid.uuid4().hex[:8]
        key = str(a.get('key') or '').strip()
        if not key or key.startswith('••••'):
            key = (before.get(aid) or {}).get('key', '')
        out.append({'id': aid, 'name': str(a.get('name') or '').strip(), 'key': key})
    return out


def _drop_spent(p, old):
    """豆包换了密钥（等于换了账号）：以前记的「用完了」作废。"""
    ids = set()
    if (p.get('key'), p.get('app_id'), p.get('access_token')) != (old.get('key'), old.get('app_id'), old.get('access_token')):
        ids.add('main')
    keys = {a['id']: a['key'] for a in old.get('more_keys') or [] if isinstance(a, dict) and a.get('id')}
    now = {a['id'] for a in p.get('more_keys') or []}
    ids |= {a['id'] for a in p.get('more_keys') or [] if keys.get(a['id']) != a['key']} | (set(keys) - now)
    if ids and p.get('spent'):
        p['spent'] = {k: v for k, v in p['spent'].items() if k.split('|')[0] not in ids}


def update(patch: dict) -> dict:
    """合并界面提交的设置。密钥字段：None 表示清除，空字符串或掩码表示不变。登录凭证不能从界面改。"""
    current = load()
    for name, p in (patch.get('providers') or {}).items():
        if name not in current['providers'] or not isinstance(p, dict):
            continue
        old = copy.deepcopy(current['providers'][name])
        for k, v in p.items():
            if k in ('has_key', 'oauth', 'account') or k not in DEFAULTS['providers'][name]:
                continue
            if k == 'more_keys':
                current['providers'][name][k] = _merge_more_keys(old.get(k), v)
                continue
            if k in SECRETS:
                if v is None:
                    current['providers'][name][k] = ''
                elif isinstance(v, str) and v.strip() and not v.startswith('••••'):
                    current['providers'][name][k] = v.strip()
                continue
            current['providers'][name][k] = v.strip() if isinstance(v, str) else v
        if name == 'doubao' and 'spent' not in p:
            _drop_spent(current['providers'][name], old)
    for k, v in patch.items():
        if k == 'providers' or k not in DEFAULTS:
            continue
        current[k] = _merge(DEFAULTS[k], v) if isinstance(DEFAULTS[k], dict) else v
    save(current)
    return current


def set_oauth(name: str, oauth: dict) -> None:
    s = load()
    s['providers'][name]['oauth'] = oauth or {}
    save(s)


def add_recent(path: str, title: str) -> None:
    s = load()
    items = [r for r in s.get('recent', []) if r.get('path') != path]
    items.insert(0, {'path': path, 'title': title})
    s['recent'] = items[:15]
    save(s)


# ------------------------------------------------------------------ 播放进度 ----
def get_positions() -> dict:
    data = _read_json(POSITIONS_FILE, {})
    return data if isinstance(data, dict) else {}


def get_position(path: str) -> float:
    try:
        return float(get_positions().get(os.path.abspath(path).lower(), {}).get('t', 0))
    except (ValueError, AttributeError, TypeError):
        return 0.0


def set_position(path: str, t: float, duration: float = 0) -> None:
    """记住每个视频播放到哪里（最多记 2000 个）。"""
    with _lock:
        data = get_positions()
        data[os.path.abspath(path).lower()] = {'t': round(max(0.0, t), 2), 'd': round(duration or 0, 1), 'at': int(time.time())}
        if len(data) > 2000:
            data = dict(sorted(data.items(), key=lambda kv: kv[1].get('at', 0))[-2000:])
        _write_json(POSITIONS_FILE, data, indent=None)


def move_paths(old: str, new: str) -> None:
    """文件或文件夹在软件里改了名：播放进度、最近打开里的路径跟着改（文件夹的话，里面所有文件都改）。"""
    o, n = os.path.abspath(old), os.path.abspath(new)
    ol = o.lower()

    def moved(p):
        pl = os.path.abspath(p).lower()
        if pl == ol:
            return n
        if pl.startswith(ol.rstrip('\\/') + os.sep):
            return n + os.path.abspath(p)[len(o):]
        return None
    with _lock:
        data = get_positions()
        changed = {}
        for k, v in list(data.items()):
            m = moved(k)
            if m:
                changed[m.lower()] = v
                del data[k]
        if changed:
            data.update(changed)
            _write_json(POSITIONS_FILE, data, indent=None)
    s = load()
    items = s.get('recent', [])
    if any(moved(r.get('path') or '') for r in items):
        update({'recent': [dict(r, path=moved(r.get('path') or '') or r.get('path')) for r in items]})


# ------------------------------------------------------------------ 老师档案 ----
def load_teachers() -> list:
    data = _read_json(TEACHERS_FILE, [])
    return [t for t in data if isinstance(t, dict) and t.get('id')] if isinstance(data, list) else []


def save_teacher(t: dict) -> dict:
    with _lock:
        items = load_teachers()
        t = {
            'id': t.get('id') or uuid.uuid4().hex[:10],
            'name': str(t.get('name') or '').strip() or '未命名老师',
            'about': str(t.get('about') or '').strip(),
            'terms': str(t.get('terms') or '').strip(),
            'notes': str(t.get('notes') or '').strip(),
            'lang': t.get('lang') if t.get('lang') in ('zh', 'en') else 'zh',
            'folder': str(t.get('folder') or '').strip().strip('/\\'),
            'template': str(t.get('template') or '').strip(),
            'terms_file': str(t.get('terms_file') or '').strip(),
            'display': str(t.get('display') or '').strip(),
        }
        items = [x for x in items if x['id'] != t['id']] + [t]
        items.sort(key=lambda x: x['name'])
        _write_json(TEACHERS_FILE, items)
        return t


def delete_teacher(tid: str) -> None:
    with _lock:
        _write_json(TEACHERS_FILE, [x for x in load_teachers() if x['id'] != tid])


def get_teacher(tid: str, resolve=True):
    """resolve=True 时，把关联的术语表文件读进来（每次都读最新的），用于识别、校对和 AI。"""
    t = next((t for t in load_teachers() if t['id'] == tid), None) if tid else None
    return resolve_teacher(t) if (t and resolve) else t


def read_text_file(path) -> str:
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError('读不出这个文件的文字，请用 UTF-8 编码保存后再试')


def resolve_teacher(t: dict) -> dict:
    """老师档案关联了 Obsidian 里的术语表文件时：
    从文件里读出术语和识别错误对照，加在档案里手写的术语后面；整份文件作为「术语说明」给 AI 参考。"""
    path = (t or {}).get('terms_file')
    if not path:
        return t
    from .llm import terms_from_markdown
    out = dict(t)
    try:
        text = read_text_file(path)
    except (OSError, ValueError):
        out['terms_file_error'] = '找不到或读不出关联的术语表文件：' + path
        return out
    extracted = terms_from_markdown(text)
    out['terms'] = '\n'.join(x for x in (t.get('terms', ''), extracted) if x.strip())
    out['notes'] = '\n\n'.join(x for x in (text.strip(), t.get('notes', '')) if x.strip())
    out['terms_from_file'] = extracted
    return out


# ------------------------------------------------------------------ 媒体库 ----
def load_library() -> dict:
    data = _read_json(LIBRARY_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault('folders', [])
    data.setdefault('files', [])
    return data


def save_library(data: dict) -> None:
    with _lock:
        _write_json(LIBRARY_FILE, data)
