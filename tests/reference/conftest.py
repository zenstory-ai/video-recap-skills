"""Put the video-reference scripts on sys.path; this group runs in its own pytest process."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-reference" / "scripts"))

import copy  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

DURATION = 60.0
CUTS = [5.0, 10.0, 12.0, 20.2, 30.0, 31.0, 40.4, 50.0]
ASR_TEXT = {0: "他是天下最尊贵的私生子", 10: "你可知道我是谁的儿子", 40: "范闲你给我站住"}


def _measurements():
    return {
        "schema": "video-reference.measurements.v3",
        "source": {"size": 1, "mtime_ns": 1, "duration_s": DURATION,
                   "canvas": {"width": 1280, "height": 720}, "fps": 25.0,
                   "subtitle_streams": 0, "audio_streams": 1},
        "settings": {"detector": "scdet-isolated-v1", "hard_score": 10.0, "soft_score": 4.0,
                     "isolation_ratio": 2.0, "isolation_window_s": 0.3, "score_floor": 2.0, "scaled": True},
        "shots": {"cuts": list(CUTS), "count": 9, "median_s": 5.0, "cuts_per_min": 8.0, "review_windows": []},
        "loudness": {"integrated_lufs": -14.8, "lra_lu": 6.4, "true_peak_dbtp": -1.0,
                     "short_term_1s": [-16.0] * 10 + [-20.0] * 50},
    }


def _breakdown():
    return {
        "schema": "video-reference.breakdown.v1",
        "labels": {
            "audio_spans": [
                {"start": 0, "end": 10, "owner": "narration", "narration_job": "context"},
                {"start": 10, "end": 20, "owner": "original_dialogue"},
                {"start": 20, "end": 40, "owner": "narration", "narration_job": "causal_link"},
                {"start": 40, "end": 50, "owner": "original_dialogue"},
                {"start": 50, "end": 60, "owner": "music"},
            ],
            "sections": [
                {"start": 0, "end": 6, "function": "hook"},
                {"start": 6, "end": 30, "function": "setup"},
                {"start": 30, "end": 50, "function": "turn"},
                {"start": 50, "end": 60, "function": "payoff"},
            ],
            "subtitles": {"burned": True, "max_lines": 1, "marks_original": "「」", "evidence_t": [12.0]},
            "basis": "5s ASR 窗口 + 故事板",
        },
        "source_facts": [
            {"id": "f1", "dimension": "narrative_structure", "statement": "开场旁白交代主角身世后切入原声冲突",
             "t": [0, 20], "entities": ["范闲"]},
            {"id": "f2", "dimension": "pacing", "statement": "旁白段切点密于原声段",
             "measure": ["derived.by_owner.narration.cuts_per_min"], "entities": []},
        ],
        "methods": [
            {"id": "m1", "dimension": "audio_visual", "rule": "旁白段用短镜头推进信息，原声段放长镜头让整句台词落地",
             "applies_when": "对白冲突强、需旁白补前情的剧集解说", "avoid_when": "主要靠动作声承载的段落",
             "applies_to": "visual_audio_board", "evidence": ["f2", "measure:derived.switch_on_cut_share"],
             "targets": {"narration_cuts_per_min": {"from": "derived.by_owner.narration.cuts_per_min"}}},
            {"id": "m2", "dimension": "narrative_structure", "rule": "先用旁白交代身份，再让第一段原声冲突接管",
             "applies_when": "主角身份需要一句话交代的单集解说", "applies_to": "story_plan", "evidence": ["f1"],
             "targets": {"first_original": {"from": "derived.first_original_at"},
                         "structure": {"from": "derived.structure"}}},
            {"id": "m3", "dimension": "pacing", "rule": "整体保持中速切换，不靠快切制造紧张",
             "applies_when": "对白驱动的剧情", "applies_to": "clip_plan", "evidence": ["measure:shots.median_s"],
             "targets": {"shot_median_s": {"from": "shots.median_s"}}},
            {"id": "m4", "dimension": "shots_editing", "rule": "声音归属切换尽量落在画面切点上",
             "applies_when": "旁白与原声交替的段落", "applies_to": "clip_plan",
             "evidence": ["measure:derived.switch_on_cut_share"]},
            {"id": "m5", "dimension": "narration_subtitles", "rule": "单行字幕，原声台词加直角引号区分旁白",
             "applies_when": "旁白与原声台词都上字幕时", "applies_to": "style_card", "evidence": ["f2"]},
        ],
        "skipped_dimensions": {},
    }


@pytest.fixture
def measurements():
    return _measurements()


@pytest.fixture
def breakdown():
    return _breakdown()


@pytest.fixture
def asr_segments():
    return [{"start": float(t), "end": float(t + 5), "text": ASR_TEXT.get(t, "这一段是旁白说明" if t < 40 else "")}
            for t in range(0, 60, 5)]


@pytest.fixture
def research():
    return {"characters": {"范闲": "主角", "庆帝": "皇帝"}}


@pytest.fixture
def work_dir(tmp_path, measurements, breakdown, asr_segments, research):
    """A complete work_dir; tests mutate `breakdown` / `research` before calling `write`."""
    def write(bd=None, rs=None):
        files = {
            "reference_measurements.json": measurements,
            "reference_breakdown.json": breakdown if bd is None else bd,
            "asr_result.json": asr_segments,
            "asr_timing_evidence.json": {"status": "AVAILABLE_COARSE", "glossary": {"names": ["范闲"]}},
            "background_research.json": research if rs is None else rs,
        }
        for name, payload in files.items():
            (tmp_path / name).write_text(json.dumps(copy.deepcopy(payload), ensure_ascii=False), encoding="utf-8")
        return tmp_path
    return write
