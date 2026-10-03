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
from consolidate import _apply_deterministic_asr_research_fallback
research = {"characters": {"范闲": "主角"},
            "character_details": {"五竹": {"aliases": ["五竹叔"], "role": "护卫"}}}
asr = [{"start": 0.0, "end": 4.0, "text": "五竹叔你等等我"},
       {"start": 4.0, "end": 8.0, "text": "范闲你给我站住"}]
model_index = {"characters": [{"name": "五竹", "aliases": [], "asr_mentions": ["抱五竹筐突围的蒙眼护卫"]}]}
index = _apply_deterministic_asr_research_fallback(model_index, asr_result=asr, background_research=research)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(index, fh, ensure_ascii=False)
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
    index_path = tmp_path / "understanding_index.json"
    _run(UNDERSTANDING_SCRIPTS, PRODUCE, str(index_path))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    mentions = {c["name"]: c["asr_mentions"] for c in index["characters"]}
    assert any(isinstance(m, dict) and m.get("matched_aliases") for m in mentions["五竹"]), mentions

    names = json.loads(_run(REFERENCE_SCRIPTS, CONSUME, str(index_path)))

    assert {"五竹", "五竹叔", "范闲"} <= set(names), names
