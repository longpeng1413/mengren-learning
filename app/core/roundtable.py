"""圆桌讨论和主题蒸馏（2026-10-24，知识库《AI知识蒸馏规范》V2.0 第六节）。

圆桌讨论：作者就一个问题请几位老师一起交流。
1. 每位老师各自回答：只用他自己的老师画像和资料（`mentor.build_messages`，多加一段「你只代表这位老师」），一位一次 AI，互不干扰；
2. 需要时互相回应一轮：每位老师看了别人的回答后回应，仍然只能用自己课程里的内容；
3. AI 总结共识、分歧和各自的条件（标明是 AI 归纳）。
讨论记录存在 `%APPDATA%\\猛人学习\\roundtables\\`（用户的资料，不自动删，界面上能删）；作者点「收藏到 Obsidian」，
整场讨论写进 `04、外部知识转化库/02、主题蒸馏资料/对应主题/日期、问题、多位老师讨论.md`（覆盖前备份）。

主题蒸馏：选一个主题和几位老师 → 每位老师从观点库里挑和主题相关的条目、相关的课程原文（本机打分，不花钱），
AI 整理出「他关于这个主题讲了什么」→ AI 合并成比较几位老师的文档 → 写进
`02、主题蒸馏资料/对应主题/日期、主题、跨来源蒸馏.md`。记录存在 `topics\\`，结果以 Obsidian 里的文件为准。

出处：老师自己的回答沿用 ［S02 19:54］（界面按是哪位老师说的来认）；总结和主题蒸馏里写成 ［老师名 S02-21］。
提示词都是固定的常量（界面「查看提示词」、知识库《08、AI蒸馏提示词》显示的就是它们，见 `prompt_list`）。
"""
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config, distill, llm, mentor, tasks

ROUND_DIR = config.DATA_DIR / 'roundtables'
TOPIC_DIR = config.DATA_DIR / 'topics'
TOPIC_SUB = '02、主题蒸馏资料'
TOPIC_FOLDERS = ['01、交易认知相关资料', '02、交易系统相关资料', '03、分析技术相关资料', '04、操作技术相关资料',
                 '05、执行力管理相关资料', '06、跨主题综合资料']
TOPIC_ATOMS = 80        # 主题蒸馏时每位老师最多带上多少条相关观点
TOPIC_WINDOWS = 8       # 和相关的课程原文片段
BACKUP_KEEP = 30
REF_RE = re.compile(r'[［\[]\s*([^\s［\]］\[]{1,20})\s+(S\d{2,3}-[A-Za-z0-9]+|S\d{2,3}\s+\d{1,2}:\d{2}(?::\d{2})?)')

_running = {}           # 圆桌讨论id / 主题蒸馏id → job
_lock = threading.Lock()


class RoundError(Exception):
    pass


# ------------------------------------------------------------- 提示词 ----
ROUND_ANSWER = """# 这是一场圆桌讨论
用户请了几位老师（{names}）一起讨论同一个问题。你只代表「{me}」：按上面的规则，只用你自己课程里的内容回答，出处照常写成 ［S02 19:54］ 或 ［S02-21］。
不要替其他老师说话，也不要猜测其他老师会怎么说。"""

ROUND_REPLY = """这是圆桌讨论的第二轮。用户的问题是：
{question}

下面是其他老师对这个问题的回答。请你以「{me}」的身份回应：哪些和你讲的一致，哪些不一致、为什么（按你课程里讲过的），你还要补充什么。
只能用你自己课程里的内容，出处照常写成 ［S02 19:54］ 或 ［S02-21］；超出你课程的按规则标【AI 推测】。不要重复你上一轮已经说过的话，简短。

{others}"""

ROUND_SUMMARY_SYSTEM = """你在主持一场多位交易老师的圆桌讨论，现在要写总结。下面是用户的问题和每位老师（AI 按各自的课程扮演）的回答。

规则：
1. 只依据这些回答，不补充、不评价谁对谁错，不替老师下结论。
2. 引用时写成 ［老师名 S02-21］ 或 ［老师名 S02 19:54］：把回答里的出处前面加上是哪位老师，不许编。
3. 回答里标了【AI 推测】的，总结里也要说明那是推测，不是课程内容。
4. 分歧要说清原因：是真的矛盾，还是适用条件不同（市场、周期、手法、资金、阶段）、层级不同（原则和执行）、说法不同。
5. 这是 AI 归纳，不是任何一位老师的原话，也不是用户的结论。

用 Markdown，按这几个小标题写（小标题照抄，不要写开场白）：
### 共识
### 分歧和原因
### 各自的适用条件
### 还待验证的问题"""

TOPIC_TEACHER_SYSTEM = """你在帮用户做「主题蒸馏」的第一步：从一位交易老师的资料里，整理出他关于一个主题讲了什么。下面会给出主题、老师名字和他的资料（观点库里和主题相关的条目、相关的课程原文）。

规则：
1. 只用给你的资料。不补充、不评价，不替老师总结出他没说的结论。
2. 每条都标出处，写成 ［老师名 S02-21］（观点库编号）或 ［老师名 S02 19:54］（课程时间），必须是资料里有的，不许编。
3. 条件和边界照写；老师没讲明的写「（未讲明）」。关键的话用「」引原话。
4. 老师说得很满的（一定、绝对、唯一……）标 ⚠️。老师自述（经历、立场）保留，标「老师自述：」。
5. 资料里和这个主题无关的不要写；这位老师没讲到这个主题，就直接写「资料里没有讲到这个主题」。

用 Markdown，按这几个小标题写（小标题照抄）：
### 他的主要观点
### 他给的方法、条件和边界
### 他没讲明或没讲到的"""

TOPIC_MERGE_SYSTEM = """你在帮用户做「主题蒸馏」：围绕一个主题，比较几位交易老师分别怎么说。用户写书时会参考它，也会顺着出处回到老师的原话。下面是主题和每位老师关于这个主题的整理（已经带出处）。

规则：
1. 只用下面各位老师的整理。不补充、不评价谁对谁错，不替老师、也不替用户下结论。
2. 每条保留出处 ［老师名 S02-21］ 或 ［老师名 S02 19:54］，沿用整理里已有的，不许编。
3. 共识只写确实几位老师都讲过的；只有一位老师讲的，放在他自己那里。
4. 分歧要说清原因：是真的矛盾，还是适用条件不同（市场、周期、手法、资金、阶段）、层级不同（原则和执行）、说法不同、老师前后说法变了。
5. 老师说得很满的保留 ⚠️；老师没讲明的写「（未讲明）」。
6. 这是 AI 归纳，不是作者的结论。

用 Markdown，从下面第一个小标题开始写（不要写总标题、不要写开场白），小标题照抄：
## 一、一句话
## 二、每位老师怎么说
（每位老师一个 ### 小标题）
## 三、共识
## 四、分歧和原因
## 五、适用条件和边界
## 六、来源质量
（按下面给的「资料情况」写：每位老师是系统课程还是零散内容、几节课、来源有没有人工校对）
## 七、还待验证的问题"""


def prompt_list():
    """软件用的全部蒸馏提示词（界面「查看提示词」和知识库《08、AI蒸馏提示词》用）。"""
    return [
        {'key': 'extract', 'title': '老师蒸馏 ① 逐节提取观点库', 'when': '每节课一次。附上这节课的课程重点（只作参考）和完整字幕「[时间] 原文」。AI 只返回 JSON，编号、出处格式和表格由程序写。', 'text': distill.EXTRACT_SYSTEM},
        {'key': 'group', 'title': '老师蒸馏 ② 分组小结（大课才用）', 'when': '观点库超过约 9 万字时，按课次每组约 6 万字先写小结。', 'text': distill.GROUP_SYSTEM},
        {'key': 'merge', 'title': '老师蒸馏 ② 合并成课程蒸馏', 'when': '附上整份观点库（或各组小结）。', 'text': distill.MERGE_SYSTEM},
        {'key': 'persona', 'title': '老师蒸馏 ③ 写老师画像（第二～九节）', 'when': '附上课程蒸馏、全部老师自述、术语和观点库。', 'text': distill.PERSONA_SYSTEM},
        {'key': 'rules', 'title': '老师画像第一节：给 AI 的规则（程序固定写，不让 AI 改）', 'when': '「老师名」「几节课」由程序填。',
         'text': distill.persona_rules('老师名', 5, 'S01', 'S05')},
        {'key': 'chat', 'title': '和老师对话：每次回答的要求', 'when': '交给 AI 的资料依次是：老师画像全文、课程列表、课程蒸馏、观点库（不超过 4 万字全给，多了挑和问题相关的 40 条）、和问题最相关的 10 段课程原文，最后是这几条要求；再带上最近 12 条对话。', 'text': mentor.CHAT_RULES},
        {'key': 'round', 'title': '圆桌讨论 ① 每位老师各自回答（追加在对话要求后面）', 'when': '每位老师一次，资料和「和老师对话」一样，只用他自己的。', 'text': ROUND_ANSWER},
        {'key': 'reply', 'title': '圆桌讨论 ② 互相回应', 'when': '作者点「让老师们互相回应」时，每位老师一次。', 'text': ROUND_REPLY},
        {'key': 'summary', 'title': '圆桌讨论 ③ 总结', 'when': '附上问题和每位老师的回答。', 'text': ROUND_SUMMARY_SYSTEM},
        {'key': 'topic1', 'title': '主题蒸馏 ① 每位老师关于这个主题讲了什么', 'when': f'每位老师一次。附上主题、他的课程列表、观点库里和主题最相关的 {TOPIC_ATOMS} 条、最相关的 {TOPIC_WINDOWS} 段课程原文（本机按字词重合挑选）。', 'text': TOPIC_TEACHER_SYSTEM},
        {'key': 'topic2', 'title': '主题蒸馏 ② 合并成比较几位老师的文档', 'when': '附上主题、每位老师的整理和资料情况（几节课、有没有人工校对）。', 'text': TOPIC_MERGE_SYSTEM},
    ]


def prompts_markdown():
    """知识库《02、规则体系/01、通用规则/08、AI蒸馏提示词》的全文（由程序生成，和软件实际用的一字不差）。"""
    out = ['# AI蒸馏提示词', '',
           '> 猛人学习「老师工作台」实际使用的全部蒸馏提示词，由软件生成，和软件里「查看提示词」显示的一字不差。规则见 [[05、AI知识蒸馏规范]]。',
           '> 提示词改了会重新生成这份文件；不要只在这里改（软件不读这份文件），要改请告诉 AI 改软件。', '']
    for k, p in enumerate(prompt_list(), 1):
        out += ['---', '', f'## {k}、{p["title"]}', '', f'**什么时候用、还附上什么**：{p["when"]}', '', '```text', p['text'].strip(), '```', '']
    return '\n'.join(out)


# --------------------------------------------------------------- 工具 ----
def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _safe(s, n=24):
    s = re.sub(r'[\\/:*?"<>|\s#^\[\]？！!。，,、；;：“”‘’（）()《》【】]+', '', str(s or ''))  # 文件名里不要标点（问题常以问号结尾）
    return s[:n] or '未命名'


def _uri(path):
    vault = (config.load().get('obsidian') or {}).get('vault') or ''
    if not vault or not mentor._norm(path).startswith(mentor._norm(vault).rstrip('\\/') + '\\'):
        return ''
    from urllib.parse import quote
    import os
    rel = os.path.relpath(path, vault).replace('\\', '/')
    return f'obsidian://open?vault={quote(Path(vault).name)}&file={quote(rel)}'


def _backup(path, kind):
    folder = config.DATA_DIR / 'backups' / kind
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, folder / f'{Path(path).stem}-{time.strftime("%Y%m%d-%H%M%S")}.md')
    for old in sorted(folder.glob('*.md'), key=lambda p: p.stat().st_mtime)[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)


def _ready(mid):
    m = mentor.get(mid)
    f = mentor.files_of(m['folder']) if Path(m['folder']).is_dir() else {}
    if not f.get('atoms') or not f.get('persona'):
        raise RoundError(f'「{m["name"]}」还没蒸馏完（没有观点库或老师画像），先完成蒸馏')
    return m


def _check_mids(mids):
    mids = list(dict.fromkeys(str(x) for x in mids or []))
    if len(mids) < 2:
        raise RoundError('请至少选两位老师')
    return [_ready(mid)['id'] for mid in mids]


def _write_md(path, text, kind):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        _backup(path, kind)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def _atoms_link(m):
    """指向这位老师观点库的 Obsidian 链接（带路径，几位老师的观点库同名也不会混）。"""
    import os
    a = mentor.files_of(m['folder']).get('atoms') if Path(m['folder']).is_dir() else None
    vault = (config.load().get('obsidian') or {}).get('vault') or ''
    if not a:
        return m['name']
    if vault and mentor._norm(a).startswith(mentor._norm(vault).rstrip('\\/') + '\\'):
        rel = os.path.relpath(a, vault).replace('\\', '/')[:-3]
        return f'[[{rel}|{m["name"]}的观点库]]'
    return f'{m["name"]}的观点库'


def bad_refs(text, mentors_by_name):
    """［老师名 S02-21］ 这种出处里，老师不在、或者编号 / 课程在他的资料里找不到的。"""
    bad = []
    cache = {}
    for name, ref in REF_RE.findall(text):
        m = mentors_by_name.get(name)
        if not m:
            bad.append(f'{name} {ref}')
            continue
        if m['id'] not in cache:
            ov = mentor.overview(m['id'])
            cache[m['id']] = (set(ov['atoms']), {s['sid'] for s in ov['mentor'].get('sources') or []})
        atoms, sids = cache[m['id']]
        ok = ref in atoms or distill._norm_id(ref) in atoms if '-' in ref else ref.split()[0] in sids
        if not ok:
            bad.append(f'{name} {ref}')
    return sorted(set(bad))


def running(key):
    with _lock:
        job = _running.get(key)
        return job.id if job and job.status == 'running' else None


def _start(key, kind, fn):
    from . import jobs
    with _lock:
        job = _running.get(key)
        if job and job.status == 'running':
            raise RoundError('上一次还没做完，请等它做完或先停止')
        job = jobs.start(kind, fn)
        _running[key] = job
    return job


# ----------------------------------------------------------- 圆桌讨论 ----
def _rpath(cid):
    if not re.fullmatch(r'[0-9a-f]{12}', str(cid or '')):
        raise RoundError('没有这个圆桌讨论')
    return ROUND_DIR / f'{cid}.json'


def list_rounds():
    out = []
    if ROUND_DIR.is_dir():
        for p in ROUND_DIR.glob('*.json'):
            c = mentor._load_json(p)
            if c:
                out.append({'id': c['id'], 'title': c.get('title') or '新的圆桌讨论', 'updated': c.get('updated', ''),
                            'names': [x['name'] for x in c.get('people') or []], 'count': len(c.get('messages') or []),
                            'running': running(c['id'])})
    return sorted(out, key=lambda c: c['updated'], reverse=True)


def load_round(cid):
    c = mentor._load_json(_rpath(cid))
    if not c:
        raise RoundError('这个圆桌讨论不见了')
    return c


def _save_round(c):
    c['updated'] = _now()
    mentor._save_json(_rpath(c['id']), c)


def new_round(mids):
    mids = _check_mids(mids)
    c = {'id': uuid.uuid4().hex[:12], 'title': '新的圆桌讨论', 'created': _now(), 'updated': _now(),
         'people': [{'mid': mid, 'name': mentor.get(mid)['name']} for mid in mids], 'messages': []}
    _save_round(c)
    return c


def rename_round(cid, title):
    c = load_round(cid)
    c['title'] = str(title or '').strip()[:60] or c['title']
    _save_round(c)
    return c


def delete_round(cid):
    if running(cid):
        raise RoundError('这个圆桌讨论还在进行，先停止')
    _rpath(cid).unlink(missing_ok=True)


def _history(c, mid, upto=None):
    """给某位老师带上的对话：之前的问题和他自己的回答（不带别的老师的）。"""
    out = []
    for x in c['messages'][:upto]:
        if x['role'] == 'user':
            out.append({'role': 'user', 'content': x['content']})
        elif x['role'] == 'teacher' and x.get('mid') == mid and x.get('round', 1) == 1:
            out.append({'role': 'assistant', 'content': x['content']})
    return out


def _last_q(c):
    i = max((k for k, x in enumerate(c['messages']) if x['role'] == 'user'), default=-1)
    if i < 0:
        raise RoundError('还没有提问')
    return i


def _say(job, s, name, messages, label, who, c, msg):
    """让一个人（某位老师 / 总结）说一段：边写边推给界面；停止时保存已写的部分。返回是否被停止。"""
    parts = []
    job.push([{'kind': 'start', 'who': who, 'name': msg.get('name') or '', 'role': msg['role'], 'round': msg.get('round', 1)}])

    def piece(p):
        if p is None:
            parts.clear()
            job.push([{'kind': 'reset', 'who': who}])
        else:
            parts.append(p)
            job.push([{'kind': 'text', 'who': who, 'text': p}])
    stopped = False
    try:
        distill._ask(job, s, name, messages, label, on_piece=piece)
    except tasks.WorkError as e:
        if e.code != 'cancelled':
            raise
        stopped = True
    text = ''.join(parts).strip()
    if text:
        cur = load_round(c['id'])
        cur['messages'].append(dict(msg, content=text + ('\n\n（已停止）' if stopped else ''), model=distill._model(s, name), time=_now()))
        _save_round(cur)
    job.push([{'kind': 'done', 'who': who}])
    return stopped


def _summary_messages(c):
    qi = _last_q(c)
    q = c['messages'][qi]['content']
    blocks = []
    for x in c['messages'][qi + 1:]:
        if x['role'] == 'teacher':
            blocks.append(f'## {x["name"]}{"（第二轮回应）" if x.get("round") == 2 else ""}\n\n{x["content"]}')
    if not blocks:
        raise RoundError('这个问题还没有老师回答')
    return [{'role': 'system', 'content': ROUND_SUMMARY_SYSTEM},
            {'role': 'user', 'content': f'# 用户的问题\n{q}\n\n# 每位老师的回答\n\n' + '\n\n---\n\n'.join(blocks)}]


def ask(cid, question, summarize=True):
    """提问：每位老师各自回答，再（默认）总结。后台做，界面按推出来的条目边写边显示。"""
    question = str(question or '').strip()
    if not question:
        raise RoundError('问题是空的')
    if running(cid):
        raise RoundError('上一个问题还没讨论完，请等它做完或先停止')
    c = load_round(cid)
    s = config.load()
    name = s['chat']
    llm.check(s, name)
    for p in c['people']:
        _ready(p['mid'])
    c['messages'].append({'role': 'user', 'content': question, 'time': _now()})
    if c.get('title') in ('', '新的圆桌讨论'):
        c['title'] = re.sub(r'\s+', ' ', question)[:30]
    _save_round(c)
    names = '、'.join(p['name'] for p in c['people'])

    def run(job):
        qi = _last_q(load_round(cid))
        for k, p in enumerate(c['people']):
            job.report(k / (len(c['people']) + 1), f'{p["name"]}正在回答…')
            m = mentor.get(p['mid'])
            msgs, _ = mentor.build_messages(m, _history(load_round(cid), p['mid'], qi), question,
                                            extra=ROUND_ANSWER.format(names=names, me=m['name']))
            if _say(job, s, name, msgs, f'{m["name"]}回答', p['mid'], c, {'role': 'teacher', 'mid': p['mid'], 'name': m['name'], 'round': 1}):
                return {'chat': load_round(cid), 'stopped': True}
        if summarize:
            job.report(len(c['people']) / (len(c['people']) + 1), 'AI 正在总结…')
            _say(job, s, name, _summary_messages(load_round(cid)), '圆桌总结', 'summary', c, {'role': 'summary', 'name': 'AI 总结'})
        return {'chat': load_round(cid)}
    return {'job': _start(cid, 'round', run).id, 'chat': c}


def reply(cid, summarize=True):
    """第二轮：每位老师看了别人的回答后回应，再（默认）重新总结。"""
    c = load_round(cid)
    qi = _last_q(c)
    q = c['messages'][qi]['content']
    first = {x['mid']: x['content'] for x in c['messages'][qi + 1:] if x['role'] == 'teacher' and x.get('round', 1) == 1}
    if len(first) < 2:
        raise RoundError('至少要有两位老师回答过这个问题，才能互相回应')
    s = config.load()
    name = s['chat']
    llm.check(s, name)
    names = '、'.join(p['name'] for p in c['people'])

    def run(job):
        for k, p in enumerate(c['people']):
            if p['mid'] not in first:
                continue
            job.report(k / (len(c['people']) + 1), f'{p["name"]}正在回应…')
            m = mentor.get(p['mid'])
            others = '\n\n---\n\n'.join(f'## {o["name"]}的回答\n\n{first[o["mid"]]}' for o in c['people'] if o['mid'] != p['mid'] and o['mid'] in first)
            hist = _history(load_round(cid), p['mid'], qi) + [{'role': 'user', 'content': q}, {'role': 'assistant', 'content': first[p['mid']]}]
            msgs, _ = mentor.build_messages(m, hist, ROUND_REPLY.format(question=q, me=m['name'], others=others),
                                            extra=ROUND_ANSWER.format(names=names, me=m['name']))
            if _say(job, s, name, msgs, f'{m["name"]}回应', p['mid'], c, {'role': 'teacher', 'mid': p['mid'], 'name': m['name'], 'round': 2}):
                return {'chat': load_round(cid), 'stopped': True}
        if summarize:
            job.report(len(c['people']) / (len(c['people']) + 1), 'AI 正在总结…')
            _say(job, s, name, _summary_messages(load_round(cid)), '圆桌总结', 'summary', c, {'role': 'summary', 'name': 'AI 总结'})
        return {'chat': load_round(cid)}
    return {'job': _start(cid, 'round', run).id}


def summarize(cid):
    c = load_round(cid)
    msgs = _summary_messages(c)
    s = config.load()
    name = s['chat']
    llm.check(s, name)

    def run(job):
        job.report(0.1, 'AI 正在总结…')
        _say(job, s, name, msgs, '圆桌总结', 'summary', c, {'role': 'summary', 'name': 'AI 总结'})
        return {'chat': load_round(cid)}
    return {'job': _start(cid, 'round', run).id}


def round_md(c):
    people = [mentor.get(p['mid']) if (mentor.MENTOR_DIR / p['mid']).is_dir() else {'id': p['mid'], 'name': p['name'], 'folder': ''} for p in c['people']]
    first_q = next((x['content'] for x in c['messages'] if x['role'] == 'user'), c.get('title', ''))
    names = '、'.join(m['name'] for m in people)
    links = '、'.join(_atoms_link(m) if m.get('folder') else m['name'] for m in people)
    out = [f'---\n类型: 多位老师讨论\n老师:\n' + ''.join(f'  - {m["name"]}\n' for m in people)
           + f'状态: AI 按各位老师的画像扮演，待作者审核\n生成: 猛人学习「圆桌讨论」{time.strftime("%Y-%m-%d")}\ntags:\n  - 课程蒸馏\n  - 圆桌讨论\n---\n',
           f'# 多位老师讨论：{re.sub(chr(10), " ", first_q)[:40]}\n',
           f'> 在猛人学习「圆桌讨论」里请{names}一起讨论。每位老师的回答是 AI 只按他自己的老师画像和课程资料写的，不等于老师原话；'
           f'每位老师那一段里的出处 ［S02 19:54］、［S02-21］ 对应他自己的观点库（{links}）。「AI 总结」是 AI 归纳，不是任何一位老师的原话，也不是你的结论。\n']
    for x in c['messages']:
        if x['role'] == 'user':
            quoted = '\n'.join('> ' + ln if ln.strip() else '>' for ln in x['content'].strip().splitlines())
            out.append(f'---\n\n## 问：{re.sub(chr(10), " ", x["content"].strip())[:40]}\n\n{quoted}\n')
        elif x['role'] == 'teacher':
            out.append(f'### {x["name"]}{"（回应其他老师）" if x.get("round") == 2 else ""}\n\n{x["content"].strip()}\n')
        elif x['role'] == 'summary':
            out.append(f'### AI 总结（不是任何一位老师的原话）\n\n{x["content"].strip()}\n')
    return '\n'.join(out)


def save_round(cid, folder):
    """整场讨论写进 Obsidian 的主题蒸馏资料（以后再收藏就更新同一个文件，覆盖前备份）。"""
    if folder not in TOPIC_FOLDERS:
        raise RoundError('请选一个主题文件夹')
    c = load_round(cid)
    if not any(x['role'] != 'user' for x in c['messages']):
        raise RoundError('还没有老师回答，没有可收藏的')
    base = Path(distill._vault()) / distill.KB_DIR / TOPIC_SUB / folder
    old = (c.get('saved') or {}).get('path') or ''
    path = Path(old) if old and Path(old).parent == base else base / f'{time.strftime("%Y%m%d")}、{_safe(c["title"])}、多位老师讨论.md'
    _write_md(path, round_md(c), '圆桌讨论')
    c['saved'] = {'path': str(path), 'folder': folder, 'time': _now()}
    _save_round(c)
    return {'file': str(path), 'uri': _uri(path), 'chat': c}


# ----------------------------------------------------------- 主题蒸馏 ----
def _tpath(tid):
    if not re.fullmatch(r'[0-9a-f]{12}', str(tid or '')):
        raise RoundError('没有这个主题蒸馏')
    return TOPIC_DIR / f'{tid}.json'


def list_topics():
    out = []
    if TOPIC_DIR.is_dir():
        for p in TOPIC_DIR.glob('*.json'):
            t = mentor._load_json(p)
            if t:
                out.append(dict(t, running=running(t['id'])))
    return sorted(out, key=lambda t: t.get('created', ''), reverse=True)


def get_topic(tid):
    t = mentor._load_json(_tpath(tid))
    if not t:
        raise RoundError('这个主题蒸馏的记录不见了')
    f = t.get('file') or ''
    text = mentor._read(f) if f and Path(f).is_file() else ''
    names = {p['name']: p['mid'] for p in t.get('people') or []}
    return {'topic': dict(t, running=running(tid)), 'text': text, 'uri': _uri(f) if text else '', 'names': names}


def delete_topic(tid):
    """只删软件里的记录；Obsidian 里写好的文件不动。"""
    if running(tid):
        raise RoundError('这个主题蒸馏还在做，先停止')
    _tpath(tid).unlink(missing_ok=True)


def _reviewed(m):
    a = mentor.files_of(m['folder']).get('atoms')
    hit = re.search(r'^来源人工校对:\s*(\d+)/(\d+)', mentor._read(a)[:2000], re.M) if a else None
    return f'{hit.group(1)}/{hit.group(2)} 节人工校对过' if hit else '人工校对情况未记录'


def start_topic(topic, mids, folder, note='', tid=None):
    """开始（或重新做）一个主题蒸馏，后台做。"""
    topic = str(topic or '').strip()
    if not topic:
        raise RoundError('请填主题')
    if folder not in TOPIC_FOLDERS:
        raise RoundError('请选结果放在哪个主题文件夹')
    mids = _check_mids(mids)
    distill._vault()
    s = config.load()
    name = s['chat']
    llm.check(s, name)
    t = mentor._load_json(_tpath(tid)) if tid else None
    t = t or {'id': uuid.uuid4().hex[:12], 'created': _now()}
    t.update(topic=topic, folder=folder, note=str(note or '').strip(), people=[{'mid': m, 'name': mentor.get(m)['name']} for m in mids],
             done=False, updated=_now())
    mentor._save_json(_tpath(t['id']), t)

    def run(job):
        per, n = [], len(mids)
        q = topic + ' ' + t['note']
        for k, mid in enumerate(mids):
            m = mentor.get(mid)
            job.report(0.8 * k / n, f'整理「{m["name"]}」关于「{topic}」讲了什么（{k + 1}/{n}）…')
            f = mentor.files_of(m['folder'])
            atoms = list(mentor.parse_atoms(mentor._read(f['atoms'])).values())
            picked = mentor.rank(q, atoms, lambda a: a['text'] + a['cond'], TOPIC_ATOMS)
            wins = sorted(mentor.rank(q, mentor._windows(m), lambda w: w['text'], TOPIC_WINDOWS), key=lambda w: (w['sid'], w['start']))
            srcs = m.get('sources') or []
            info = f'{len(srcs)} 节课，{_reviewed(m)}'
            if not picked and not wins:
                text = '资料里没有讲到这个主题。'
            else:
                user = (f'主题：{topic}\n' + (f'用户想重点看：{t["note"]}\n' if t['note'] else '') + f'老师：{m["name"]}（{info}）\n课程：\n'
                        + '\n'.join(f'- {x["sid"]}：{x["title"]}' for x in srcs)
                        + '\n\n# 观点库里和主题相关的条目\n' + ('\n'.join(f'- [{a["id"]}]（{a["src"]}，{a["kind"]}）{a["text"]}' + (f'　条件：{a["cond"]}' if a['cond'] else '') for a in picked) or '（没有）')
                        + '\n\n# 和主题相关的课程原文（字幕片段）\n' + ('\n'.join(f'- [{w["sid"]} {mentor._ts(w["start"])}–{mentor._ts(w["end"])}] {w["text"]}' for w in wins) or '（没有）'))
                text = distill._ask(job, s, name, [{'role': 'system', 'content': TOPIC_TEACHER_SYSTEM}, {'role': 'user', 'content': user}], f'整理「{m["name"]}」')
            per.append((m, text, info))
        job.report(0.8, '合并成比较几位老师的文档…')
        user = (f'主题：{topic}\n' + (f'用户想重点看：{t["note"]}\n' if t['note'] else '')
                + '\n# 资料情况\n' + '\n'.join(f'- {m["name"]}：{info}' for m, _, info in per)
                + '\n\n# 每位老师关于这个主题的整理\n\n' + '\n\n---\n\n'.join(f'## {m["name"]}\n\n{text}' for m, text, _ in per))
        body = distill._from_heading(distill._ask(job, s, name, [{'role': 'system', 'content': TOPIC_MERGE_SYSTEM}, {'role': 'user', 'content': user}], '合并主题蒸馏'), '## 一')
        people = [m for m, _, _ in per]
        bad = bad_refs(body, {m['name']: m for m in people})
        model = distill._model(s, name)
        md = (f'---\n类型: 主题蒸馏\n主题: {topic}\n老师:\n' + ''.join(f'  - {m["name"]}\n' for m in people)
              + f'状态: AI 蒸馏，待作者审核\n来源人工校对:\n' + ''.join(f'  - {m["name"]}：{info}\n' for m, _, info in per)
              + f'生成: 猛人学习「主题蒸馏」{time.strftime("%Y-%m-%d")} · {model}\ntags:\n  - 课程蒸馏\n  - 主题蒸馏\n---\n\n'
              f'# {topic}：几位老师怎么说\n\n'
              f'> 主题蒸馏：AI 从{"、".join(_atoms_link(m) for m in people)}里挑出和「{topic}」相关的内容，比较几位老师的共识、分歧和条件（由猛人学习「主题蒸馏」生成）。'
              '出处 ［老师名 S02-21］ 是那位老师观点库的编号，［老师名 S02 19:54］ 是他第几节课第几分几秒。\n'
              '> 这里写的是「几位老师怎么说」，不是正确答案，也不是你的结论。\n\n' + body.strip() + '\n'
              + (f'\n---\n\n> 程序检查：上面有 {len(bad)} 处出处在那位老师的资料里找不到（可能是 AI 写错了），审核时留意：' + '、'.join(bad[:30]) + '\n' if bad else ''))
        base = Path(distill._vault()) / distill.KB_DIR / TOPIC_SUB / folder
        old = Path(t['file']) if t.get('file') and Path(t['file']).parent == base else None
        path = old or base / f'{time.strftime("%Y%m%d")}、{_safe(topic)}、跨来源蒸馏.md'
        _write_md(path, md, '主题蒸馏')
        cur = mentor._load_json(_tpath(t['id'])) or t
        cur.update(file=str(path), done=True, updated=_now(), model=model, bad_refs=len(bad))
        mentor._save_json(_tpath(t['id']), cur)
        return {'topic': cur, 'file': str(path), 'uri': _uri(path), 'bad_refs': len(bad)}
    job = _start(t['id'], 'topic', run)
    return {'job': job.id, 'topic': t}
