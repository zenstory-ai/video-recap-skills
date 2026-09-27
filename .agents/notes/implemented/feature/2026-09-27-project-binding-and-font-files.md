# Agent Note: 资源库第 3 期——项目绑定与字体文件

Status: implemented

## Problem

第 1、2 期（[[2026-09-27-resource-library-format]]、[[2026-09-27-resource-lock]]）让资源与模板可以登记、让每次运行留下资源记录，
但运行本身还是靠一堆 `SUBTITLE_*`、`BGM_PATH`、音色参数拼出来的：同一个系列换一台机器、换一个会话，就要重新抄一遍环境变量；
字幕字体只能写系统字体名，渲染机上没装就悄悄换成别的字体。总体方案见 [[2026-09-27-resource-template-library-and-dashboard]]。

## Decision

- 新增 `recap_project.json`（`video-recap.project.v1`）：`library`（相对项目文件）与 `bindings`（`subtitle_style` / `packaging` 模板写 `id@vN`，
  `voice` / `bgm` 写资源 id）。`recap.py --project <文件或目录>` 在任何阶段开始前由 `scripts/project_binding.py` 解析。
- 解析只产出各阶段已有的设置：字幕样式 → `SUBTITLE_PLAY_RES_X/Y`（模板画布）、`SUBTITLE_FONT_SIZE`、`SUBTITLE_OUTLINE`、`SUBTITLE_SHADOW`、颜色、
  `SUBTITLE_MAX_CHARS/LINES`、`band` → 底对齐 + `SUBTITLE_MARGIN_V`、字体 → `SUBTITLE_FONT_NAME`（+ `SUBTITLE_FONT_FILE`）；音色 → provider 与
  `--mimo-tts-voice` / `--voice-ref` / `FISH_TTS_REFERENCE_ID` / `INDEX_TTS_VOICE`；BGM → `BGM_PATH`。写进 recap 进程的环境与参数，子进程照常继承；
  阶段技能不认识资源库。
- 只有 `adopted` 模板能绑定；参考音频声音授权为 `denied` 拒绝绑定；显式参数或环境变量与绑定不一致时停止并点名；
  合成前（`_deliver` 开头，TTS 之前）核对模板画布与实际成片画布，不一致即停止。dub 与本地采用三件套不接受 `--project`。
- 续跑命令写 `--project`，由绑定得到的值（`_bound_from_project`）不再重复写出。
- video-assemble 新增 `SUBTITLE_FONT_FILE`：ASS 烧录的 `subtitles` 滤镜加 `fontsdir=<字体所在目录>`，画面文字 `drawtext` 加 `fontfile=`；
  family 名仍由 `SUBTITLE_FONT_NAME` 给出。`assembly_manifest` 的 `subtitle_style.font_file` 记录它，`resource_lock` 据此对上 `font` 资源。
- `library.py check` 增加：字幕样式引用的字体资源必须有 `font.family`；字幕样式出现不认识的参数即报错；字幕样式必须给出 `max_chars`，
  且 `size_px × max_chars` 不得超过画布宽减去两侧默认边距——真实端到端运行里，只给字号的模板让 18 字一行宽到 936px，被合成阶段的视觉 QC 拦下，这类问题应在登记时发现。
- `--project` 在解析前转成绝对路径，续跑命令从任何目录都能用。
- 示例项目 `examples/demo-project/recap_project.json` 绑定合成示例库。
- 被绑定的资源、以及模板引用的资源，只要 `check` 对其记录报了错误就拒绝绑定（缺文件、Fish / index-tts 缺 `voice_id`、未知 provider 等），
  不会在花钱的阶段之后才崩溃或悄悄换成默认音色；数值型字幕参数（字号、每行字数、行数、字幕带）必须是整数；
  已设置的 `VIDEO_RECAP_MATERIAL_LIBRARY_DIR` 与项目库不同即停止；`adopted-packet-copy` 不接受 BGM 绑定；
  环境变量冲突按数值、按解析后的路径比较，等价写法不算冲突。
- `scan_library` 对任何结构异常的记录只报 `malformed` 等错误、从不抛异常；`resource_lock` 写入失败只打印警告，不让已完成的渲染失败。

## Alternatives considered

- **阶段技能直接接受 `--template` / 读取资源库** — 最强理由：单独调用 video-assemble 也能套模板。否：模板解析代码要复制进多个技能，
  或跨技能 import；编排器解析、以已有设置下发，阶段技能只认具体值。
- **显式设置优先，静默覆盖项目绑定** — 最强理由：临时试一个字号不用改项目文件。否：成片会与项目记录不一致而无人察觉；
  冲突时停止并点名，想临时改就去掉 `--project` 或改绑定。
- **从 TTF 的 name 表读出 family 名，省掉 `font.family`** — 最强理由：少填一个字段、不会填错。否：标准库没有字体解析，
  自己解析 name 表要处理多平台编码与 TTC 集合；在资源记录里写一次 family，由 `check` 保证它存在。
- **把 `band` 映射为 `--subtitle-y-top/--subtitle-y-bot`** — 最强理由：已有的实测字幕带参数语义最接近。否：那组参数会默认开启原字幕遮罩，
  是"贴合原片硬字幕"的功能；模板的 band 只是本片字幕的位置，映射为底对齐边距即可。

## Consequences

- **收益**：一个项目文件就能复现同一系列的字幕外观、音色与 BGM；字体随资源库走，不依赖渲染机装了什么；
  绑定、显式设置与画布不一致都在花钱的阶段之前暴露。
- **代价**：config-playbook 里"没有工具读取配置文件"不再成立（已改写）；recap 进程会改写自己的环境变量；
  `SUBTITLE_FONT_NAME` 必须与字体文件内的 family 名一致，写错时 libass 会回退到其他字体——`check` 只能保证字段存在，不能核对内容。
  `packaging` 绑定本期只校验与记录，合成在第 5 期接入。

## Verification

真实端到端（MiMo ASR / VLM / 评审 / TTS + 本机 ffmpeg，900x1600 合成测试片，绑定 `examples/demo-project`）：理解后暂停、续跑完成合成与剪映导出；
`resource_lock.json` 记录模板 `clean-white@v1`、音色 `narrator-demo`（授权 unknown，结束时打印提示）与 BGM `pulse-demo`；抽帧确认字幕落在模板字幕带内。

`tests/orchestrator/test_project_binding.py`（14 个）覆盖解析映射、字体资源、八种拒绝情形、画布核对、完整 full 流程下发的参数与环境、
续跑命令与示例项目；`tests/orchestrator/test_resource_library.py` 新增字体 family 与未知参数两例；
`tests/assemble/test_pure_assemble.py` 覆盖 `fontsdir` / `fontfile` 的滤镜文本（含冒号与空格路径）。
