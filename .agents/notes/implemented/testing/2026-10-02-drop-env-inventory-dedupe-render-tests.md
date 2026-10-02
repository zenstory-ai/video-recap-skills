# Agent Note: 删除环境变量清单，合并重复的真实渲染测试

Status: implemented

## Problem

- `tests/orchestrator/env-inventory-v1.json`（0.6.0 引入，[[2026-09-20-slim-skill-layer]] 把它挪进测试目录）给每个环境变量包一层
  `{"classification": "value|url|file_path"}`。唯一读取方 `test_env_inventory.py` 只用键名：名字不得像凭据，且 AST 扫到的读取都必须登记。
  分类没有任何读取方；检查只有单向，变量删掉后条目不报错，`SOURCE_VIDEO` 已经是一条死行。每加一个环境变量都要改这份 JSON，却不换来任何运行时行为。
- 同一批 PR #106 留下的 assemble 测试有几组重复，每组都多跑一到两次真实 ffmpeg 渲染：
  - `test_explicit_audio_mix.py` 的两个渲染用例是 `test_explicit_mix_boundaries.py` 中样本级用例的弱化版。
  - `strict_publish` 只有两处 `_build_assembly_qc` 调用，却被四个回滚用例覆盖：seal 文件两个，boundaries 文件参数化两个。
  - 三个「复制整个 skill、`python -I` 跑 CLI」的用例，外加一个 grep `--help` 的用例。
  - `test_source_score.py` 有两个用例被 boundaries 文件里的样本级用例完全覆盖。
  - `test_pure_assemble.py` 用 mock 检查无音轨源的 `anullsrc` 字符串，真实渲染用例已覆盖同一情形。
  - `test_assemble_integration.py` 的 loudnorm 关闭用例，注释说驱动的是已删除的 zone/quiet 闪避分支。
  - `test_cut_then_adopted_audio.py` 把 bed + adopt 整条链跑了两遍，第一遍只断言 `actual_place_start == 0.25`。
- `test_movie_clock_boundaries.py` 跨文件导入 `test_explicit_audio_mix` 的 `explicit_case` 与 `_quiet`。

## Decision

- 删除 `env-inventory-v1.json`。`test_env_inventory.py` 改名为 `test_env_reads.py`，保留 `_skill_script_trees` 与 `_literal_env_reads` 两个 AST 扫描函数。
  测试直接断言：六个 skill 的全部字面环境变量读取中，除以 `_API_KEY` 结尾的名字外，没有名字含 `KEY` / `SECRET` / `PASSWORD` 或以 `_TOKEN` 结尾。
  另断言扫描结果包含 `MIMO_TOKEN_PLAN_CLUSTER`、`VLM_MAX_TOKENS`、`MIMO_API_KEY`，防止扫描器失灵后空集合也能通过；
  其中前两个名字像 token 却不该被拦，也一并验证了规则不误报。
- 删除 `test_explicit_audio_mix.py`：
  - 两个重复渲染用例删除。
  - `test_real_native_stereo_anti_phase_and_whole_voice_bus` 补上被删用例独有的断言：`explicit_audio_mix` 为 True、mix 报告 `FINALIZED`、
    每段 `output_start_sample` / `gain` 与采用文件一致、placed PCM 为 48000 Hz。
  - `load_adoption` 单元用例、缺字段参数化用例和唯一保留的复制 skill CLI 用例改挂到 `adopted_case`，CLI 用例仍覆盖 `--narration-adoption` 与 `--audio-mix-adoption` 同传和排他重试。
  - `explicit_case`、`_quiet` 挪进唯一消费方 `test_movie_clock_boundaries.py`。夹具不再预渲染一个随即被覆盖的 2 秒画面，由调用方自己渲染 `picture.mp4`。
- seal 文件的两个 QC 回滚用例删除；boundaries 的参数化回滚用例记录每次 QC 时 `output.mp4` 是否存在，断言第一次 QC 时严格成片名尚未出现，并要求报错含 `QC`。
- 删除 seal 与 source_score 的复制 skill CLI 用例，以及 `test_cli_help_exposes_explicit_narration_adoption_option`。隔离导入已由契约测试覆盖，source_score 的 CLI 仍由 `test_cut_then_adopted_audio.py` 端到端执行。
  `prepare_source_score` 拒绝已存在目标目录的唯一断言原本在被删的 CLI 用例里，现在由函数级用例承接，并多断言旧产物字节不变。
- 删除 `test_real_reorder_gain_continuous_score_and_receipt`（receipt 字段断言并入 boundaries 的 half-open reorder 用例）、`magnitude()`、`constant_pcm()`
  与 `test_raw_half_cosine_fades_clamp_outside_their_windows`。
- 删除 mock 版无音轨源用例与 loudnorm 关闭的真实渲染；无音轨源的真实渲染本来就关闭 loudnorm，模块说明与注释不再提闪避分支。
- `test_cut_then_adopted_audio.py` 保留两次 cut 与顺序断言（只有 red,blue 这次能抓到按 source_id 字母序排片的错误），删掉第一遍 bed / mix / adopt。
- 删除只钉 playbook 一句话的 `test_shared_craft_guide_includes_required_evidence`；运行时 brief 含 `clip_plan.json.required_evidence` 仍由 `test_io_fixes` 断言。
  钉密集切点文案的 `test_dense_scene_cut_policy_distinguishes_source_and_edit_created_cuts` 不在本篇删除，留给移动那段文字的 Phase-2 S6 一并处理。

## Alternatives considered

- **把清单缩成按字母排序的名字列表，并补反向检查** — 最强理由：保留一份可浏览的变量目录，反向检查还能抓到死行。否：这份目录没有任何 SKILL.md 或 README 链接，
  用户可调的变量以 `config-playbook.md` 为准；名字列表照样要求每个新变量改两处，而凭据名检查直接作用在读取上，保护力一样。
- **保留 `test_explicit_audio_mix.py`，只删两个重复用例** — 最强理由：改动最小。否：剩下的用例都能挂到 `adopted_case`，删掉第二个真实渲染夹具省一次 setup，
  也消除了 movie_clock 的跨文件夹具导入。`explicit_case` 本身不能并到 `adopted_case`：movie_clock 用例会把 receipt 截到 64000 样本，而 `adopted_case` 的第三段起点在 67891。
- **cut_then_adopted 只跑一次 cut** — 最强理由：再省一次子进程。否：manifest 顺序是 red,blue，只做 blue,red 时按 `source_id` 字母序排片的错误也能通过；cut 子进程是这条测试里便宜的部分。

## Consequences

- **收益**：测试净减约 640 行（其中 JSON 338 行），assemble 组少 5 次真实渲染、一个真实渲染夹具和 2 次复制 skill 的 CLI 子进程，orchestrator 最慢的用例少一次 source_score 与一次 recap/assemble 子进程。
  新增环境变量不再需要改测试夹具；E1、1.4 等删变量的改动也不必再同步这份 JSON。
- **代价**：不再有带分类的环境变量目录；新环境变量不会被迫经过「登记」这一步审阅。只走 narration adoption、不带 `--audio-mix-adoption` 的严格发布路径不再单独测 QC 回滚，
  它与显式混音共用 `strict_publish` 的同一个 except 分支。source_score 的 CLI 不再在复制出的隔离 skill 里单独运行；无音轨源的 `anullsrc` 滤镜拼写只由真实输出间接保证。
