import json

import pytest

import reference
from reference_check import check_breakdown, export_errors, leak_corpus, leak_errors, run_check


def _errors_after(work_dir, breakdown, research, mutate):
    mutate(breakdown, research)
    return run_check(work_dir(breakdown, research))["errors"]


def test_valid_breakdown_passes_and_export_carries_methods_without_source_facts(work_dir, tmp_path):
    out = tmp_path / "out" / "production_reference.json"

    assert reference.main(["check", "--work-dir", str(work_dir())]) == 0
    assert reference.main(["export", "--work-dir", str(tmp_path), "--out", str(out)]) == 0

    text = out.read_text(encoding="utf-8")
    production = json.loads(text)
    assert production["schema"] == "video-reference.production.v1"
    for leaked in ("开场旁白交代主角身世", "旁白段切点密于原声段", "范闲", "evidence", '"from"',
                   "source_facts", "labels", "statement", "entities", "mtime_ns", str(tmp_path)):
        assert leaked not in text, leaked
    methods = {m["id"]: m for m in production["methods"]}
    assert methods["m1"]["targets"]["narration_cuts_per_min"] == {"value": 8.0, "provenance": "labeled"}
    assert methods["m3"]["targets"]["shot_median_s"] == {"value": 5.0, "provenance": "measured"}
    assert methods["m2"]["targets"]["first_original"]["value"] == {"fraction": 0.167}
    assert production["profile"]["narration_share"] == {"value": 0.5, "provenance": "labeled"}
    assert production["profile"]["first_original_at"]["value"] == {"fraction": 0.167}
    assert production["subtitles"] == {"burned": True, "max_lines": 1, "marks_original": "「」"}
    assert [s["function"] for s in production["structure"]] == ["hook", "setup", "turn", "payoff"]


def _set(path, value):
    def mutate(breakdown, _research):
        node = breakdown
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def _append_rule(text, index=0):
    def mutate(breakdown, _research):
        breakdown["methods"][index]["rule"] += text
    return mutate


def _drop_method(dimension):
    def mutate(breakdown, _research):
        breakdown["methods"] = [m for m in breakdown["methods"] if m["dimension"] != dimension]
    return mutate


def _without_fact_key(key):
    def mutate(breakdown, _research):
        del breakdown["source_facts"][0][key]
    return mutate


REJECTIONS = [
    ("entity-from-facts", _append_rule("，像范闲那样"), "R6"),
    ("entity-from-background-research", _append_rule("，学庆帝的压迫感"), "R6"),
    ("eight-char-asr-quote", _append_rule("，比如你可知道我是谁的儿子"), "R6"),
    ("absolute-timecode", _append_rule("，在 1:23 处收束"), "R6"),
    ("absolute-second", _append_rule("，第12秒切原声"), "R6"),
    ("absolute-path", _append_rule("，参考 /Users/me/clip.mp4"), "R6"),
    ("quote-split-by-punctuation", _append_rule("，用「你可知道，我是谁的儿子」收尾"), "R6"),
    ("name-split-by-space", _append_rule("，像范 闲那样"), "R6"),
    ("chinese-clock-time", _append_rule("，在1分23秒收束"), "R6"),
    ("one-segment-root-path", _append_rule("，参考 /tmp 下的片子"), "R6"),
    ("object-valued-skipped-reason", _set(["skipped_dimensions"], {"pacing": {"范闲在京都被刺杀": "x"}}), "R1"),
    ("object-valued-marks-original", _set(["labels", "subtitles", "marks_original"], {"范闲你给我站住": 1}), "R1"),
    ("non-integer-max-lines", _set(["labels", "subtitles", "max_lines"], "1"), "R1"),
    ("non-bool-burned", _set(["labels", "subtitles", "burned"], 1), "R1"),
    ("evidence-on-file-identity", _set(["methods", 0, "evidence"], ["measure:source.size"]), "R4"),
    ("evidence-on-schema-string", _set(["methods", 0, "evidence"], ["measure:schema"]), "R4"),
    ("fact-measure-outside-roots", _set(["source_facts", 1, "measure"], ["source.mtime_ns"]), "R3"),
    ("target-whole-derived", _set(["methods", 2, "targets"], {"x": {"from": "derived"}}), "R5"),
    ("target-list-index", _set(["methods", 2, "targets"], {"x": {"from": "shots.cuts.3"}}), "R5"),
    ("target-absolute-first-original", _set(["methods", 2, "targets"], {"x": {"from": "derived.first_original_at.s"}}), "R5"),
    ("target-raw-cut-list", _set(["methods", 2, "targets"], {"x": {"from": "shots.cuts"}}), "R5"),
    ("dangling-evidence", _set(["methods", 0, "evidence"], ["f9"]), "R4"),
    ("empty-evidence", _set(["methods", 0, "evidence"], []), "R4"),
    ("target-with-value", _set(["methods", 2, "targets"], {"x": {"from": "shots.median_s", "value": 3}}), "R5"),
    ("unresolvable-from", _set(["methods", 2, "targets"], {"x": {"from": "derived.nope"}}), "R5"),
    ("from-outside-measurements", _set(["methods", 2, "targets"], {"x": {"from": "source.size"}}), "R5"),
    ("missing-dimension", _drop_method("narration_subtitles"), "R7"),
    ("fact-with-rule", _set(["source_facts", 0, "rule"], "x"), "R1"),
    ("method-with-statement", _set(["methods", 0, "statement"], "x"), "R1"),
    ("unknown-owner", _set(["labels", "audio_spans", 4, "owner"], "bgm"), "R1"),
    ("unknown-function", _set(["labels", "sections", 0, "function"], "outro"), "R1"),
    ("t-beyond-duration", _set(["source_facts", 0, "t"], [10, 75]), "R3"),
    ("fact-without-entities", _without_fact_key("entities"), "R3"),
    ("fact-unresolvable-measure", _set(["source_facts", 1, "measure"], ["shots.nope"]), "R3"),
    ("overlapping-spans", _set(["labels", "audio_spans", 1, "start"], 8), "R2"),
    ("span-gap-over-half-second", _set(["labels", "audio_spans", 1, "start"], 11), "R2"),
    ("spans-do-not-reach-the-end", _set(["labels", "audio_spans", 4, "end"], 58), "R2"),
    ("chinese-numeral-clock-time", _append_rule("，在一分三十秒左右切到原声"), "R6"),
    ("leak-in-skipped-reason", _set(["skipped_dimensions"], {"pacing": "范闲那条线没有可迁移的节奏"}), "R6"),
    ("cut-fix-removes-nothing", _set(["labels", "cut_fixes"], {"remove": [13.0]}), "R2"),
    ("cut-fix-adds-a-detected-cut", _set(["labels", "cut_fixes"], {"add": [10.05]}), "R2"),
    ("cut-fix-outside-the-video", _set(["labels", "cut_fixes"], {"add": [61.0]}), "R2"),
    ("cut-fix-not-numbers", _set(["labels", "cut_fixes"], {"add": ["12s"]}), "R1"),
    ("cut-fix-unknown-key", _set(["labels", "cut_fixes"], {"move": [12.0]}), "R1"),
]


@pytest.mark.parametrize("mutate, rule", [(m, r) for _, m, r in REJECTIONS], ids=[i for i, _, _ in REJECTIONS])
def test_check_rejects(work_dir, breakdown, research, mutate, rule):
    errors = _errors_after(work_dir, breakdown, research, mutate)

    assert any(error.startswith(rule) for error in errors), errors


def test_skipped_dimension_with_a_reason_satisfies_coverage(work_dir, breakdown, research):
    _drop_method("narration_subtitles")(breakdown, research)
    breakdown["skipped_dimensions"] = {"narration_subtitles": "ASR 不可用，未测语速"}

    assert run_check(work_dir(breakdown))["errors"] == []


def test_labels_only_breakdown_prints_derived_and_asks_for_methods(work_dir, breakdown):
    labels_only = {"schema": breakdown["schema"], "labels": breakdown["labels"]}

    report = run_check(work_dir(labels_only))

    assert report["derived"]["by_owner"]["narration"]["share"] == 0.5
    assert {error.split()[1].rstrip(":") for error in report["errors"]} == {
        "narrative_structure", "pacing", "shots_editing", "narration_subtitles", "audio_visual"}


def test_export_with_errors_exits_1_and_writes_nothing(work_dir, breakdown, research, tmp_path):
    _append_rule("，像范闲那样")(breakdown, research)
    out = tmp_path / "production_reference.json"

    assert reference.main(["export", "--work-dir", str(work_dir(breakdown)), "--out", str(out)]) == 1
    assert not out.exists()


def test_skipped_reason_is_leak_scanned_at_check_time_not_only_at_export(work_dir, breakdown, tmp_path):
    # pacing is covered by m3, but the reason is exported verbatim, so check must already reject it.
    breakdown["skipped_dimensions"] = {"pacing": "范闲那条线没有可迁移的节奏"}
    out = tmp_path / "out" / "production_reference.json"

    assert reference.main(["check", "--work-dir", str(work_dir(breakdown))]) == 1
    assert reference.main(["export", "--work-dir", str(tmp_path), "--out", str(out)]) == 1
    assert not out.exists()


def test_cut_fixes_replace_the_detector_cuts_everywhere_and_mark_shot_values_reviewed(work_dir, breakdown, tmp_path):
    breakdown["labels"]["cut_fixes"] = {"remove": [12.0, 31.0], "add": [45.0]}
    out = tmp_path / "out" / "production_reference.json"

    report = run_check(work_dir(breakdown))
    assert report["errors"] == []
    # narration spans 0-10 and 20-40 now hold 5.0, 20.2 and 30.0 (31.0 removed)
    assert report["derived"]["by_owner"]["narration"]["cuts_per_min"] == 6.0
    assert reference.main(["export", "--work-dir", str(tmp_path), "--out", str(out)]) == 0

    production = json.loads(out.read_text(encoding="utf-8"))
    assert production["profile"]["cuts_per_min"] == {"value": 7.0, "provenance": "reviewed"}
    assert production["profile"]["shot_median_s"] == {"value": 7.4, "provenance": "reviewed"}
    methods = {m["id"]: m for m in production["methods"]}
    assert methods["m3"]["targets"]["shot_median_s"] == {"value": 7.4, "provenance": "reviewed"}
    assert methods["m1"]["targets"]["narration_cuts_per_min"] == {"value": 6.0, "provenance": "labeled"}
    assert production["cut_detection"] == {
        "detector": "scdet-isolated-v1", "hard_score": 10.0, "soft_score": 4.0, "isolation_ratio": 2.0,
        "isolation_window_s": 0.3, "scaled": True, "agent_added": 1, "agent_removed": 2}


def test_export_without_fixes_records_the_detector_and_measured_provenance(work_dir, tmp_path):
    out = tmp_path / "out" / "production_reference.json"

    assert reference.main(["export", "--work-dir", str(work_dir()), "--out", str(out)]) == 0

    production = json.loads(out.read_text(encoding="utf-8"))
    assert production["profile"]["cuts_per_min"] == {"value": 8.0, "provenance": "measured"}
    assert production["cut_detection"]["soft_score"] == 4.0
    assert production["cut_detection"]["agent_added"] == production["cut_detection"]["agent_removed"] == 0


def test_unreviewed_suppressed_candidates_warn_until_cut_fixes_is_written(breakdown, measurements, asr_segments):
    measurements["shots"]["review_windows"] = [[3.0, 3.6]]
    evidence = {"status": "AVAILABLE_COARSE"}

    before = check_breakdown(breakdown, measurements, asr_segments=asr_segments, asr_evidence=evidence)
    breakdown["labels"]["cut_fixes"] = {}
    after = check_breakdown(breakdown, measurements, asr_segments=asr_segments, asr_evidence=evidence)

    assert before["errors"] == after["errors"] == []
    assert any("frames --review" in w for w in before["warnings"])
    assert not any("frames --review" in w for w in after["warnings"])


def test_quote_split_across_two_asr_windows_is_still_caught(breakdown, measurements):
    # No single window holds 8 of the shared characters; only the joined pair does.
    windows = [{"start": 5.0, "end": 10.0, "text": "么好地方啊"}, {"start": 0.0, "end": 5.0, "text": "城里不是什"}]
    breakdown["methods"][0]["rule"] += "，城里不是什么好地方"

    report = check_breakdown(breakdown, measurements, asr_segments=windows, asr_evidence={"status": "AVAILABLE_COARSE"})

    assert any(e.startswith("R6 methods.m1.rule") and "城里不是什么好地" in e for e in report["errors"]), report["errors"]


@pytest.mark.parametrize("leak", ["小范大人", "监察院", "红楼梦", "那座城"])
def test_research_aliases_cultural_items_and_quoted_terms_feed_the_name_scan(breakdown, measurements, leak):
    research = {
        "characters": {"范闲": "主角"},
        "character_details": {"范闲": {"aliases": ["小范大人"], "role": "主角"}},
        "cultural_notes": [{"item": "监察院", "explanation": "特务机构"}, {"item": "转世/重生设定", "explanation": "x"}],
        "synopsis": "书名出自《红楼梦》，老太太说「那座城」凶险。",
    }
    breakdown["methods"][0]["rule"] += f"，让{leak}尽早露面"

    errors = check_breakdown(breakdown, measurements, research=research)["errors"]

    assert any(leak in e for e in errors), errors


def test_shared_wording_with_a_fact_names_the_fact_side(breakdown, measurements):
    breakdown["methods"][0]["rule"] += "，开场旁白交代主角身世"

    errors = check_breakdown(breakdown, measurements)["errors"]

    assert any(e.startswith("R6 methods.m1.rule") and "source_facts" in e for e in errors), errors


def test_understanding_index_characters_feed_the_name_scan(work_dir, breakdown, research, tmp_path):
    _append_rule("，学林婉儿的压迫感")(breakdown, research)
    root = work_dir(breakdown, {})
    (root / "understanding_index.json").write_text(json.dumps(
        {"characters": [{"name": "林婉儿", "aliases": ["郡主"], "asr_mentions": ["婉儿"]}]}, ensure_ascii=False),
        encoding="utf-8")

    errors = run_check(root)["errors"]

    assert any("R6" in e and "林婉儿" in e for e in errors), errors


def test_warns_when_understanding_artifacts_describe_another_video(breakdown, measurements, asr_segments):
    evidence = {"status": "AVAILABLE_COARSE", "source_video": {"size": 999, "mtime_ns": 1}}

    report = check_breakdown(breakdown, measurements, asr_segments=asr_segments, asr_evidence=evidence)

    assert report["errors"] == []
    assert any("source_video" in w for w in report["warnings"])


def test_export_rescan_scans_keys_for_leaks():
    corpus = leak_corpus([{"statement": "x", "entities": ["范闲"]}], [], None, None)

    errors = export_errors({"subtitles": {"marks_original": {"范闲你给我站住": 1}}}, corpus)

    assert any("R8 $.subtitles.marks_original.范闲你给我站住" in e for e in errors), errors


def test_export_rescan_rejects_source_only_keys_and_leaks():
    corpus = leak_corpus([{"statement": "x", "entities": ["范闲"]}], [], None, None)
    production = {"methods": [{"rule": "像范闲那样", "evidence": ["f1"]}], "statement": "x"}

    errors = export_errors(production, corpus)

    assert any("R8 $.statement" in e for e in errors)
    assert any("R8 $.methods[0].evidence" in e for e in errors)
    assert any("R8 $.methods[0].rule" in e and "范闲" in e for e in errors)


def test_warnings_flag_numbers_in_rule_missing_applies_when_and_unknown_asr(breakdown, measurements):
    del breakdown["methods"][2]["applies_when"]
    breakdown["methods"][2]["rule"] = "镜头中位控制在 3 秒左右"

    report = check_breakdown(breakdown, measurements, asr_segments=[], asr_evidence=None, research=None)

    assert report["errors"] == []
    assert any("applies_when" in w for w in report["warnings"])
    assert any("数字" in w for w in report["warnings"])
    assert any("UNKNOWN" in w for w in report["warnings"])


EMPTY_CORPUS = {"names": [], "cjk": set(), "latin": set()}


@pytest.mark.parametrize("text, flagged", [
    ("在1:23处切原声", "1:23"),            # no space: CJK counts as \w, so \b would miss it
    ("参考/Users/me/clip.mp4的节奏", "/Users/me/"),
    ("参考C:\\clips\\a.mp4", "C:"),
    ("开场15秒内给出问题", None),          # relative durations are allowed
    ("旁白/原声/音乐整块交替", None),      # prose slashes are not paths
    ("比例保持在 3:2 左右", None),          # ratios are not timecodes
    ("原声/BGM 整块交替", None),             # a one-segment prose slash is not a path
    ("在一分三十秒左右切原声", "一分三十秒"),
    ("这句十分重要，留给原声", None),       # 十分 without 秒 is an adverb, not a clock time
])
def test_leak_scan_handles_text_glued_to_cjk(text, flagged):
    errors = leak_errors(text, "m1.rule", EMPTY_CORPUS)

    if flagged is None:
        assert errors == []
    else:
        assert any(flagged in error for error in errors), errors
