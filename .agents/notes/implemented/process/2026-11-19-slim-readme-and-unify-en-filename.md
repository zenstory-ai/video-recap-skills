# Agent Note: 统一英文 README 文件名为 README_EN.md、精简页尾章节并去除工程黑话

Status: implemented

后续更新：[[2026-09-16-readme-for-readers-not-engines]]、[[2026-09-20-readme-real-outputs-and-faq]]。

## Problem

1. **英文 README 命名与组织内其余仓库不一致**：`zenstory-ai` 其余仓库均使用 `README_EN.md`，唯独本仓库沿用 `README.en.md`，导致跨仓链接规范、发布必需文件清单（`.release/policy.json`）与插件测试守卫各自特判。
2. **正文仍残留面向开发者的架构黑话**：虽然 [[2026-11-14-de-jargon-relax-craft-gates-and-split-promo-copy]] 清理了口号词，但 `README.md` 与英文版面向创作者介绍产品时仍大量使用“把整条产线收敛为两个自包含技能”“句界安全剪辑”“确定性底线门禁”“内置声明式包装引擎”“瞬态峰值对齐音效”“五维分类素材库”“看片返修锁定账本”等内部实现术语，提高了编导与剪辑读者的理解门槛。
3. **页尾章节重复堆积**：顶部 masthead 已包含指向 `./LICENSE` 的 MIT 徽章，`流程与架构` 一节也已链接 `data-schema.md` 与 `docs/architecture.md`，文末却又重复放置四行二十余个链接的 `文档导航` 大表、独立的 `开源许可` 小节以及带长句解释的 `致谢` 列表，且组织矩阵小节标题与同组织其他仓库的 `ZenStory AI 项目` / `Part of ZenStory AI` 不统一。

## Decision

1. 将 `README.en.md` 改名为 `README_EN.md`，同步更新 `README.md` 顶部语言切换链接、`.release/policy.json` 的 `requiredPaths` 与 `tests/orchestrator/test_plugin_manifest.py` 的读取路径。
2. 保留首屏居中 masthead、样片视频、`examples/guohuo-60s/` 原样 JSON 节选以及 `Python 3.10+` 与零 `pip install` 承诺，将定位句、`这是什么`、`流程与架构`、`看看它的输出` 说明段、`进阶能力一览` 与 `常见问题` 改写为面向视频创作者的自然白话中文及地道英文。
3. 删除文末独立的 `文档导航` / `Documentation` 四行链接大表与 `开源许可` / `License` 小节（`data-schema.md` 与 `docs/architecture.md` 保留在 `流程与架构` 表后）；`致谢` / `Acknowledgments` 精简为纯链接列表并置于 `ZenStory AI 项目` / `Part of ZenStory AI` 之前。

## Alternatives considered

- **保留 `README.en.md` 并加符号链接 `README_EN.md`**：最强理由是兼容收藏了旧文件名的外部直链。不采用：双文件名会让 Git 树、Windows 检出与 `.release/policy.json` 归档同时携带两份相同文件，直接统一为组织标准的 `README_EN.md` 更干净。
- **保留文末四行 `文档导航` 索引表**：最强理由是方便深入阅读 `docs/` 下全部专题文章。不采用：首屏与正文已给出项目主页、核心示例目录及架构/数据结构入口，二十余个站内外链接堆在 README 末尾对初次上手读者用处有限且易随文档增删失修。

## Consequences

- **收益**：中英双语 README 文件名、首尾结构与 `zenstory-ai` 全组织对齐，篇幅由 330 / 331 行精简至 316 / 317 行，创作者无需先懂内部模块术语即可看懂“先剪画面再写词、重头戏留原声、自带横竖屏画框字幕、导出剪映草稿”四项核心价值。
- **代价**：指向旧路径 `blob/main/README.en.md` 的外部书签需改访 `README_EN.md`；需要查阅 `docs/` 下细分专题长文的读者需直接浏览 `docs/` 目录或项目主页。
