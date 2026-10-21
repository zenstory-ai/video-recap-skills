# 写稿前补充调研指南（可选）

开始写稿时先读取已有的 `work_dir/background_research.json`。若理解索引不足以确认作品名、角色关系、剧情前提或科普概念，可在写稿前做一次补充调研，并把可靠结果写回该文件。**新写入的资料不会自动重跑或改写已有 VLM / ASR 产物**；本技能只把它作为 `context-only` 写作与建议型评审背景，当前画面事实仍以时间线证据为准。调研不是必需步骤；没有检索工具、网络不可用或来源不明确时直接跳过。

## 通用流程

1. 从文件名或用户描述里提取可搜索关键词。
2. 搜索 1-3 个高价值问题，不要过度调研。
3. 只记录对解说有帮助的事实：人物关系、剧情前提、世界观、当前集上下文、术语解释。来源互相矛盾时只用用户上下文或画面/ASR 可验证的部分；可能剧透后续的信息只用于理解人物关系，不在解说里剧透用户没要求的后续剧情。
4. 写入 `work_dir/background_research.json`。
5. 把调研结果与 `vlm_analysis.json` / `asr_result.json` 分开使用：前者补背景，后两者证明当前画面与对白。
6. 换一组关键词重试一次仍查不到可靠信息时，跳过调研并回到当前创作阶段；若方案尚未完成，先写 `recap_story_plan.json` 与 `visual_audio_board.json`，再按当前 full / cut 阶段写 `narration.json` 或 `clip_plan.json`。

## 按视频类型搜索策略

### 短剧 / 电视剧

| 搜索关键词 | 目标字段 |
|-----------|---------|
| `{作品名} 剧情 介绍 人物` | synopsis, characters |
| `{作品名} 人物 关系` | characters |
| `{作品名} 第{N}集 剧情` | episode_context |

### 电影

| 搜索关键词 | 目标字段 |
|-----------|---------|
| `{电影名} 剧情 简介` | synopsis |
| `{电影名} 影评 解读` | cultural_notes |

### 纪录片 / 科普视频

| 搜索关键词 | 目标字段 |
|-----------|---------|
| `{主题} 背景 知识` | worldbuilding |
| `{核心概念} 解释` | synopsis |
| `{主题} 最新 进展` | cultural_notes |

## 写入格式

写入 `work_dir/background_research.json`，只填搜到的字段，未搜到的字段省略，不要写 `null` 或空字符串。

```json
{
  "synopsis": "...",
  "characters": {
    "角色名": "简介或关系"
  },
  "worldbuilding": "...",
  "episode_context": "...",
  "cultural_notes": [
    {"item": "...", "explanation": "..."}
  ]
}
```
