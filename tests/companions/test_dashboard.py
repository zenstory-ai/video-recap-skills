"""Tests for skills/video-recap/scripts/dashboard.py."""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import dashboard


def _setup_workspace(root: Path, sample_timeline: dict) -> tuple[Path, Path]:
    run_dir = root / "runs" / "ep01"
    run_dir.mkdir(parents=True)
    (run_dir / "timeline.json").write_text(json.dumps(sample_timeline), encoding="utf-8")
    (run_dir / "output.mp4").write_bytes(b"0123456789ABCDEF")
    (run_dir / "check.json").write_text(
        json.dumps({"schema_version": 1, "media": "output.mp4", "ok": True,
                    "checks": {"container_valid": {"status": "pass", "detail": "ok"}}, "limitations": []}),
        encoding="utf-8",
    )
    (run_dir / "EDIT_PLAN.md").write_text("# Edit Plan\n</script><script>alert(1)</script>\n", encoding="utf-8")
    (run_dir / "BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    (run_dir / "MEDIA_NOTES.md").write_text("# Media Notes\n", encoding="utf-8")

    lib_dir = root / "lib"
    (lib_dir / "assets").mkdir(parents=True)
    (lib_dir / "assets" / "beat.wav").write_bytes(b"RIFF_AUDIO_BYTES")
    (lib_dir / "materials" / "ep01-123").mkdir(parents=True)
    (lib_dir / "materials" / "ep01-123" / "material.md").write_text("# Material\n", encoding="utf-8")
    (lib_dir / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "name": "Studio Lib", "assets": [
            {"id": "beat1", "kind": "bgm", "path": "assets/beat.wav", "tags": ["upbeat"], "license": "cc0"}
        ]}),
        encoding="utf-8",
    )
    return run_dir, lib_dir


def test_build_review_html_escapes_script_and_embeds_payload(tmp_path: Path, sample_timeline):
    run_dir, _ = _setup_workspace(tmp_path, sample_timeline)
    out_html = dashboard.build_review_html(run_dir)
    assert out_html == run_dir / "review.html"
    text = out_html.read_text(encoding="utf-8")
    assert "__EMBEDDED_DATA__" not in text
    assert "</script><script>alert(1)</script>" not in text
    assert "<\\/script>" in text
    assert "Demo Cut" in text
    assert "output.mp4" in text
    assert "EDIT_PLAN.md" in text and "BRIEF.md" in text and "MEDIA_NOTES.md" in text


def test_build_review_html_discovers_out_final_mp4(tmp_path: Path, sample_timeline):
    run_dir = tmp_path / "run_out"
    (run_dir / "out").mkdir(parents=True)
    (run_dir / "timeline.json").write_text(json.dumps(sample_timeline), encoding="utf-8")
    (run_dir / "out" / "final.mp4").write_bytes(b"MP4BYTES")
    html = dashboard.build_review_html(run_dir).read_text(encoding="utf-8")
    assert "out/final.mp4" in html


def test_serve_api_range_streaming_and_security_guards(tmp_path: Path, sample_timeline):
    _setup_workspace(tmp_path, sample_timeline)
    outside = tmp_path.parent / "outside_secret.mp4"
    outside.write_bytes(b"SECRET")
    link = tmp_path / "runs" / "ep01" / "escape.mp4"
    symlink_created = False
    try:
        link.symlink_to(outside)
        symlink_created = True
    except OSError:
        pass

    server = dashboard.make_server(tmp_path, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def req(method: str, path: str, headers: dict | None = None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
            conn.request(method, path, headers=hdrs)
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            return resp.status, dict(resp.getheaders()), body

        # Overview discovers run & library
        st, hdrs, body = req("GET", "/api/overview")
        assert st == 200
        assert "default-src 'self'" in hdrs.get("Content-Security-Policy", "")
        assert hdrs.get("X-Content-Type-Options") == "nosniff"
        ov = json.loads(body.decode("utf-8"))
        assert len(ov["runs"]) == 1 and ov["runs"][0]["path"] == "runs/ep01"
        assert len(ov["libraries"]) == 1 and ov["libraries"][0]["path"] == "lib"

        # Run detail & Library detail (including audio preview URL)
        st, _, body = req("GET", "/api/run?path=runs/ep01")
        assert st == 200
        run_data = json.loads(body.decode("utf-8"))
        assert run_data["video_url"] == "runs/ep01/output.mp4"
        assert len(run_data["clips"]) == 2

        st, _, body = req("GET", "/api/library?path=lib")
        assert st == 200
        lib_data = json.loads(body.decode("utf-8"))
        assert lib_data["assets"][0]["media_url"] == "lib/assets/beat.wav"
        assert lib_data["assets"][0]["media_kind"] == "audio"
        assert lib_data["materials"][0]["id"] == "ep01-123"

        # Range streaming (206), invalid range (416), HEAD (200 empty body)
        st, hdrs, body = req("GET", "/api/media?path=runs/ep01/output.mp4", {"Range": "bytes=2-5"})
        assert st == 206
        assert body == b"2345"
        assert hdrs.get("Content-Range") == "bytes 2-5/16"

        st, _, _ = req("GET", "/api/media?path=runs/ep01/output.mp4", {"Range": "bytes=99-120"})
        assert st == 416

        st, _, body = req("HEAD", "/api/media?path=runs/ep01/output.mp4")
        assert st == 200 and body == b""

        # Read-only 405, DNS-rebinding 403, path-jail & symlink escape 403
        st, hdrs, _ = req("POST", "/api/overview")
        assert st == 405 and "GET" in hdrs.get("Allow", "")

        st, _, _ = req("GET", "/api/overview", {"Host": "evil.example:80"})
        assert st == 403
        st, _, _ = req("GET", "/api/overview", {"Origin": "http://evil.example"})
        assert st == 403
        st, _, _ = req("GET", "/api/overview", {"Sec-Fetch-Site": "cross-site"})
        assert st == 403
        st, _, _ = req("GET", "/api/media?path=../outside_secret.mp4")
        assert st == 403
        if symlink_created:
            st, _, _ = req("GET", "/api/media?path=runs/ep01/escape.mp4")
            assert st == 403
        st, _, _ = req("GET", "/api/media?path=runs/ep01/timeline.json")
        assert st == 403
    finally:
        server.shutdown()
        server.server_close()
