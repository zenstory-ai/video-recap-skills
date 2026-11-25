# Agent Note: 清掉 v0.7 残留：死代码、失真文档与无人引用的示例

Status: implemented

## Problem

v0.8.0（[[2026-11-21-atomic-primitives-and-timeline-bus]]）重写了全部脚本，但只读审计发现仓库里还留着三类与现状不符的东西：

1. **死代码与只为测试存在的接口**：`export_jianying.py` 复制了一份闪避计算，外面包着 `try: import _audio`，而 `_audio` 永远可导入，这份副本从不执行；真正的调用又没传 `base_dir`，相对路径的旁白文件按当前工作目录解析。`_common.color_tag_filter`、`sanitize_error` 别名、`write_srt(max_chars=…)`、`frames.py` 恒为假的 `labeled_ok`、导出器的 `fps` / `kind` / `est_total` 字段都没有生产调用方。
2. **文档写了代码不做的事**：`mix.flash_guard`、`mix.voice_over_bgm_db`、`meta.assets[]` 没有任何代码读取；`subtitle_style.font_file` 只被素材库审计读到，烧录字幕不用；剪映文档说会换算裁切与焦点、保留淡入淡出、按 MD5 去重、字幕语义折行，实际都不做；`tts.py` 只发音色 ID，文档却说会附参考音频；`--preview` 写成 480p；包装文档的 callout 槽位名、默认动效 `zoom_in`、SRT 去标点默认值都与代码不一致；配套工具数漏了 `plan.py`；七个被读取的环境变量没写进文档。
3. **v0.7 遗物**：`examples/demo-project/`、`examples/resource-library/templates|samples/`、`examples/guohuo-60s/` 的 v2 规划产物与 Remotion 工程、`tools/measure_subtitle.py` 都只服务已删除的旧流水线；约 120 篇 implemented 笔记描述已删除的代码却没有指向；`library.py cache-restore` 只保护旧名 `PLAN.md` / `notes.md`，会用缓存覆盖工作区里更新过的 `MEDIA_NOTES.md`。

## Decision

- **删**：上述死代码与对应测试（`test_mimo_client_safety.py` 与 `test_common_sanitize_api_error_redacts_secrets` 重复；`test_guohuo_example.py`、`test_measure_subtitle.py` 随文件删除；`tests/reference/conftest.py` 的 v0.7 夹具、退役 skill 名与 `scripts/<子包>/` 断言、`ENV_READERS` 里不存在的 helper 名）。`export_jianying.py` 直接 `from _audio import ducking_keyframes` 并传 `base_dir`。`__all__` 只留别的模块或测试真正导入的名字。示例目录只保留 `resource-library` 的 catalog / presets / resources 与 `guohuo-60s/README.md`（改写为 v0.7 归档说明，保留成片链接）。
- **接线（小改）**：`render.py` 在 `subtitle_style.font_file` 存在时给 `ass` 滤镜加 `:fontsdir='<字体目录>'`（同 `_esc_path` 转义），`font_name` 须与字体文件的字体族名一致；`--out-srt` 默认去行尾标点；callout 默认动效改为 `none`；`cache-restore` 遇到工作区已有的 `.md` 一律不覆盖，缓存清单与 `dashboard.py` 改用现行文件名。
- **改文档不改代码**：剪映导出、远程调用、预览尺寸、静图推镜、闪帧说法按代码实情改写；`flash_guard` / `voice_over_bgm_db` / `meta.assets[]` 从 schema 删除；`references/remote-services.md` 补环境变量清单。
- **笔记**：v0.8.0 前描述已删代码的 implemented 笔记在 `Status:` 下加一行“后续决定”指向 v0.8 笔记；逻辑仍在的写明现在的函数位置；`proposed/architecture/2026-10-02-skill-ownership-function-parity-and-budgets.md` 转 `rejected/`。

## Alternatives considered

- **把剪映导出补齐到文档描述的样子（换算裁切/焦点、导出淡入淡出、字幕折行）** — 最强理由：文档写的正是用户想要的能力。不做的原因：这是新功能，要对剪映坐标与文本样式做实机验证，超出清理范围；先让文档说实话，补功能另开。
- **保留 `examples/guohuo-60s/` 全部文件作历史** — 最强理由：完整的创作复盘对读者有参考价值。不用的原因：那些 JSON 是 `timeline.json` v2 与已删脚本的产物，读者照着做会走进不存在的命令；历史仍可在 `v0.7.x` 标签下看到，目录里留成片链接即可。
- **`flash_guard` 改为真的去实现闪帧修补** — 最强理由：字段名已在文档里出现过。不用的原因：`check.py` 的 `flash_sliver` 提示加 `plan.py` 贴源切点已经覆盖实际需求，自动修补会悄悄改 Agent 的剪辑决定，与“判断归 Agent”相冲突。

## Consequences

- 收益：运行时 Python 少约 70 行，测试与文档只描述真实行为；`font_file` 第一次真正影响烧录字幕；剪映导出的相对旁白路径不再依赖当前目录；更新过的素材笔记不会被缓存冲掉。
- 代价：依赖 `examples/guohuo-60s/` JSON 或 `tools/measure_subtitle.py` 的读者要回到 `v0.7.x` 标签；callout 默认动效与 SRT 默认去标点是可见的默认值变化（已记入 CHANGELOG `Changed`）；剪映导出的能力缺口现在写在文档里，而不是被掩盖。
- 同次审计发现 `render.py` 丢了 `-map_metadata -1` / `-map_chapters -1`（旧笔记 [[2026-10-03-strip-inherited-container-metadata]] 的行为未随 v0.8 保留），已在同一批改动里恢复并加测试，记在那篇笔记里。
