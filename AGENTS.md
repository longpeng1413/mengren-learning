# 维护说明（给 AI 编程助手和接手的开发者）

先读 README.md 了解软件做什么。这份文件记的是**代码里看不出来、但改错了会出事**的约定。

用户是这个软件唯一的使用者兼所有者，不是程序员：和他沟通用中文、讲结果和影响，不讲术语；动手前说清楚要改什么，改完说清楚测了什么。

## 绝对不要做的事

- **不要强行结束用户正在运行的「猛人学习.exe」**（不要一上来就 taskkill / Stop-Process）。他可能正在看课、改字幕，强关会丢未保存的修改。用户已允许升级时关闭它，但要**正常退出**（程序会先保存），见下面「打包和更新」。
- **点右上角 × 默认不退出，收到右下角托盘**（1.7.13，用户要求；设置「关于与数据 → 点右上角 × 时」可以改成直接退出，`close_to_tray`）。托盘在 `app/tray.py`（pystray，打包要 `--hidden-import pystray._win32`）：单击打开，右键「打开猛人学习」「退出」。所以 `CloseMainWindow()` 只会把窗口收起来；要让它保存后退出，运行 `猛人学习.exe --quit`（命名事件通知正在运行的那个走 `on_closing(force=True)`）。再次双击时 `bring_front` 把收起的窗口显示出来。
- **不要改回本机 HTTP 服务器**。界面必须用 `index.as_uri()`（`file://`）加载，通过 pywebview 的 `js_api` 和后台通信。用户电脑上的 360 主动防御和 Clash Verge 会干扰 127.0.0.1 回环连接（传输卡在约 64KB 或被重置），以前因此出过「图标只显示一半」「界面卡住」。注意：给 `create_window` 传普通路径时 pywebview 会**悄悄**起一个本地服务器，所以一定要传 `file://` URI。
- **不要在关闭事件里同步调用 `window.evaluate_js`**。`closing` 事件在界面线程里执行，同步等界面会死锁（以前出过「猛人字幕.exe 未响应」，软件那时还叫猛人字幕）。现在的做法见 `app/main.py` 的 `on_closing`：先返回 False 取消关闭 → 后台线程通知界面 `window.prepareClose()` 保存 → 等 `api._quit`（最多 4 秒）→ `window.destroy()` → `os._exit` 兜底。
- **改设置只用 `config.update(部分设置)`；`config.save()` 是保存整份设置**。2026-10-11 曾用 `config.save({'book_rules_file': …})` 把用户的密钥、ChatGPT 登录、Obsidian 位置全部冲掉。现在 `save()` 会从现有文件补回没传的项，但仍应该用 `update()`。
- **读不出数据文件时不能当成空的**（1.7.10）：`config._read_json` 读失败会重试、再用 `.bak`，都不行就抛 `DataError`，界面显示原因；只有文件本来不存在才用默认值。每次写之前留 `.bak`，每天第一次写留快照到 `%APPDATA%\猛人学习\backups\数据`（保留 30 天）。
- **同一份数据只开一个窗口**（`main.single_instance`，按数据目录建互斥锁；`--selftest` 不受限）。再双击会切到已开的窗口。
- **不要把 API 密钥写进日志、报错信息或仓库**。密钥字段见 `config.SECRETS`，发给界面前用 `config.public()` 打码。用户的设置在 `%APPDATA%\猛人学习\`，不在项目里。
- **从 Claude 桌面版里启动的程序（包括你跑的脚本），写 `%APPDATA%` 会被 Windows 转存**到 `%LOCALAPPDATA%\Packages\Claude_…\LocalCache\Roaming\`，而且只有 Claude 启动的程序看得到。2026-10 因此出过「从桌面打开媒体库空白、设置像没了」：用户好几天都在用我启动的那个窗口，数据全写进了转存文件夹。现在 `app/core/appdata.py` 防住了：被转存时软件通过资源管理器重新打开自己；正常启动时把转存的数据先备份再搬回来。所以：
  - 给用户打开软件用 `explorer.exe "…\猛人学习.exe"`（或桌面快捷方式），不要直接启动 exe；
  - 用脚本读写用户的真实设置时，你看到 / 写进的可能是转存的那份。要看真正的文件用 `\\localhost\C$\Users\…\AppData\Roaming\猛人学习`；改设置尽量让用户在软件里改。
  - 启动日志里有一行「数据文件夹：…｜settings.json 多大…｜媒体库几个文件夹」，再有「数据不见了」先看这一行。

### 改名（1.6）

软件原名「猛人字幕」，1.6 起叫「猛人学习」。兼容要一直保留：`config._migrate_data_dir()` 把 `%APPDATA%\猛人字幕` 搬到 `%APPDATA%\猛人学习`；`doc.load` 还认旧的 `.猛人字幕.json`，保存时改成 `.猛人学习.json`；设置导入认两个名字；`find_note` 认两个标签。项目文件夹 2026-10-12 从 `E:\Claude-猛人字幕` 搬到 `D:\mengren-learning`（和 GitHub 仓库同名，用户要求软件开发文件都放 D 盘）。
- **不要动 `D:\Claude-乔木剪藏`**（以前在 `E:\Claude-乔木剪藏`），那是另一个项目。

## 必须保持的行为

### 字幕文件兼容

字幕存在视频旁边的 `视频名.mp4.猛人学习.json`（写不进去时放 `%APPDATA%\猛人学习\library\`），见 `app/core/doc.py`。用户已经有大量做好的字幕：

- 新增字段可以，但读取时要给旧文件缺的字段兜底；
- 不要改名或删除已有字段（`segments`、`summary`、`chat`、`versions`、`teacher`、`reviewed`、`corrected`、`clip`、`pending_task`、`doubts` 等）；
- `frames` 是可插的图：`[{t: 秒, why: 为什么挑这张, note: 一句话说明, caption: 旧版的画面字幕（新版不再写）, figure: 可插图, manual: 手动截取}]`。截图本身不存，放在缓存 `frames/` 里，需要时按时间重新截（`frames.grab`）；
- `doubts` 是 AI 校对标出的存疑句：`[{t: 句子开始时间, reason: 原因, text: 原句}]`，按开始时间对应到字幕（`segments` 每段只存 4 个字段，`clean_segments` 会去掉别的字段，所以不能把标记放进段里）；
- 每段的格式是 `{start, end, text, tr}`，秒为单位。
- `clip` 是剪藏记录 `{path, time, hash}`：`hash` 是 `doc.note_hash`（会写进笔记的内容的指纹），之后内容变了，媒体库显示「待更新剪藏」（`doc.clip_stale`）。1.7.2 以前剪藏的没有 hash，不提示。剪藏不会自动更新 Obsidian（用户选的是「提醒」，不是自动同步）。`clip.file_hash` 是写出去的笔记文件的指纹（`doc.note_fingerprint`，不算属性里的 status）：重新剪藏前如果笔记在 Obsidian 里改过（没有指纹的旧剪藏看文件修改时间），`doc.clip` 返回 `conflict`，界面让用户确认，确认后（`force`）先备份到 `%APPDATA%\猛人学习\backups\笔记` 再覆盖。软件不从 Obsidian 读回改动。

重新剪藏是**合并**，不是整篇覆盖（1.7.5，`doc.merge_note`）：软件只负责 `APP_KEYS` 里的属性、第一个 `##` 之前的标题说明、`## 课程重点`、`## 完整字幕`；用户自己的 `##` 小节、属性、tags（取并集）、写在这几块里的标注（`> [!…]`，按它上面那一行重新定位，找不到就放到那一节最后）都保留；旧版本写的 `## 画面记录` 去掉。`file_hash` 带 `v2:` 前缀，只算软件负责的部分（去掉标注和空行，不算状态和剪藏时间），所以用户只加了自己的东西不会弹窗。课程重点模板要求 AI 不用标注框，标注留给用户。

书籍笔记（1.7.9，`book.clip_book`）同样合并：`merge_note(kind="book")`，软件负责 `BOOK_KEYS` 里的属性、标题和来源说明、「一、本章主题」这类编号小节，以及上次剪藏时记下的小节名（`clip.files[文件名].sections`，全书总览的小节不编号）；用户的小节跟着它前面那个软件小节走；软件这次不再写的小节里的标注挪到文末。每篇笔记的指纹和标签也记在 `clip.files` 里，改过的先问（`conflict` + `changed`），确认后备份。

### 术语表语法（`app/core/llm.py` 的 `parse_terms`）

```
术语1, 术语2          → 专用术语：传给识别服务当热词
错写 => 正确          → 只是提示 AI 结合上下文判断，不直接替换
错写 =>! 正确         → 识别完直接替换（apply_fixes 只处理这一种）
```

不要把 `=>` 改成直接替换：用户的术语表里有「分 => 峰」这类单字对照，直接替换会把「部分」改成「部峰」。老师档案的「术语说明」（`notes`）只给 AI 参考，最多取 20000 字，不传给识别服务。

老师档案可以关联 Obsidian 里的术语表文件（`terms_file`）。用户的术语表以 Obsidian 里那份为准，软件不另存副本：`config.get_teacher()` 每次读文件，用 `llm.terms_from_markdown()` 抽出术语和识别错误对照，加在手写术语后面，整份文件当术语说明。界面上的老师列表（`load_teachers()`）是没合并过的原始档案。

### 课程重点（`llm.DEFAULT_TEMPLATE`）

用户的明确要求：**不设固定模板**，按老师这节课实际的知识体系和逻辑框架组织，可以调整顺序、合并重复，但**只写老师讲过的**，不让 AI 猜测、补充或评价；定义、口诀、数字照录，每条标时间戳，拿不准的标「存疑」。改提示词时不要违背这条。

用户 2026-10 追加的三条（模板和查漏都要遵守）：老师的自述、经历、出版、原创声明、课程安排一律保留，标「老师自述：」，不评价、不删除；口述与课件不一致时分别保留，加「整理者注」；老师没讲明的标「（未讲明）」，不补写。

用户 2026-10 明确决定：剪藏笔记的主体**保持「课程重点」**这种写法，不改成知识库规范里按时间顺序的「来源级整理」。独立来源审计也不做进软件（用户在单独的聊天窗口里做）。

生成课程重点后默认自动「查漏补缺」（`llm.review_summary`）：AI 只返回要补的条目和放在哪个小标题下，由程序插进去，不让 AI 重写整篇。

**课程重点比字幕旧时提醒**（2026-10-14，用户选的方案 B：流程不变，只提醒）：一键整理在用户人工校对存疑句之前就写了课程重点，之后改了字幕，重点里还是改之前的说法（实测 10.08 答疑课：「一破就追」被写成「破级有力就追」）。生成时在 `doc.summary_src` 记下当时每句字幕的短指纹，`doc.summary_changes(doc)` 算出之后改了几句（以前生成、没有 summary_src 的，按手动改过的句子数估算）；≥ `SUMMARY_STALE_AT`（3）句时，课程重点上方显示提醒和「重新生成」按钮，媒体库标「重点待更新」。不自动重新生成、不拦着剪藏。

### AI 校对

只修正识别错误，不改写句子、不润色。

### 重新识别

重新识别、清空、恢复之前会把旧字幕存成历史版本（最多 5 份），不要直接覆盖。

### 剪藏到 Obsidian（`doc.to_markdown` / `doc.clip`）

笔记属性名用中文、只留有用的（用户 2026-10 要求，1.7.8 起：视频文件、人工校对、剪藏时间、tags；**不写状态**（以前给多轮 AI 交叉审核用的「待审计」，用户 2026-10 决定不再交叉审核；旧笔记里还是「待审计」的重新剪藏时去掉，用户改过的保留）；标题下面不写老师、系列、时长那一行（1.7.8，用户决定：归类自己在 Obsidian 里做）。**不写老师**：术语表档案是几位老师共用的，名字不是讲课老师，只当标签；老师由用户在 Obsidian 的「老师」属性里自己填，所以「老师」不在 `APP_KEYS` 里，重新剪藏保留。1.7.4～1.7.7 软件写过的「老师」（值是术语表名字）在旧笔记里遇到会去掉；`clip.tags` 记上次软件写的标签，用来去掉过时的标签；视频日期是文件修改时间、会误导，语言一眼就知道，用户明确都不要；书籍笔记也是中文属性名），`tags` 是 Obsidian 固定的属性名，不能改。读旧笔记时英文的 `status`、`source` 也要认（`_old_status`、`find_note`）。

笔记结构是：属性（frontmatter）→ `## 课程重点`（含必要的插图）→ `## 完整字幕`（`[mm:ss] 原文`，下一行是译文）。用户 2026-10 要求：**不附画面记录**，笔记只要课程重点和完整字幕，简单、实用、完整、忠实。用户用这份笔记做知识「蒸馏」，结构要稳定。同一节课再剪藏是更新原笔记，不是新建。

### 逐课积累术语表（`llm.suggest_terms` / `glossary.py`）

每句字幕的改动记在 `doc.fixes`（`[{t, before, after, by: ai|frames|manual}]`）：AI 校对、画面复核的由 `workflow.record_fix` 记，手动修改的由 app.js 的 `recordFix` 记（两边写法相同）。「新术语」：`llm.fix_spans` 取出换掉的几个字，连同完整字幕交给 AI，找术语表没有的新术语和识别错误，程序再去掉已有的；**用户勾选确认后**才写（用户术语表的「新术语加入规则」要求人工确认，只记名称，不写定义）。关联了文件时写进文件末尾的「逐课补充（猛人学习）」一节（`| 术语 | 首次出现 | 老师原话 |` 和 `| ASR常见识别 | 正确术语 | 出处 |` 两张表，`terms_from_markdown` 认得），文件其他部分不动，写前备份到 `%APPDATA%\猛人学习\backups\术语表`（留 30 份）。没关联文件时加进老师档案的术语。

## Agent 自动整理（命令行，2026-10-14）

让 Claude Code、Codex（以后 ChatGPT Dots）不点界面就能整理课程。用法见 `docs/命令行.md`。用户定的范围：

- **只是把「一键整理」自动化，不改工作流**。顺序：生成字幕（有就跳过）→ AI 校对 → 找关键帧 → 画面复核存疑句 → 翻译（**只有英文**）→ 课程重点 → 查漏 → 新术语（只放进 `terms_pending`）。**不剪藏、不写术语表、不打开「已校对」**：这些由用户在软件里人工校对后自己做。结果写在原来的字幕文件里，媒体库里直接能看到。
- **书**：分章（没认出章节时 AI 读目录）→ 逐章整理 → 全书总览，同样不剪藏。
- 识别只用云端，`--model local` 留着接口、明确返回 `not_supported`。
- Codex 写的升级提示词里的结构化重点、模板可配置、术语五分类、草稿区、HTTP API **用户明确不要**。

结构：
```
界面 app.js ──→ bridge.py ──┐
命令行 cli.py ──────────────┼──→ core/workflow.py（任务层）──→ asr / llm / frames / book / doc（核心，不变）
（以后 MCP，stdio，不开端口）┘        └─ core/tasks.py（任务记录、错误码、重试、「正在整理」标记、取消）
```

- `workflow.py` 每一步：读最新字幕文件 → 调核心模块 → **再读一次**（期间用户可能在界面里改过）→ 按规则合并 → 保存。一步做到一半失败什么都不写（以前界面边收边存，出错后「结果又出现一半」）；已完成的步骤再运行时跳过（看字幕文件里的结果和新字段 `steps`），除非 `--steps x --force`。
- **界面和命令行只有这一套规则**（2026-10-14 第二步）：界面上的整理按钮（生成字幕、AI 校对、找关键帧、画面复核、翻译、课程重点、查漏补缺、新术语、整理章节、全书总览）和「一键整理」都调 `bridge.do_steps` → `workflow.process(caller='gui', check_library=False, redo=这几步)`，做完 `do_doc` 重新读字幕文件。以前的 `do_transcribe / do_correct / do_translate / do_review / do_terms_suggest / do_frames_extract / do_doubts_recheck / do_book_organize / do_book_overview` 已删除，**不要再在 app.js 里写合并规则**，改整理规则只改 `workflow.py`。
  - 界面专用的选项（用户在界面上自己选的）：`allow_no_teacher`（没选老师也校对）、`any_lang`（中文也翻译）、`translate_all`、`chapters`（只整理勾选的章）、`allow_partial`（章节没整理完也写总览）。Agent 不用这些，行为按一键整理的默认勾选。
  - 边做边显示：`on_items` 推 `{kind: 'cue'}`（识别出的字幕）和 `{kind: 'text'}`（课程重点正在写的文字），只显示不写盘。识别中途出错时界面问「要保留已识别的部分吗」，留下就由界面保存。
  - 界面的撤销、绿色下划线：任务前记下每句原文，重新读取后对比标出（校对、复核不增删句子）。
  - 还留在 app.js 的：手动改字幕时的 `recordFix`（只有界面有手动修改）、清空 / 恢复历史版本的 `pushVersion`、剪藏、AI 读目录分章、单张截图写说明。
- **保存冲突**：字幕文件新增 `rev`（每次保存加 1）。`doc.save(path, doc, base_rev)` 发现磁盘上的 rev 和调用方读到的不一样就不写、返回 `conflict`；界面（`save()` / `onSaveConflict()`）问用户「重新打开看自动整理的结果」还是「保留我的覆盖」。写文件用 `doc.write_lock`（`%APPDATA%\猛人学习\locks\`）防止两个进程同时写。
- Agent 正在整理的课有 `locks\<key>.busy` 标记（记着进程号，进程不在了就失效），界面打开时提示。
- AI 校对前存一份「AI 校对前」历史版本（最多 5 份，和重新识别共用），不满意可以在「历史版本」里恢复。
- 界面自测用假的识别和 AI：`app/core/fake_ai.py`，只在 `main.py --selftest=… --fake-ai` 时装上（`run_ui_test.py` 会传），用 `test-media/ui-ai/` 里的视频和书测每个整理按钮和一键整理。
- 只处理媒体库里的文件（`workflow.allowed`），其他路径返回 `not_in_library`。
- 命令行从 Claude 桌面版启动时可能被转存（见上面「转存」）：`cli.use_real_appdata()` 检测到就改用 `\\localhost\C$\…` 读写真实数据。日志写 `agent.log`（不和界面的 `app.log` 抢着轮换）。
- 入口：`mengren.cmd`（项目根目录，用 `.venv` 的 Python 跑 `app/cli.py`）。打包版还没有控制台版 exe（窗口程序打不出命令行输出），需要时再加。

## 架构速览

- `app/main.py`：入口、版本号 `VERSION`、开窗口、关闭流程、卡死监视（界面 20 秒无响应时把各线程堆栈写进日志）、自测入口。
- `app/bridge.py`：界面调用后台的唯一入口 `Api.call(name, body)` → 分派到 `do_<name>`。出错会写日志并返回 `{'ok': False, 'error': ...}`。新增功能就加一个 `do_xxx`，前端用 `api('xxx', {...})` 调。
- 耗时任务用 `app/core/jobs.py` 在后台线程跑（只在内存里），界面用 `api('job', {id, since})` 轮询进度；边做边显示的内容（识别出的字幕、课程重点的文字、AI 对话的回答）作为 job 的条目推给界面。整理步骤的 job 里跑的是 `workflow.process`，它另外在 `%APPDATA%\猛人学习\tasks\` 留持久的任务记录（界面和命令行的都在）。
- `app/core/`：
  - `asr.py`：识别。豆包有多种模型（`DOUBAO_MODELS`）：极速版 base64 直传；流式版走 WebSocket 二进制协议（发送和接收分开两个线程，不要改回「发一包等一包」，那样只有一两倍速；长视频切成约 300 秒的段、两路同时发，界面按发送比例显示进度）；标准版和闲时版要先上传到火山 TOS 拿预签名链接。
  - `llm.py`：AI 调用，支持 OpenAI 兼容接口、ChatGPT 账号（codex）、Claude（官方 anthropic SDK）、Gemini（REST）。
  - `codex_auth.py`：ChatGPT 账号登录，借用 Codex 命令行工具的 OAuth 方式。端口 1455 被占用时用「粘贴回调网址」兜底。
  - `media.py`：ffmpeg 相关，包括探测、能否直接播放、转码、抽 16k 单声道音频、按静音切段、检查视频结尾是否损坏。
  - 插图原则（用户明确要求）：**非必要不插图**，知识库要小、便于以后 AI 蒸馏。课程重点最多 3 张，只插文字确实说不清的；剪藏时图片缩到 960 宽、压缩（`doc._small_copy`）。
  - `frames.py`：关键帧。候选按 15 秒一段挑「最像背景」的小图（避开老师走动挡屏），加上老师说「请看」的时间；能看图的 AI 先筛选、再确认并写一句说明，只留 `figure` 为真的，超过 8 张时 `frames.pick` 按说明去重（只看文字）。用户 2026-10 明确：识别画面**只为插必要的图**，不要照录课件、板书，不要写大段画面记录。课程重点里写 `![[画面 mm:ss]]`，剪藏时 `doc.place_figures` 换成截图并复制到笔记旁边的 `image/`。只用豆包识别的字幕（`.猛人学习.json`），不读用户以前本地模型生成的 `.srt`。
  - 一键整理的顺序是用户定的：先 AI 按术语表校对，再找关键帧，再「用画面复核存疑句」（`frames.recheck_doubts`：截下每句存疑句说话时的画面，长句每 3 秒一张，最多 4 张，看烧录字幕和课件核对）。不要把找关键帧挪回校对前面。
  - AI 消息内容可以是图文混排 `[{'type': 'text'|'image', …}]`，`llm.stream` 按各家格式转换（`_to_openai` / `_to_codex` / `_to_claude` / `_to_gemini`）。
  - `config.py`：设置、老师、媒体库、播放进度和存放位置。缓存和日志默认放软件目录下的「缓存（可随时删除）」「日志（可随时删除）」，可以自定义（`storage_dir`，重启生效），软件目录不可写时退回 LOCALAPPDATA。
  - `cache.py`：清理。音频保留 3 天，转换后的视频最多 5GB，截图 30 天没用就删；`clean_marks` 删掉 `locks\` 里一天没用的锁文件、程序已经不在的「正在整理」标记。软件启动时清一次，命令行的 process / run / resume 开始前也清一次（命令行从源码跑，缓存在项目文件夹里，以前没人清）。
  - `log.py`：日志最多 2MB × 4 个文件。打包后 stdout/stderr 是 None，已重定向到日志。
  - `workflow.py`：任务层，所有整理步骤的规则（见上面「Agent 自动整理」）；`tasks.py`：任务记录、错误码、重试、「正在整理」标记、取消；`fake_ai.py`：界面自测用的假 AI。
  - `doc.py`、`library.py`、`jobs.py`。
- `app/cli.py`：命令行（给 Agent 用），只是一层薄入口，调 `workflow`。
- `app/web/`：纯 HTML/CSS/JS，没有构建步骤，没有框架。

## 开发、测试

Windows 上：

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app\main.py                    rem 运行（加 --debug 打开开发者工具）
.venv\Scripts\python tests\test_backend.py          rem 后台测试，不联网
.venv\Scripts\python tests\test_agent.py            rem Agent 自动整理和命令行的测试，不联网（识别和 AI 用假的）
.venv\Scripts\python tests\run_ui_test.py           rem 界面测试：弹出真实窗口自动操作，最后连关闭流程一起测
.venv\Scripts\python tests\run_ui_test.py --exe     rem 同上，测 dist-next 里打包好的 exe
```

- 后台测试最后会用 pyflakes 查「用了没定义的变量」（1.6.1 出过 `bridge.do_correct` 里变量名写错、AI 校对一用就报错，界面测试没覆盖到）。
- 改完至少跑后台测试、Agent 测试和界面测试，全部通过才算完成；改了界面就在 `tests/ui_selftest.js` 里补检查，改了后台就在 `tests/test_backend.py` 里补，改了整理步骤或命令行就在 `tests/test_agent.py` 里补。
- 界面测试要弹出真实窗口，**在沙盒里通常跑不了**，需要无沙盒权限。测试数据放在项目里的 `.testdata/`（已忽略），不会碰用户的真实设置。
- 测试视频由 `tests/make_test_media.py` 用 ffmpeg 自动生成，不进仓库。
- 需要 ffmpeg / ffprobe 在 PATH 里（`winget install Gyan.FFmpeg`）。
- 用 Python 脚本或编辑工具改文件。不要用 shell heredoc 写含中文、反斜杠的代码，以前出过转义和换行符损坏。`.bat` 文件必须是 CRLF 换行。

## 打包和更新用户本机的程序

用户从桌面快捷方式打开 `dist\猛人学习\猛人学习.exe`。

1. `.venv\Scripts\python build.py` → 输出到 `dist-next\`（不碰正在用的 `dist\`）。
2. `.venv\Scripts\python tests\run_ui_test.py --exe` 测打包版。
3. 换进 `dist`：用户已允许升级时关闭正在运行的程序。先正常退出（运行 `& "D:\mengren-learning\dist\猛人学习\猛人学习.exe" --quit`，等它自己保存退出，最多等 15 秒左右，还不退出才 `Stop-Process`；1.7.12 及以前的版本没有 --quit，用 `CloseMainWindow()`），然后执行「更新到新版.bat」里同样的步骤（把 `dist\猛人学习` 里的「缓存（可随时删除）」「日志（可随时删除）」移进 `dist-next\猛人学习`，删掉 `dist`，把 `dist-next` 改名为 `dist`）。如果用户在另一个对话里正用命令行整理课程，不影响换新版（命令行用的是源码，不是 `dist`），最后用 `explorer.exe "D:\mengren-learning\dist\猛人学习\猛人学习.exe"` 启动（不要直接启动，见上面「转存」）并告诉用户。用户也可以自己双击「更新到新版.bat」。

## 发新版本

1. 改 `app/main.py` 里的 `VERSION`。
2. 测试、打包（见上）。
3. 提交并推送到 `main`（仓库：https://github.com/longpeng1413/mengren-learning）。
4. 打包发布用 `release.py`（见下面「发布压缩包」），用 `gh release create v版本号` 发布。**附件文件名必须是英文**，例如 `mengren-learning-1.7.13-windows-with-ffmpeg.zip`，否则 GitHub 会把中文吞掉。中文名写在附件的 label 里。发布说明里提醒：程序没有数字签名，可能会被安全软件提示。
5. README 的功能说明有变化时一起更新。

推送、发布这类对外操作，先问用户。

### 发布压缩包

`.venv\Scripts\python release.py --src dist-next` 在 `release\` 里打 `mengren-learning-版本-windows-with-ffmpeg.zip`（附带本机 winget 装的 ffmpeg.exe、ffprobe.exe 和它的 LICENSE / README，GPL 要求一起分发；软件会先找自己文件夹里 `ffmpeg\` 下的 ffmpeg）。**用户 2026-10 要求：GitHub Release 一律只发这个附带 ffmpeg 的包**，别人下载解压就能用；不再发不带 ffmpeg 的包，也不用往桌面放压缩包。打包时不要带软件文件夹里的缓存和日志（会有好几 GB），也**不要带任何个人设置**：用户会直接把包发给朋友。

`app/core/portable.py`：软件文件夹里有 `猛人学习设置.json`、而这台电脑还没有设置时，第一次启动自动导入，导入后改名「…（已导入，可删除）」。

## 书籍和文档（1.6）

- `app/core/book.py`：PDF 用 pypdfium2 读文字层、认图片 / 矢量图、渲染页面（需要 Pillow）；Word 直接解析 docx 的 XML；TXT / MD 按标题切。书上页码 = PDF 页序 − `shift`，自动从页面上下单独的数字认，用户可以改。
- 书籍的整理结果也存在旁边的 `.猛人学习.json`，`kind: 'book'`，字段：`title, author, shift, chapters[{title, start, end, md}], overview, clip`，`segments` 留空列表（兼容）。
- 整理一章时一次最多 18 页 / 36000 字 / 10 张页面图，超了先分段写笔记再合成；整理完按「没被引用的页」查漏（`review_chapter`）。用户在设置里关联了知识库的整理规范（`book_rules_file`）时，整份规范交给 AI，以它为准。
- PDF 旁边同名的 .docx（WPS OCR 导出）自动当正文参考（`companion_docx` / `companion_slice`：按章名在 Word 全文里找这一章，跳过目录）；媒体库不单独列这份 Word。
- 每章整理后三步：`review_chapter`（没被引用的页查漏）→ `verify_doubts`（把「需要人工确认」里提到的页截图给 AI 核对，能确认的改正并记进「已确认的 OCR 修正」）。
- 笔记文件名用 `chapter_files`：前言、目录不编号，正文章节从 01 开始。
- 原书图示：AI 写 `![[原书图示 第N页 图号 名称 @左,上,右,下]]`，剪藏时 `book.place_figures` 按比例裁剪、缩到 960 宽压缩。非必要不用，每章最多 2 张。
- 书籍界面在 `#bookMain`，`S.mode === 'book'`；视频相关按钮加了 `video-only` 类，在书籍界面隐藏。

## 没有进仓库的东西（只在用户电脑的 `D:\mengren-learning` 里）

- `design/`：图标设计稿、原图、字体、其他配色方案。正式图标是「紫晶」（1.7.12 起，`design/recolor_full.py` 的 full-4：字、龙纹、边框、背景整体换成紫色，和金色的猛人截图教鞭、红宝石色的猛人快传区分开），在 `app/assets/` 和 `app/web/icon.png`。之前的「帝王紫」只换了背景、字还是金色，和另外两个软件放在一起分不清。
- `.venv/`、`dist/`、`dist-next/`、`build/`、`release/`。

## 还没在真实使用中验证的

- Claude、Gemini、豆包大模型：用户还没有 API Key，代码按官方文档写的，没实测过。
- 英文视频的完整流程（Whisper 识别 + 翻译）。
- 书籍整理：用户还没用真书测过（软件和命令行都是）。
- （已实测）2026-10-14 用命令行整理过「市场观念」几节真课：豆包极速版识别 + ChatGPT 账号做校对、看图、课程重点、查漏、新术语，都正常。
