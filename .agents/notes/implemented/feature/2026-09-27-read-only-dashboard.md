# Agent Note: 只读 dashboard（剪辑台）

Status: implemented

## Problem

总体方案 [[2026-09-27-resource-template-library-and-dashboard]] 第 4 期：项目、运行、QC 与资源库只能靠
`recap_inspect.py`、`library.py check` 和手工翻目录查看。一次 cut 运行要看清「剪了哪几段、每段多长、旁白压在哪里、
成片四轨怎么叠、QC 卡在哪、用了哪些资源、授权确认没有」，需要打开五六个 JSON 并在脑子里对时间轴。
兄弟项目（drama-skills 的短剧创作台 v2、oh-story 的写作台）已经有本机看板和统一的 ZenStory 视觉语言，这里没有。

约束：技能自包含、只用 Python 标准库；CI 覆盖 macOS / Ubuntu / Windows；不新增 CONFIG 键与环境变量；
每个模块不超过 800 行；看板不能成为第二个事实来源。

## Decision

**入口与模块**（全部在 `skills/video-recap/` 内，不 import 其他技能）

- `scripts/dashboard_server.py --root <目录> [--port 0] [--host 127.0.0.1] [--open]`：标准库 `ThreadingHTTPServer`，前台运行、打印地址，
  Agent 放到后台跑。`--host` 只接受 IPv4 回环（`localhost` 映射为 127.0.0.1），其他地址直接退出并给中文说明。
- `scripts/dashboard_io.py`：唯一的路径闸门 `resolve_under`、按上限读 JSON 的 `read_json`（2 MB，永不抛异常）、媒体白名单、标记文件发现。
- `scripts/dashboard_runs.py`：一个 work_dir 的各阶段视图，服务端解析；运行状态复用 `recap_inspect.cmd_state`。
- `scripts/dashboard_templates.py`：模板参数的中文行与示意几何（字幕带与最长一行、包装图层与安全区）。
- `scripts/dashboard_data.py`：总览、资源库（复用 `library.scan_library`）、项目绑定解析（复用 `project_binding.resolve_project`）、搜索。
- `assets/dashboard/{index.html,tokens.css,styles.css,app.js,views.js}`：原生 HTML/CSS/JS，无构建、无外部 CDN。
  import 关系 `server → data → {runs, templates} → io`，无环。

**严格只读**

- 只有 GET / HEAD；其余任何方法（含 `http.server` 本来回 501 的未知方法）一律 405 并带 `Allow: GET, HEAD`。
  没有任何接口写文件，也不写会话文件（方案里的 `--detach / --status / --stop` 与 `<root>/.video-recap/` 会话文件因此不做）。
- Host 必须是 `127.0.0.1:<端口>` 或 `localhost:<端口>`，带 Origin 时必须同源，`Sec-Fetch-Site` 为 `cross-site` / `same-site` 时拒绝，
  否则 403（防 DNS rebinding 与跨站 `<img>` / `<video>` 探测）。
- 请求路径一律是 `--root` 相对路径：拒绝绝对路径、Windows 盘符、`..`；`Path.resolve()` 后必须仍在 root 内，
  指向 root 外的符号链接因此被拒。媒体只放行 mp4 / mov / webm / m4a / wav / mp3 / png / jpg / jpeg / webp，单文件上限 4 GB，
  支持单段 HTTP Range（206 / 416），视频可拖动。
- 读取任何数据文件（run 产物、资源库记录、项目文件）前先 `lstat`：符号链接、FIFO / 设备等非普通文件、超过 2 MB 的文件一律不读，
  该视图显示原因；运行状态交给 `recap_inspect` 之前先检查 run 目录顶层每个 JSON。手写记录里的 `canvas`、`tags`、`demonstrates`
  在服务端规范为正整数或字符串数组后才进页面，前端所有数值经 `num()`、文本经 `esc()` 插入，视图构建出错时显示错误面板。
- 响应头：`Content-Security-Policy: default-src 'self'; media-src 'self'; img-src 'self' data:; …`、`X-Content-Type-Options: nosniff`、
  `Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`Cross-Origin-Resource-Policy: same-origin`。CSP 不允许内联脚本和内联样式，数据驱动的宽度与播放头经 CSSOM 写入。

**发现**：从 `--root` 广度优先，深度 ≤ 8、目录 ≤ 5000，跳过隐藏目录、`node_modules`、`__pycache__` 与符号链接目录；
`library.json` 所在目录不再下探（交给 `scan_library`）。超限只给一条中文警告。嵌套运行（多源的 `sources/<id>/`）挂到最近的上级运行，
运行挂到最近的（含自身）项目目录。

**严重度**（服务端给出，前端只按它上色）：`danger` 只给必须改的——QC 阻断、剪辑计划阻断、资源库错误、不会生效的项目绑定
（找不到、未采用、类型不符、缺版本、没有资源库）；`warn` 给建议项——授权或声音授权未确认、`resource_lock.json` 的注意项、
无法解析的文件、配音 partial；`todo` 给等 Agent 写的产物（信号色）；`ok` 为已完成。总览「下一步」卡片取前三条里最重的一级：
只有建议项时是 warn 色，不是红色。阶段栏圆点同一套：QC 有阻断为 danger，资源有注意项为 warn。

**视图**（URL hash 路由，可收藏、可后退；视图分发用 `switch`）

- `#/` 总览：四个数字、「下一步」最多三条（QC 阻断 → 等 Agent 写的产物 → 授权与声音授权 → 库错误 → 绑定问题 → 无法解析的文件），
  每条带「复制给助手」与「打开」；下面是项目与运行（阶段圆点）、资源库卡片、完整的「需要注意」。
- `#/run/<rel>[/<view>]`：阶段栏 概况 · 理解 · 剪辑 · 旁白 · 成片 · QC · 资源，每格一个状态点加一个数字；
  full 模式没有「剪辑」，非 narration 声音模式没有「旁白」。剪辑 = `clip_plan_validated.json` 的节奏条（宽度 ∝ 时长）与片段表；
  旁白 = `narration.json` 按输出时间排列；成片 = 绑定 `assembly_manifest.json` `final_output` 的播放器，加 `timeline.json` 的
  画面 / 旁白 / 背景音乐 / 字幕四轨，播放头随 `currentTime` 移动、点轨道可跳转；QC = `final_qc` / `golden_eval` / `assembly_qc` / `mimo_qc`
  的中文结论与前 20 条发现；资源 = `resource_lock.json`，需要注意的条目排最前，角色显示为中文（原片 / 音色 / 背景音乐 /
  字幕字体 / 包装图层），文件名突出、目录灰色截断并在悬停时显示全路径。每条资源归到一处登记：`library`（资源库，附授权与声音授权）、
  `material`（原片按设计只在素材库，不算未登记）、`unregistered`（配了资源库却没登记）或 `none`。
- `#/library/<rel>[/resources|templates|samples]`：资源按类型分组（授权、声音授权、音色、来源、被哪些项目绑定）；样片显示示范项与
  不能照搬的内容；图片、音频、样片视频集中放在文字下方。模板显示 `id@vN`、状态、画布、采用记录、样片链接，参数是一张表：
  中文名（字体、字号、每行字数、描边、阴影、主色、描边色、最多行数、字幕带；包装为图层数与安全区）、带单位的值、ASS 颜色的色块，
  以及来源徽标（实测 / 调出 / 指定 / 未知）；完整参数收在可展开的「原始 JSON」里。每个模板有一块按画布等比缩小的示意画布，明确标注
  「示意图，不是渲染」：`subtitle_style` 画出字幕带和一行恰好 `max_chars` 个字、按比例缩放字号、底对齐且底边距 = 画布高 − `band.y_bot`，
  并给出一行宽度与可用宽度（两侧各 40px）的对比；`packaging` 按 rect 摆放每层图片（只在图片位于 `--root` 内时经 `/api/media` 提供）
  并画出安全区，下面列出图层名、位置和图片资源。示意几何全部以画布百分比由服务端算出，前端经 CSSOM 写入。
- `#/project/<rel>`：每个绑定解析到什么（模板 `id@vN` 与状态、资源标题与授权），不会生效的绑定整行标成 danger；另有「运行时下发的设置」：
  用 `project_binding.resolve_project` 以空环境变量和默认参数（`tts_provider=auto`，其余为空）解析，列出会下发的 `SUBTITLE_*` /
  `BGM_PATH` 与参数改动，解析失败（`BindingError`）时显示它的原话。本机环境变量与命令行参数不影响这一栏。
  库在 `--root` 之外时仍读取它来解析绑定，但不提供浏览与预览。
- ⌘K 或 `/` 打开搜索，`GET /api/search?q=` 在服务端匹配旁白、剪辑理由、资源 / 模板 / 样片的 id 与标题。

**服务端解析，逐视图降级**：前端不解析任何产物。某个产物读不出结构时，只有那个视图退回「一句说明 + 原文（前 20000 字）」，
其余视图照常；运行清单坏了时阶段状态按文件是否存在显示。`recap_project.json` 与 `resource_lock.json` 的格式归第 3、2 期
（[[2026-09-27-project-binding-and-font-files]]、[[2026-09-27-resource-lock]]），这里宽松读取、缺字段不报错。

**媒体只认绑定**：成片只取 `assembly_manifest.json` 的 `final_output`，剪后母版只取 cut 阶段固定产物 `edited_source.mp4`，
资源与样片只取记录里列出的文件；不按 `recap_*.mp4` 之类的文件名猜。

**动作**：凡是需要生产的地方只有「复制给助手」，复制一句中文请求贴回对话；看板本身不发起任何生成、剪辑或合成。

**视觉**：沿用 ZenStory 设计语言。`tokens.css` 与 drama-skills / oh-story 的同名文件逐字节相同（sha1 `ed9dd31d9e6da2589516ef49e472e59839e0a873`），
用独立 `<link>` 先于 `styles.css` 加载；`styles.css` 的颜色、字体、圆角、阴影只用 `var(--zs-…)`。字标是共用的 `.zs-mark` 加单字「片」，
旁边写 VIDEO RECAP 与「剪辑台」。标题与旁白正文用宋体。明暗默认跟随系统，顶栏按钮切换后把 `light` / `dark` 存进
`localStorage` 键 `video_recap_dashboard_theme`（读写失败只在本页生效），在 `app.js` 最开头应用。1440×900 与 390×844 无横向滚动。

## Alternatives considered

- **单页、无页签、左侧导航加右侧详情**（本期最初的规格）
  - 最强理由：一种阅读模式，实现最小，不需要路由。
  - 不用：兄弟项目 v2 已经推翻单页（drama-skills 的 dashboard-v2-stage-views 笔记），原型证明按阶段分视图读得更快；一次运行的
    剪辑、旁白、成片、QC、资源堆在一列里，节奏条、四轨时间线这类结构只能靠滚动找。改为 hash 路由的阶段视图，与兄弟项目一致。
- **像兄弟项目一样允许在看板里编辑（改绑定、标授权、采用模板）**
  - 最强理由：少切回对话；drama / story 已有带版本号的保存与冲突保护，可以照搬。
  - 不用：这里的可编辑对象是资源库记录与运行产物，采用与授权必须留下用户原话和范围，由 Agent 在对话里确认后写；
    写接口需要令牌、原子写、冲突检测，还会让看板成为第二个事实来源。只读也让安全面只剩 GET。
- **Node / Vite 前端**
  - 最强理由：组件化、类型检查、热更新，写复杂交互更快。
  - 不用：技能要求只依赖 Python 标准库、可单独安装；构建产物要进仓库或要求用户装 Node。原生 JS 拆成 `app.js`（外壳）与
    `views.js`（视图）两个文件已足够。
- **访问令牌**
  - 最强理由：同机其他进程或网页即便知道端口也读不到数据。
  - 不用：只读、只绑回环、校验 Host / Origin 已挡住浏览器跨站与 DNS rebinding；令牌要在 URL 或会话里传递，还要落盘或打印，
    对只读看板收益小。若以后加写接口必须重新考虑。
- **前端解析产物**
  - 最强理由：不用给每种产物加服务端代码。
  - 不用：JS 与 `recap_inspect` / `library` 会各有一份解析；坏文件的降级也要在前端再写一遍。服务端复用现有函数，接口仍只是 GET。
- **按文件名找成片（`recap_*.mp4`）**
  - 最强理由：`assembly_manifest.json` 缺失或旧版时也能看到视频。
  - 不用：文件名不是契约，旧成片、别名、中间代理都可能匹配，挂上去会被当成本轮交付；只认 manifest 绑定。

## Consequences

- **收益**：打开即知哪次运行在等 Agent、哪次有 QC 阻断、哪些资源授权没确认；剪辑节奏、旁白落点、成片四轨、资源记录各有专门视图；
  坏文件只影响自己的卡片；与兄弟项目共用一套视觉和 `tokens.css`。
- **代价**：新增约 1300 行 Python（五个模块）与约 1150 行前端（不含共用 `tokens.css`），42 个测试；每次请求都重新扫描与解析，
  运行很多时总览会变慢；`tokens.css` 由三个仓库共用，改动要同步并更新测试里的 sha1；宋体依赖系统字体。
- **已知上限**：只支持单段 Range；库在 `--root` 外时只解析绑定不预览；字幕示意用界面字体而不是模板字体，只核对位置与宽度，
  不代表真实字形；深色主题下深色包装图层在棋盘格上对比偏弱。

## Verification

`PYTHON=<py3.12> scripts/test.sh`：orchestrator 412 passed（`tests/orchestrator/test_dashboard.py` 42 个，含严重度分类、
模板参数行与示意几何、ASS 颜色换算、绑定解析目标、运行时下发设置不受本机环境变量影响、资源登记分类），其余组只有既有的
cut 1 + assemble 6 个 ffmpeg 环境失败；`ruff check skills tests` 无告警。变异验证：去掉 Host 校验、去掉 `resolve_under` 的越界检查、
去掉 405 分发，对应用例分别失败；往 `styles.css` 加一个字体栈或裸色值，令牌用例失败。手工：对临时根目录启动服务，
`curl /api/overview` 返回 1 库 / 1 项目 / 4 次运行与三条下一步，POST 得 405，错误 Host 得 403，Range 得 206；
浏览器 1440×900 浅色与 390×844 深色逐页（16 个路由）检查无横向滚动并截图目检，播放头随播放移动，⌘K 搜索可跳到旁白段落，主题切换刷新后保留。
