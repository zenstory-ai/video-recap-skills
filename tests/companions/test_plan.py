"""Tests for skills/video-recap/scripts/plan.py (long-form recap compiler)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

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
    for wav in ("n1.wav", "n2.wav", "n3.wav"):
        (tmp_path / wav).write_bytes(b"")
    tl = {"canvas": {"fps": 25}, "sources": {"s1": "ep1.mp4"},
          "audio": {"narration": [{"id": "n1", "text": "他刚丢了工作，只好去找旧同学", "file": "n1.wav", "duration": 2.0},
                                  {"id": "n2", "text": "后来他又回到小镇上", "file": "n2.wav", "duration": 1.0},
                                  {"id": "n3", "text": "她终于见到了那个人", "file": "n3.wav", "duration": 1.0}]},
          "recap": {"transcripts": {"s1": "t1.json"}, "crop": {"x": 0, "y": 0, "w": 100, "h": 80}, "items": [
              {"type": "narr", "id": "n1", "source": "s1", "text": "他刚丢了工作，只好去找旧同学", "shots": [[10.0, 13.0], [20.0, 23.0]]},
              {"type": "orig", "id": "o1", "source": "s1", "in": 50.0, "out": 52.3},
              {"type": "narr", "id": "n2", "source": "s1", "text": "后来他又回到小镇上", "shots": [[11.0, 13.5]]},
              {"type": "narr", "id": "n3", "source": "s1", "text": "她终于见到了那个人", "shots": [[60.0, 60.9]]}]}}
    monkeypatch.setattr(plan, "_pauses", lambda wav: [1.0])
    res = plan.build_plan(tl, tmp_path, {"s1": []})
    assert [e.split(" ")[0] for e in res["errors"]] == ["n3"]  # 1.25s needed, 0.9s given (< 0.75x): reported, not stretched
    assert any("goes back" in w for w in res["warnings"]) and any("reuse" in w for w in res["warnings"])
    clips = res["video"]
    assert [c["id"] for c in clips] == ["n1c0", "n1c1", "o1p0", "n2c0"]
    assert all(abs(((c["out"] - c["in"]) * 25) - round((c["out"] - c["in"]) * 25)) < 1e-6 for c in clips)
    assert clips[0]["audio"] == "mute" and clips[2]["audio"] == "keep" and clips[0]["crop"]["h"] == 80
    assert tl["audio"]["narration"][0]["at"] == 0.1
    assert [s["style"] for s in res["subtitles"]] == ["narration", "narration", "dialogue", "narration"]


def test_build_plan_rejects_a_clause_picture_that_crosses_a_cut(tmp_path: Path, monkeypatch):
    (tmp_path / "n1.wav").write_bytes(b"")
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
    for wav in ("n1.wav", "n2.wav"):
        (tmp_path / wav).write_bytes(b"")
    tl = {"canvas": {"fps": 25}, "sources": {"s1": "ep1.mp4"},
          "audio": {"narration": [{"id": "n1", "text": "他想起当年帮过他的那个人，这一次终于松了口", "file": "n1.wav", "duration": 2.0},
                                  {"id": "n2", "text": "她在门口开口求他", "file": "n2.wav", "duration": 1.0}]},
          "recap": {"transcripts": {"s1": "t1.json"}, "items": [
              {"type": "narr", "id": "n2", "source": "s1", "text": "她在门口开口求他", "window": [20.0, 30.0], "shots": [[21.0, 24.0]]},
              {"type": "narr", "id": "n1", "source": "s1", "text": "他想起当年帮过他的那个人，这一次终于松了口", "window": [30.0, 41.0], "shots": [[31.0, 34.0], [90.0, 93.0]]},
              {"type": "orig", "id": "o1", "source": "s1", "in": 40.0, "out": 42.3}]}}
    monkeypatch.setattr(plan, "_pauses", lambda wav: [1.0])
    res = plan.build_plan(tl, tmp_path, {"s1": []})
    assert not res["errors"]
    hints = [w for w in res["warnings"] if w.startswith(("n1", "n2"))]
    assert len(hints) == 2  # n2 is clean; n1 picks outside its window and its window runs into o1
    assert "outside its window" in hints[0] and "[1]" in hints[0] and "runs into o1" in hints[1]


def test_clause_durations_always_positive_when_early_pause_pushes_past_expect():
    parts = ["一二三四五六", "七八九十一二", "这是一段很长很长很长很长很长很长很长很长很长的结尾句"]
    durs = plan.clause_durations(parts, 3.0, [1.0])
    assert all(d > 0 for d in durs)
    assert abs(sum(durs) - 3.0) < 1e-3
    assert durs == [1.0, 0.375, 1.625]
    # Pause too close to end leaves insufficient room for 2 remaining clauses -> skipped
    durs2 = plan.clause_durations(["很长很长很长很长很长的前半句", "一二三四五六", "七八九十一二"], 3.0, [2.6])
    assert all(d > 0 for d in durs2) and durs2[0] < 2.4
    # Very short total where no 0.3s margin exists still stays strictly positive
    durs3 = plan.clause_durations(["一二三四五六", "七八九十一二", "十三四五六七"], 0.5, [0.25])
    assert all(d > 0 for d in durs3) and abs(sum(durs3) - 0.5) < 1e-3


def test_align_cuts_avoids_chaining_and_picks_nearest_cut():
    assert plan.align_cuts([10.3, 10.6, 10.9], 10.0, 15.0, t0=10.8, t1=14.0) == (10.3, 15.0)
    assert plan.align_cuts([14.65, 14.95], 10.0, 15.0, t0=10.1, t1=14.5) == (10.0, 14.95)
    assert plan.align_cuts([14.95, 14.65], 10.0, 15.0, t0=10.1, t1=14.5) == (10.0, 14.95)
    assert plan.align_cuts([10.08, 10.35], 10.0, 15.0, t0=10.2, t1=14.5) == (10.08, 15.0)
    assert plan.align_cuts([10.15], 10.0, 10.3, t0=10.1, t1=10.2) == (10.0, 10.3)


def test_pauses_pairs_line_by_line_with_negative_silence_start(monkeypatch):
    stderr = (
        "[silencedetect @ 0x1] silence_start: -0.0002\n"
        "[silencedetect @ 0x1] silence_end: 0.08 | silence_duration: 0.0802\n"
        "[silencedetect @ 0x1] silence_end: 0.50 | silence_duration: 0.10\n"
        "[silencedetect @ 0x1] silence_start: 1.40\n"
        "[silencedetect @ 0x1] silence_end: 1.60 | silence_duration: 0.20\n"
        "[silencedetect @ 0x1] silence_start: 2.80\n"
    )
    monkeypatch.setattr(
        plan, "run_cmd",
        lambda cmd, timeout=None: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=stderr),
    )
    assert plan._pauses(Path("dummy.wav")) == [0.04, 1.5]


def test_sync_voice_handles_missing_duration_without_key_error():
    tl = {
        "recap": {"items": [{"type": "narr", "id": "n1", "text": "甲乙丙丁戊己"}]},
        "audio": {"narration": [{"id": "n1", "text": "甲乙丙丁戊己", "file": "a.wav"}]},
    }
    assert plan.sync_voice(tl) == 1
    assert "file" not in tl["audio"]["narration"][0]


def test_build_plan_validates_narration_sync_wav_shots_orig_and_zero_start(tmp_path: Path, monkeypatch):
    (tmp_path / "ok.wav").write_bytes(b"")
    called: list[Path] = []
    monkeypatch.setattr(plan, "_pauses", lambda wav: called.append(wav) or [])
    tl = {
        "canvas": {"fps": 25},
        "sources": {"s1": "ep1.mp4"},
        "audio": {"narration": [
            {"id": "n_text", "text": "旧文案在这里", "file": "ok.wav", "duration": 1.0},
            {"id": "n_spoken", "text": "相同显示文案", "spoken_text": "旧读音", "file": "ok.wav", "duration": 1.0},
            {"id": "n_missing", "text": "音频文件不见了", "file": "missing.wav", "duration": 1.0},
            {"id": "n_badshot", "text": "镜头格式不对", "file": "ok.wav", "duration": 1.0},
            {"id": "n_zero", "text": "从零秒开始的镜头", "file": "ok.wav", "duration": 1.0},
        ]},
        "recap": {"items": [
            {"type": "narr", "id": "n_text", "source": "s1", "text": "新文案在这里", "shots": [[1.0, 4.0]]},
            {"type": "narr", "id": "n_spoken", "source": "s1", "text": "相同显示文案", "spoken_text": "新读音", "shots": [[1.0, 4.0]]},
            {"type": "narr", "id": "n_missing", "source": "s1", "text": "音频文件不见了", "shots": [[1.0, 4.0]]},
            {"type": "narr", "id": "n_badshot", "source": "s1", "text": "镜头格式不对", "window": [0.0, 5.0], "shots": ["ab"]},
            {"type": "narr", "id": "n_zero", "source": "s1", "text": "从零秒开始的镜头", "shots": [[0.0, 1.2395]]},
            {"type": "orig", "id": "o_neg", "source": "s1", "in": -1.0, "out": 2.0},
            {"type": "orig", "id": "o_inv", "source": "s1", "in": 5.0, "out": 5.0},
            {"type": "orig", "id": "o_zero", "source": "s1", "in": 10.0, "out": 10.01},
        ]},
    }
    res = plan.build_plan(tl, tmp_path, {"s1": []})
    err_ids = [e.split()[0].rstrip(":") for e in res["errors"]]
    assert err_ids == ["n_text", "n_spoken", "n_missing", "n_badshot", "o_neg", "o_inv", "o_zero"]
    assert all(p.name != "missing.wav" for p in called)
    assert len(res["video"]) == 1 and res["video"][0]["id"] == "n_zero" + "c0" and res["video"][0]["in"] == 0.0


def test_plan_cli_probe_relative_paths_and_missing_cuts(tmp_path: Path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "raw").mkdir(parents=True)
    (proj / "media").mkdir(parents=True)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    (proj / "raw" / "ep1.mp4").write_bytes(b"")
    (proj / "n1.wav").write_bytes(b"")
    tl_path = proj / "timeline.json"
    probe_path = proj / "media" / "probe.json"
    report_path = proj / "media" / "report.json"
    tl_data = {
        "canvas": {"fps": 25},
        "sources": {"s1": "raw/ep1.mp4", "bgm": "raw/bgm.mp3"},
        "audio": {"narration": [{"id": "n1", "text": "她终于见到了那个人", "file": "n1.wav", "duration": 1.0}]},
        "recap": {"items": [{"type": "narr", "id": "n1", "source": "s1", "text": "她终于见到了那个人", "shots": [[10.0, 12.0]]}]},
    }
    tl_path.write_text(json.dumps(tl_data, ensure_ascii=False), encoding="utf-8")
    probe_path.write_text(json.dumps({"sources": [{"path": "raw/ep1.mp4", "cuts": [11.0]}]}), encoding="utf-8")
    monkeypatch.setattr(plan, "_pauses", lambda wav: [])
    monkeypatch.chdir(other_cwd)

    # Relative path raw/ep1.mp4 resolves via base even from a different cwd -> detects cut at 11.0
    rc = plan.main(["build", str(tl_path), "--probe", str(probe_path), "--report", str(report_path)])
    assert rc == 3
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    assert any("crosses a source cut at 11.00" in e for e in rep["errors"])

    # Missing "cuts" key on a used source -> error (unused bgm does not error)
    probe_path.write_text(json.dumps({"sources": [{"path": "raw/ep1.mp4"}]}), encoding="utf-8")
    rc2 = plan.main(["build", str(tl_path), "--probe", str(probe_path), "--report", str(report_path)])
    assert rc2 == 3
    rep2 = json.loads(report_path.read_text(encoding="utf-8"))
    assert any("s1:" in e and "cuts" in e for e in rep2["errors"])
    assert not any("bgm:" in e for e in rep2["errors"])

    # Source not in probe.json at all -> warning/hint (not silent)
    probe_path.write_text(json.dumps({"sources": [{"path": "raw/other.mp4", "cuts": [5.0]}]}), encoding="utf-8")
    rc3 = plan.main(["build", str(tl_path), "--probe", str(probe_path), "--report", str(report_path)])
    assert rc3 == 0
    rep3 = json.loads(report_path.read_text(encoding="utf-8"))
    assert any("s1:" in w and "no entry" in w for w in rep3["warnings"])

    # Unique basename fallback works; ambiguous basename warns
    probe_path.write_text(json.dumps({"sources": [{"path": "moved/ep1.mp4", "cuts": [11.0]}]}), encoding="utf-8")
    cuts_ok, errs_ok, warns_ok = plan._cuts_by_source(tl_data, proj, str(probe_path))
    assert cuts_ok["s1"] == [11.0] and not errs_ok and not warns_ok

    probe_path.write_text(json.dumps({"sources": [
        {"path": "a/ep1.mp4", "cuts": [1.0]}, {"path": "b/ep1.mp4", "cuts": [2.0]},
    ]}), encoding="utf-8")
    cuts_amb, errs_amb, warns_amb = plan._cuts_by_source(tl_data, proj, str(probe_path))
    assert cuts_amb["s1"] == [] and not errs_amb and any("s1:" in w for w in warns_amb)


def _one_narr(tmp_path: Path, text: str, shots: list, **item) -> dict:
    (tmp_path / "n1.wav").write_bytes(b"")
    return {"canvas": {"fps": 25}, "sources": {"s1": "ep1.mp4"},
            "audio": {"narration": [{"id": "n1", "text": text, "file": "n1.wav", "duration": 1.0}]},
            "recap": {"crop": {"x": 0, "y": 0, "w": 100, "h": 80},
                      "items": [{"type": "narr", "id": "n1", "source": "s1", "text": text, "shots": shots, **item}]}}


def test_build_plan_slows_a_short_close_up_down_to_three_quarters_speed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(plan, "_pauses", lambda wav: [])
    tl = _one_narr(tmp_path, "她终于见到了那个人", [[10.0, 11.0]], crop={"x": 0, "y": 0, "w": 100, "h": 60})
    res = plan.build_plan(tl, tmp_path, {"s1": []})
    (clip,) = res["video"]
    assert not res["errors"] and clip["in"] == 10.0 and clip["out"] == 11.0 and clip["crop"]["h"] == 60  # per-item crop wins
    assert abs((clip["out"] - clip["in"]) / clip["speed"] - 1.24) < 1e-6  # timeline keeps the clause length (31 frames)
    res = plan.build_plan(_one_narr(tmp_path, "她终于见到了那个人", [[10.0, 10.9]]), tmp_path, {"s1": []})
    assert "at least 0.93s slowed" in res["errors"][0] and not res["video"]


def test_build_plan_hints_sentence_length_and_overlapping_originals(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(plan, "_pauses", lambda wav: [])
    tl = _one_narr(tmp_path, "他走了", [[10.0, 12.0]])
    tl["recap"]["items"] += [{"type": "orig", "id": "o1", "seg": "hook", "source": "s1", "in": 40.0, "out": 45.0},
                             {"type": "orig", "id": "o2", "source": "s1", "in": 44.0, "out": 48.0}]
    hints = plan.build_plan(tl, tmp_path, {"s1": []})["warnings"]
    assert any(h.startswith("n1: narration has 3 characters") for h in hints)
    assert any("o1 and o2 play overlapping original audio (the hook reuses a body original)" in h for h in hints)
    assert not any("reuse the same source picture" in h for h in hints)  # reported once, per block


def test_plan_qa_samples_body_clauses_into_sheets(tmp_path: Path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    src = tmp_path / "ep1.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4", "-pix_fmt", "yuv420p", str(src)], check=True)
    tl = {"canvas": {"fps": 25}, "sources": {"s1": str(src)},
          "recap": {"items": [{"type": "narr", "id": "h1", "seg": "hook", "source": "s1", "text": "钩子这一句不抽"},
                              {"type": "narr", "id": "n1", "source": "s1", "text": "他刚丢了工作，回老家却撞见当年的对头"}]},
          "video": [{"id": "h1c0", "source": "s1", "in": 0.0, "out": 1.0, "audio": "mute"},
                    {"id": "n1c0", "source": "s1", "in": 1.0, "out": 2.0, "audio": "mute", "crop": {"x": 0, "y": 0, "w": 320, "h": 200}},
                    {"id": "o1p0", "source": "s1", "in": 2.0, "out": 3.0, "audio": "keep"},
                    {"id": "n1c1", "source": "s1", "in": 3.0, "out": 3.9, "audio": "mute", "speed": 0.9}]}
    meta = plan.qa_samples(tl, tmp_path, tmp_path / "qa", n=40)
    assert [m["id"] for m in meta] == ["n1c0", "n1c1"] and meta[1]["clause"] == "回老家却撞见当年的对头"
    assert meta[0]["at"] == 1.0 and (tmp_path / "qa" / "sample_0.jpg").is_file()
    assert json.loads((tmp_path / "qa" / "sample.json").read_text(encoding="utf-8"))[1]["row"] == 1


def test_plan_script_prints_narration_and_the_dialogue_inside_each_original(tmp_path: Path, capsys):
    (tmp_path / "t.json").write_text(json.dumps({"segments": [
        {"start": 1.0, "end": 2.0, "text": "before"}, {"start": 5.0, "end": 6.0, "text": "你来了", "speaker": "A"},
        {"start": 6.5, "end": 7.5, "text": "嗯"}, {"start": 9.0, "end": 10.0, "text": "after"}]}), encoding="utf-8")
    tl = {"recap": {"transcripts": {"s1": "t.json"}, "items": [
        {"type": "narr", "id": "n1", "seg": "hook", "text": "她等了十年"},
        {"type": "orig", "id": "o1", "source": "s1", "in": 4.9, "out": 7.7}]}}
    (tmp_path / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    assert plan.main(["script", str(tmp_path / "timeline.json")]) == 0
    assert capsys.readouterr().out.splitlines() == ["n1 [hook] N: 她等了十年", "o1 O s1 4.9-7.7: A: 你来了 / 嗯"]


def test_sync_voice_carries_recap_narration_gain_to_every_line():
    tl = {"recap": {"narration_gain": 1.6, "items": [
        {"type": "narr", "id": "n1", "text": "她找上了老同学"}, {"type": "orig", "id": "o1", "source": "s1", "in": 1.0, "out": 3.0},
        {"type": "narr", "id": "n2", "text": "他没有马上答应"}]}}
    plan.sync_voice(tl)
    assert [n.get("gain") for n in tl["audio"]["narration"]] == [1.6, 1.6]
    del tl["recap"]["narration_gain"]
    plan.sync_voice(tl)
    assert all("gain" not in n for n in tl["audio"]["narration"])
