# Agent Note: 粗粒度 ASR 句末锚点如实标注，原声压低有上限

Status: implemented

## Problem

MiMo ASR 的时间只到窗口级（最短 5 秒，实际多为 15 秒）。`detect.py` 的 `detect_speech_boundary_anchors` 按标点在窗口文本里的字符位置算一个"期望时间"（`seg_start + W * match.end()/len(text)`），吸附到最近的短停顿，再用"停顿中点到期望时间的距离"定 `confidence`。这个距离只说明吸附得准不准，不说明句末真在这里：期望时间本身就是按字数比例猜的。盘龙片段（`reuse_e2e/pl`）里 7 个锚点有 4 个的 `expected_time` 恰好等于窗口末端（105.0 / 165.0 / 210.0 / 225.0），4 个被标成 `high`（132.30、177.22、181.42、210.19）。

下游五处只做 `{high, medium}` 二选一：video-cut `sentence_boundaries.py`、video-assemble `audio_mix.py`、video-script `speech_ownership.py`、video-understanding `briefing/timeline.py`、video-recap `recap_timeline.py`。只改标签会变成新门禁：锚点集一空，`narration_lint` 对每个原声入口报 `source_sentence_anchors_unavailable`，`audio_mix` 以 `anchors_unavailable` 阻断。所以标签和消费方选择必须同一次改。

另外三处随之暴露：

- 旁白结束后原声一直压低到下一个锚点，没有上限，没有锚点就压到片尾。`pl/timeline.json` 里旁白 30.175 结束，`source_duck_end` 57.293、`restore_at` 57.421，原声被压了 27.2 秒，`assembly_qc` 仍是 PASS。终版里不带旁白的压低时长合计 25.3 秒，v1 是 45.3 秒；必须听见的原声对白被压住 7.1 秒（终版）和 21.8 秒（v1）。
- 输出时钟的锚点文件里 `pause_end` 仍是原片时间（`time 12.504, pause_end 132.304`），因为 `briefing/timeline.py` 和 `recap_timeline.py` 用 `dict(anchor)` 复制后只改了 `time` 和 `pause_start`。
- 已有工作目录里的锚点文件永远不会重算：`understanding_runner.py` 和 `understanding_brief.py` 只在文件不存在时调检测器，旧的 `high` 标签会一直留着。

## Decision

- **锚点如实标注**（`detect.py`）：吸附逻辑不变，锚点集合和 `time` 与之前逐字节相同。每个锚点新增 `timing_bound_seconds = max(pause_start - seg_start, seg_end - pause_start)` 和 `timing_basis: "asr_window"`——句末可能在窗口内任意位置，这是停顿到它的最坏距离，不依赖语速假设。`confidence` 改由 `max(timing_bound, alignment_error)` 定，阈值不变（≤0.6 high，≤1.2 medium，否则 low）。新增 `boundary_use`：high/medium 为 `verified`；否则 `alignment_error ≤ 1.2` 为 `unverified`（正好是旧的 high/medium 规则，选中集合不变）；其余为 `none`。报告 `schema_version: 2`。
- **旧文件自动重算**：`detect.anchors_current(work_dir)` 判断文件存在且 `schema_version == 2`；两个调用方改为"不是当前版本就重算"。
- **消费方按 `boundary_use` 选**：五处都用同一条内联规则 `anchor.get("boundary_use") or ("verified" if confidence in {high, medium} else "none")`，保留 `!= "none"` 的锚点。缺字段的旧文件按旧规则读。这是一次字段读取加兼容兜底，不是共享函数，不进 parity 清单。
  - video-cut：锚点窗口带上 `boundary_use`；`_combine_boundary_windows` 保留 `kind`，同一区间只要有静音窗或已验证锚点就按已验证算。只靠 `unverified` 锚点才算安全的边界，`reason` 记 `unverified_sentence_boundary`，状态仍是 `safe`。
  - video-script：`narration_lint` 的 `interrupts_source_sentence` 在 `anchor_confidence` 旁加 `anchor_boundary_use`。不新增警告码。
  - video-assemble：锚点行带 `verified`；入口和恢复点落在未验证锚点上时状态为 `sentence_boundary_unverified`，否则 `sentence_boundary`。冷开场和 `unsafe_entry` 不变。
  - video-understanding / video-recap 的 brief：未验证锚点显示为 `[unverified ±N.Ns]`，并说明它们是 ASR 标点估计吸附到短停顿：不切词，但不保证句子已说完，门禁照常生效。`builder.py` 里 cut 门禁的描述改为"落在 ASR 讲话区间内的边界会被阻断，除非落在安静窗或句末停顿估计上"。
- **输出时钟一致**：两处重映射都写 `source_pause_end = 原片 pause_end`、`pause_end = time`（输出时钟）。
- **原声压低有上限**（`audio_mix._apply_source_sentence_handoffs`）：新增 `SOURCE_HANDOFF_MAX_HOLD_SECONDS = 3.0`。恢复点只取 `run_end - 0.01 ≤ time ≤ run_end + 3.0` 内的第一个可用锚点，`duck_end` / `restore_at` 算法不变。没有候选时：离片尾不足 3 秒仍 `held_to_timeline_end`；否则新状态 `bounded_release`，`duck_end = run_end`、`restore_at = run_end + fade`（与 `no_source_speech` 同值），不阻断。`anchors_unavailable` 分支不变。handoff 报告每行加 `hold_seconds`；`assembly_qc.summary.max_source_duck_hold_seconds` 记录 `source_restore_at - actual_place_end` 的最大值，只作信息，不阻断。
- 文档：`video-recap/references/data-schema.md` 的锚点 schema 与 cut 检查说明、`video-assemble/SKILL.md` 的压低规则同步更新。

离线回放（把 `pl` 的输出锚点按新规则重标后，用 `assembly_manifest.json` 的实际放置时间走新的 `_apply_source_sentence_handoffs`）：7 个锚点全部变为 `low`，`timing_bound_seconds` 10.3–14.8 秒，原 4 个 high 变 `unverified`，另 3 个变 `none`；12.5–30.18 的旁白段在 30.48 `bounded_release`（原 57.42），54.1–56.75 在 57.42 `sentence_boundary_unverified`。

## Alternatives considered

- **按最高语速 `r_max` 推出更紧的误差上限**：最强理由是上限更紧，文本填满窗口时还能得到 high。没采用：它建立在约 8 字/秒的假设上，在今天的 MiMo 窗口上标签完全一样；窗口上限更简单，也不会错。
- **误差取 W/2**：公式最简单。没采用：最坏情况是 `max(f, 1−f)·W ≥ W/2`，W/2 是下界，不是上界。
- **消费方接受任意置信度的锚点**：能多出入口和恢复点。没采用：这是拿更差的证据放松门禁；这里的 low 锚点晚了 10.7 秒、落在一声"啊！"上或落在歌词里。
- **新增 lint 警告 `unverified_sentence_entry`**：让不确定性更显眼。没采用：每个 MiMo 入口都会触发，等于噪音；如实标签写在 brief 和状态字段里。
- **按停顿区间而不是中点吸附**：与 `time = pause_end` 更一致。没采用：它会改变哪些停顿成为锚点，没有证据表明有好处。
- **final_qc 加一个压低时长的提示**：补上"final_qc 不报"的缺口。没采用：有了 3 秒上限，压低时长按构造就有界，探测器抓不到新东西，还要给只看容器的 QC 加一处跨 skill 读取；`assembly_qc.summary.max_source_duck_hold_seconds` 给出同样的可见性。
- **在实测声学停顿处恢复，把 `acoustic_pauses` 映射到输出时钟**：恢复点也不切词。没采用：要多改两个文件，收益很小——这段素材里每次旁白结束后的第一个停顿都在 2.95–3.5 秒之后，已超出上限；在 run_end 处 0.3 秒渐强可以接受。
- **缩小 ASR 窗口**：时间更细。没采用：fine_asr 显示 1.5 秒窗口在音乐上会幻听。

## Consequences

- **收益**：所有声称"已验证句末"的地方现在都如实写"unverified"；门禁没有新增、放松或删除，旧 `pl` 旁白和剪辑方案的 error / blocking 数不变。原声在旁白后最多再压 3 秒，必须听见的对白不再被远处锚点或片尾压住；QC 摘要能看到最长压低延续。旧工作目录的锚点文件会自动升级到 schema 2。
- **代价**：旁白结束后最多可能听到 3 秒内原声句子的尾巴（`bounded_release` 在 run_end 处 0.3 秒渐强）。现在的 MiMo 窗口下 `verified` 不会出现，brief 里所有锚点都会带 `unverified ±N s`。
- 只有单测和离线回放证据；需要对盘龙片段在新工作目录里真实重跑，确认锚点（schema 2、0 个 high/medium、4 个 unverified）、`max_source_duck_hold_seconds ≤ 3.31`、不带旁白的压低合计 ≤ 5 秒。
- 相关：[[2026-06-17-block-delivery-full-volume-original-audio]]（原声成块满音量的初衷，这次的上限是在保护它）。
