"""老师蒸馏（2026-10-24，老师工作台第二步）：从媒体库里一位老师的课，自动生成老师工作台要的三份文件。

流程照「人间落叶」手工跑通的原型（用户试问过、觉得答得好）：
1. 逐节提取观点库：每节课单独调一次 AI，交给它完整字幕（带时间）和课程重点，AI 只返回结构化的条目
   （观点 / 方法 / 定义、老师自述、术语、可以实践的、存疑）；编号、出处格式、表格由程序来写，
   出处时间超出这节课长度的标「时间待核对」。做完一节存一节（`mentors/老师id/distill.json`），中途断了下次接着做。
2. 合并成课程蒸馏：观点库不大就整份交给 AI；太大（峰级这种几十节的课）先按顺序分组写小结，再合并。
   AI 写的编号在观点库里找不到的，程序在文末标出来。
3. 老师画像：第一节「给 AI 的规则」由程序固定写（对话时 AI 严格按它来），后面几节 AI 依据课程蒸馏、老师自述、术语写。
4. 写进 Obsidian `04、外部知识转化库/01、老师分身蒸馏资料/老师名/`，文件名和人间落叶的一样；覆盖之前先备份到
   `%APPDATA%\\猛人学习\\backups\\蒸馏\\老师名-时间\\`（每位老师留 20 份）。上次写完之后在 Obsidian 里改过的，开始前先问用户。

以后老师又有新课：加进来再蒸馏，只提取新课和字幕改过的课，再重新合并。课程编号 S01… 一旦分配就不变
（对话、对话精选里的出处靠它）。
"""
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config, doc as docs, llm, mentor, tasks
from .log import log

KB_DIR = '04、外部知识转化库'
MENTOR_SUB = '01、老师分身蒸馏资料'   # 知识库《AI知识蒸馏规范》V2.0：04 分成「01、老师分身蒸馏资料」「02、主题蒸馏资料」
FILES = {'atoms': '01、观点库（逐节提取）.md', 'distill': '02、课程蒸馏.md', 'persona': '03、老师画像（对话用）.md'}
CHUNK_CHARS = 50000      # 一节课的字幕超过这么多字，分几段提取
MERGE_LIMIT = 90000      # 观点库不超过这么多字，整份交给 AI 合并；超过就先分组小结
GROUP_CHARS = 60000      # 分组小结时每组大约多少字
PERSONA_ATOMS = 60000    # 写老师画像时，观点库不超过这么多字就整份附上，超过只附课程蒸馏里引用到的
RETRIES = 3
RETRY_WAIT = (10, 60, 180)
BACKUP_KEEP = 20
PROMPT_VER = 1           # 提示词改了就加 1，合并、画像会重做（逐节提取不受影响）

_running = {}            # 老师id → 正在蒸馏的 job
_lock = threading.Lock()


class DistillError(Exception):
    pass


# ------------------------------------------------------------- 提示词 ----
EXTRACT_SYSTEM = """你在帮用户做「老师课程蒸馏」的第一步：把一节课里老师讲的内容，逐条拆成「观点库」。
用户以后会让 AI 依据这份观点库扮演这位老师和他对话，所以忠实是第一位的。

规则：
1. 只写老师这节课讲过的。不补充、不评价，不替老师总结出他没说的结论。老师没讲明的前提写「未讲明」，不替老师补。
2. 每条都要有出处：这条在下面字幕里的时间，写成 mm:ss 或 mm:ss–mm:ss（超过一小时写 h:mm:ss）。必须是字幕里真实出现的时间，不许编。
3. 尽量贴近老师原意；关键的话用「」引用原话（可以去掉口头禅，不改意思）。定义、口诀、数字照录。
4. 一条只讲一件事。老师用比喻、例子讲的，把比喻和例子写进去（以后模仿老师说话要用）。
5. 只有老师用了绝对化的说法（一定、必死、绝对、唯一、永远、根本不可能……）时，「说得很满」才填 true；一般的强调不算。
   「条件」只写老师自己讲到的前提、适用范围或例外；只是复述场景（例如「演示的数据」「每次开仓」）的不写，填 —。
6. 老师讲自己的（经历、立场、风格、出版、原创声明、课程安排、说以后要讲什么），放进「自述」，一律保留，不评价。
7. 口述前后矛盾、数字对不上、字幕明显识别错的，放进「存疑」，写清楚哪个时间、哪里不对；不要擅自改。口述和课件不一致时两边都写。
8. 「类型」只有三种：观点（看法、主张）、方法（具体做法、算法、练习）、定义（老师对某个说法的解释）。
9. 不要漏：这节课的主要内容都要覆盖到。一般 15～40 条，内容多的课可以更多。
10. 「课程重点」是别人整理的，只能帮你看结构，内容以字幕原话为准。

只返回 JSON，格式：
{"说明": "一两句话：这节课讲什么、和前面的课是什么关系（不知道就空着）",
 "观点": [{"内容": "…", "条件": "老师讲到的前提或边界；没有就写 —；老师没讲明写 未讲明", "出处": "01:31–02:20", "类型": "观点", "说得很满": false}],
 "自述": [{"内容": "…", "出处": "00:02–00:19"}],
 "术语": [{"术语": "…", "意思": "老师的意思（按他的说法）", "出处": "03:35"}],
 "实践": [{"内容": "可以拿去做的事", "依据": [3, 5]}],
 "存疑": ["S 开头的编号不用写，写时间和原因，例如：04:46 两组数字对不上……"]}
其中「依据」是「观点」列表里的第几条（从 1 开始）。"""

GROUP_SYSTEM = """你在帮用户做「老师课程蒸馏」。下面是这位老师一部分课程的观点库（逐节提取，每条有编号，例如 S03-02）。
请把这几节课合并写成一份小结，后面会和其他几组的小结再合并成整门课的蒸馏。

规则：只用观点库里的内容，不补充、不评价；每一条结论后面标观点库编号，例如（S03-02、S05-15），编号必须是观点库里有的；
老师说得很满的保留 ⚠️；老师自述保留，标「老师自述：」；前后说法不一致的两边都写；老师没讲明的写「（未讲明）」。

用 Markdown，按这几个小标题写：
### 这几节的主线
### 核心观点（合并后）
### 反复强调的
### 前后不一致或有张力的
### 没讲到的（老师说以后讲、或明确不讲）
### 可以实践的"""

MERGE_SYSTEM = """你在帮用户做「老师课程蒸馏」的第二步：把逐节提取的观点库，合并成整门课的「课程蒸馏」。
用户会把它和老师画像一起交给 AI，让 AI 扮演这位老师和他对话；他也会自己读它，弄懂老师的体系。

规则：
1. 只用给你的资料里的内容。不补充、不评价，不写老师没讲的；老师没讲明的写「（未讲明）」。
2. 每一条都标观点库编号，例如（S03-02、S05-15）。编号必须是资料里有的，不许编。
3. 按老师自己的逻辑组织，不按课次；可以合并重复、调整顺序。
4. 老师说得很满的，保留 ⚠️ 提醒。老师自述（经历、立场、课程安排）保留，标「老师自述：」。
5. 前后说法不一致的，两边都写出来，放进第五节，交给用户判断，不替老师圆。
6. 写的是「老师怎么说」，不是正确答案，也不是用户的结论。

用 Markdown，从下面第一个小标题开始写（不要写总标题，不要写开场白），小标题照抄：
## 一、一句话
（一两句话说清这门课讲的是什么；再写适合谁、不适合谁，有老师原话依据的才写）
## 二、老师的体系（按他自己的逻辑，不按课次）
（用 ```text 代码块画一张从上到下的结构图，每一层标课次）
## 三、核心观点（合并后）
（按主题分成几组，用 ### A. … 这样的小标题；每条一句话加粗要点，后面标编号）
## 四、他反复强调的
（在几节课里都讲过的，写清在哪几节讲过）
## 五、前后说法不一致或有张力的地方（待你判断）
## 六、课程里没讲到的（他说以后讲、或明确不讲）
## 七、可以拿去实践的（汇总）"""

PERSONA_SYSTEM = """你在帮用户做「老师课程蒸馏」的第三步：写这位老师的「老师画像」。
用户会把画像交给 AI，让 AI 扮演这位老师（数字分身）和他探讨问题；扮演规则已经写好了（第一节），你写后面几节。

规则：
1. 只依据给你的资料（课程蒸馏、老师自述、术语、观点库），不补充、不评价，不写资料里没有的经历和观点。
2. 每条后面标出处：观点库编号写成［S02-21］，课程时间写成［S02 19:54］，必须是资料里有的。
3. 老师的原话用「」引用，照录。
4. 写具体、能用：AI 要靠它学会这位老师怎么想、怎么说，所以多写他的思考顺序、常用比喻、口头禅和说话方式。

用 Markdown，从下面第一个小标题开始写（不要写总标题、不要写第一节），小标题照抄：
## 二、他是谁
（身份、偏做什么、课程由哪几部分组成；只写他自己说过的）
## 三、他的核心信念（回答问题时的底层立场）
（编号列表，8～12 条）
## 四、他怎么思考一个问题（模仿这个顺序）
## 五、他反对什么（会明确批评）
## 六、他常用的比喻（回答时可以用，标出处）
（用表格：比喻 | 用来讲什么 | 出处）
## 七、说话风格
## 八、他没讲、或明确不碰的（回答时要说明）
## 九、回答示范
（举 2～3 个用户可能问的问题，按第一节的规则写老师会怎么答：带出处；其中一个是课程没讲的问题，示范怎么标【AI 推测：…】）"""


def persona_rules(name, n, first='S01', last='S01'):
    """老师画像第一节：给 AI 的规则（程序固定写，对话时 AI 严格按它来）。"""
    return f"""## 一、你的身份和规则（给 AI）

你现在是交易课程老师「**{name}**」的数字分身。你的全部知识来自他的 {n} 节课（{first}～{last}，见观点库）。用户想和你探讨交易问题，你要**忠实地用他的知识、思路和说话方式**来回答。

### 必须遵守

1. **只用课程里的知识说话，并标出处。**课程里讲过的内容，在句末标出处，格式 `［S02 19:54］`（课程时间）或 `［S02-21］`（观点库编号）。关键说法尽量引用他的原话，用「」括起来。
2. **超出课程的问题，可以给看法，但必须单独标注：**
   > 【AI 推测：{name}可能的看法，不是课程内容】……
   推测必须从他讲过的原则推出来，并说清是从哪几条推的（例如「从 S02-03、S04-15 推测」）。推不出来就说「课程没讲，没法替他说」。
3. **不掺你（AI）自己的观点。**用户明确要求「AI 你自己怎么看」时，另起一段：
   > 【AI 自己的看法，不代表{name}】……
4. **课程里明确没讲、他说以后再讲的话题**（见第八节），要照实说没讲，再按规则 2 决定要不要给推测。
5. **他不懂、不碰的东西不要装懂**（见第八节）。
6. **不给具体的买卖建议**，不回答「会不会涨、要不要卖」这类问题，把话题拉回他讲的方法和规则。
7. **他的立场不等于正确**：用户问「他说得对不对」时，按规则 3 给 AI 自己的看法并标注。
8. 回答要直接，像这位老师在跟学生聊天：先给结论，再讲为什么，最后给能马上做的事。"""


# --------------------------------------------------------------- 工具 ----
def _sha(*parts):
    return hashlib.sha1('\x00'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:16]


def _cell(s):
    return re.sub(r'\s+', ' ', str(s or '')).replace('|', '｜').strip() or '—'


def _ts(t):
    return mentor._ts(t)


def _times(src):
    return [mentor.parse_time(x) for x in re.findall(r'\d{1,2}:\d{2}(?::\d{2})?', str(src or ''))]


def _fix_src(sid, src, duration):
    """出处统一写成「S01 05:19–07:08」；时间超出这节课长度的标出来。"""
    src = re.sub(r'^\s*S\d{2,3}\s*', '', str(src or '')).strip().replace('-', '–').replace('~', '–').replace('～', '–')
    ts = _times(src)
    if not ts:
        return f'{sid}（时间未标）', False
    bad = bool(duration) and any(t > duration + 5 for t in ts)
    return f'{sid} {src}' + ('（时间待核对）' if bad else ''), not bad


def _state_path(mid):
    return mentor._mdir(mid) / 'distill.json'


def load_state(mid):
    return mentor._load_json(_state_path(mid), None) or {'lessons': [], 'cache': {}, 'written': {}}


def save_state(mid, st):
    mentor._save_json(_state_path(mid), st)


def _vault():
    v = (config.load().get('obsidian') or {}).get('vault') or ''
    if not v or not Path(v).is_dir():
        raise DistillError('先在「设置 → Obsidian」里选好 Obsidian 库的位置，蒸馏结果要写进库里的「04、外部知识转化库/01、老师分身蒸馏资料」')
    return v


def target_folder(name):
    name = re.sub(r'[\\/:*?"<>|]', '', str(name or '')).strip()
    if not name:
        raise DistillError('请填老师的名字')
    return Path(_vault()) / KB_DIR / MENTOR_SUB / name


def _lesson_doc(path):
    d = docs.load(path) if path and Path(path).is_file() else None
    return d if d and d.get('kind') != 'book' else None


def _note_name(d):
    p = ((d or {}).get('clip') or {}).get('path') or ''
    return Path(p).stem if p and Path(p).is_file() else ''


def _hash_doc(d):
    return _sha(*(s.get('text', '') for s in d.get('segments') or []))


# -------------------------------------------------------- 新建 / 加课 ----
def create(name, paths):
    """新建一位老师（或给同一个文件夹的老师加课）。只记下要用哪些课，不调用 AI。"""
    folder = target_folder(name)
    paths = _check_paths(paths)
    m = next((x for x in mentor.list_mentors(chats=False) if mentor._same(x['folder'], folder)), None)
    if m is None:
        m = {'id': uuid.uuid4().hex[:10], 'name': folder.name, 'folder': str(folder),
             'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'sources': []}
    m['distilled'] = True
    m.setdefault('distill_done', False)
    mentor._save_json(mentor._mdir(m['id']) / 'mentor.json', {k: v for k, v in m.items() if k != 'chats'})
    add_lessons(m['id'], paths)
    return mentor.get(m['id'])


def _check_paths(paths):
    from . import workflow
    out = []
    for p in paths or []:
        p = str(p)
        if not workflow.allowed(p):
            raise DistillError(f'《{Path(p).stem}》不在媒体库里')
        d = _lesson_doc(p)
        if not d or not d.get('segments'):
            raise DistillError(f'《{Path(p).stem}》还没有字幕，先生成字幕再蒸馏')
        out.append(p)
    if not out:
        raise DistillError('请至少选一节课')
    return out


def add_lessons(mid, paths):
    """加课：已经在的不重复加；新课按顺序接着编号（S01… 一旦分配不再变）。"""
    st = load_state(mid)
    have = {mentor._norm(x['path']) for x in st['lessons']}
    n = max((int(x['sid'][1:]) for x in st['lessons']), default=0)
    added = 0
    for p in paths:
        if mentor._norm(p) in have:
            continue
        n += 1
        d = _lesson_doc(p) or {}
        st['lessons'].append({'sid': f'S{n:02d}', 'path': str(p), 'title': d.get('title') or Path(p).stem, 'added': time.strftime('%Y-%m-%d %H:%M:%S')})
        have.add(mentor._norm(p))
        added += 1
    save_state(mid, st)
    if added:
        mentor.update(mid, distill_done=False)
    return added


def remove_lesson(mid, sid):
    """不再用某节课（编号空着，不给别的课用）。"""
    st = load_state(mid)
    st['lessons'] = [x for x in st['lessons'] if x['sid'] != sid]
    save_state(mid, st)
    mentor.update(mid, distill_done=False)


# ---------------------------------------------------------- 现在的状态 ----
def status(mid):
    """界面要的：每节课提取过没有、字幕改过没有；三份文件写过没有、在 Obsidian 里改过没有。"""
    m = mentor.get(mid)
    st = load_state(mid)
    lessons = []
    for x in st['lessons']:
        d = _lesson_doc(x['path'])
        state = 'missing' if not d or not d.get('segments') else ('todo' if not x.get('data') else
                                                                  ('changed' if _hash_doc(d) != x.get('hash') else 'done'))
        lessons.append({'sid': x['sid'], 'title': x['title'], 'path': x['path'], 'state': state, 'points': len((x.get('data') or {}).get('观点') or [])})
    return {'mentor': m, 'lessons': lessons, 'conflicts': conflicts(m, st), 'running': running(mid),
            'complete': bool(st.get('done')) and all(x['state'] == 'done' for x in lessons)}


def _paths(m):
    folder = Path(m['folder'])
    have = mentor.files_of(folder) if folder.is_dir() else {}
    return {k: (have.get(k) or folder / FILES[k]) for k in FILES}


def conflicts(m, st=None):
    """要覆盖的文件里，上次写完以后在 Obsidian 里改过的（或者不是软件写的）。"""
    st = st or load_state(m['id'])
    out = []
    for k, p in _paths(m).items():
        if p.is_file() and _sha(mentor._read(p)) != (st.get('written') or {}).get(k):
            out.append(p.name)
    return out


def running(mid):
    with _lock:
        job = _running.get(mid)
        return job.id if job and job.status == 'running' else None


# --------------------------------------------------------------- 调 AI ----
def _ask(job, settings, name, messages, label, json_mode=False):
    """调一次 AI（边收边显示写了多少字）；临时出错按 workflow 的规则重试。"""
    tries = 0
    while True:
        try:
            parts, n = [], 0
            for piece in llm.stream(settings, name, messages, job.cancel, json_mode=json_mode):
                parts.append(piece)
                n += len(piece)
                if len(parts) % 30 == 0:
                    job.report(job.progress, f'{label}：AI 已写 {n} 字…')
            if job.cancel.is_set():
                raise tasks.WorkError('cancelled', '已取消')
            text = ''.join(parts).strip()
            if not text:
                raise llm.LlmError('AI 没有返回内容')
            return text
        except Exception as e:  # noqa: BLE001
            err = tasks.error_dict(e)
            if job.cancel.is_set() or err['error_code'] == 'cancelled':
                raise tasks.WorkError('cancelled', '已取消')
            if not err['retryable'] or tries >= RETRIES:
                raise
            wait = RETRY_WAIT[min(tries, len(RETRY_WAIT) - 1)]
            tries += 1
            log.warning('蒸馏：%s出错，%s 秒后第 %d 次重试：%s', label, wait, tries, err['message'])
            end = time.monotonic() + wait
            while (left := end - time.monotonic()) > 0:
                job.report(job.progress, f'{label}出错（{err["message"][:60]}），{max(1, round(left))} 秒后第 {tries} 次重试…')
                if job.cancel.wait(min(left, 5)):
                    raise tasks.WorkError('cancelled', '已取消')
            settings = config.load()


def _json(text):
    try:
        data = llm._json_from(text)
    except ValueError:
        raise llm.LlmError('AI 返回的格式不对（不是 JSON），请重试或换一个 AI 服务')
    if not isinstance(data, dict):
        raise llm.LlmError('AI 返回的格式不对，请重试')
    return data


def _sub_lines(segs):
    return [f'[{_ts(float(s["start"]))}] {s["text"]}' for s in segs]


def extract_lesson(job, settings, name, x, d, frac):
    """一节课 → 结构化的条目（字幕太长时分几段，合起来）。"""
    segs = d.get('segments') or []
    lines = _sub_lines(segs)
    chunks, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) > CHUNK_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        chunks.append(cur)
    summary = (d.get('summary') or '').strip()
    out = {'说明': '', '观点': [], '自述': [], '术语': [], '实践': [], '存疑': []}
    for k, ch in enumerate(chunks):
        part = f'（这节课字幕较长，分 {len(chunks)} 段提取，这是第 {k + 1} 段，只提取这一段里的内容）\n' if len(chunks) > 1 else ''
        user = (f'课程：{x["title"]}\n{part}\n'
                + (f'# 课程重点（别人整理的，只作参考）\n{summary[:20000]}\n\n' if summary and k == 0 else '')
                + '# 完整字幕（[时间] 原文）\n' + '\n'.join(ch))
        label = f'提取《{x["title"]}》' + (f'（第 {k + 1}/{len(chunks)} 段）' if len(chunks) > 1 else '')
        job.report(frac, label + '…')
        data = _json(_ask(job, settings, name, [{'role': 'system', 'content': EXTRACT_SYSTEM}, {'role': 'user', 'content': user}], label, json_mode=True))
        base = len(out['观点'])
        for key in ('观点', '自述', '术语', '存疑'):
            out[key] += [v for v in (data.get(key) or []) if v]
        for p in data.get('实践') or []:
            if isinstance(p, dict) and p.get('内容'):
                refs = [int(i) + base for i in (p.get('依据') or []) if str(i).isdigit()]
                out['实践'].append({'内容': p['内容'], '依据': refs})
        if data.get('说明') and not out['说明']:
            out['说明'] = str(data['说明'])
    if not out['观点']:
        raise llm.LlmError(f'《{x["title"]}》没有提取出观点，请重试或换一个 AI 服务')
    return out


# ------------------------------------------------------------ 写观点库 ----
def lesson_md(x, duration=0):
    """一节课的那一部分（和人间落叶的格式一样）。返回 (markdown, 观点数, 时间超出课长的条数)。"""
    sid, data = x['sid'], x.get('data') or {}
    bad = 0
    rows = []
    for i, p in enumerate([p for p in data.get('观点') or [] if isinstance(p, dict)], 1):
        src, ok = _fix_src(sid, p.get('出处'), duration)
        bad += not ok
        kind = _cell(p.get('类型') or '观点')
        if kind not in ('观点', '方法', '定义'):
            kind = '观点'
        rows.append(f'| {sid}-{i:02d} | {_cell(p.get("内容"))} | {_cell(p.get("条件"))} | {src} | {kind}{" ⚠️" if p.get("说得很满") else ""} |')
    out = [f'## {sid}　{x["title"]}', '']
    if data.get('说明'):
        out += [f'> {_cell(data["说明"])}', '']
    out += ['### 观点', '', '| 编号 | 观点 | 条件 / 边界 | 出处 | 类型 |', '| --- | --- | --- | --- | --- |', *rows, '']
    selfs = [p for p in data.get('自述') or [] if isinstance(p, dict)]
    if selfs:
        out += ['### 老师自述（做老师画像用）', '', '| 编号 | 内容 | 出处 |', '| --- | --- | --- |']
        for i, p in enumerate(selfs, 1):
            src, ok = _fix_src(sid, p.get('出处'), duration)
            bad += not ok
            out.append(f'| {sid}-A{i} | {_cell(p.get("内容"))} | {src} |')
        out.append('')
    terms = [p for p in data.get('术语') or [] if isinstance(p, dict) and p.get('术语')]
    if terms:
        out += ['### 老师的术语（本节）', '', '| 术语 | 老师的意思 | 出处 |', '| --- | --- | --- |']
        out += [f'| {_cell(p["术语"])} | {_cell(p.get("意思"))} | {_fix_src(sid, p.get("出处"), duration)[0]} |' for p in terms]
        out.append('')
    prac = [p for p in data.get('实践') or [] if isinstance(p, dict)]
    if prac:
        out += ['### 可以拿去实践的', '']
        for p in prac:
            refs = '、'.join(f'{sid}-{i:02d}' for i in p.get('依据') or [] if 1 <= i <= len(rows))
            out.append(f'- {_cell(p["内容"])}' + (f'（{refs}）' if refs else ''))
        out.append('')
    doubts = [str(v).strip() for v in data.get('存疑') or [] if str(v).strip()]
    if doubts:
        out += ['### 存疑和待核对', ''] + [f'- {sid} {re.sub(r"^S\d{2,3}\s*", "", v)}' for v in doubts] + ['']
    return '\n'.join(out), len(rows), bad


def atoms_md(m, st):
    """整份观点库。返回 (markdown, 正文（给 AI 合并用）, 统计)。"""
    name = m['name']
    src_rows, parts, total, bad, reviewed = [], [], 0, 0, 0
    for x in st['lessons']:
        if not x.get('data'):
            continue
        d = _lesson_doc(x['path']) or {}
        reviewed += bool(d.get('reviewed'))
        note = _note_name(d) or x.get('note') or ''
        x['note'] = note
        src_rows.append(f'| {x["sid"]} | {_cell(x["title"])} | ' + (f'[[{note}]]' if note else f'（还没剪藏到 Obsidian：{_cell(Path(x["path"]).name)}）') + ' |')
        body, n, b = lesson_md(x, d.get('duration') or 0)
        parts.append(body)
        total += n
        bad += b
    body = '\n\n---\n\n'.join(parts)
    n = len(parts)
    head = (f'---\n老师: {name}\n类型: 观点库\n状态: AI 提取，待作者审核\n{review_key(reviewed, n)}\n生成: 猛人学习「老师蒸馏」{time.strftime("%Y-%m-%d")}\ntags:\n  - 课程蒸馏\n---\n\n'
            f'# {name}：观点库（逐节提取）\n\n'
            f'> 这是「{name}」课程蒸馏的第一步：AI 逐节把老师讲的观点拆成一条一条，并标出处（由猛人学习「老师蒸馏」生成）。后面的「课程蒸馏」和「老师画像」都从这里来。\n'
            '> 出处是 AI 依据完整字幕标的，程序只核对了时间有没有超出课程长度；要核对原话，回到那篇笔记的「完整字幕」，或在老师工作台里点出处跳到那一秒。\n'
            + review_note(reviewed, n) + '\n'
            '## 来源\n\n| 编号 | 课程 | 笔记 |\n| --- | --- | --- |\n' + '\n'.join(src_rows) + '\n\n'
            '## 怎么读这张表\n\n'
            '- **出处**：`S01 05:19` 表示第 1 节课 5 分 19 秒。\n'
            '- **类型**：**观点**是老师的看法或主张；**方法**是具体做法、算法、练习；**定义**是老师对某个说法的解释；**自述**是老师讲自己的经历、立场、风格（做老师画像用）。\n'
            '- 引号「」里是老师原话（只做了去口头禅的最小处理）。\n'
            '- ⚠️：老师用了「一定、必死、很难」这类说得很满的话，审核时留意它的前提。\n'
            '- 「条件 / 边界」写的是老师自己讲到的前提；老师没讲的写「未讲明」，不替老师补。\n'
            '- 「时间待核对」：AI 标的时间超出了这节课的长度，可能标错了。\n\n---\n\n')
    return head + body + '\n', body, {'points': total, 'bad_times': bad, 'reviewed': reviewed}


def review_key(reviewed, n):
    """属性里的一行：来源有几节人工校对过（知识蒸馏规范 V2.0 第四节：不强制校对，没校对的要注明）。"""
    return f'来源人工校对: {reviewed}/{n} 节'


def review_note(reviewed, n):
    if reviewed >= n:
        return ''
    return (f'> 来源笔记有 {n - reviewed} 节还没人工校对（老师普通话标准、识别准确时可以不校对）；'
            '提取时发现字幕可能识别错的地方，放进了各节的「存疑和待核对」。\n')


def _ids_in(text):
    """文字里提到的观点库编号（含「S03-02、03」这种省略写法）。"""
    out = set()
    for m in re.finditer(r'(S\d{2,3})-(A?\d{1,3})(?!\d)((?:\s*[、,，～~–-]\s*A?\d{1,3}(?!\d))*)', text):
        out.add(f'{m.group(1)}-{m.group(2)}')
        for k in re.findall(r'A?\d{1,3}', m.group(3) or ''):
            out.add(f'{m.group(1)}-{k}')
    return out


def _norm_id(aid):
    sid, _, k = aid.partition('-')
    return f'{sid}-A{int(k[1:])}' if k.startswith('A') else f'{sid}-{int(k):02d}'


def bad_refs(text, atoms):
    """AI 写的编号里，观点库没有的（区间写法 S03-02～05 只查两头）。"""
    return sorted({a for a in _ids_in(text) if _norm_id(a) not in atoms})


# -------------------------------------------------------------- 整个流程 ----
def start(mid, force=False):
    """开始（或接着）蒸馏。在 Obsidian 里改过要覆盖的文件时返回 conflict，用户确认后 force。"""
    from . import jobs
    m = mentor.get(mid)
    if not m.get('distilled'):
        raise DistillError('这位老师是导入的，不是在软件里蒸馏的；要重新蒸馏，请用「新建老师」从媒体库选课')
    with _lock:
        job = _running.get(mid)
        if job and job.status == 'running':
            return {'job': job.id}
    st = load_state(mid)
    if not st['lessons']:
        raise DistillError('还没有选课')
    c = conflicts(m, st)
    if c and not force:
        return {'conflict': c}
    s = config.load()
    llm.check(s, s['chat'])
    job = jobs.start('distill', lambda j: run(mid, j))
    with _lock:
        _running[mid] = job
    return {'job': job.id}


def run(mid, job):
    s = config.load()
    name = s['chat']
    m = mentor.get(mid)
    st = load_state(mid)
    st['done'] = False
    save_state(mid, st)
    t0 = time.time()
    # 1. 逐节提取
    todo = []
    for x in st['lessons']:
        d = _lesson_doc(x['path'])
        if not d or not d.get('segments'):
            if not x.get('data'):
                raise DistillError(f'{x["sid"]}《{x["title"]}》的字幕找不到了（视频挪了位置或删了？），可以在「更新蒸馏」里去掉这节课')
            continue
        if not x.get('data') or _hash_doc(d) != x.get('hash'):
            todo.append((x, d))
    for k, (x, d) in enumerate(todo):
        frac = 0.7 * k / max(1, len(todo))
        job.report(frac, f'逐节提取观点：第 {k + 1}/{len(todo)} 节《{x["title"]}》…')
        data = extract_lesson(job, s, name, x, d, frac)
        x.update(data=data, hash=_hash_doc(d), done=time.strftime('%Y-%m-%d %H:%M:%S'), model=_model(s, name), title=d.get('title') or x['title'])
        save_state(mid, st)  # 做完一节存一节
    job.report(0.7, '整理观点库…')
    full, body, stat = atoms_md(m, st)
    atoms = mentor.parse_atoms(full)
    cache, used = st.setdefault('cache', {}), set()

    def cached(key, fn):
        used.add(key)
        if key not in cache:
            cache[key] = fn()
            save_state(mid, st)
        return cache[key]

    # 2. 合并成课程蒸馏（太大先分组小结）
    if len(body) <= MERGE_LIMIT:
        material = '# 观点库\n\n' + body
    else:
        groups, cur, size = [], [], 0
        for x in st['lessons']:
            if not x.get('data'):
                continue
            part = lesson_md(x)[0]
            if cur and size + len(part) > GROUP_CHARS:
                groups.append(cur)
                cur, size = [], 0
            cur.append((x, part))
            size += len(part)
        if cur:
            groups.append(cur)
        notes = []
        for k, g in enumerate(groups):
            label = f'分组小结 {k + 1}/{len(groups)}（{g[0][0]["sid"]}～{g[-1][0]["sid"]}）'
            job.report(0.7 + 0.1 * k / len(groups), label + '…')
            text = '\n\n---\n\n'.join(p for _, p in g)
            notes.append(f'## {g[0][0]["sid"]}～{g[-1][0]["sid"]} 小结\n\n' + cached(
                _sha('group', PROMPT_VER, text), lambda: _ask(job, s, name, [{'role': 'system', 'content': GROUP_SYSTEM}, {'role': 'user', 'content': text}], label)))
        material = '# 各组小结（每组几节课，编号是观点库的）\n\n' + '\n\n'.join(notes)
    names = '\n'.join(f'- {x["sid"]}：{x["title"]}' for x in st['lessons'] if x.get('data'))
    user = f'老师：{m["name"]}\n课程：\n{names}\n\n{material}'
    job.report(0.8, '合并成课程蒸馏…')
    distill = cached(_sha('merge', PROMPT_VER, user), lambda: _ask(job, s, name, [{'role': 'system', 'content': MERGE_SYSTEM}, {'role': 'user', 'content': user}], '合并成课程蒸馏'))
    distill = _from_heading(distill, '## 一')
    # 3. 老师画像
    selfs = [f'- [{a["id"]}]（{a["src"]}）{a["text"]}' for a in atoms.values() if a['kind'] == '自述']
    terms = [f'| {_cell(t["术语"])} | {_cell(t.get("意思"))} | {_fix_src(x["sid"], t.get("出处"), 0)[0]} |'
             for x in st['lessons'] if x.get('data') for t in x['data'].get('术语') or [] if isinstance(t, dict) and t.get('术语')]
    if len(body) <= PERSONA_ATOMS:
        extra = '# 观点库\n\n' + body
    else:
        ids = {_norm_id(a) for a in _ids_in(distill)}
        extra = '# 观点库（课程蒸馏里引用到的条目）\n\n' + '\n'.join(
            f'- [{a["id"]}]（{a["src"]}，{a["kind"]}）{a["text"]}' + (f'　条件：{a["cond"]}' if a['cond'] else '') for a in atoms.values() if a['id'] in ids)
    puser = (f'老师：{m["name"]}（共 {len(names.splitlines())} 节课）\n课程：\n{names}\n\n# 课程蒸馏\n\n{distill}\n\n'
             f'# 老师自述（全部）\n\n' + '\n'.join(selfs) + '\n\n# 老师的术语\n\n| 术语 | 老师的意思 | 出处 |\n' + '\n'.join(terms) + f'\n\n{extra}')
    job.report(0.9, '写老师画像…')
    persona = cached(_sha('persona', PROMPT_VER, puser), lambda: _ask(job, s, name, [{'role': 'system', 'content': PERSONA_SYSTEM}, {'role': 'user', 'content': puser}], '写老师画像'))
    persona = _from_heading(persona, '## 二')
    for k in list(cache):
        if k not in used:
            del cache[k]
    # 4. 写进 Obsidian
    job.report(0.98, '写进 Obsidian…')
    have = [x['sid'] for x in st['lessons'] if x.get('data')]
    n = len(have)
    model = _model(s, name)
    bad_d, bad_p = bad_refs(distill, atoms), bad_refs(persona, atoms)
    distill_md = (f'---\n老师: {m["name"]}\n类型: 课程蒸馏\n状态: AI 蒸馏，待作者审核\n{review_key(stat["reviewed"], n)}\n生成: 猛人学习「老师蒸馏」{time.strftime("%Y-%m-%d")} · {model}\ntags:\n  - 课程蒸馏\n---\n\n'
                  f'# {m["name"]}：课程蒸馏\n\n'
                  f'> 依据 {n} 节课（见 [[{Path(FILES["atoms"]).stem}]]）。这里是 AI 把观点库合并后的结果：老师的体系、反复强调的观点、前后不一致处和课程没讲的内容。每条都能顺着编号回到观点库，再回到字幕原话。\n'
                  '> 这里写的是「老师怎么说」，不是正确答案，也不是你的结论。\n\n' + distill.strip() + '\n'
                  + _bad_note(bad_d))
    persona_md = (f'---\n老师: {m["name"]}\n类型: 老师画像\n状态: AI 起草，待作者试用和审核\n{review_key(stat["reviewed"], n)}\n生成: 猛人学习「老师蒸馏」{time.strftime("%Y-%m-%d")} · {model}\ntags:\n  - 课程蒸馏\n  - 老师画像\n---\n\n'
                  f'# {m["name"]}：老师画像（对话用）\n\n'
                  f'> 用法：在猛人学习「老师工作台」里和{m["name"]}对话时，软件会把本文、[[{Path(FILES["atoms"]).stem}]]、[[{Path(FILES["distill"]).stem}]] 和相关的课程原文一起交给 AI。\n'
                  '> 第一节是给 AI 的规则（软件固定写的），后面几节是 AI 依据课程写的。\n\n---\n\n'
                  + persona_rules(m['name'], n, have[0], have[-1]) + '\n\n---\n\n' + persona.strip() + '\n' + _bad_note(bad_p))
    written = write_files(m, st, {'atoms': full, 'distill': distill_md, 'persona': persona_md})
    st['done'] = True
    st['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    st['model'] = model
    save_state(mid, st)
    mentor.update(mid, distill_done=True, distill_updated=st['updated'], distill_model=model)
    m = mentor.refresh(mid)
    log.info('蒸馏完成：%s，%d 节课（这次提取 %d 节），%d 条观点，用时 %.0f 秒', m['name'], n, len(todo), stat['points'], time.time() - t0)
    return {'mentor': m, 'lessons': n, 'extracted': len(todo), 'points': stat['points'], 'bad_times': stat['bad_times'],
            'bad_refs': len(bad_d) + len(bad_p), 'files': written, 'folder': m['folder'], 'seconds': round(time.time() - t0)}


def _from_heading(text, head):
    """AI 有时在前面加开场白或总标题：从第一个指定的小标题开始取。"""
    text = re.sub(r'^\s*```(?:markdown|md)?\s*\n|\n```\s*$', '', text.strip())
    i = text.find(head)
    return text[i:] if i > 0 else text


def _bad_note(bad):
    if not bad:
        return ''
    return (f'\n---\n\n> 程序检查：上面有 {len(bad)} 处编号在观点库里找不到（可能是 AI 写错了），审核时留意：'
            + '、'.join(bad[:30]) + ('…' if len(bad) > 30 else '') + '\n')


def _model(s, name):
    return f'{llm.PROVIDER_NAMES.get(name, name)} · {(s["providers"].get(name) or {}).get("chat_model", "")}'


def write_files(m, st, texts):
    """写三份文件；已有的先整体备份（每位老师留 BACKUP_KEEP 份）。"""
    paths = _paths(m)
    folder = Path(m['folder'])
    folder.mkdir(parents=True, exist_ok=True)
    olds = [p for p in paths.values() if p.is_file()]
    if olds:
        root = config.DATA_DIR / 'backups' / '蒸馏'
        b = root / f'{folder.name}-{time.strftime("%Y%m%d-%H%M%S")}'
        b.mkdir(parents=True, exist_ok=True)
        for p in olds:
            shutil.copy2(p, b / p.name)
        for old in sorted(root.glob(f'{folder.name}-*'))[:-BACKUP_KEEP]:
            shutil.rmtree(old, ignore_errors=True)
    st['written'] = {}
    for k, text in texts.items():
        p = paths[k]
        tmp = p.with_name(p.name + '.tmp')
        tmp.write_text(text, encoding='utf-8')
        tmp.replace(p)
        st['written'][k] = _sha(mentor._read(p))
    return [str(p) for p in paths.values()]
