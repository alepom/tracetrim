"""A second, independent blind reader for a sample of protocol v3b's answers, after the Gemini judge.

  python -m experiments.v3b.second_reader prepare DIR   # DIR/cards/*.json and DIR/ISTRUZIONI.md; private map in out/v3b
  python -m experiments.v3b.second_reader compare DIR   # after DIR/scores.json: results/v3b/second_reader.json

The sample: for each of the 12 tasks, one answer of R and one of P, the repetition drawn with seed 130441 (24
cards). Each card is built as the judge's (the task with its system instructions, the full v3 rubric, the answer
with arm cues scrubbed, the reference evidence), under a new random id. The map from ids to answers stays in
out/v3b/second-reader-map.json, git-ignored; the reader never sees it, nor the arm, the traces or the judge's
scores. The comparison reports the agreement of the reader with the judge's combined scores on the same answers
(exact, within one point, quadratic-weighted kappa per dimension) and P minus R on the sample by each.
Added after protocol v3b's results, at the author's request (REPORT-14c): a check of the judge, not an outcome.
"""
from __future__ import annotations

import argparse
import json
import random
import secrets
from pathlib import Path
from statistics import mean

from experiments.paths import PROMPTS, ROOT
from experiments.v3 import coordinator

from .run import PRIVATE, campaign_slots, where
from .tools import task_data
from .worlds import DATA

SEED = 130441
MAP = PRIVATE / "second-reader-map.json"
DIMS = coordinator.DIMS


def sample() -> list[str]:
    rng = random.Random(SEED)
    tasks = sorted({s.split("/")[0] for s in campaign_slots()})
    reps = sorted({int(s.split("/")[1]) for s in campaign_slots()})
    return [f"{t}/{rng.choice(reps)}/{arm}" for t in tasks for arm in ("R", "P")]


def prepare(directory: Path) -> int:
    if MAP.exists():
        raise FileExistsError(MAP)
    camp = where(PRIVATE)
    system = (PROMPTS / "c4-system.md").read_text()
    rubric = coordinator.RUBRIC.read_text()
    chosen = sample()
    random.Random(SEED + 1).shuffle(chosen)
    ids = {slot: secrets.token_hex(16) for slot in chosen}
    cards = directory / "cards"
    cards.mkdir(parents=True, exist_ok=False)
    for slot in chosen:
        task = slot.split("/")[0]
        events = [json.loads(x) for x in camp.trace(slot).read_text().splitlines() if x.strip()]
        answers = [e["text"] for e in events if e["kind"] == "message"]
        answer, _ = coordinator.scrub(answers[-1] if answers else "")
        card = {"id": ids[slot], "task": system + "\n" + task_data(task)["prompt"], "rubric": rubric,
                "answer": answer, "evidence": coordinator.load(DATA / "reference" / f"{task}.json")}
        (cards / f"{ids[slot]}.json").write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n")
    MAP.write_text(json.dumps(ids, indent=1) + "\n")
    return len(chosen)


def kappa(pairs):
    n = len(pairs)
    observed = sum((a - b) ** 2 for a, b in pairs) / n
    expected = sum((a - b) ** 2 for a, _ in pairs for _, b in pairs) / n ** 2
    return 1 - observed / expected if expected else None


def compare(directory: Path) -> dict:
    ids = json.loads(MAP.read_text())
    reader = coordinator.ratings(directory / "scores.json", list(ids.values()))
    judged = json.loads((PRIVATE / "judged.json").read_text())["scores"]
    agreement = {}
    for d in DIMS:
        # The judge's combined score can be a half point (mean of two passes): agreement is on rounded halves.
        pairs = [(reader[i][d], judged[s][d]) for s, i in ids.items()]
        agreement[d] = {"exact": mean(abs(a - b) < 0.5 for a, b in pairs),
                        "within_one": mean(abs(a - b) <= 1 for a, b in pairs),
                        "quadratic_kappa": kappa(pairs),
                        "mean_reader": mean(a for a, _ in pairs), "mean_judge": mean(b for _, b in pairs)}

    def composite(row):
        return mean(row[d] for d in DIMS)
    diff = {}
    for who, score in (("reader", lambda s: composite(reader[ids[s]])), ("judge", lambda s: composite(judged[s]))):
        per_task = {}
        for s in ids:
            t, _, arm = s.split("/")
            per_task.setdefault(t, {})[arm] = score(s)
        diff[who] = mean(v["P"] - v["R"] for v in per_task.values())
    out = {"protocol": "protocol-v3b.md (version 2)", "check": "second blind reader, added after the results",
           "reader": "Astra, an AI agent of another session, blind to the arm and to the judge's scores",
           "sample": {"answers": len(ids), "per_arm": len(ids) // 2, "seed": SEED},
           "agreement_with_judge": agreement, "composite_P_minus_R_on_sample": diff,
           "scores_sha256": coordinator.digest(directory / "scores.json")}
    path = ROOT / "results" / "v3b" / "second_reader.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["prepare", "compare"])
    p.add_argument("directory", type=Path)
    a = p.parse_args(argv)
    if a.command == "prepare":
        print(f"{prepare(a.directory)} cards in {a.directory / 'cards'}; map in {MAP}")
    else:
        print(json.dumps(compare(a.directory), indent=2))


if __name__ == "__main__":
    main()
