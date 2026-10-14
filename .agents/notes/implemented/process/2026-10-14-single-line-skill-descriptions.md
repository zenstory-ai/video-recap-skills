# Agent Note: SKILL.md 的 description 写成单行

Status: implemented

## Problem

七个 `SKILL.md` 的 `description` 都用 YAML 折叠块（`description: >` 加缩进续行）。README 把 OpenClaw 列为支持的宿主，而 OpenClaw 文档写明 frontmatter 先按 YAML 解析，失败时退回只认单行键值的解析器；在那条退路上只有单行值能被完整读到。组织内其他 skill 仓库（oh-story-claudecode、drama-skills、novel-to-game）都写单行，oh-story-claudecode 还用 `scripts/check-openclaw-skills.sh` 守这条约定，本仓库与它们不一致。仓库测试的 `_frontmatter` 同样跳过缩进续行，从未检查过 `description` 的内容。

## Decision

- 七个 `skills/*/SKILL.md` 的 `description` 改为单行纯量，文本与原折叠块经 YAML 解析后的值逐字相同（含折行处折叠出的空格），最长 578 字符。
- `tests/orchestrator/test_plugin_manifest.py::test_skill_descriptions_are_single_line_values` 要求每个技能的 `description` 非空、不以 `>` / `|` 开头、不超过 1024 字符。
- 包字节变了，`.clawhub/publish.json` 里七个条目各升一个补丁号，用固定的中央 `clawhub_release.py lock` 重新锁 `packageDigest`，video-recap 的五个伴随依赖同步到新版本，仍为 `blocked`。

## Alternatives considered

- **保留折叠块，只在 oh-story-claudecode 里删掉“必须单行”的约束** —— 最强理由：OpenClaw 当前文档说先走 YAML，折叠块大概率能正常解析，本仓库零改动。否：退路解析器仍只认单行，两种写法都能被 YAML 读，只有单行在两条路径上都成立；且需要改动另一个仓库的规则与 CI。

## Consequences

- 收益：description 在 YAML 与单行退路两种解析下读到同一段文字，与组织内其他 skill 仓库一致；以后写回折叠块会被测试拦下。
- 代价：长描述在源码里是一整行，diff 只能整行比对；七个 ClawHub 包都要发一个只改 frontmatter 排版的补丁版本。

## Verification

`python3 scripts/test.py orchestrator`、`ruff check skills tests scripts`；新测试在改动前的 SKILL.md 上失败、改动后通过。转换脚本对每个文件断言 `yaml.safe_load` 前后 frontmatter 完全相等。中央脚本按 `validate-clawhub.yml` 固定的 `c584f3caac2f47168781f7165b38200f1bda2350` 运行 `lock`，再以 `--base-ref` 指向改动前的 main 运行 `validate` 通过。
