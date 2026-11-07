#!/usr/bin/env python3
"""video-understanding consolidation / 整理 (index build-up).

Optional, synchronous, in-pipeline. One LLM pass over the video's own signal rolls up
per-scene vlm_analysis into a global character / relationship / plot index ->
understanding_index.json.

The pass is idempotent (a fresh artifact is reused); a failure raises and the runner
records it in consolidation.status.json. A cut-off or non-JSON model reply is a failure too
(ConsolidateIncomplete), never an empty artifact reported as done. A pure seam + thin driver,
so it is unit-testable with a mocked api_call. NON-required: the pipeline runs unchanged without it.
"""

import json
import re
from pathlib import Path

from lib import CONFIG, log, api_call, load_background_research
from index_normalize import normalize_index, scenes_end
from understanding_cache import _artifact_identity, _artifact_meta_path

# Output budgets. A 5-min recap's index ran past 3000 tokens and came back cut off
# (finish_reason=length); a cut-off reply parses to nothing, so it must never be written as
# a finished artifact. One retry doubles the budget; a second cut-off raises.
_INDEX_MAX_TOKENS = 8000
_RETRY_BUDGET_FACTOR = 2


class ConsolidateIncomplete(RuntimeError):
    """The model reply was cut off or is not JSON; nothing was written."""


class ConsolidateNoKey(RuntimeError):
    """The pass needs the model but no API key is set; nothing was sent or written."""


INDEX_SCHEMA_VERSION = 2

INDEX_PROMPT = """你在根据逐场景画面分析、ASR对白、background_research术语表，为一个视频建立【全局理解索引】，供后续写解说词时保持人物/关系/主线一致。
规则：
- visual/asr 是当前视频事实证据；每个人物、关系、剧情节点、物件尽量给 evidence_ids。
- background_research 只能用于人名/别名/术语/身份消歧，默认 support=context_only；不能把后续剧情或未出现关系升级为当前画面事实。
- ASR 中出现但画面描述未命名的人名，应进入 characters[*].asr_mentions。
- 输出要紧凑、必须是完整 JSON：只收对解说有用的角色/关系/节点；description/relation/text 每条不超过 40 字；visual_descriptions 最多 3 条；每项 evidence_ids 最多 6 个；plot_points 最多 20 条。
只返回 JSON：
{"characters":[{"name":"角色名或外观指代","description":"身份/特征","aliases":[],"visual_descriptions":[],"asr_mentions":[],"research_role":"","evidence_ids":[],"confidence":"high|medium|low"}],
 "relationships":[{"a":"角色","b":"角色","relation":"关系","evidence_ids":[],"support":"direct|indirect|context_only"}],
 "plot_points":[{"time":"00:00","text":"按时间顺序的关键剧情节点","evidence_ids":[]}],
 "entities":[{"name":"重要物件/地点/线索","evidence_ids":[]}],
 "research_glossary":[{"name":"名字/术语","aliases":[],"role":"说明","support":"context_only"}]}"""


# ── pure seams (no I/O; unit-testable) ────────────────────────────────────────


def _research_glossary_from_context(background_research):
    glossary = []
    if not isinstance(background_research, dict):
        return glossary
    chars = background_research.get("characters")
    if isinstance(chars, dict):
        for name, role in chars.items():
            if str(name).strip():
                glossary.append(
                    {
                        "name": str(name).strip(),
                        "aliases": [],
                        "role": str(role).strip(),
                        "support": "context_only",
                    }
                )
    details = background_research.get("character_details")
    if isinstance(details, dict):
        for name, info in details.items():
            if not isinstance(info, dict):
                continue
            aliases = (
                [str(a).strip() for a in (info.get("aliases") or []) if str(a).strip()]
                if isinstance(info.get("aliases"), list)
                else []
            )
            role = str(info.get("role", "")).strip()
            if str(name).strip() or aliases or role:
                glossary.append(
                    {
                        "name": str(name).strip(),
                        "aliases": aliases,
                        "role": role,
                        "support": "context_only",
                    }
                )
    return glossary


def build_index_messages(
    vlm_analysis, asr_result=None, background_research=None
):
    lines = []
    for i, scene in enumerate(vlm_analysis or []):
        sid = scene["scene_id"]
        start = float(scene["start"])
        end = float(scene["end"])
        desc = scene["description"].strip().replace("\n", " ")
        facts = scene.get("frame_facts")  # vlm.py only writes the key when non-empty
        fact_txt = ""
        if facts:
            actions = []
            for ts in sorted(facts.keys(), key=float):
                actions.extend(facts[ts])
            if actions:
                fact_txt = " | 帧实: " + "；".join(a for a in actions[:6] if a)
        lines.append(f"[visual:{i} 场景{sid} {start:.0f}-{end:.0f}s] {desc}{fact_txt}")
    asr_lines = []
    for i, seg in enumerate(asr_result or []):
        text = seg["text"].strip()
        if not text:
            continue
        asr_lines.append(
            f"[asr:{i} {float(seg['start']):.0f}-{float(seg['end']):.0f}s] {text}"
        )
    glossary = _research_glossary_from_context(background_research)
    glossary_lines = []
    for i, item in enumerate(glossary[:80]):
        aliases = "/".join(item.get("aliases") or [])
        alias_txt = f" aliases={aliases}" if aliases else ""
        glossary_lines.append(
            f"[research:{i} support=context_only] {item.get('name', '')}{alias_txt}: {item.get('role', '')}"
        )
    user = (
        f"{INDEX_PROMPT}\n\n"
        f"## 逐场景画面分析（共 {len(lines)} 段）\n" + "\n".join(lines) + "\n\n"
        f"## ASR dialogue（共 {len(asr_lines)} 段）\n"
        + ("\n".join(asr_lines) or "(无)")
        + "\n\n"
        "## Research glossary（clock=null/context_only，不得升级为当前剧情事实）\n"
        + ("\n".join(glossary_lines) or "(无)")
    )
    return [{"role": "user", "content": user}]


def _index_from_reply(data):
    """The index fields of a parsed model reply: lists only, research kept as context."""
    out = {"schema_version": INDEX_SCHEMA_VERSION}
    for key in (
        "characters",
        "relationships",
        "plot_points",
        "entities",
        "research_glossary",
    ):
        val = data.get(key)
        out[key] = val if isinstance(val, list) else []
    for item in out["research_glossary"]:
        if isinstance(item, dict):
            item["support"] = "context_only"
    return out


def _extract_json(text):
    raw = str(text or "")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else raw
    if not fence:
        first, last = candidate.find("{"), candidate.rfind("}")
        if first != -1 and last > first:
            candidate = candidate[first : last + 1]
    try:
        return json.loads(candidate)
    except ValueError:
        return None


def _index_meta(work_dir, vlm_analysis):
    """Provenance of understanding_index.json: `source` (vlm_analysis.json identity) + `model`
    are what brief consumers check; the other inputs and the prompt text are the producer's
    own rebuild gate. `asr_clean` stays null: the cleaned transcript is gone, and an index an
    old run built from one must not match while one built from raw ASR keeps matching."""
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "source": _artifact_identity(work_dir / "vlm_analysis.json"),
        "inputs": {
            "asr_result": _artifact_identity(work_dir / "asr_result.json"),
            "asr_clean": None,
            "background_research": _artifact_identity(work_dir / "background_research.json"),
        },
        "scene_count": len([s for s in (vlm_analysis or []) if isinstance(s, dict)]),
        "model": CONFIG["vlm_model"],
        "prompt": INDEX_PROMPT,
    }


def _index_cache_matches(work_dir, vlm_analysis):
    meta_path = _artifact_meta_path(work_dir / "understanding_index.json")
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return meta == _index_meta(work_dir, vlm_analysis)


# ── thin drivers (I/O + api_call) ─────────────────────────────────────────────


def _load(work_dir, name):
    """Read one of this skill's own JSON artifacts: absent → None, corrupt → raise."""
    path = Path(work_dir) / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def consolidate_index(work_dir):
    work_dir = Path(work_dir)
    vlm_analysis = _load(work_dir, "vlm_analysis.json")
    if not vlm_analysis:
        log("consolidate(index): 无 vlm_analysis.json，跳过")
        return None
    out_path = work_dir / "understanding_index.json"
    if out_path.exists() and _index_cache_matches(work_dir, vlm_analysis):
        cached = _load(work_dir, "understanding_index.json")
        repaired = _normalized_index(cached, vlm_analysis) if isinstance(cached, dict) else cached
        if repaired != cached:
            # An index written before the deterministic repairs existed: fix it in place
            # instead of paying for a new model call.
            _store_index(work_dir, repaired, vlm_analysis)
        log("consolidate(index): understanding_index.json 已最新，跳过")
        return repaired
    if not CONFIG["api_key"]:
        # Offline runs reuse a fresh index; a pass that would call the model skips instead of
        # sending an unauthenticated request that can only 401.
        log(f"consolidate(index): 未设置 {CONFIG['api_env_var']}，跳过（不发送请求）")
        raise ConsolidateNoKey()
    asr_result = _load(work_dir, "asr_result.json") or []
    background_research = load_background_research(work_dir)
    reply = _complete_json_reply(
        build_index_messages(
            vlm_analysis,
            asr_result=asr_result,
            background_research=background_research,
        ),
        _INDEX_MAX_TOKENS,
    )
    index = _index_from_reply(reply)
    if not index["research_glossary"]:
        index["research_glossary"] = _research_glossary_from_context(background_research)
    index = _normalized_index(index, vlm_analysis)
    _store_index(work_dir, index, vlm_analysis)
    log(
        f"consolidate(index): 写出 understanding_index.json（角色 {len(index['characters'])}）"
    )
    return index


def _normalized_index(index, vlm_analysis):
    """Merge split characters and canonicalize plot times (index_normalize)."""
    index, repairs = normalize_index(index, duration=scenes_end(vlm_analysis))
    if repairs["merged_characters"] or repairs["dropped_plot_times"]:
        log(
            f"consolidate(index): 合并重复角色 {repairs['merged_characters']} 个，"
            f"丢弃无效剧情时间 {repairs['dropped_plot_times']} 个"
        )
    return index


def _store_index(work_dir, index, vlm_analysis):
    out_path = work_dir / "understanding_index.json"
    out_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    _artifact_meta_path(out_path).write_text(
        json.dumps(_index_meta(work_dir, vlm_analysis), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _response_text(resp):
    """(content, finish_reason) of the first choice; ("", None) on a malformed response."""
    try:
        choice = resp["choices"][0]
        return choice["message"]["content"] or "", choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        log("consolidate: API 返回结构异常")
        return "", None


def _complete_json_reply(messages, max_tokens):
    """Call the model and return the parsed JSON object, or raise ConsolidateIncomplete.

    A cut-off reply (finish_reason=length) is retried once with a larger budget; an
    unparseable reply is not retried (a bigger budget would not fix it)."""
    budget = max_tokens
    for attempt in range(2):
        content, finish = _response_text(
            api_call(
                {
                    "model": CONFIG["vlm_model"],
                    "messages": messages,
                    "max_tokens": budget,
                    "temperature": 0.2,
                }
            )
        )
        if finish == "length":
            if attempt == 0:
                log(f"consolidate(index): 输出被截断（max_tokens={budget}），加大预算重试一次")
                budget *= _RETRY_BUDGET_FACTOR
                continue
            raise ConsolidateIncomplete(
                "consolidate(index): 输出被截断（finish_reason=length，"
                f"max_tokens={budget}），未写出结果"
            )
        data = _extract_json(content)
        if not isinstance(data, dict):
            raise ConsolidateIncomplete(
                "consolidate(index): 模型返回的不是可解析的 JSON"
                f"（finish_reason={finish}），未写出结果"
            )
        return data
