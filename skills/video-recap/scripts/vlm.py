import base64
import json
import re
from collections import OrderedDict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from extract import (
    FRAME_TIME_CONVENTION_VERSION,
    frame_time,
    parse_frame_number,
)
from lib import CONFIG
from lib import (
    log, api_call, load_prompt, is_moderation_refusal,
)

# ── Step 4: VLM 视觉分析 ─────────────────────────────────────────────

def vlm_prompt_payload():
    """The exact prompt/context text the VLM stage sends; the VLM cache key compares it by
    equality, so the prompt that is sent and the one that is keyed are built in one place."""
    prompt = load_prompt("VLM_DEPTH_PROMPT")
    if not prompt:
        raise RuntimeError("references/prompt-templates.md 缺少 VLM_DEPTH_PROMPT 模板")
    context = CONFIG.get("context_info", "")
    if context:
        prompt = f"已知信息：{context}\n\n{prompt}"
    return {"prompt_text": prompt, "context_info": context}


def _parse_vlm_depth_response(raw_text):
    """解析 VLM 深度分析响应，提取【描述】、【帧标签】和【深层分析】"""
    if not raw_text or not raw_text.strip():
        return "(VLM 无法识别此场景画面)", "", {}

    # 提取【描述】
    desc_match = re.search(r'【描述】\s*\n?(.*?)(?=【帧标签】|【深层分析】|$)', raw_text, re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else raw_text.strip()

    # 提取【帧标签】
    frame_facts = {}
    facts_match = re.search(r'【帧标签】\s*\n?(.*?)(?=【深层分析】|$)', raw_text, re.DOTALL)
    if facts_match:
        for line in facts_match.group(1).strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # 格式: "12.0s | 男子拿起茶壶对嘴喝, 满脸疲惫"
            m = re.match(r'(\d+(?:\.\d+)?)\s*s?\s*\|\s*(.+)', line)
            if m:
                ts = m.group(1)
                actions = [a.strip() for a in re.split(r"[，,；;、]+", m.group(2)) if a.strip()]
                if actions:
                    frame_facts[ts] = actions

    # 提取【深层分析】
    depth_match = re.search(r'【深层分析】\s*\n?(.*?)$', raw_text, re.DOTALL)
    depth_analysis = depth_match.group(1).strip() if depth_match else ""

    if not description:
        description = "(VLM 无法识别此场景画面)"

    return description, depth_analysis, frame_facts


def _max_frames_for_duration(duration):
    """Frames the VLM sees for one scene: ~1 per `vlm_seconds_per_frame`, floor 3, capped by
    `vlm_max_frames`. Replaces the old hard cap of 6 that starved long/merged scenes."""
    spf = float(CONFIG["vlm_seconds_per_frame"])
    ceiling = int(CONFIG["vlm_max_frames"])
    return max(3, min(ceiling, round(max(0.0, float(duration)) / spf)))


def _vlm_scene_cache_path(work_dir):
    return Path(work_dir) / "vlm_scene_cache.json"


def _load_vlm_scene_cache(work_dir, settings):
    """Per-scene VLM resume cache (scene_key -> analysis) written under `settings`.

    Tolerant: {} if absent/corrupt, and {} when the stored request settings differ, so a
    partial-failure cache is never resumed after an output-affecting setting changed."""
    path = _vlm_scene_cache_path(work_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("settings") != settings:
        return {}
    scenes = data.get("scenes")
    return scenes if isinstance(scenes, dict) else {}


def _flush_vlm_scene_cache(work_dir, settings, cache):
    """Persist the resume cache atomically (temp + rename) so an abort/crash never corrupts it."""
    path = _vlm_scene_cache_path(work_dir)
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"settings": settings, "scenes": cache}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        log(f"VLM 场景缓存写入失败（忽略）: {exc}")


def _looks_rate_limited(message):
    """Heuristic: was a scene failure a transient rate-limit (worth a low-concurrency retry) rather
    than a persistent error (empty response / parse failure) that re-running would not fix?"""
    m = str(message).lower()
    return "429" in m or "too many requests" in m or "rate limit" in m or "限流" in m


def analyze_scenes(scenes, frames, work_dir, *, resume=True):
    """对每个场景的关键帧调用 VLM 进行视觉分析（并行）"""
    if not scenes:
        analyses = []
        vlm_file = work_dir / "vlm_analysis.json"
        vlm_file.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
        log("VLM 分析完成: 0 个场景")
        return analyses

    if not frames:
        raise RuntimeError("VLM 分析需要先提取至少一帧；frames 为空")

    fps = CONFIG["fps"]
    if fps <= 0:
        raise ValueError("CONFIG['fps'] 必须大于 0；请先运行完整 pipeline 或指定 --fps")

    vlm_prompt = vlm_prompt_payload()["prompt_text"]

    # 构建帧时间映射 (frame_NNNNN.jpg -> time in seconds)。换算规则由 extract.py 独家定义：
    # ffmpeg 首帧在 t=0 而文件编号从 1 起，所以是 (n-1)/fps，不是 n/fps。
    frame_times = {}
    for f in frames:
        num = parse_frame_number(f)
        if num is None:
            continue
        frame_times[f] = frame_time(num, fps)

    # base64 编码缓存（LRU）。原来是无上界的 dict：整个 VLM 阶段会把用到的每一帧的 base64
    # 都常驻内存，而 base64 比原图还大 1/3。40 分钟视频 fps=1 约 2400 帧 → 数百 MB 常驻。
    # 相邻场景才会复用同一帧，容量取「并发数 × 单场景最大帧数」就够，再多也命中不了。
    b64_capacity = max(
        8,
        int(CONFIG["vlm_max_frames"]) * int(CONFIG["vlm_workers"]),
    )
    b64_cache = OrderedDict()
    b64_lock = Lock()

    def _get_b64(frame_path):
        with b64_lock:
            cached = b64_cache.get(frame_path)
            if cached is not None:
                b64_cache.move_to_end(frame_path)
                return cached
        encoded = base64.b64encode(frame_path.read_bytes()).decode()
        with b64_lock:
            b64_cache[frame_path] = encoded
            b64_cache.move_to_end(frame_path)
            while len(b64_cache) > b64_capacity:
                b64_cache.popitem(last=False)
        return encoded

    def _analyze_single_scene(i, scene):
        """分析单个场景，返回 (scene_id, result_dict)"""
        scene_frames = [f for f, t in frame_times.items()
                        if scene["start"] <= t <= scene["end"]]
        if not scene_frames:
            mid = (scene["start"] + scene["end"]) / 2
            scene_frames = [min(frames, key=lambda f: abs(frame_times.get(f, 999) - mid))]

        duration = scene["end"] - scene["start"]
        max_frames = _max_frames_for_duration(duration)

        if len(scene_frames) > max_frames:
            step = len(scene_frames) / max_frames
            scene_frames = [scene_frames[int(j * step)] for j in range(max_frames)]
        else:
            scene_frames = scene_frames[:max_frames]

        content_parts = []
        for f in scene_frames:
            b64 = _get_b64(f)
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })

        # 帧级事实标签：将帧时间注入prompt
        frame_ts_list = [f"{frame_times[f]:.1f}s" for f in scene_frames]
        frame_ts_text = "帧时间点: " + ", ".join(frame_ts_list)
        content_parts.append({"type": "text", "text": frame_ts_text + "\n\n" + vlm_prompt})

        payload = {
            "model": CONFIG["vlm_model"],
            "messages": [{"role": "user", "content": content_parts}],
            "max_tokens": int(CONFIG["vlm_max_tokens"]),
        }

        log(f"VLM 分析场景 {i+1}/{len(scenes)} ({len(scene_frames)} 帧)...")

        raw_response = ""
        for attempt in range(3):
            resp = api_call(payload)
            try:
                msg = resp["choices"][0]["message"]
                raw_response = (msg.get("content") or msg.get("reasoning_content") or "")
            except (KeyError, IndexError):
                log(f"VLM 返回异常: {json.dumps(resp, ensure_ascii=False)[:200]}")

            if raw_response.strip():
                break

            if attempt < 2:
                log(f"  场景 {i+1} VLM 返回空，重试 ({attempt+2}/3)...")
                retry_parts = content_parts[:-1]
                retry_parts.append({"type": "text", "text": frame_ts_text + "\n\n" + vlm_prompt + "\n请务必按格式输出，不要留空。"})
                payload = {
                    "model": CONFIG["vlm_model"],
                    "messages": [{"role": "user", "content": retry_parts}],
                    "max_tokens": int(CONFIG["vlm_max_tokens"]),
                }

        if not raw_response.strip():
            raise RuntimeError("VLM 连续 3 次返回空内容")

        refused = is_moderation_refusal(raw_response)
        if refused:
            # A moderation refusal is not a scene description; record the scene as unreadable.
            log(f"  场景 {i+1} 被 MiMo 内容审核拒绝，记为无法识别")
        # 解析 【描述】、【帧标签】和【深层分析】
        description, depth_analysis, frame_facts = _parse_vlm_depth_response(
            "" if refused else raw_response
        )

        result = {
            "scene_id": i,
            "start": scene["start"],
            "end": scene["end"],
            "description": description,
            "depth_analysis": depth_analysis,
        }
        if frame_facts:
            result["frame_facts"] = frame_facts
        if refused:
            result["analysis_status"] = "moderation_refused"

        return i, result

    # 断点续传：逐场景持久化分析结果，避免少数场景失败（多为 429 限流）时整批画面理解全部作废。
    # The resume cache is valid only under the SAME output-affecting settings the outer stage
    # gate tracks (understanding_cache._vlm_cache_payload). vlm_prompt already folds in
    # context_info (and background_research). The request/endpoint settings below are NOT in
    # vlm_prompt, so they are stored explicitly — otherwise a partial-failure cache reused after
    # flipping e.g. mimo_disable_thinking would yield a stale/mixed analysis.
    cache_settings = {
        "vlm_prompt": vlm_prompt,
        "vlm_model": CONFIG.get("vlm_model"),
        "vlm_max_tokens": CONFIG.get("vlm_max_tokens"),
        "vlm_seconds_per_frame": CONFIG.get("vlm_seconds_per_frame"),
        "vlm_max_frames": CONFIG.get("vlm_max_frames"),
        "fps": round(float(fps), 3),
        "frame_time_convention": FRAME_TIME_CONVENTION_VERSION,
        "api_url": CONFIG.get("api_url"),
        "mimo_disable_thinking": CONFIG.get("mimo_disable_thinking", True),
        "mimo_media_resolution": CONFIG.get("mimo_media_resolution"),
    }
    cache = _load_vlm_scene_cache(work_dir, cache_settings) if resume else {}

    def _scene_cache_key(i, scene):
        return "|".join(str(x) for x in (
            i, round(float(scene["start"]), 3), round(float(scene["end"]), 3),
        ))

    analyses = [None] * len(scenes)
    todo = []
    for i, s in enumerate(scenes):
        analyses[i] = cache.get(_scene_cache_key(i, s))
        if analyses[i] is None:
            todo.append(i)
    if todo and len(todo) < len(scenes):
        log(f"VLM 复用 {len(scenes) - len(todo)} 个已缓存场景，待分析 {len(todo)} 个")

    def _run_pass(indices, workers):
        """分析给定场景索引；结果按批持久化到续传缓存（在主线程，无需加锁）。返回 (i, err) 失败列表。

        续传缓存每次都是整份重写，原来每完成一个场景就刷一次 → 写入量随场景数平方增长
        （300 个场景约 75MB 冗余写），而且序列化发生在收结果的主循环上。改成按批刷：
        崩溃最多丢失不到一批的进度，而这些场景本来就会在重跑时被重新分析。
        """
        if not indices:
            return []
        failures = []
        pending = 0
        # 与并发度对齐：一批大致就是「同时在飞的那几个场景」。
        flush_every = max(1, min(16, workers))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(_analyze_single_scene, i, scenes[i]): i for i in indices}
            try:
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        idx, result = future.result()
                        analyses[idx] = result
                        cache[_scene_cache_key(idx, scenes[idx])] = result
                        pending += 1
                        if pending >= flush_every:
                            _flush_vlm_scene_cache(work_dir, cache_settings, cache)
                            pending = 0
                    except Exception as e:  # noqa: BLE001 - 单个场景失败不能拖垮其余已完成的
                        log(f"VLM 场景 {i+1} 分析失败: {e}")
                        failures.append((i, str(e)))
            finally:
                # 无论正常结束、失败还是中断，都把这一轮已完成的场景落盘。
                if pending:
                    _flush_vlm_scene_cache(work_dir, cache_settings, cache)
        return failures

    base_workers = min(len(todo) or 1, int(CONFIG["vlm_workers"]))
    log(f"VLM 并行分析 {len(todo)} 个场景 (workers={base_workers})...")
    failures = _run_pass(todo, base_workers)
    if failures:
        # 限流(429)多是并发瞬时拥塞，降并发重试一轮（已成功的从缓存跳过）；其它错误（空响应/解析失败）
        # 重试无益，直接保留为失败，不浪费一轮调用。
        rate_limited = [i for i, msg in failures if _looks_rate_limited(msg)]
        persistent = [(i, msg) for i, msg in failures if not _looks_rate_limited(msg)]
        if rate_limited:
            retry_workers = max(1, base_workers // 4)
            log(f"VLM {len(rate_limited)} 个场景疑似限流(429)，降并发到 {retry_workers} 重试...")
            persistent += _run_pass(rate_limited, retry_workers)
        failures = persistent

    if failures:
        sample = "; ".join(f"场景 {i+1}: {msg}" for i, msg in failures[:3])
        raise RuntimeError(
            f"VLM 分析失败 {len(failures)}/{len(scenes)} 个场景。其余已缓存到 vlm_scene_cache.json，"
            f"重跑可断点续传（只重试失败场景）。示例: {sample}"
        )

    vlm_file = work_dir / "vlm_analysis.json"
    vlm_file.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
    _vlm_scene_cache_path(work_dir).unlink(missing_ok=True)  # 完整理解已生成，续传缓存可清理
    log(f"VLM 分析完成: {len(analyses)} 个场景")
    return analyses
