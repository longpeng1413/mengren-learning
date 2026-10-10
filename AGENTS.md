# 维护说明（给 AI 编程助手和接手的开发者）

先读 README.md 了解软件做什么。这份文件记的是**代码里看不出来、但改错了会出事**的约定。

用户是这个软件唯一的使用者兼所有者，不是程序员：和他沟通用中文、讲结果和影响，不讲术语；动手前说清楚要改什么，改完说清楚测了什么。

## 绝对不要做的事

- **不要强行结束用户正在运行的「猛人学习.exe」**（不要一上来就 taskkill / Stop-Process）。他可能正在看课、改字幕，强关会丢未保存的修改。用户已允许升级时关闭它，但要**正常关闭**（等同点右上角 ×，程序会先保存），见下面「打包和更新」。
- **不要改回本机 HTTP 服务器**。界面必须用 `index.as_uri()`（`file://`）加载，通过 pywebview 的 `js_api` 和后台通信。用户电脑上的 360 主动防御和 Clash Verge 会干扰 127.0.0.1 回环连接（传输卡在约 64KB 或被重置），以前因此出过「图标只显示一半」「界面卡住」。注意：给 `create_window` 传普通路径时 pywebview 会**悄悄**起一个本地服务器，所以一定要传 `file://` URI。
- **不要在关闭事件里同步调用 `window.evaluate_js`**。`closing` 事件在界面线程里执行，同步等界面会死锁（以前出过「猛人字幕.exe 未响应」，软件那时还叫猛人字幕）。现在的做法见 `app/main.py` 的 `on_closing`：先返回 False 取消关闭 → 后台线程通知界面 `window.prepareClose()` 保存 → 等 `api._quit`（最多 4 秒）→ `window.destroy()` → `os._exit` 兜底。
- **不要把 API 密钥写进日志、报错信息或仓库**。密钥字段见 `config.SECRETS`，发给界面前用 `config.public()` 打码。用户的设置在 `%APPDATA%\猛人学习\`，不在项目里。

### 改名（1.6）

软件原名「猛人字幕」，1.6 起叫「猛人学习」。兼容要一直保留：`config._migrate_data_dir()` 把 `%APPDATA%\猛人字幕` 搬到 `%APPDATA%\猛人学习`；`doc.load` 还认旧的 `.猛人字幕.json`，保存时改成 `.猛人学习.json`；设置导入认两个名字；`find_note` 认两个标签。项目文件夹 `E:\Claude-猛人字幕` 没改名。
- **不要动 `E:\Claude-乔木剪藏`**，那是另一个项目。

## 必须保持的行为

### 字幕文件兼容

字幕存在视频旁边的 `视频名.mp4.猛人学习.json`（写不进去时放 `%APPDATA%\猛人学习\library\`），见 `app/core/doc.py`。用户已经有大量做好的字幕：

- 新增字段可以，但读取时要给旧文件缺的字段兜底；
- 不要改名或删除已有字段（`segments`、`summary`、`chat`、`versions`、`teacher`、`reviewed`、`corrected`、`clip`、`pending_task`、`doubts` 等）；
- `frames` 是关键帧和画面记录：`[{t: 秒, why: 为什么挑这张, note: 画面记录, caption: 画面上的字幕, figure: 可插图, manual: 手动截取}]`。截图本身不存，放在缓存 `frames/` 里，需要时按时间重新截（`frames.grab`）；
- `doubts` 是 AI 校对标出的存疑句：`[{t: 句子开始时间, reason: 原因, text: 原句}]`，按开始时间对应到字幕（`segments` 每段只存 4 个字段，`clean_segments` 会去掉别的字段，所以不能把标记放进段里）；
- 每段的格式是 `{start, end, text, tr}`，秒为单位。

### 术语表语法（`app/core/llm.py` 的 `parse_terms`）

```
术语1, 术语2          → 专用术语：传给识别服务当热词
错写 => 正确          → 只是提示 AI 结合上下文判断，不直接替换
错写 =>! 正确         → 识别完直接替换（apply_fixes 只处理这一种）
```

不要把 `=>` 改成直接替换：用户的术语表里有「分 => 峰」这类单字对照，直接替换会把「部分」改成「部峰」。老师档案的「术语说明」（`notes`）只给 AI 参考，最多取 20000 字，不传给识别服务。

老师档案可以关联 Obsidian 里的术语表文件（`terms_file`）。用户的术语表以 Obsidian 里那份为准，软件不另存副本：`config.get_teacher()` 每次读文件，用 `llm.terms_from_markdown()` 抽出术语和识别错误对照，加在手写术语后面，整份文件当术语说明。界面上的老师列表（`load_teachers()`）是没合并过的原始档案。

### 课程重点（`llm.DEFAULT_TEMPLATE`）

用户的明确要求：**不设固定模板**，按老师这节课实际的知识体系和逻辑框架组织，可以调整顺序、合并重复，但**只写老师讲过的**，不让 AI 猜测、补充或评价；定义、口诀、数字照录，每条标时间戳，拿不准的标「存疑」。老师自述和课程安排要保留并注明。改提示词时不要违背这条。

用户 2026-10 明确决定：剪藏笔记的主体**保持「课程重点」**这种写法，不改成知识库规范里按时间顺序的「来源级整理」。独立来源审计也不做进软件（用户在单独的聊天窗口里做）。

生成课程重点后默认自动「查漏补缺」（`llm.review_summary`）：AI 只返回要补的条目和放在哪个小标题下，由程序插进去，不让 AI 重写整篇。

### AI 校对

只修正识别错误，不改写句子、不润色。

### 重新识别

重新识别、清空、恢复之前会把旧字幕存成历史版本（最多 5 份），不要直接覆盖。

### 剪藏到 Obsidian（`doc.to_markdown` / `doc.clip`）

笔记结构是：属性（frontmatter）→ `## 课程重点` → `## 完整字幕`（`[mm:ss] 原文`，下一行是译文）。用户用这份笔记做知识「蒸馏」，结构要稳定。同一节课再剪藏是更新原笔记，不是新建。

## 架构速览

- `app/main.py`：入口、版本号 `VERSION`、开窗口、关闭流程、卡死监视（界面 20 秒无响应时把各线程堆栈写进日志）、自测入口。
- `app/bridge.py`：界面调用后台的唯一入口 `Api.call(name, body)` → 分派到 `do_<name>`。出错会写日志并返回 `{'ok': False, 'error': ...}`。新增功能就加一个 `do_xxx`，前端用 `api('xxx', {...})` 调。
- 耗时任务（识别、翻译、校对、AI 回答）用 `app/core/jobs.py` 在后台线程跑，界面用 `api('job', {id, since})` 轮询进度；AI 的流式输出也作为 job 的条目推给界面。
- `app/core/`：
  - `asr.py`：识别。豆包有多种模型（`DOUBAO_MODELS`）：极速版 base64 直传；流式版走 WebSocket 二进制协议；标准版和闲时版要先上传到火山 TOS 拿预签名链接。
  - `llm.py`：AI 调用，支持 OpenAI 兼容接口、ChatGPT 账号（codex）、Claude（官方 anthropic SDK）、Gemini（REST）。
  - `codex_auth.py`：ChatGPT 账号登录，借用 Codex 命令行工具的 OAuth 方式。端口 1455 被占用时用「粘贴回调网址」兜底。
  - `media.py`：ffmpeg 相关，包括探测、能否直接播放、转码、抽 16k 单声道音频、按静音切段、检查视频结尾是否损坏。
  - 插图原则（用户明确要求）：**非必要不插图**，知识库要小、便于以后 AI 蒸馏。课程重点最多 3 张，只插文字确实说不清的；剪藏时图片缩到 960 宽、压缩（`doc._small_copy`）。
  - `frames.py`：关键帧。候选按 15 秒一段挑「最像背景」的小图（避开老师走动挡屏），加上老师说「请看」的时间；能看图的 AI 先筛选、再写画面记录。课程重点里写 `![[画面 mm:ss]]`，剪藏时 `doc.place_figures` 换成截图并复制到笔记旁边的 `image/`。只用豆包识别的字幕（`.猛人学习.json`），不读用户以前本地模型生成的 `.srt`。
  - AI 消息内容可以是图文混排 `[{'type': 'text'|'image', …}]`，`llm.stream` 按各家格式转换（`_to_openai` / `_to_codex` / `_to_claude` / `_to_gemini`）。
  - `config.py`：设置、老师、媒体库、播放进度和存放位置。缓存和日志默认放软件目录下的「缓存（可随时删除）」「日志（可随时删除）」，可以自定义（`storage_dir`，重启生效），软件目录不可写时退回 LOCALAPPDATA。
  - `cache.py`：清理。音频保留 3 天，转换后的视频最多 5GB。
  - `log.py`：日志最多 2MB × 4 个文件。打包后 stdout/stderr 是 None，已重定向到日志。
  - `doc.py`、`library.py`、`jobs.py`。
- `app/web/`：纯 HTML/CSS/JS，没有构建步骤，没有框架。

## 开发、测试

Windows 上：

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app\main.py                    rem 运行（加 --debug 打开开发者工具）
.venv\Scripts\python tests\test_backend.py          rem 后台测试，不联网
.venv\Scripts\python tests\run_ui_test.py           rem 界面测试：弹出真实窗口自动操作，最后连关闭流程一起测
.venv\Scripts\python tests\run_ui_test.py --exe     rem 同上，测 dist-next 里打包好的 exe
```

- 改完至少跑后台测试和界面测试，全部通过才算完成；改了界面就在 `tests/ui_selftest.js` 里补检查，改了后台就在 `tests/test_backend.py` 里补。
- 界面测试要弹出真实窗口，**在沙盒里通常跑不了**，需要无沙盒权限。测试数据放在项目里的 `.testdata/`（已忽略），不会碰用户的真实设置。
- 测试视频由 `tests/make_test_media.py` 用 ffmpeg 自动生成，不进仓库。
- 需要 ffmpeg / ffprobe 在 PATH 里（`winget install Gyan.FFmpeg`）。
- 用 Python 脚本或编辑工具改文件。不要用 shell heredoc 写含中文、反斜杠的代码，以前出过转义和换行符损坏。`.bat` 文件必须是 CRLF 换行。

## 打包和更新用户本机的程序

用户从桌面快捷方式打开 `dist\猛人学习\猛人学习.exe`。

1. `.venv\Scripts\python build.py` → 输出到 `dist-next\`（不碰正在用的 `dist\`）。
2. `.venv\Scripts\python tests\run_ui_test.py --exe` 测打包版。
3. 换进 `dist`：用户已允许升级时关闭正在运行的程序。先正常关闭（PowerShell：`Get-Process 猛人学习 | ForEach-Object { $_.CloseMainWindow() }`，等它自己保存退出，最多等 15 秒左右，还不退出才 `Stop-Process`），然后执行「更新到新版.bat」里同样的步骤（把 `dist\猛人字幕` 里的「缓存（可随时删除）」「日志（可随时删除）」移进 `dist-next\猛人字幕`，删掉 `dist`，把 `dist-next` 改名为 `dist`），最后启动 `dist\猛人字幕\猛人字幕.exe` 并告诉用户。用户也可以自己双击「更新到新版.bat」。

## 发新版本

1. 改 `app/main.py` 里的 `VERSION`。
2. 测试、打包（见上）。
3. 提交并推送到 `main`（仓库：https://github.com/longpeng1413/mengren-subtitles）。
4. 把 `dist-next\猛人学习` 文件夹打成 zip，用 `gh release create v版本号` 发布。**附件文件名必须是英文**，例如 `mengren-subtitles-1.4.0-windows.zip`，否则 GitHub 会把中文吞掉。中文名写在附件的 label 里。发布说明里提醒：要先装 ffmpeg；程序没有数字签名，可能会被安全软件提示。
5. README 的功能说明有变化时一起更新。

推送、发布这类对外操作，先问用户。

## 书籍和文档（1.6）

- `app/core/book.py`：PDF 用 pypdfium2 读文字层、认图片 / 矢量图、渲染页面（需要 Pillow）；Word 直接解析 docx 的 XML；TXT / MD 按标题切。书上页码 = PDF 页序 − `shift`，自动从页面上下单独的数字认，用户可以改。
- 书籍的整理结果也存在旁边的 `.猛人学习.json`，`kind: 'book'`，字段：`title, author, shift, chapters[{title, start, end, md}], overview, clip`，`segments` 留空列表（兼容）。
- 整理一章时一次最多 18 页 / 36000 字 / 10 张页面图，超了先分段写笔记再合成；整理完按「没被引用的页」查漏（`review_chapter`）。用户在设置里关联了知识库的整理规范（`book_rules_file`）时，整份规范交给 AI，以它为准。
- PDF 旁边同名的 .docx（WPS OCR 导出）自动当正文参考（`companion_docx` / `companion_slice`：按章名在 Word 全文里找这一章，跳过目录）；媒体库不单独列这份 Word。
- 每章整理后三步：`review_chapter`（没被引用的页查漏）→ `verify_doubts`（把「需要人工确认」里提到的页截图给 AI 核对，能确认的改正并记进「已确认的 OCR 修正」）。
- 笔记文件名用 `chapter_files`：前言、目录不编号，正文章节从 01 开始。
- 原书图示：AI 写 `![[原书图示 第N页 图号 名称 @左,上,右,下]]`，剪藏时 `book.place_figures` 按比例裁剪、缩到 960 宽压缩。非必要不用，每章最多 2 张。
- 书籍界面在 `#bookMain`，`S.mode === 'book'`；视频相关按钮加了 `video-only` 类，在书籍界面隐藏。

## 没有进仓库的东西（只在用户电脑的 `E:\Claude-猛人字幕` 里）

- `design/`：图标设计稿、原图、字体、其他配色方案。正式图标是「帝王紫」，在 `app/assets/` 和 `app/web/icon.png`。
- `.venv/`、`dist/`、`dist-next/`、`build/`、`release/`。

## 还没在真实使用中验证的

- Claude、Gemini、豆包大模型：用户还没有 API Key，代码按官方文档写的，没实测过。
- ChatGPT 账号登录：已登录，但具体功能用户还在试。
- 英文视频的完整流程（Whisper 识别 + 翻译）。
