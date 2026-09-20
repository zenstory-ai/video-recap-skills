# Agent Note: timeline.json 是后端无关的权威时间线，剪映导出只是可选 sidecar

Status: implemented

## Problem

用户要能在剪映里继续精修（原片、解说、BGM、字幕分轨可编辑），但剪映草稿协议是私有 JSON、随版本变化、依赖桌面应用；把它做进核心渲染路径会让整个项目绑死在剪映上，也无法在 CI 里验证。

## Decision

- `video-assemble` 每次渲染都写 `work_dir/timeline.json`（schema v2，秒与增益，无剪映专有单位），ffmpeg 渲染的 `recap_<stem>.mp4` 是最终成片的判定标准。
- 剪映导出只在 `--export-jianying` / `EXPORT_JIANYING=1` 时由 `jianying_optional.py` 懒加载 `export_jianying`；渲染路径 never import 任何 `jianying_*` 模块（`test_core_assemble_does_not_import_exporter` 在干净解释器里断言）。导出失败只记日志，never 使已渲染的 mp4 失效。
- 导出器只依赖 Python stdlib + ffprobe。协议 JSON 模板钉在 duo-video `ef4eb46`（MIT，`references/jianying/SOURCE.md`），builder 本地实现；never vendor 上游可执行代码、资源包或示例凭证。需要官方资源包的能力标记为 `supported_offline_payload`，只接受调用方合法提供的离线资源。
- 写入安全：`validate_draft_name` 拒绝空名、绝对路径、`..` 与路径分隔符；非空目标目录不覆盖而创建编号兄弟目录；整个草稿先写临时目录再 `os.replace` 原子发布。
- `jianying_bundle_media` 默认 `True`：视频 / 音频 / 图片复制进 `Resources/local/{video,audio,image}` 并写 `draft_meta_info.json` 索引；`--jianying-no-bundle-media` 只适合剪映能直接访问原路径的环境。
- v1 时间线在导出边界迁移到 v2，未知 schema 版本直接拒绝；剪映专有字段（变速、倒放、转场、mask、LUT、资源轨）是 timeline 的附加字段，不改变 ffmpeg 路径的语义。

来源：060185a (#9)、1a47349 (#10)、8e8332c (#11)、02e402f (#13)、9a3b152、edc5f79 (#60)

## Alternatives considered

- **媒体打包默认关闭，草稿引用原路径**（#10 的初始默认）— 最强理由：不复制大文件、导出快、磁盘省。否：macOS 剪映运行在沙箱里读不到外部路径，草稿打开全部离线"暂无访问权限"；#11 把默认翻成打包，只保留 opt-out。
- **直接 vendor pyJianYingDraft / capcut-mate / duo-video 代码** — 最强理由：省掉自己实现 builder。否：许可与凭证风险，且会把核心依赖引向第三方库；只借鉴 schema、钉住 JSON 模板，代码自写。

## Consequences

- **收益**：核心成片与剪映零耦合，草稿可 clone / 搬目录后打开；协议模板有 golden 测试对照上游版本。
- **代价**：草稿是成片的近似——引用未烧字幕的源片，硬字幕仍在，需在剪映内另行遮罩；默认打包会复制媒体占空间；协议钉在特定 duo-video 版本，剪映桌面版升级后必须真机 smoke（当前只验证过剪映专业版 10.8.7-beta1 的打开 / 保存 / 重开），不承诺每个版本兼容。
