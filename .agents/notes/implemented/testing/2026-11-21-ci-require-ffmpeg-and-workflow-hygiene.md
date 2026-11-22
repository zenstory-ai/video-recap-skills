# Agent Note: CI 启用 Linux 真实 ffmpeg 渲染门禁与工作流卫生治理

Status: implemented

## Problem

`.github/workflows/skill-validate.yml` 在三个平台均未安装 `ffmpeg` / `ffprobe`，导致每次 CI 运行都会静默跳过 116–117 条真实音视频与字幕渲染测试（约占全套测试 7%），且未开启 `RECAP_REQUIRE_FFMPEG=1` 失败即停保护。同时，`skill-validate.yml`、`validate-clawhub.yml`、`publish-clawhub.yml` 缺少 `timeout-minutes` 或 PR 并发取消控制，`publish-clawhub.yml` 每 12 小时全量对账一次过于频繁。

## Decision

- 在 `.github/workflows/skill-validate.yml` 的 Linux (`ubuntu-latest`) 作业中安装 `ffmpeg` 与 `fonts-dejavu-core`，并在安装步骤校验 `subtitles`/`ass`（libass）滤镜可用，同时向测试步骤注入 `RECAP_REQUIRE_FFMPEG=1`。
- 在 `scripts/test.py` 启动前实现 `RECAP_REQUIRE_FFMPEG` 的 fail-closed 预检：一旦该环境变量为真值，缺失 `ffmpeg`、`ffprobe` 或 `libass` `subtitles` 滤镜立即以退出码 2 中止，不再依赖零散单测各自判断。
- 为 `skill-validate.yml` 和 `validate-clawhub.yml` 增加仅对 `pull_request` 生效的 `concurrency`（`push` 到 `main` 不取消）与 `timeout-minutes`；将 `publish-clawhub.yml` 的定时对账从 `17 */12 * * *` 降频为每周一次 `17 3 * * 1`，并为 `resolve-source` 增加 `timeout-minutes: 15`。

## Alternatives considered

- **三平台（Ubuntu/macOS/Windows）全部安装 ffmpeg**：覆盖面最广，但 GitHub Actions 上 macOS Homebrew 与 Windows Chocolatey 安装带 libass 的 ffmpeg 耗时长且易受镜像波动影响；先在 Ubuntu 强制 fail-closed 跑满真实渲染链路，macOS/Windows 继续专注跨平台路径、编码与打包契约，性价比最高。
- **只在个别 pytest 用例里检查 `RECAP_REQUIRE_FFMPEG`**：改动更小，但全仓 116 条跳过用例分布在多个测试文件，集中由 `scripts/test.py` 在入口强制预检才能保证整个套件不会在缺少二进制或 libass 时静默降级。

## Consequences

- **收益**：每次 PR 与 `main` CI 在 Linux 上强制执行全部真实 ffmpeg/ffprobe/libass 渲染测试；连续推送 PR 自动取消旧运行并防止超时挂起；ClawHub 定时空转成本大幅降低。
- **代价**：`validate (ubuntu-latest)` 单次运行会增加 apt 安装与真实渲染耗时；macOS/Windows 仍会跳过 ffmpeg 渲染用例（仅执行纯 Python 与跨平台契约测试）。
