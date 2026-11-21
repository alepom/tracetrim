"""Freeze protocol v3b after calibration and before the campaign: `protocol-v3b.lock`.

  python -m experiments.v3b.freeze --seal     # writes the lock (once)
  python -m experiments.v3b.freeze --verify

The lock holds the SHA-256 of the protocol, every v3b script and material file (tasks, keys,
reference evidence, selection, schedule), the protocol-v3 files this study reuses (policy P,
prompt, rubric, loop, judge, coordinator, server, checks, prices, the library), the private
calibration scores and the private anonymous mapping. Changing any of them after the freeze needs
a dated deviation.
"""
import argparse
import json

from experiments.paths import ROOT
from experiments.v3.coordinator import digest

from .judging import LOCK
from .run import CALIBRATION, PRIVATE
from .worlds import DATA

REUSED = ["experiments/v3/material/policies/C4/P/config.json",
          "experiments/v3/material/policies/C4/P/report.projection.json",
          "experiments/prompts/c4-system.md", "experiments/v3/rubric.md", "experiments/v3/live.py",
          "experiments/v3/judge_gemini.py", "experiments/v3/coordinator.py", "experiments/v3/api_records.py",
          "experiments/v3/prices-judge.json", "experiments/prices.json", "experiments/caps.py",
          "experiments/simulated/server_c4.py", "experiments/tools.py", "experiments/checks.py",
          "experiments/gemini.py", "experiments/v04.py", "experiments/paths.py"]


def manifest() -> dict:
    paths = {ROOT / "protocol-v3b.md", *(ROOT / p for p in REUSED)}
    paths.update((ROOT / "experiments/v3b").glob("*.py"))
    paths.update(DATA.rglob("*.json"))
    paths.update((ROOT / "src/tracetrim").rglob("*.py"))
    return {"format": "tracetrim/protocol-v3b@1",
            "files": {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)},
            "calibration_scores_sha256": digest(CALIBRATION / "scores.json"),
            "calibration_scores_v1_sha256": digest(CALIBRATION / "scores-v1.json"),
            # Sealed only when the plan keeps the Gemini judge (material/selection.json).
            "anonymous_mapping_sha256": digest(PRIVATE / "mapping.json") if (PRIVATE / "mapping.json").exists() else None}


def verify() -> int:
    expected = json.loads(LOCK.read_text())
    if manifest() != expected:
        actual = manifest()
        changed = sorted(f for f in set(expected["files"]) | set(actual["files"])
                         if expected["files"].get(f) != actual["files"].get(f))
        raise ValueError(f"v3b frozen materials differ: {changed[:10]}")
    return len(expected["files"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--seal", action="store_true")
    g.add_argument("--verify", action="store_true")
    a = p.parse_args()
    if a.seal:
        if LOCK.exists():
            raise FileExistsError(LOCK)
        LOCK.write_text(json.dumps(manifest(), indent=2) + "\n")
    print(f"Verified {verify()} frozen v3b files, the calibration scores and the private mapping")
