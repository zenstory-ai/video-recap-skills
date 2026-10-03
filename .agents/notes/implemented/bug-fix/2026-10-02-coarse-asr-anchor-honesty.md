# Agent Note: 粗粒度 ASR 句末锚点如实标注，原声压低有上限，语气词窗口可剪

Status: implemented

## Problem

MiMo ASR 的时间只到窗口级（最短 5 秒，实际多为 15 秒）。`detect.py` 的 `detect_speech_boundary_anchors` 按标点在窗口文本里的字符位置算一个"期望时间"（`seg_start + W * match.end()/len(text)`），吸附到最近的短停顿，再用"停顿中点到期望时间的距离"定 `confidence`。这个距离只说明吸附得准不准，不说明句末真在这里：期望时间本身就是按字数比例猜的。盘龙片段（`reuse_e2e/pl`）里 7 个锚点有 4 个的 `expected_time` 恰好等于窗口末端（105.0 / 165.0 / 210.0 / 225.0），4 个被标成 `high`（132.30、177.22、181.42、210.19）。

下游五处只做 `{high, medium}` 二选一：video-cut `sentence_boundaries.py`、video-assemble `audio_mix.py`、video-script `speech_ownership.py`、video-understanding `briefing/timeline.py`、video-recap `recap_timeline.py`。只改标签会变成新门禁：锚点集一空，`narration_lint` 对每个原声入口报 `source_sentence_anchors_unavailable`，`audio_mix` 以 `anchors_unavailable` 阻断。所以标签和消费方选择必须同一次改。

另外四处随之暴露：

- cut 门禁把任何有文字的 ASR 窗口整段当作讲话（`sentence_boundaries._load_source_speech_spans`）。真实 ASR 里 `15-30 "Hi."`、`30-45 / 135-150 / 150-165 "啊！"`，只有 `105-120` 是空的，于是整段素材可合法下刀的时间只有 26.7 秒，第一个片中可下刀点在 101.0 秒，剪辑方案退化成单个片段 119.8–202.9。烧录字幕证明 16–44 和 136–164 没有对白。

- 旁白结束后原声一直压低到下一个锚点，没有上限，没有锚点就压到片尾。`pl/timeline.json` 里旁白 30.175 结束，`source_duck_end` 57.293、`restore_at` 57.421，原声被压了 27.2 秒，`assembly_qc` 仍是 PASS。终版里不带旁白的压低时长合计 25.3 秒，v1 是 45.3 秒；必须听见的原声对白被压住 7.1 秒（终版）和 21.8 秒（v1）。
- 输出时钟的锚点文件里 `pause_end` 仍是原片时间（`time 12.504, pause_end 132.304`），因为 `briefing/timeline.py` 和 `recap_timeline.py` 用 `dict(anchor)` 复制后只改了 `time` 和 `pause_start`。
- 已有工作目录里的锚点文件永远不会重算：`understanding_runner.py` 和 `understanding_brief.py` 只在文件不存在时调检测器，旧的 `high` 标签会一直留着。

## Decision

- **锚点如实标注**（`detect.py`）：吸附逻辑不变，锚点集合和 `time` 与之前逐字节相同。每个锚点新增 `timing_bound_seconds = max(pause_start - seg_start, seg_end - pause_start)` 和 `timing_basis: "asr_window"`——句末可能在窗口内任意位置，这是停顿到它的最坏距离，不依赖语速假设。`confidence` 改由 `max(timing_bound, alignment_error)` 定，阈值不变（≤0.6 high，≤1.2 medium，否则 low）。新增 `boundary_use`：high/medium 为 `verified`；否则 `alignment_error ≤ 1.2` 为 `unverified`（正好是旧的 high/medium 规则，选中集合不变）；其余为 `none`。报告 `schema_version: 2`。
- **旧文件升级，不丢锚点**：`detect.anchors_current(work_dir)` 判断文件存在且 `schema_version == 2`；两个调用方改调 `detect.ensure_speech_boundary_anchors`。不是当前版本时：有 `audio.wav` 就重新检测；没有音频（素材库恢复只拷锚点不拷 `audio.wav`，cut 第二遍 `--brief-only` 就是这种情况）就用 `asr_result.json` 原地重标——schema 1 已存 `pause_start`、`alignment_error` 和 `asr_segment_index`，足以算出 `timing_bound_seconds`、`confidence`、`boundary_use`，锚点时间不变，报告记 `upgraded_from_schema: 1`；任何锚点对不回 ASR 窗口（下标越界或 `text_tail` 不在窗口文本里）就整份不动，消费方把它当作未验证读（见下）。可用锚点永远不会被 `unavailable` 文件覆盖；只有文件本来就不存在且没有音频时，才照旧写 `unavailable`。
- **消费方按 `boundary_use` 选**：五处都用同一条内联规则 `anchor.get("boundary_use") or ("unverified" if confidence in {high, medium} else "none")`，保留 `!= "none"` 的锚点。缺字段的锚点来自 schema 1，也就是旧的粗粒度估计器，所以 high/medium 选中集合不变、但一律标 `unverified`：assemble 交接状态记 `sentence_boundary_unverified`，lint 的 `anchor_boundary_use` 记 `unverified`，brief 显示 `[unverified]`（schema 1 没有误差上限可写）。这是一次字段读取加兼容兜底，不是共享函数，不进 parity 清单。
  - video-cut：锚点窗口带上 `boundary_use`；`_combine_boundary_windows` 保留 `kind`，同一区间只要有静音窗或已验证锚点就按已验证算。只靠 `unverified` 锚点才算安全的边界，`reason` 记 `unverified_sentence_boundary`，状态仍是 `safe`。
  - video-script：`narration_lint` 的 `interrupts_source_sentence` 在 `anchor_confidence` 旁加 `anchor_boundary_use`。不新增警告码。
  - video-assemble：锚点行带 `verified`；入口和恢复点落在未验证锚点上时状态为 `sentence_boundary_unverified`，否则 `sentence_boundary`。多个锚点同时覆盖入口（或同在恢复窗口内）时优先取已验证的，没有才取时间上最早的。冷开场和 `unsafe_entry` 不变。
  - video-understanding / video-recap 的 brief：未验证锚点显示为 `[unverified ±N.Ns]`，并说明它们是 ASR 标点估计吸附到短停顿：不切词，但不保证句子已说完，门禁照常生效。`builder.py` 里 cut 门禁的描述改为"落在 ASR 讲话区间内的边界会被阻断，除非落在安静窗或句末停顿估计上"。
- **输出时钟一致**：两处重映射都写 `source_pause_end = 原片 pause_end`、`pause_end = time`（输出时钟）；`expected_time` 同样映射到输出时钟，原片值保留在 `source_expected_time`。
- **原声压低有上限**（`audio_mix._apply_source_sentence_handoffs`）：新增 `SOURCE_HANDOFF_MAX_HOLD_SECONDS = 3.0`。恢复点只取 `run_end - 0.01 ≤ time ≤ run_end + 3.0` 内的第一个可用锚点，`duck_end` / `restore_at` 算法不变。没有候选时：离片尾不足 3 秒仍 `held_to_timeline_end`；否则新状态 `bounded_release`，`duck_end = run_end`、`restore_at = run_end + fade`（与 `no_source_speech` 同值），不阻断。`anchors_unavailable` 分支不变。handoff 报告每行加 `hold_seconds`；`assembly_qc.summary.max_source_duck_hold_seconds` 记录 `source_restore_at - actual_place_end` 的最大值，只作信息，不阻断。
- **语气词窗口不算对白**（video-cut `_load_source_speech_spans`，只在 cut 内）：新增 `_NON_DIALOGUE_TOKENS`（啊 嗯 哼 哦 呃 唉 嘿 呦 哈 呀，以及 hi yeah ok okay oh uh ah hmm）。按标点和空白切词后每个词都在集合里（或是只由这些汉字组成的词，如"啊啊"）的窗口是"只有语气词"，不算讲话区间；唯一例外是它与对白窗口首尾相接（间隔 ≤0.05 秒）时，在相接一侧保留 1.0 秒保护区间 `[start, start+1]` 或 `[end-1, end]`，因为台词会跨过 15 秒窗口边界（"大地守护圣铠"在 13.2–15.4）。门禁规则本身不变：落在对白区间内的边界仍阻断；"救我！"这类两字真台词仍是对白。没有文字的窗口照旧不算讲话；只有标点或符号的窗口（"……"、"？"、"♪♪"）切不出词，仍算对白——ASR 常用"……"表示听不清的讲话。
- 文档：`video-cut/SKILL.md`、`video-recap/references/data-schema.md` 的锚点 schema 与 cut 检查说明、`video-assemble/SKILL.md` 的压低规则同步更新。

离线回放 cut 门禁（盘龙原片 300 秒，10ms 步长逐点试 `enforce_clip_sentence_boundaries`）：合法下刀时间从 26.6 秒增到 82.4 秒，第一个片中合法点从 101.0 秒提前到 16.06 秒，44–101 仍然没有合法点；旧剪辑方案 119.8 / 202.9 两个边界仍为 safe、无 blocking。

离线回放压低（把 `pl` 的输出锚点按新规则重标后，用 `assembly_manifest.json` 的实际放置时间走新的 `_apply_source_sentence_handoffs`）：7 个锚点全部变为 `low`，`timing_bound_seconds` 10.3–14.8 秒，原 4 个 high 变 `unverified`，另 3 个变 `none`；12.5–30.18 的旁白段在 30.48 `bounded_release`（原 57.42），54.1–56.75 在 57.42 `sentence_boundary_unverified`。

## Alternatives considered

- **按最高语速 `r_max` 推出更紧的误差上限**：最强理由是上限更紧，文本填满窗口时还能得到 high。没采用：它建立在约 8 字/秒的假设上，在今天的 MiMo 窗口上标签完全一样；窗口上限更简单，也不会错。
- **误差取 W/2**：公式最简单。没采用：最坏情况是 `max(f, 1−f)·W ≥ W/2`，W/2 是下界，不是上界。
- **消费方接受任意置信度的锚点**：能多出入口和恢复点。没采用：这是拿更差的证据放松门禁；这里的 low 锚点晚了 10.7 秒、落在一声"啊！"上或落在歌词里。
- **新增 lint 警告 `unverified_sentence_entry`**：让不确定性更显眼。没采用：每个 MiMo 入口都会触发，等于噪音；如实标签写在 brief 和状态字段里。
- **按停顿区间而不是中点吸附**：与 `time = pause_end` 更一致。没采用：它会改变哪些停顿成为锚点，没有证据表明有好处。
- **final_qc 加一个压低时长的提示**：补上"final_qc 不报"的缺口。没采用：有了 3 秒上限，压低时长按构造就有界，探测器抓不到新东西，还要给只看容器的 QC 加一处跨 skill 读取；`assembly_qc.summary.max_source_duck_hold_seconds` 给出同样的可见性。
- **用声学停顿当作"建议性"剪辑边界（57 个区间）**：最强理由是不靠词表就能让 0–101 秒可剪。没采用：会丢掉真实保护——密集对白里每个 ≥0.12 秒的词间停顿都成了只出警告的句中切点，门禁还多出第三种状态。语气词规则只在字幕证明没有对白的地方增加可剪时间。
- **understanding、script、assemble 用同一个对白定义**：新可剪的区间也能直接写旁白，不会报锚点错误。没采用：它会改变 full 模式的 `overlaps_speech` 和压低电平，需要 3 份以上副本或新的产物字段；等真实重跑看到 Agent 在新区间里反复写不进旁白，再决定要不要统一。
- **在实测声学停顿处恢复，把 `acoustic_pauses` 映射到输出时钟**：恢复点也不切词。没采用：要多改两个文件，收益很小——这段素材里每次旁白结束后的第一个停顿都在 2.95–3.5 秒之后，已超出上限；在 run_end 处 0.3 秒渐强可以接受。
- **旧文件一律交给检测器重算**：最简单，只有一条路径。没采用：素材库恢复的工作目录没有 `audio.wav`，检测器只能写空的 `unavailable` 文件，并且因为它已是 schema 2 再也不会重算；随后 `narration_lint` 对每个原声入口报 `source_sentence_anchors_unavailable`，`audio_mix` 以 `anchors_unavailable` 阻断，正是本次要避免的"空锚点集变成新门禁"。
- **缩小 ASR 窗口**：时间更细。没采用：fine_asr 显示 1.5 秒窗口在音乐上会幻听。

## Consequences

- **收益**：所有声称"已验证句末"的地方现在都如实写"unverified"；门禁没有新增、放松或删除，旧 `pl` 旁白和剪辑方案的 error / blocking 数不变。原声在旁白后最多再压 3 秒，必须听见的对白不再被远处锚点或片尾压住；QC 摘要能看到最长压低延续。旧工作目录的锚点文件会自动升级到 schema 2：有音频时重新检测，素材库恢复（无音频）时按 ASR 窗口原地重标，标不了就保持 schema 1，消费方照旧选中 high/medium 锚点但标为 `unverified`，不会因为升级失去锚点，也不会把旧估计说成已验证。
- **代价**：语气词窗口变得可剪。旁白入口后来也采用同一条语气词规则（[[2026-10-02-interjection-entry-ownership]]）。旁白结束后 3 秒内没有锚点时，原声在 run_end 处 0.3 秒渐强回满，可能从一句话的中间开始听到（`bounded_release`）；有锚点时最多多压 3 秒。现在的 MiMo 窗口下 `verified` 不会出现，brief 里所有锚点都会带 `unverified ±N s`。
- 只有单测和离线回放证据；需要对盘龙片段在新工作目录里真实重跑，确认锚点（schema 2、0 个 high/medium、4 个 unverified）、cut 合法时间 ≥75 秒且 Agent 是否用到 101 秒之前的素材、`max_source_duck_hold_seconds ≤ 3.31`、不带旁白的压低合计 ≤ 5 秒。
- 重访信号：统计 `entry_time` 落在新可剪区间里的 `interrupts_source_sentence` 错误数；Agent 在那里反复写不进旁白，就是统一对白定义的证据。真实重跑已出现（冷开场桥段 output 16.0–21.8 / source 16–22 被硬阻断），入口规则的统一见 [[2026-10-02-interjection-entry-ownership]]。
- 相关：[[2026-06-17-block-delivery-full-volume-original-audio]]（原声成块满音量的初衷，这次的上限是在保护它）。
