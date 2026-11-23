"""Freeze protocol v3e before the campaign: `protocol-v3e.lock`.

  python -m experiments.v3e.freeze --seal     # writes the lock (once)
  python -m experiments.v3e.freeze --verify
"""
import argparse
import json

from experiments.paths import ROOT
from experiments.v3.coordinator import digest

from .run import DATA

LOCK = ROOT / "protocol-v3e.lock"
REUSED = ["protocol-v3b.md", "protocol-v3c.md",
          "experiments/v3/material/policies/C4/P/config.json",
          "experiments/v3/material/policies/C4/P/report.projection.json",
          "experiments/prompts/c4-system.md", "experiments/v3/live.py", "experiments/v3/tools.py",
          "experiments/v3b/facts.py", "experiments/v3b/hidden.py", "experiments/v3b/analysis.py",
          "experiments/v3b/tools.py", "experiments/v3b/server_c4b.py", "experiments/v3b/worlds.py",
          "experiments/v3/api_records.py", "experiments/simulated/server_c4.py", "experiments/tools.py",
          "experiments/checks.py", "experiments/paths.py", "experiments/caps.py", "experiments/prices.json",
          "experiments/v3c/run.py", "experiments/v3c/analysis.py", "pyproject.toml"]


def manifest() -> dict:
    paths = {ROOT / "protocol-v3e.md", *(ROOT / p for p in REUSED)}
    paths.update((ROOT / "experiments/v3e").glob("*.py"))
    paths.update(DATA.rglob("*.json"))
    paths.update((ROOT / "experiments/v3b/material").glob("C4b-*.json"))
    paths.update((ROOT / "experiments/v3b/material/reference").glob("C4b-*.json"))
    paths.update(p for p in (ROOT / "experiments/v3/material").rglob("C4-*.json"))          # the ordinary tasks
    paths.update(p for p in (ROOT / "src/tracetrim").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {"format": "tracetrim/protocol-v3e@1", "files": {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}}


def verify() -> int:
    expected = json.loads(LOCK.read_text())
    actual = manifest()
    if actual != expected:
        changed = sorted(f for f in set(expected["files"]) | set(actual["files"])
                         if expected["files"].get(f) != actual["files"].get(f))
        raise ValueError(f"v3e frozen materials differ: {changed[:10]}")
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
    print(f"Verified {verify()} frozen v3e files")
