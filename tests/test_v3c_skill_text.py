"""The skill text that Study 4, Part C measured is the one the library ships.

The paper says that release 1.4.0 serves, as its default, the text frozen by protocol-v3c.lock: the skill files keep
the hashes in the lock, and the two descriptions of `expand` keep the text of the freeze commit (2ba6833).
"""
import hashlib
import json
from pathlib import Path

from tracetrim import engine, skill

ROOT = Path(__file__).resolve().parents[1]
# sha256 of engine._EXPAND_V1 and engine._EXPAND_V2 at 2ba6833, the commit that froze protocol v3c.
EXPAND_V1 = "161711fa5900aa5a2d783dc12f41bff4089dbef10c5af1809c6650de882d8f0c"
EXPAND_V2 = "8a0ce1b038aeb13eebac2cbd4cc7ed822d6e6268cbae19636e082d1897b49351"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_skill_files_keep_the_hashes_frozen_for_part_c():
    files = json.loads((ROOT / "protocol-v3c.lock").read_text())["files"]
    locked = {k: v for k, v in files.items() if k.startswith("src/tracetrim/skill")}
    assert "src/tracetrim/skill.py" in locked and len(locked) >= 5
    assert {k: sha((ROOT / k).read_bytes()) for k in locked} == locked


def test_expand_texts_are_those_of_the_freeze():
    assert sha(engine._EXPAND_V1.encode()) == EXPAND_V1
    assert sha(engine._EXPAND_V2.encode()) == EXPAND_V2


def test_version_two_is_the_default():
    assert skill.VERSION == "2"
    assert engine.EXPAND["description"] == engine._EXPAND_V2
