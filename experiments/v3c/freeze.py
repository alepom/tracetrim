"""Freeze protocol v3c before the campaign: `protocol-v3c.lock`.

  python -m experiments.v3c.freeze --seal     # writes the lock (once)
  python -m experiments.v3c.freeze --verify

The lock holds the SHA-256 of the protocol, every v3c script and the schedule, the files of protocols v3 and v3b
this study reuses (policy P, tasks, keys, reference evidence, scorer, loop, judge, coordinator, servers, checks,
prices, prompt, rubric), the whole library under test (src/tracetrim, skill texts included) and the private
anonymous mapping. Changing any of them after the freeze needs a dated deviation.
"""
import argparse
import json

from experiments.paths import ROOT
from experiments.v3.coordinator import digest

from .judging import LOCK
from .run import DATA, PRIVATE

REUSED = ["protocol-v3.md", "protocol-v3b.md",
          "experiments/v3/material/policies/C4/P/config.json",
          "experiments/v3/material/policies/C4/P/report.projection.json",
          *(f"experiments/v3/material/C4-{i:02}.json" for i in range(1, 11)),
          "experiments/prompts/c4-system.md", "experiments/v3/rubric.md", "experiments/v3/live.py",
          "experiments/v3/tools.py", "experiments/v3/judge_gemini.py", "experiments/v3/coordinator.py",
          "experiments/v3/prices-judge.json", "experiments/v3b/facts.py", "experiments/v3b/hidden.py",
          "experiments/v3b/analysis.py", "experiments/v3b/tools.py", "experiments/v3b/server_c4b.py",
          "experiments/v3b/worlds.py", "experiments/v3/api_records.py", "experiments/simulated/server_c4.py",
          "experiments/tools.py", "experiments/checks.py", "experiments/gemini.py", "experiments/v04.py",
          "experiments/paths.py", "experiments/caps.py", "experiments/prices.json", "pyproject.toml"]


def manifest() -> dict:
    paths = {ROOT / "protocol-v3c.md", *(ROOT / p for p in REUSED)}
    paths.update((ROOT / "experiments/v3c").glob("*.py"))
    paths.update(DATA.rglob("*.json"))
    paths.update((ROOT / "experiments/v3b/material").glob("C4b-*.json"))
    paths.update((ROOT / "experiments/v3b/material/reference").glob("C4b-*.json"))
    paths.update(p for p in (ROOT / "src/tracetrim").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {"format": "tracetrim/protocol-v3c@1",
            "files": {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)},
            "anonymous_mapping_sha256": digest(PRIVATE / "mapping.json")}


def verify() -> int:
    expected = json.loads(LOCK.read_text())
    actual = manifest()
    if actual != expected:
        changed = sorted(f for f in set(expected["files"]) | set(actual["files"])
                         if expected["files"].get(f) != actual["files"].get(f))
        raise ValueError(f"v3c frozen materials differ: {changed[:10]}"
                         + ("" if actual["anonymous_mapping_sha256"] == expected["anonymous_mapping_sha256"]
                            else " and the private mapping"))
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
    print(f"Verified {verify()} frozen v3c files and the private mapping")
