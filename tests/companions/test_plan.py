"""Tests for skills/video-recap/scripts/plan.py (long-form recap compiler)."""
from __future__ import annotations

import json
from pathlib import Path

import plan


def test_clauses_split_at_pauses_and_merge_short_parts():
    assert plan.clauses("他刚丢了工作，回老家却撞见当年的对头。") == ["他刚丢了工作", "回老家却撞见当年的对头"]
    assert plan.clauses("结果，她输了一整天的比赛，好") == ["结果，她输了一整天的比赛，好"]


def test_clause_durations_cut_at_nearest_real_pause():
    durs = plan.clause_durations(["一二三四五六", "七八九十一二"], 4.0, pauses=[2.3, 3.9])
    assert durs == [2.3, 1.7]
    assert plan.clause_durations(["一二三四五六", "七八九十一二"], 4.0, pauses=[]) == [2.0, 2.0]


def test_align_cuts_never_clips_speech_beyond_three_frames():
    assert plan.align_cuts([10.2], 10.0, 15.0, t0=10.3, t1=14.0) == (10.2, 15.0)
    assert plan.align_cuts([10.35], 10.0, 15.0, t0=10.05, t1=14.0) == (10.0, 15.0)
    assert plan.align_cuts([14.9], 10.0, 15.0, t0=10.1, t1=14.95) == (10.0, 14.9)
    assert plan.align_cuts([14.72], 10.0, 15.0, t0=10.1, t1=15.2) == (10.0, 14.72)  # 0.28s sliver of the next shot: cut wins
    assert plan.align_cuts([14.65], 10.0, 15.0, t0=10.1, t1=15.2) == (10.0, 15.0)   # 0.35s would clip real speech: keep


def test_orig_parts_drop_long_pauses_and_floor_to_frames():
    segs = [{"start": 10.2, "end": 11.0, "text": "a"}, {"start": 11.3, "end": 12.0, "text": "b"}, {"start": 16.0, "end": 17.0, "text": "c"}]
    parts = plan.orig_parts({"in": 10.0, "out": 17.3}, segs, cuts=[], fps=25.0)
    assert [(p["in"], [s["text"] for s in p["segs"]]) for p in parts] == [(10.0, ["a", "b"]), (15.7, ["c"])]
    assert all(abs((p["out"] - p["in"]) * 25 - p["frames"]) < 1e-6 for p in parts)
    assert parts[0]["out"] <= 12.3 + 1e-9


def test_sync_voice_keeps_measured_audio_only_for_unchanged_text():
    tl = {"recap": {"items": [{"type": "narr", "id": "n1", "text": "甲，乙"}, {"type": "narr", "id": "n2", "text": "丙"}]},
          "audio": {"narration": [{"id": "n1", "text": "甲，乙", "file": "a.wav", "duration": 1.0}, {"id": "n2", "text": "旧", "file": "b.wav", "duration": 1.0}]}}
    assert plan.sync_voice(tl) == 1
    assert tl["audio"]["narration"][0]["file"] == "a.wav" and "file" not in tl["audio"]["narration"][1]


def test_build_plan_lays_out_frames_reports_and_never_rewrites(tmp_path: Path, monkeypatch):
    (tmp_path / "t1.json").write_text(json.dumps({"segments": [{"start": 50.2, "end": 52.0, "text": "你好"}]}), encoding="utf-8")
    tl = {"canvas": {"fps": 25}, "sources": {"s1": "ep1.mp4"},
          "audio": {"narration": [{"id": "n1", "text": "他刚丢了工作，只好去找旧同学", "file": "n1.wav", "duration": 2.0},
                                  {"id": "n2", "text": "后来他又回到小镇上", "file": "n2.wav", "duration": 1.0},
                                  {"id": "n3", "text": "她终于见到了那个人", "file": "n3.wav", "duration": 1.0}]},
          "recap": {"transcripts": {"s1": "t1.json"}, "crop": {"x": 0, "y": 0, "w": 100, "h": 80}, "items": [
              {"type": "narr", "id": "n1", "source": "s1", "text": "他刚丢了工作，只好去找旧同学", "shots": [[10.0, 13.0], [20.0, 23.0]]},
              {"type": "orig", "id": "o1", "source": "s1", "in": 50.0, "out": 52.3},
              {"type": "narr", "id": "n2", "source": "s1", "text": "后来他又回到小镇上", "shots": [[11.0, 13.5]]},
              {"type": "narr", "id": "n3", "source": "s1", "text": "她终于见到了那个人", "shots": [[60.0, 61.0]]}]}}
    monkeypatch.setattr(plan, "_pauses", lambda wav: [1.0])
    res = plan.build_plan(tl, tmp_path, {"s1": []})
    assert [e.split(" ")[0] for e in res["errors"]] == ["n3"]  # 1.25s needed, 1.0s given: reported, not stretched
    assert any("goes back" in w for w in res["warnings"]) and any("reuse" in w for w in res["warnings"])
    clips = res["video"]
    assert [c["id"] for c in clips] == ["n1c0", "n1c1", "o1p0", "n2c0"]
    assert all(abs(((c["out"] - c["in"]) * 25) - round((c["out"] - c["in"]) * 25)) < 1e-6 for c in clips)
    assert clips[0]["audio"] == "mute" and clips[2]["audio"] == "keep" and clips[0]["crop"]["h"] == 80
    assert tl["audio"]["narration"][0]["at"] == 0.1
    assert [s["style"] for s in res["subtitles"]] == ["narration", "narration", "dialogue", "narration"]


def test_build_plan_rejects_a_clause_picture_that_crosses_a_cut(tmp_path: Path, monkeypatch):
    tl = {"canvas": {"fps": 25}, "sources": {"s1": "ep1.mp4"},
          "audio": {"narration": [{"id": "n1", "text": "她终于见到了那个人", "file": "n1.wav", "duration": 1.0}]},
          "recap": {"items": [{"type": "narr", "id": "n1", "source": "s1", "text": "她终于见到了那个人", "shots": [[10.0, 12.0]]}]}}
    monkeypatch.setattr(plan, "_pauses", lambda wav: [])
    res = plan.build_plan(tl, tmp_path, {"s1": [11.0]})
    assert len(res["errors"]) == 1 and "crosses a source cut at 11.00" in res["errors"][0] and not res["video"]


def test_orig_parts_snap_edges_without_entering_neighbouring_lines():
    segs = [{"start": 5.0, "end": 9.9, "text": "prev"}, {"start": 10.2, "end": 11.0, "text": "a"}, {"start": 11.3, "end": 12.0, "text": "b"},
            {"start": 12.6, "end": 13.0, "text": "next"}]
    calls = []

    def snap(t, lo, hi):
        calls.append((round(t, 2), round(lo, 2), round(hi, 2)))
        return lo
    parts = plan.orig_parts({"in": 10.0, "out": 12.4}, segs[1:3], cuts=[], fps=25.0, snap=snap)
    assert calls[0] == (10.0, 9.7, 10.25) and calls[1][1] == 12.1 and parts[0]["in"] == 9.7
    calls.clear()
    plan.orig_parts({"in": 10.0, "out": 12.4}, segs, cuts=[], fps=25.0, snap=snap)
    assert calls[0][1] == 9.95 and calls[1][2] == 12.55  # not before the previous line ends, not after the next one starts


def test_build_plan_hints_original_blocks_longer_than_30s(tmp_path: Path, monkeypatch):
    (tmp_path / "t1.json").write_text(json.dumps({"segments": [{"start": 10 + 2 * i, "end": 11 + 2 * i, "text": f"l{i}"} for i in range(20)]}), encoding="utf-8")
    tl = {"canvas": {"fps": 25}, "sources": {"s1": "ep1.mp4"}, "audio": {"narration": []},
          "recap": {"transcripts": {"s1": "t1.json"}, "items": [{"type": "orig", "id": "o1", "source": "s1", "in": 9.8, "out": 49.3}]}}
    res = plan.build_plan(tl, tmp_path, {"s1": []})
    assert any(w.startswith("o1: original block runs") for w in res["warnings"]) and not res["errors"]


def test_build_plan_hints_pictures_outside_the_window_and_lead_ins_that_give_the_original_away(tmp_path: Path, monkeypatch):
    (tmp_path / "t1.json").write_text(json.dumps({"segments": [{"start": 40.2, "end": 42.0, "text": "我答应你"}]}), encoding="utf-8")
    tl = {"canvas": {"fps": 25}, "sources": {"s1": "ep1.mp4"},
          "audio": {"narration": [{"id": "n1", "text": "他想起当年帮过他的那个人，这一次终于松了口", "file": "n1.wav", "duration": 2.0},
                                  {"id": "n2", "text": "她在门口开口求他", "file": "n2.wav", "duration": 1.0}]},
          "recap": {"transcripts": {"s1": "t1.json"}, "items": [
              {"type": "narr", "id": "n2", "source": "s1", "text": "她在门口开口求他", "window": [20.0, 30.0], "shots": [[21.0, 24.0]]},
              {"type": "narr", "id": "n1", "source": "s1", "text": "他想起当年帮过他的那个人，这一次终于松了口", "window": [30.0, 41.0], "shots": [[31.0, 34.0], [70.0, 73.0]]},
              {"type": "orig", "id": "o1", "source": "s1", "in": 40.0, "out": 42.3}]}}
    monkeypatch.setattr(plan, "_pauses", lambda wav: [1.0])
    res = plan.build_plan(tl, tmp_path, {"s1": []})
    assert not res["errors"]
    hints = [w for w in res["warnings"] if w.startswith(("n1", "n2"))]
    assert len(hints) == 2  # n2 is clean; n1 picks outside its window and its window runs into o1
    assert "outside its window" in hints[0] and "[1]" in hints[0] and "runs into o1" in hints[1]
