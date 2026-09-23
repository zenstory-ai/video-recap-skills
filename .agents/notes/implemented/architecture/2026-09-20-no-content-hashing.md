# Agent Note: 移除全部内容哈希与指纹，缓存与绑定改用路径、大小、mtime 与字典相等

Status: implemented

## Problem

审计（2026-09-20）统计到六个 skill 里约 4,000 行代码、约 270 个测试函数的存在理由是内容哈希：
一类是缓存键（TTS 分段、cut 渲染、理解阶段 sidecar、VLM/MiMo 缓存、consolidate 产物、素材库、MiMo QC），
另一类是"字节身份"绑定（narration/audio-mix adoption、strict_publish、source_score receipt、pair_media、
compose_foreground、字幕轨、shot_review 计划绑定、recap manifest 交叉核验、asr_timing_evidence sidecar、QC 产物指纹）。
同一个最终 MP4 在一次严格运行里被 sha256 十到十二次，每份 prepared bed 十三次加两次完整解码；
消费方重算生产方指纹还造成过漂移 bug（brief 永远拒收 `asr_clean.json`）。用户判断这些校验没有实际价值，
要求全部清掉。

## Decision

- skills/*/scripts 不再 import hashlib、不再定义 `file_fingerprint` / `stable_hash` / `sha256_file` /
  `artifact_fingerprint` 及其同类；唯一例外是 `jianying/writer.py` 的 `_md5`（剪映草稿格式要求的字段）。
- 缓存复用 = 输出存在非空 + 输入文件 `file_identity(path) -> {size, mtime_ns}` 相等 + 设置字典相等
  （+ 文本相等，如 TTS 的 `spoken_text`）。各 skill 自带一份 `file_identity`，不共享。
- agent 手写产物与工具校验产物之间的陈旧判断用 `st_mtime_ns(validated) >= st_mtime_ns(raw)`。
- binding / receipt / adoption 文件只记录路径、计数、时长、采样率、时钟事实与状态；消费方只检查引用文件存在
  且形状正确，不重哈希；`assert_current` 式逐步重验整体删除；`strict_publish` 变为写 binding → QC → 发布 → 第二次 QC。
- 调用方 JSON 里旧的 `sha256` / `*_sha256` 键被忽略，不拒绝（`strict_inputs.without_digests`）。
- `recap_run_manifest.json` / `assembly_manifest.json` 用 `source_video_identity {path,size,mtime_ns}`；
  素材库 `source_id = src_<stem>_<size>`；MiMo QC 缓存比较存储的 `cache_input` 字典。
- 删除 `INDEX_TTS_CACHE_REVISION`、`TTS_CACHE_VERSION`、`DUB_TTS_CACHE_VERSION`、
  `EDITED_SOURCE_RENDER_ALGORITHM_VERSION`、`GEOMETRY_RENDER_ALGORITHM_VERSION`、`SUBTITLE_RENDER_VERSION`、
  `SUBTITLE_TEXT_NORMALIZE_VERSION`、`GLOSSARY_POLICY_VERSION`、`RECEIPT_SCHEMA/VERSION`、`CONVERSION_POLICY`；
  删除 `tests/orchestrator/test_fingerprint_contract.py`。

本篇翻转 [[2026-06-15-stable-output-alias-minimal-resume-gate]] 中"缓存复用按内容而非 mtime"与
"`source_video_fingerprint`（全量 sha256）"的决定，以及 [[2026-06-14-self-contained-skills-duplicated-libs]]
中由跨 skill 测试钉住 `file_fingerprint` 行为的做法。

## Alternatives considered

- **只删校验类哈希，保留内容寻址的缓存键** — 最强理由：改一次旁白只重合成受影响段落，靠的是内容指纹；
  mtime 在 cp/解压后可能相同或误导。否：用户明确选择全部清掉；`size + mtime_ns + 设置/文本相等`覆盖了
  正常编辑流程，遗漏的只有"同大小同 mtime 的内容替换"这一人为场景。
- **给 sha256 加 (dev, inode, size, mtime) memo，保留语义只去重复计算** — 最强理由：零语义变化。
  否：memo 本身就是承认 size/mtime 足以判定身份，那哈希只剩仪式。
- **保留 binding 的 sha256 字段但不再校验** — 最强理由：产物格式不变。否：写而不读的字段是下一轮漂移的温床。

## Consequences

- **收益**：skills 脚本从约 33,400 行（本日三轮改动前）降到约 31,600 行，其中本轮净减约 1,050 行；一次严格 assemble 不再对多 GB 文件做十几次全量读取；
  跨 skill 不再有"指纹契约"需要 parity 测试。
- **代价**：外部改动文件而 size 与 mtime_ns 均未变时不会被察觉；binding 不再是防篡改证据，只是消费记录；
  旧 work_dir 的 sidecar（含指纹字段、旧 `edited_source.mp4.meta.json` v2、旧 asr_timing_evidence v1）一律视为缓存未命中。
- 重访信号：出现"同一路径被替换成同大小同 mtime 的不同内容"导致的错误复用时，先加 `st_ino`/`ctime_ns` 到
  `file_identity`，再考虑恢复内容哈希。

## Verification

无 ffmpeg：`scripts/test.sh` 七组全绿。有 ffmpeg 7.0（static）：失败集合是 main 分支同环境失败集合的真子集
（cut 1、assemble 6，均为既有环境性失败；少掉的一个正是本轮修复的 manifest bug）。`ruff check skills tests` 无告警；
`grep -ri "hashlib\|sha256\|fingerprint" skills` 只剩剪映 md5 与"忽略旧 sha256 键"的说明。
