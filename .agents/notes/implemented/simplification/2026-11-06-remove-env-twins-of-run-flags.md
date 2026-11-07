# Agent Note: 移除每次运行选择的环境变量分身

Status: implemented

翻转了 [[2026-10-02-resume-command-echoes-argv]] 里 `EDIT_MODE` / `TARGET_DURATION` / 字幕坐标由续跑命令钉住的做法。

后续决定：解说评审已整体移除，见 [[2026-11-06-remove-narration-review-chain]]，`--review-narration` 系列 flag 不再存在。

## Problem

`EDIT_MODE`、`TARGET_DURATION`、`REVIEW_NARRATION`、`REQUIRE_NARRATION_REVIEW`、`ALLOW_PARTIAL_TTS`、`EXPORT_JIANYING`、`SUBTITLE_Y_TOP`、`SUBTITLE_Y_BOT` 都是某个 CLI flag 的第二种写法，选的又都是“这一次运行怎么跑”。续跑时它们的处理并不一致：`_ENV_FILLED` 把其中四个写进续跑命令，因为续跑的 shell 不一定带同样的环境；另外四个没写，于是靠环境变量打开的严格评审在新 shell 里续跑时悄悄失效。环境变量折叠进 args 也让校验顺序变复杂。

## Decision

- 上述八个名字不再读取。对应的 CONFIG 键保留，默认值写死，只由 flag 设置。
- 其中 `EDIT_MODE` 和 `TARGET_DURATION` 设了就报错，recap 与 understand 都在做任何工作之前退出，并指明改用哪个 flag。不读取的环境变量不会报错，`EDIT_MODE=cut` 会静默跑 full，这是唯一会静默改掉成片形态的一对，所以拒绝保留一个版本，之后整段删除。这也覆盖 `EDIT_MODE=dub`。
- `_edit_mode` 只负责 `--edit-mode dub` 的专门报错，[[2026-11-06-remove-dub-mode]] 里的事实已就地更新。
- `_ENV_FILLED` 只剩 `tts_provider` 和 `voice_ref`。旧 manifest 里 argv 带的 `--edit-mode` 等 flag 照常解析。
- `REVIEW_NARRATION`、`REQUIRE_NARRATION_REVIEW` 的 flag 对应物后来随评审链一起删除（[[2026-11-06-remove-narration-review-chain]]），它们不再有替代写法。`REQUIRE_NARRATION_REVIEW=1` 后来改为报错（[[2026-11-06-refuse-require-narration-review]]）。
- `TTS_PROVIDER`、`VOICE_REF`、各密钥和输出 / 字幕样式默认值是机器级配置，保留。

## Alternatives considered

- **保留环境变量，把另外四个也钉进续跑命令。** 最强理由：不破坏任何现有配置，也修好了不一致。没采用：继续维护每次运行的选择有两种写法，以后每加一个环境分身都要记得登记。
- **八个名字都报错一个版本。** 最强理由：提示最完整，用户不会被静默改变行为。没采用：其余六个的默认路径就是未设置，为它们各写拒绝逻辑并不划算，其中四个失效后的结果可以从成片或日志看出。这个理由对 `REVIEW_NARRATION` 不成立：它是外发开关，设了 `0` 的环境会在任何结果可见之前就把稿件发出去；这个风险在评审链移除后不再存在（[[2026-11-06-remove-narration-review-chain]]）。`REQUIRE_NARRATION_REVIEW` 失效后门禁静默消失，后来按同一标准改为 `=1` 报错（[[2026-11-06-refuse-require-narration-review]]）。
- **有显式 flag 时不报 `EDIT_MODE`。** 最强理由：传了 `--edit-mode` 环境变量就不起作用了，不必打断。没采用：要区分显式与默认，多一层判断；而这个变量本来就该从环境里删掉。

## Consequences

- 收益：少 8 个环境变量，`_ENV_FILLED` 少 4 项，删除 `_optional_env_int`，resume 的不一致消失。
- 代价：shell 配置或 CI 里设了这些名字的人要改。设了 `ALLOW_PARTIAL_TTS=1` 的运行会在部分失败时停下。
- 是否在默认路径：未设置时行为完全不变。
- 下一步：发布一个版本后删除 `recap_cli.parse_args`（含 `REQUIRE_NARRATION_REVIEW` 那段）与 `understanding_runner.main` 里的拒绝。
