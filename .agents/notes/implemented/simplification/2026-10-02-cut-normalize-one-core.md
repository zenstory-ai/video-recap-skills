# Agent Note: cut 的标准化与吸附各只留一条路径，删掉 clip padding

Status: implemented

## Problem

video-cut 的计划标准化和边界吸附各有两份几乎相同的实现，外加几个没人用的旋钮：

- `cut_contract.normalize_clip_plan` 与 `normalize_multi_source_clip_plan` 有大约 85% 的循环是重复的：计划形状解析、目标时长、padding、最短时长裁剪、重叠检查、游标排布和总时长。差别只在怎么找到来源、片段多两个键、计划多 `sources` 还是 `source_duration`。
- 单源吸附写在 `cut_cli.main` 里（切镜头 → 起点吸附 → 终点吸附 → 句界闸门），多源的 `snap_multi_source_clips` 按来源分组后把同样四步又写了一遍，docstring 自称 "Mirrors the single-source"。两份要一起改，漏改一份就漂移。
- `--clip-padding` / `CLIP_PADDING`：recap 从不传，默认 0。padding 在标准化里加上，随后吸附与 `enforce_clip_sentence_boundaries` 照样会把边界移到句末或停顿，盲加的余量起不到作用。为了它还要区分"作者写的区间"和"加 padding 后的区间"来做重叠判断（0.4.0 修过一次误判）。
- `--sources-manifest` 的读取接受三种容器（数组、`{sources:[...]}`、以 `source_id` 为键的映射）和一串别名（`id`/`name`、`path`/`video_path`/`video`/`file`、`duration_seconds`/`source_duration`）。唯一的生产者是 recap 的 `_write_multi_source_manifest`，所有测试也只用 `{sources:[{source_id, source_path, ...}]}`，文档里一种形状都没写。片段级的 `id` 还会被当作 `source_id` 读，一个片段自带 `id` 标签却漏写 `source_id` 时会被静默误读。
- `shot_review.py` 有三个只出现在 `add_argument` 里的召回参数（`--max-short-seconds`、`--dense-window-seconds`、`--min-dense-cuts`），`cut.py --review-shots` 与测试都不传。

## Decision

- `cut_contract._normalize_clips(raw_plan, target_duration, min_clip_duration, allow_overlap, resolve_source)` 是唯一的标准化循环。`resolve_source(raw, idx)` 返回 `(overlap_key, source_duration, clip_fields)`：单源是 `(None, video_duration, {})`，多源按 `source_id` 查清单并返回 `source_id` / `source_path`。两个公开函数只是包一层，补上 `source_duration` 或 `sources` 与 `allow_overlap`，产出的键和键序与之前一致。重叠报错只在多源时带 ` for source_id <id>`。
- `sentence_boundaries.snap_source_clips(plan, video, duration, work_dir, *, ..., source_id=None, source_work_dir=None)` 是单个来源的吸附流程：切镜头避让在前，静音/句末吸附在后，`enforce_clip_sentence_boundaries` 最后把关。单源时 `source_id=None`，读项目根的理解产物；`snap_multi_source_clips(plan, sources, work_dir, **snap_options)` 对每个来源分组调用它，再按计划顺序重排输出时间线。`cut_cli` 用一个 `snap_options` 字典从 `CONFIG` 取参数，两条分支各是"标准化 + 吸附"两行。
- 删除 `--clip-padding`、`CONFIG["clip_padding"]`、`CLIP_PADDING`（含 `env-inventory-v1.json` 条目）以及 `_overlaps_authored_range`。没有 padding 后，作者区间和输出区间只差夹到片长，重叠仍按作者写的入出点判断。
- `normalize_sources_manifest` 只接受 `SOURCES_MANIFEST_SHAPE`：`{"sources": [{"source_id", "source_path"[, "duration", "source_work_dir"]}]}`，其他键忽略。`duration` 缺省时用 ffprobe 读取。容器不对时报错正文就是这个形状，`--sources-manifest` 的帮助文字也引用它。多源片段只认 `source_id`，计划顶层只认 `target_duration`。形状写进 video-cut SKILL.md §2，recap 的 `audio-routing.md` 在手写 `SOURCES_JSON` 处也写明。
- `shot_review.py` 删去上述三个召回参数，`summarize_candidates` 的同名关键字参数保留（测试直接调用）。`--max-short-frames` 保留，因为 `references/shot-review.md` 把它写成固定帧数的覆盖方式，`test_shot_review.py` 也钉住了它写进 `report.policy` 的行为。
- 用一份带静音、句末锚点、ASR 与假切镜头的夹具，分别跑旧版与新版的"标准化 + 吸附"，单源与多源输出 JSON 逐字节相同。

## Alternatives considered

- **把单源与多源合成一条完整路径（单源当作只有一个来源的多源）。** 最强理由：渲染、吸附、recap 都只剩一种计划形状，重复会消失得更彻底。不采用：单源计划没有 `source_id` / `source_path`，`clip_plan_validated.json` 的形状、渲染缓存 sidecar 与 recap 的读法都要跟着改，审计里这条已被推迟（"revisit after S1-S5"）。本次只合并两份重复的循环，输出形状不动。
- **保留 `--clip-padding`，只删别名。** 最强理由：手动调用 cut 的人可以一次给所有片段统一加前后余量，不用逐条改计划。不采用：padding 在吸附之前施加，最终边界由句界闸门决定，盲加的余量会被吸附重新挪动；仓库里没有调用方，留着它就得继续维护"作者区间 vs padding 区间"这套重叠语义和三组测试。
- **继续宽容手写清单的多种形状。** 最强理由：独立使用 video-cut 的人手写清单时少踩坑。不采用：这些形状从未写进文档，也没有测试，宽容只是让错误更晚暴露（例如 `id` 被误读成来源）；改成一种形状并在报错里写明，同样不难上手。
- **连 `--max-short-frames` 一起删。** 最强理由：四个召回参数一次清干净。不采用：它是 `shot-review.md` 写明的覆盖方式，删它要同时改文档和测试，省下的只有两行。

## Consequences

- **收益**：
  - `cut_contract.py` 从 412 行降到约 330 行，`cut_cli.py` 从 302 行降到 242 行。单源与多源的吸附顺序只在 `snap_source_clips` 一处定义，不会再一边改了一边没改。
  - 多源清单的契约第一次写进文档，报错直接给出期望形状。片段 `id` 不再被误当来源。
  - 少一个 CLI 参数、一个环境变量和三个 shot_review 参数，测试少了只为 padding 存在的环境变量探针与 CLI 用例。
- **代价**：
  - `cut.py --clip-padding` 与 `shot_review.py` 的三个参数会报 argparse 错误；设置了 `CLIP_PADDING` 的环境不再有效果。
  - 手写的数组或映射形状清单、带别名键的清单、片段只写 `id`、计划写 `target_duration_seconds` 的输入都会被拒或不再生效，需要按文档的形状改写。
  - 测试里给 `cut_cli` 打的吸附桩要改为打在 `sentence_boundaries` 上，因为 `cut_cli` 不再直接导入这些函数。
- **默认路径**：recap 写的清单、计划与默认配置下，`clip_plan_validated.json` 与 `edited_source.mp4` 不变。

相关：[[2026-10-02-collapse-cut-brief-facades]]、[[2026-10-02-drop-write-only-artifacts]]、[[2026-06-14-self-contained-skills-duplicated-libs]]。
