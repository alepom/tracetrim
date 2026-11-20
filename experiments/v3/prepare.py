"""Prepare fixed policies and audit novelty against historical material, locally."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from experiments.paths import ROOT, STUDY_TRACES, PROMPTS
from experiments.v04 import fixed_policy
from .tools import task_data
from .worlds import DATA, CASES, write


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train():
    hashes = {}
    for case in CASES:
        paths = sorted((STUDY_TRACES / case).glob("A*.jsonl"))
        if [p.name for p in paths] != [f"A{i}.jsonl" for i in range(1, 9)]:
            raise ValueError(f"{case}: expected A1 through A8 only")
        for p in paths:
            hashes[f"{case}/{p.name}"] = sha(p)
        for arm, mode in (("P", "all"), ("I", "record")):
            policy = fixed_policy(case, mode)
            policy.config.pop("generated", None)  # wall-clock metadata is not a policy setting
            folder = DATA / "policies" / case / arm
            write(folder / "config.json", policy.config)
            policy.projection.save(folder / policy.config["projection"])
    write(DATA / "training.json", hashes)


def audit(e2_root):
    prompts = list(PROMPTS.glob("*-user.md"))
    assert (PROMPTS / "c4v-user.md") in prompts
    historical = {p.read_text().strip().casefold() for p in prompts}
    signatures = {}
    for case in CASES:
        markers = set()
        for i in range(1, 11):
            task = f"{case}-{i:02}"
            data = task_data(task)
            text = data["prompt"].strip().casefold()
            assert text not in historical
            historical.add(text)
            world = data["world"]
            batch = set()
            if case == "C4":
                for c in world["customers"]:
                    batch.update((c["id"], c["email"]))
                for item in world["invoices"] + world["charges"]:
                    batch.add(item["id"])
                for inv in world["invoices"]:
                    batch.update(line["id"] for line in inv["lines"]["data"])
            else:
                batch.update(str(r["id"]) for r in world["runs"])
                batch.update(world["logs"])
                batch.update(c["error"] for c in data["key"]["root_causes"])
            assert markers.isdisjoint(batch), f"Repeated synthetic evidence in {task}"
            markers.update(batch)
        pattern = re.compile("|".join(map(re.escape, sorted(markers, key=len, reverse=True))))
        paths = sorted((STUDY_TRACES / case).glob("*.jsonl"))
        e2 = sorted((e2_root / case).glob("E2-*.jsonl"))
        if case == "C4" and not e2:
            raise ValueError("The historical E2 billing variants are required")
        assert len(paths) >= 8
        for p in paths + e2 + prompts:
            if pattern.search(p.read_text()):
                raise ValueError(f"Historical evidence overlaps a held-out task in {p.name}")
        signatures[case] = {"historical_runs": len(paths), "additional_e2_runs": len(e2),
                            "distinct_evidence_markers": len(markers)}
    return signatures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e2-traces", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.e2_traces), indent=2))
    train()
