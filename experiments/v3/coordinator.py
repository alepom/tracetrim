"""Coordinator-only commands. Give each judging session its own packet directory."""
import argparse
import hashlib
import json
import os
import re
import secrets
import tempfile

from experiments.paths import ROOT, PROMPTS
from .tools import task_data
from .worlds import DATA, write

RUBRIC = ROOT / "experiments/v3/rubric.md"
PRIVATE = ROOT / "out/v3"
LOCK = ROOT / "protocol-v3.lock"
DIMS = ("correctness", "completeness", "action")
GROUP = r"[\w\[\].*-]+(?: \(\d+ fields?\))?"
TRAILER = re.compile(r"\[trimmed\][ \t]+call_id=c[0-9a-f]{8} hidden: "
                     + GROUP + r"(?:, " + GROUP + r")*(?: \(\+\d+ more groups\))?", re.I)
CUE = re.compile(r"\[trimmed\]|\bcall_id\s*=\s*[\"']?[\w.-]+[\"']?|\b(?:tracetrim[_ .-])?expand\b|\bc[0-9a-f]{8}\b", re.I)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text())


def schedule():
    return load(DATA / "schedule.json")


def seal(directory):
    directory.mkdir(parents=True, exist_ok=True)
    mapping = directory / "mapping.json"
    runs = schedule()
    if len(runs) != 120 or len(set(runs)) != 120:
        raise ValueError("Incomplete schedule")
    # Atomic exclusive publication. A retry or concurrent process cannot replace the map.
    with tempfile.NamedTemporaryFile(dir=directory, mode="w", delete=False) as tmp:
        try:
            passes = [{slot: secrets.token_hex(16) for slot in runs} for _ in range(3)]
            json.dump(passes, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.link(tmp.name, mapping)
        finally:
            os.unlink(tmp.name)
    return digest(mapping)


def scrub(text):
    # For a JSON answer, clean values individually so a trailer cannot swallow later fields.
    def clean(s):
        return CUE.sub("", TRAILER.sub("", s))
    def visit(x):
        if isinstance(x, str):
            return clean(x)
        if isinstance(x, list):
            return [visit(v) for v in x]
        if isinstance(x, dict):
            return {clean(k): visit(v) for k, v in x.items()}
        return x
    try:
        obj = json.loads(text)
    except ValueError:
        result = clean(text)
        return result, result != text
    obj2 = visit(obj)
    return json.dumps(obj2, ensure_ascii=False, indent=2), obj2 != obj


def records(mapping, traces, reference, missing):
    cleaned = {}
    hints = 0
    for slot in mapping[0]:
        task, rep, arm = slot.split("/")
        path = traces / task / rep / f"{arm}.jsonl"
        if slot in missing:
            if path.exists():
                raise ValueError("Missing slot also has a final trace")
            continue
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        answers = [e["text"] for e in events if e["kind"] == "message"]
        answer, clue = scrub(answers[-1] if answers else "")
        hints += clue
        data = task_data(task)
        # Canonical original tool returns from exhaustive reference queries of this
        # immutable world. Every arm gets the same evidence, even if the agent omitted a call.
        cleaned[slot] = {"task": (PROMPTS / f"{task[:2].lower()}-system.md").read_text() + "\n" + data["prompt"],
                         "rubric": RUBRIC.read_text(), "answer": answer,
                         "evidence": load(reference / f"{task}.json")}
    return cleaned, hints


def packets(directory, traces, reference, expected, passes=(0, 1), slots=None):
    mapping_path = directory / "mapping.json"
    if digest(mapping_path) != expected:
        raise ValueError("Anonymous mapping digest mismatch")
    mapping = load(mapping_path)
    missing = load(directory / "missing.json") if (directory / "missing.json").exists() else {}
    if not set(missing) <= set(mapping[0]) or any(not reason for reason in missing.values()):
        raise ValueError("Unexpected missing slot or empty reason")
    rows, hints = records(mapping, traces, reference, missing)
    for turn in passes:
        folder = directory / f"pass-{turn+1}"
        folder.mkdir(exist_ok=False)
        for slot in sorted(slots if slots is not None else rows, key=mapping[turn].__getitem__):
            opaque = mapping[turn][slot]
            write(folder / f"{opaque}.json", {"id": opaque, **rows[slot]})
    return hints


def ratings(path, ids):
    # Rows are anonymous id -> three integer scores; reject missing and duplicate IDs.
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate score key")
        return result
    result = json.loads(path.read_text(), object_pairs_hook=unique)
    if set(result) != set(ids):
        raise ValueError("Unexpected or missing score IDs")
    for row in result.values():
        if set(row) != set(DIMS) or any(type(s) is not int or not 0 <= s <= 4 for s in row.values()):
            raise ValueError("Invalid score dimensions or values")
    return result


def score_inputs(directory, expected):
    mapping_path = directory / "mapping.json"
    if digest(mapping_path) != expected:
        raise ValueError("Anonymous mapping digest mismatch")
    maps = load(mapping_path)
    missing = load(directory / "missing.json") if (directory / "missing.json").exists() else {}
    if not set(missing) <= set(maps[0]) or any(not reason for reason in missing.values()):
        raise ValueError("Unexpected missing slot or empty reason")
    active = [slot for slot in maps[0] if slot not in missing]
    scores = [ratings(directory / f"scores-{i+1}.json", [maps[i][s] for s in active]) for i in (0, 1)]
    hashes = [digest(directory / f"scores-{i+1}.json") for i in (0, 1)]
    disputes = {}
    for slot in active:
        dims = [k for k in DIMS if abs(scores[0][maps[0][slot]][k] - scores[1][maps[1][slot]][k]) >= 2]
        if dims:
            disputes[slot] = dims
    return maps, active, scores, hashes, disputes


def adjudicate(directory, traces, reference, expected):
    _, _, _, hashes, disputes = score_inputs(directory, expected)
    packets(directory, traces, reference, expected, passes=(2,), slots=disputes)
    write(directory / "scores-receipt.json", hashes)
    return len(disputes)


def finish(directory, expected):
    maps, active, scores, hashes, disputes = score_inputs(directory, expected)
    if load(directory / "scores-receipt.json") != hashes:
        raise ValueError("A locked first- or second-pass score sheet changed")
    third = ratings(directory / "scores-3.json", [maps[2][s] for s in disputes]) if disputes else {}
    values = {}
    agreement = {}
    for d in DIMS:
        pairs = [(scores[0][maps[0][s]][d], scores[1][maps[1][s]][d]) for s in active]
        if not pairs:
            raise ValueError("No scored answers")
        n = len(pairs)
        observed = sum((a-b)**2 for a, b in pairs) / n
        expected_distance = sum((a-b)**2 for a, _ in pairs for _, b in pairs) / n**2
        agreement[d] = {"exact": sum(a == b for a, b in pairs) / n,
                        "within_one": sum(abs(a-b) <= 1 for a, b in pairs) / n,
                        "quadratic_kappa": 1 - observed / expected_distance if expected_distance else None}
        for s, (a, b) in zip(active, pairs):
            values.setdefault(s, {})[d] = third[maps[2][s]][d] if d in disputes.get(s, []) else (a+b)/2
    write(directory / "judged.json", {"scores": values, "agreement": agreement, "adjudicated": len(disputes),
                                      "score_sheet_sha256": hashes + ([digest(directory / "scores-3.json")] if disputes else [])})
    return len(values)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["seal", "pack", "adjudicate", "finish"])
    args = p.parse_args()
    if args.command == "seal":
        print(seal(PRIVATE))
    else:
        lock = load(LOCK)
        # Check all frozen materials, including the canonical reference evidence.
        from .freeze import verify
        verify()
        if args.command == "finish":
            print("Judged answers:", finish(PRIVATE, lock["anonymous_mapping_sha256"]))
            raise SystemExit(0)
        operation = packets if args.command == "pack" else adjudicate
        n = operation(PRIVATE, PRIVATE / "traces", DATA / "reference", lock["anonymous_mapping_sha256"])
        print("Answers with cues" if args.command == "pack" else "Answers for third pass", n)
