"""The read-only dashboard over real HTTP: data contracts, degradation and the security gate."""

import hashlib
import http.client
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

import dashboard_data
import dashboard_io
import dashboard_server
import dashboard_templates

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "resource-library"
SAMPLE_MP4 = EXAMPLE / "samples" / "demo-sample" / "demo-sample.mp4"
ASSETS = ROOT / "skills" / "video-recap" / "assets" / "dashboard"
SCRIPT = ROOT / "skills" / "video-recap" / "scripts" / "dashboard_server.py"
SHARED_TOKENS_SHA1 = "ed9dd31d9e6da2589516ef49e472e59839e0a873"


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def _build_root(root: Path) -> dict:
    """A library copy, a project binding into it, a nested cut run and two damaged runs."""
    shutil.copytree(EXAMPLE, root / "library")
    _write(root / "show" / "recap_project.json", {
        "schema": "video-recap.project.v1", "name": "演示项目", "library": "../library",
        "bindings": {"subtitle_style": "clean-white@v1", "packaging": "bottom-bar@v1",
                     "voice": "narrator-demo", "bgm": "missing-bgm"},
    })
    _write(root / "clean" / "recap_project.json", {
        "schema": "video-recap.project.v1", "name": "可用项目", "library": "../library",
        "bindings": {"subtitle_style": "clean-white@v1", "voice": "narrator-demo"},
    })
    ep1 = root / "show" / "ep1"
    _write(ep1 / "recap_run_manifest.json", {"schema_version": 1, "source_video": "/abs/src.mp4",
                                             "audio": {"mode": "narration"}})
    _write(ep1 / "clip_plan.json", [])
    _write(ep1 / "clip_plan_validated.json", {"clips": [
        {"clip_id": 0, "source_start": 10.0, "source_end": 14.0, "output_start": 0.0, "output_end": 4.0,
         "duration": 4.0, "reason": "b01 | 钩子 | 他终于开口"},
        {"clip_id": 1, "source_start": 20.0, "source_end": 23.0, "output_start": 4.0, "output_end": 7.0,
         "duration": 3.0, "reason": "b02 | 反转 | 门被推开"},
    ], "total_duration": 7.0})
    _write(ep1 / "narration.json", [
        {"start": 0.5, "end": 3.0, "narration": "他终于开口了。"},
        {"start": 4.2, "end": 6.5, "narration": "真相比想象更近。", "overlaps_speech": True},
    ])
    _write(ep1 / "timeline.json", {"schema_version": 2, "canvas": {"width": 900, "height": 1600, "fps": 25},
                                   "duration": 7.0, "tracks": [
        {"kind": "video", "name": "video", "clips": [
            {"source_path": "/abs/src.mp4", "source_start": 10.0, "source_end": 14.0, "timeline_start": 0.0, "timeline_end": 4.0},
            {"source_path": "/abs/src.mp4", "source_start": 20.0, "source_end": 23.0, "timeline_start": 4.0, "timeline_end": 7.0}]},
        {"kind": "audio", "name": "narration", "role": "narration", "segments": [
            {"source_path": "/w/_placed_0000.wav", "timeline_start": 0.5, "timeline_end": 3.0, "text": "他终于开口了。"}]},
        {"kind": "audio", "name": "bgm", "role": "bgm", "segments": [
            {"source_path": "/lib/pulse-demo.wav", "timeline_start": 0.0, "timeline_end": 7.0, "gain": 0.2}]},
        {"kind": "text", "name": "subtitle", "segments": [
            {"text": "他终于开口了。", "timeline_start": 0.5, "timeline_end": 3.0}]},
    ]})
    shutil.copy(SAMPLE_MP4, ep1 / "recap_ep1.mp4")
    _write(ep1 / "assembly_manifest.json", {"final_output": str(ep1 / "recap_ep1.mp4")})
    _write(ep1 / "final_qc.json", {"ok": True, "blocker_count": 0, "findings": []})
    _write(ep1 / "golden_eval.json", {"ok": False, "blocker_count": 1, "findings": [
        {"code": "golden-duration", "message": "时长不符", "blocking": True}]})
    _write(ep1 / "assembly_qc.json", {"verdict": "PASS", "blocking_codes": []})
    _write(ep1 / "mimo_qc.json", "{not json")
    bgm = root / "library" / "resources" / "bgm" / "pulse-demo" / "pulse-demo.wav"
    _write(ep1 / "resource_lock.json", {
        "schema": "video-recap.resource-lock.v1", "generated_at": "2026-09-27T00:00:00Z",
        "library": str(root / "library"), "project": {"path": str(root / "show"), "name": "演示项目"},
        "templates": [{"role": "subtitle_style", "id": "clean-white", "version": 1, "status": "adopted"}],
        "resources": [
            {"role": "source_video", "path": "/abs/src.mp4", "size": 1, "mtime_ns": 1, "detail": {}, "library": None},
            {"role": "voice", "path": "/abs/refs/narrator.wav", "size": 1, "mtime_ns": 1,
             "detail": {"provider": "mimo-tts"}, "library": None},
            {"role": "bgm", "path": str(bgm), "size": 8044, "mtime_ns": 1, "detail": {},
             "library": {"id": "pulse-demo", "kind": "bgm", "license": "owned", "consent": None}},
        ],
        "attention": [{"code": "unregistered", "role": "voice", "message": "资源库中没有登记这项资源，授权状态未知"}]})
    _write(ep1 / "sources" / "src_a" / "recap_run_manifest.json", {"schema_version": 1, "source_video": "/abs/a.mp4"})
    _write(root / "broken" / "recap_run_manifest.json", "{oops")
    _write(root / "broken" / "final_qc.json", {"ok": True, "blocker_count": 0, "findings": []})
    _write(root / "badtl" / "recap_run_manifest.json", {"schema_version": 1, "source_video": "/abs/b.mp4"})
    _write(root / "badtl" / "timeline.json", "{bad timeline")
    outside = root.parent / "outside.mp4"
    shutil.copy(SAMPLE_MP4, outside)
    try:
        (root / "escape.mp4").symlink_to(outside)
        symlinked = True
    except (OSError, NotImplementedError):
        symlinked = False
    return {"symlink": symlinked, "outside": outside}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    base = tmp_path_factory.mktemp("dashboard")
    root = base / "root"
    root.mkdir()
    info = _build_root(root)
    server = dashboard_server.make_server(root, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"root": root.resolve(), "port": server.server_address[1], **info}
    server.shutdown()
    server.server_close()


def _request(site, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", site["port"], timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


def _get_json(site, path):
    status, _, body = _request(site, "GET", path)
    assert status == 200, body
    return json.loads(body.decode("utf-8"))


def _api(site, kind, rel):
    return _get_json(site, f"/api/{kind}?path={quote(rel, safe='')}")


def test_overview_nests_runs_and_ranks_what_needs_attention(site):
    overview = _get_json(site, "/api/overview")

    assert overview["counts"] == {"libraries": 1, "projects": 2, "runs": 4,
                                  "resources": 4, "templates": 2, "samples": 1}
    runs = {run["path"]: run for run in overview["runs"]}
    assert runs["show/ep1/sources/src_a"]["parent"] == "show/ep1"
    assert runs["show/ep1"]["project"] == "show"
    assert {p["path"]: p["runs"] for p in overview["projects"]}["show"] == ["show/ep1"]
    assert [item["kind"] for item in overview["next"]] == ["blocked", "waiting", "waiting"]
    assert overview["next_severity"] == "danger"
    kinds = {(item["kind"], item.get("code")) for item in overview["attention"]}
    assert {("licence", "license_unknown"), ("binding", "not_adopted"), ("binding", "missing"),
            ("unparseable", None)} <= kinds
    assert all(item["ask"] and item["href"].startswith("#/") for item in overview["attention"])


def test_severity_is_danger_only_for_what_must_be_fixed(site):
    overview = _get_json(site, "/api/overview")
    severity = {}
    for item in overview["attention"]:
        severity.setdefault(item["kind"], set()).add(item["severity"])

    assert severity == {"blocked": {"danger"}, "binding": {"danger"}, "waiting": {"todo"},
                        "licence": {"warn"}, "unparseable": {"warn"}}
    stages = {s["key"]: s["state"] for s in _api(site, "run", "show/ep1")["stages"]}
    assert (stages["qc"], stages["resources"], stages["narration"]) == ("danger", "warn", "ok")


def test_advisories_alone_do_not_turn_the_next_steps_red(tmp_path):
    shutil.copytree(EXAMPLE, tmp_path / "library")

    overview = dashboard_data.overview(tmp_path.resolve())

    assert [item["kind"] for item in overview["next"]] == ["licence"]
    assert overview["next_severity"] == "warn"


def test_library_detail_joins_records_with_bindings_and_previews(site):
    lib = _api(site, "library", "library")

    resources = {res["id"]: res for res in lib["resources"]}
    assert resources["narrator-demo"]["bound_by"] == [{"path": "clean", "name": "可用项目", "role": "voice"},
                                                      {"path": "show", "name": "演示项目", "role": "voice"}]
    assert resources["narrator-demo"]["license"]["status"] == "unknown"
    assert resources["pulse-demo"]["files"][0]["media"] == {
        "path": "library/resources/bgm/pulse-demo/pulse-demo.wav", "kind": "audio",
        "name": "pulse-demo.wav", "size": 8044}
    assert resources["frame-demo"]["files"][0]["media"]["kind"] == "image"
    templates = {tpl["ref"]: tpl for tpl in lib["templates"]}
    clean = templates["clean-white@v1"]
    assert clean["adoption"]["scope"] and clean["params"]["size_px"]["value"] == 52
    assert clean["bound_by"][0]["role"] == "subtitle_style"
    assert clean["samples"][0]["file"]["path"] == "library/samples/demo-sample/demo-sample.mp4"
    assert lib["samples"][0]["demonstrates"] == ["字幕带位置", "画布尺寸"]


def test_templates_come_with_labelled_params_and_a_schematic_geometry(site):
    templates = {tpl["ref"]: tpl for tpl in _api(site, "library", "library")["templates"]}
    clean, bar = templates["clean-white@v1"], templates["bottom-bar@v1"]

    assert [(r["label"], r["value"], r["unit"], r["provenance_label"]) for r in clean["rows"]] == [
        ("字体", "Arial", "", ""), ("字号", "52", "px", "指定"), ("每行字数", "15", "字", "实测"),
        ("描边", "3", "px", "指定"), ("字幕带", "y 1280–1440", "px", "实测")]
    preview = clean["preview"]
    assert preview["band"] == {"top": 80.0, "height": 10.0}
    assert preview["margin_v"] == 160
    assert (len(preview["line"]["text"]), preview["line"]["bottom"], preview["line"]["width_px"],
            preview["line"]["usable_px"]) == (15, 10.0, 780, 820)
    layer = bar["preview"]["layers"][0]
    assert layer["media"]["path"] == "library/resources/image/frame-demo/frame-demo.png"
    assert layer["box"] == {"left": 0.0, "top": 0.0, "width": 100.0, "height": 100.0}
    assert bar["preview"]["safe"]["left"] == pytest.approx(40 / 9, abs=1e-3)
    assert [r["label"] for r in bar["rows"]] == ["图层", "安全区"]


@pytest.mark.parametrize("value, expected", [("&H00FFFFFF", "#FFFFFF"), ("&H0000A0FF", "#FFA000"),
                                             ("&H102030", "#302010"), ("white", None)])
def test_ass_colours_become_swatches(value, expected):
    assert dashboard_templates.ass_to_hex(value) == expected


def test_project_detail_reports_how_each_binding_resolves(site):
    project = _api(site, "project", "show")

    rows = {row["role"]: row for row in project["bindings"]}
    assert {role: (row["status"], row["severity"]) for role, row in rows.items()} == {
        "subtitle_style": ("ok", "ok"), "packaging": ("not_adopted", "danger"),
        "voice": ("ok", "ok"), "bgm": ("missing", "danger")}
    assert (rows["subtitle_style"]["target"]["title"], rows["subtitle_style"]["target"]["status"]) == ("白字细描边（演示）", "adopted")
    assert (rows["voice"]["target"]["title"], rows["voice"]["target"]["license"]) == ("MiMo 默认解说音色", "unknown")
    assert rows["packaging"]["target"]["status"] == "draft"
    assert project["application"]["ok"] is False and "draft" in project["application"]["message"]
    assert project["library"]["rel"] == "library"
    assert {run["path"] for run in project["runs"]} == {"show/ep1", "show/ep1/sources/src_a"}


def test_project_application_shows_what_recap_would_hand_the_stages(site, monkeypatch):
    monkeypatch.setenv("SUBTITLE_FONT_SIZE", "99")  # ambient env must not change the view

    application = _api(site, "project", "clean")["application"]

    assert application["ok"] is True
    assert {k: application["env"][k] for k in ("SUBTITLE_FONT_SIZE", "SUBTITLE_MAX_CHARS", "SUBTITLE_MARGIN_V")} == {
        "SUBTITLE_FONT_SIZE": "52", "SUBTITLE_MAX_CHARS": "15", "SUBTITLE_MARGIN_V": "160"}
    assert application["arg_updates"] == {"tts_provider": "mimo-tts", "mimo_tts_voice": "冰糖"}


def test_run_detail_parses_each_stage_on_the_server(site):
    run = _api(site, "run", "show/ep1")

    assert [s["key"] for s in run["stages"]] == ["home", "understanding", "cut", "narration", "film", "qc", "resources"]
    views = run["views"]
    assert [(c["output_start"], c["output_end"], c["beat"]) for c in views["cut"]["clips"]] == [
        (0.0, 4.0, "b01"), (4.0, 7.0, "b02")]
    assert [s["text"] for s in views["narration"]["segments"]] == ["他终于开口了。", "真相比想象更近。"]
    assert views["film"]["video"]["path"] == "show/ep1/recap_ep1.mp4"
    assert {k: len(v) for k, v in views["film"]["timeline"]["lanes"].items()} == {
        "video": 2, "narration": 1, "bgm": 1, "subtitles": 1}
    assert {c["file"]: c["level"] for c in run["qc"]} == {
        "final_qc.json": "ok", "golden_eval.json": "error", "assembly_qc.json": "ok", "mimo_qc.json": "unparseable"}
    lock = views["resources"]
    assert lock["attention"][0]["code"] == "unregistered"
    assert [(r["role"], r["registry"], r["name"]) for r in lock["resources"]] == [
        ("source_video", "material", "src.mp4"), ("voice", "unregistered", "narrator.wav"),
        ("bgm", "library", "pulse-demo.wav")]
    assert lock["resources"][1]["dir"] == "/abs/refs"
    assert [child["path"] for child in run["children"]] == ["show/ep1/sources/src_a"]


@pytest.mark.parametrize(
    "rel, check",
    [
        pytest.param("show/ep1", lambda r: r["qc"][3]["text"].startswith("无法解析")
                     and r["views"]["narration"]["status"] == "ok", id="malformed_qc_card"),
        pytest.param("broken", lambda r: r["state_error"].startswith("无法解析运行状态")
                     and r["qc"][0]["level"] == "ok", id="malformed_run_manifest"),
        pytest.param("badtl", lambda r: r["views"]["film"]["timeline"]["status"] == "unparseable"
                     and r["views"]["film"]["timeline"]["raw"] == "{bad timeline"
                     and r["state"]["mode"] == "full", id="malformed_timeline_falls_back_to_raw"),
    ],
)
def test_an_unparseable_artifact_degrades_only_its_own_view(site, rel, check):
    assert check(_api(site, "run", rel))


@pytest.mark.parametrize("query, group, target", [
    ("真相", "旁白", "#/run/show%2Fep1/narration?i=1"),
    ("门被推开", "剪辑", "#/run/show%2Fep1/cut?clip=1"),
    ("PULSE", "资源", "#/library/library/resources?id=pulse-demo"),
])
def test_search_finds_narration_clip_reasons_and_library_entries(site, query, group, target):
    hits = _get_json(site, f"/api/search?q={quote(query)}")["hits"]

    assert (group, target) in {(hit["group"], hit["href"]) for hit in hits}


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_every_method_but_get_and_head_is_refused(site, method):
    status, headers, _ = _request(site, method, "/api/overview", body=b"{}")

    assert status == 405
    assert headers["allow"] == "GET, HEAD"


@pytest.mark.parametrize("headers", [
    pytest.param({"Host": "evil.example"}, id="foreign_host"),
    pytest.param({"Host": "127.0.0.1:1"}, id="wrong_port"),
    pytest.param({"Origin": "http://evil.example"}, id="foreign_origin"),
])
def test_foreign_host_or_origin_is_forbidden(site, headers):
    conn = http.client.HTTPConnection("127.0.0.1", site["port"], timeout=10)
    try:
        conn.putrequest("GET", "/api/overview", skip_host="Host" in headers)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        assert conn.getresponse().status == 403
    finally:
        conn.close()


@pytest.mark.parametrize("path, expected", [
    pytest.param("/api/media?path=..%2Foutside.mp4", 403, id="dotdot"),
    pytest.param("/api/run?path=show%2F..%2F..", 403, id="dotdot_in_api"),
    pytest.param("ABSOLUTE", 403, id="absolute"),
    pytest.param("/api/media?path=escape.mp4", 403, id="symlink_outside_root"),
    pytest.param("/api/media?path=library%2Flibrary.json", 403, id="not_a_media_type"),
    pytest.param("/api/library?path=show", 404, id="not_a_library"),
    pytest.param("/api/run", 400, id="missing_path"),
])
def test_paths_outside_the_contract_are_refused(site, path, expected):
    if path == "ABSOLUTE":
        path = f"/api/media?path={quote(str(site['outside']), safe='')}"
    if "escape" in path and not site["symlink"]:
        pytest.skip("symlinks not permitted")

    assert _request(site, "GET", path)[0] == expected


def test_range_request_returns_partial_content(site):
    size = SAMPLE_MP4.stat().st_size
    status, headers, body = _request(site, "GET", "/api/media?path=show%2Fep1%2Frecap_ep1.mp4",
                                     headers={"Range": "bytes=100-199"})

    assert status == 206
    assert headers["content-range"] == f"bytes 100-199/{size}"
    assert body == SAMPLE_MP4.read_bytes()[100:200]


def test_index_and_every_asset_it_references_are_served_with_security_headers(site):
    status, headers, body = _request(site, "GET", "/")
    assert status == 200
    html = body.decode("utf-8")
    assert '<html lang="zh-CN">' in html
    refs = re.findall(r'(?:src|href)="(/[^"]*)"', html)
    assert {"/tokens.css", "/styles.css", "/app.js", "/views.js"} <= set(refs)
    for ref in ["/", *refs]:
        status, headers, _ = _request(site, "GET", ref)
        assert status == 200, ref
        assert "default-src 'self'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"


def test_styles_use_only_the_shared_zenstory_tokens():
    tokens = (ASSETS / "tokens.css").read_bytes()
    assert hashlib.sha1(tokens).hexdigest() == SHARED_TOKENS_SHA1
    styles = re.sub(r"/\*.*?\*/", "", (ASSETS / "styles.css").read_text(encoding="utf-8"), flags=re.S)
    decls = [(prop.strip(), re.sub(r"var\([^)]*\)", "", value))
             for block in re.findall(r"\{([^{}]*)\}", styles)
             for prop, _, value in (decl.partition(":") for decl in block.split(";") if ":" in decl)]
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(|\b(?:white|black)\b")
    family = re.compile(r"[\"',]|\b(?:serif|sans-serif|monospace|system-ui)\b")
    offenders = [f"{prop}:{value}" for prop, value in decls
                 if colour.search(value) or (prop in {"font", "font-family"} and family.search(value))]
    assert offenders == []
    defined = set(re.findall(r"(--zs-[a-z0-9-]+)\s*:", tokens.decode("utf-8")))
    assert set(re.findall(r"var\((--zs-[a-z0-9-]+)", styles)) <= defined


def test_serving_and_scanning_never_modify_the_root(site):
    root = site["root"]
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*")}
    for path in ["/", "/api/overview", "/api/search?q=%E7%9C%9F", "/api/library?path=library",
                 "/api/project?path=show", "/api/run?path=show%2Fep1", "/api/run?path=broken",
                 "/api/media?path=show%2Fep1%2Frecap_ep1.mp4"]:
        _request(site, "GET", path)
        _request(site, "HEAD", path)
    _request(site, "POST", "/api/overview", body=b"x")

    assert {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*")} == before


def _nest(base: Path, depth: int) -> Path:
    path = base.joinpath(*[f"d{i}" for i in range(depth)])
    path.mkdir(parents=True)
    return path


@pytest.mark.parametrize("layout, found, warned", [
    pytest.param("deep", [], "层级", id="depth_limit"),
    pytest.param("wide", None, "截断", id="directory_limit"),
    pytest.param("skipped", [], None, id="hidden_and_dependency_dirs"),
    pytest.param("symlinked", [], None, id="symlinked_dir"),
])
def test_discovery_is_bounded_and_skips_what_it_must(tmp_path, layout, found, warned):
    root = tmp_path / "root"
    root.mkdir()
    if layout == "deep":
        _write(_nest(root, dashboard_io.MAX_DEPTH + 1) / "recap_run_manifest.json", {})
    elif layout == "wide":
        for i in range(dashboard_io.MAX_DIRS + 1):
            (root / f"d{i:05d}").mkdir()
    elif layout == "skipped":
        for name in (".hidden", "node_modules", "__pycache__"):
            _write(root / name / "recap_run_manifest.json", {})
    else:
        target = tmp_path / "elsewhere"
        _write(target / "recap_run_manifest.json", {})
        try:
            (root / "link").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not permitted")

    result = dashboard_io.discover(root)

    if found is not None:
        assert result["runs"] == found
    assert any(warned in w for w in result["warnings"]) if warned else result["warnings"] == []


def test_cli_refuses_a_non_loopback_host(tmp_path):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "--root", str(tmp_path), "--host", "0.0.0.0"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )

    assert result.returncode != 0
    assert "回环" in result.stderr


def test_cross_site_subresource_requests_are_refused_and_marked_same_origin(site):
    status, _, _ = _request(site, "GET", "/api/overview", {"Host": f"127.0.0.1:{site['port']}",
                                                           "Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, headers, _ = _request(site, "GET", "/api/overview", {"Sec-Fetch-Site": "same-origin"})
    assert status == 200
    assert headers["cross-origin-resource-policy"] == "same-origin"


def _run_with(tmp_path, name, make):
    root = tmp_path / "root"
    run = root / "ep"
    _write(run / "recap_run_manifest.json", {"schema_version": 1, "source_video": "/abs/src.mp4"})
    make(run / name, tmp_path)
    return root.resolve()


def test_symlinked_run_artifact_never_exposes_a_file_outside_root(tmp_path):
    def make(path, base):
        secret = base / "secret.txt"
        secret.write_text("SECRET_TOKEN=sk-live-abcdef\n", encoding="utf-8")
        try:
            path.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not permitted")
    root = _run_with(tmp_path, "narration.json", make)

    detail = dashboard_data.run_detail(root, "ep")

    assert "SECRET_TOKEN" not in json.dumps(detail, ensure_ascii=False)


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="needs a FIFO")
def test_a_fifo_artifact_is_reported_instead_of_blocking(tmp_path):
    root = _run_with(tmp_path, "assembly_manifest.json", lambda path, _: __import__("os").mkfifo(path))
    result = {}
    worker = threading.Thread(target=lambda: result.update(overview=dashboard_data.overview(root)), daemon=True)

    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "overview blocked on a FIFO"
    assert result["overview"]["counts"]["runs"] == 1


def test_hand_written_record_fields_are_normalised_before_they_reach_the_page(tmp_path):
    lib = tmp_path / "library"
    shutil.copytree(EXAMPLE, lib)
    for rel, change in (
        ("samples/demo-sample/sample.json", {"canvas": {"width": "<meta http-equiv=refresh>", "height": 1}}),
        ("resources/bgm/pulse-demo/resource.json", {"tags": None}),
    ):
        record = lib / rel
        data = json.loads(record.read_text(encoding="utf-8"))
        data.update(change)
        record.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    detail = dashboard_data.library_detail(tmp_path.resolve(), "library")

    assert detail["samples"][0]["canvas"] is None
    assert {r["id"]: r["tags"] for r in detail["resources"]}["pulse-demo"] == []
