"""Cross-skill contract: video-reference reads the understanding_index.json video-understanding writes.

Each skill runs in its own interpreter (the bundle has no shared code and every skill ships its own
lib.py), so the producer and the consumer are driven as separate subprocesses on the same file.
The reference tests only use hand-written index fixtures; this one feeds it the producer's real shape.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNDERSTANDING_SCRIPTS = ROOT / "skills" / "video-understanding" / "scripts"
REFERENCE_SCRIPTS = ROOT / "skills" / "video-reference" / "scripts"

PRODUCE = """
import json, sys
from pathlib import Path
import consolidate
work = Path(sys.argv[1])
def write(name, payload):
    (work / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
write("vlm_analysis.json", [{"scene_id": 0, "start": 0, "end": 8, "description": "夜路"}])
write("asr_result.json", [{"start": 0.0, "end": 4.0, "text": "五竹叔你等等我"},
                          {"start": 4.0, "end": 8.0, "text": "范闲你给我站住"}])
write("background_research.json", {"characters": {"范闲": "主角"},
                                   "character_details": {"五竹": {"aliases": ["五竹叔"], "role": "护卫"}}})
consolidate.CONFIG["api_key"] = "test-key"
reply = '{"characters":[{"name":"五竹","aliases":[],"asr_mentions":["抱五竹筐突围的蒙眼护卫"]}]}'
consolidate.api_call = lambda payload: {"choices": [{"message": {"content": reply}}]}
consolidate.consolidate_index(work)
"""

CONSUME = """
import json, sys
from reference_check import leak_corpus
with open(sys.argv[1], encoding="utf-8") as fh:
    index = json.load(fh)
print(json.dumps(leak_corpus([], [], {}, {}, index=index)["names"], ensure_ascii=False))
"""


def _run(scripts_dir, code, *args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(scripts_dir)}
    result = subprocess.run([sys.executable, "-c", code, *args], cwd=str(scripts_dir), env=env,
                            capture_output=True)
    stdout, stderr = result.stdout.decode("utf-8"), result.stderr.decode("utf-8")
    assert result.returncode == 0, stderr
    return stdout


def test_reference_name_scan_reads_the_index_consolidate_writes(tmp_path):
    _run(UNDERSTANDING_SCRIPTS, PRODUCE, str(tmp_path))
    index_path = tmp_path / "understanding_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["characters"][0]["asr_mentions"] == ["抱五竹筐突围的蒙眼护卫"]

    names = json.loads(_run(REFERENCE_SCRIPTS, CONSUME, str(index_path)))

    assert {"五竹", "五竹叔", "范闲"} <= set(names), names
