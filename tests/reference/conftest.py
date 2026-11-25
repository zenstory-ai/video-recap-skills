"""Put the video-reference scripts on sys.path; this group runs in its own pytest process."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "video-reference" / "scripts"))
