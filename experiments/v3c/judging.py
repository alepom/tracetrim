"""Blind judging of protocol v3c, hard tasks only, with the coordinator and the Gemini judge of protocol v3.

  python -m experiments.v3c.judging seal          # at the freeze: the private mapping (out/v3c/mapping.json)
  python -m experiments.v3c.judging pack          # after the campaign: out/v3c/pass-1/ and pass-2/
  python -m experiments.v3c.judging judge N [K]   # the Gemini judge on pass N (PAID)
  python -m experiments.v3c.judging adjudicate    # locks both sheets, out/v3c/pass-3/ if needed
  python -m experiments.v3c.judging finish        # out/v3c/judged.json

As in protocol v3b: the task with its system instructions, the full v3 rubric, the answer with arm cues scrubbed,
the task's reference evidence (experiments/v3b/material/reference). Random packet ids per pass, sealed; only the
mapping's SHA-256 is versioned (protocol-v3c.lock).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import tempfile

from experiments.paths import PROMPTS, ROOT
from experiments.v3 import coordinator, judge_gemini
from experiments.v3b.worlds import DATA as V3B_DATA

from .run import PRIVATE, slots, task_data, where

SEEDS = {1: 130531, 2: 130532, 3: 130533}
LOCK = ROOT / "protocol-v3c.lock"


def judged_slots() -> list[str]:
    return [s for s in slots() if s.startswith("C4b-")]


def seal() -> str:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    mapping = PRIVATE / "mapping.json"
    with tempfile.NamedTemporaryFile(dir=PRIVATE, mode="w", delete=False) as tmp:
        try:
            json.dump([{s: secrets.token_hex(16) for s in judged_slots()} for _ in range(3)], tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.link(tmp.name, mapping)            # exclusive: an existing mapping is never replaced
        finally:
            os.unlink(tmp.name)
    return coordinator.digest(mapping)


def expected() -> str:
    return json.loads(LOCK.read_text())["anonymous_mapping_sha256"]


def packets(passes=(0, 1), only=None) -> int:
    mapping_path = PRIVATE / "mapping.json"
    if coordinator.digest(mapping_path) != expected():
        raise ValueError("Anonymous mapping digest mismatch")
    mapping = coordinator.load(mapping_path)
    camp = where()
    missing = coordinator.load(camp.missing) if camp.missing.exists() else {}
    system = (PROMPTS / "c4-system.md").read_text()
    rubric = coordinator.RUBRIC.read_text()
    rows, hints = {}, 0
    for slot in mapping[0]:
        if slot in missing:
            continue
        task = slot.split("/")[0]
        events = [json.loads(x) for x in camp.trace(slot).read_text().splitlines() if x.strip()]
        answers = [e["text"] for e in events if e["kind"] == "message"]
        answer, clue = coordinator.scrub(answers[-1] if answers else "")
        hints += clue
        rows[slot] = {"task": system + "\n" + task_data(task)["prompt"], "rubric": rubric, "answer": answer,
                      "evidence": coordinator.load(V3B_DATA / "reference" / f"{task}.json")}
    for turn in passes:
        folder = PRIVATE / f"pass-{turn + 1}"
        folder.mkdir(exist_ok=False)
        for slot in sorted(only if only is not None else rows, key=mapping[turn].__getitem__):
            path = folder / f"{mapping[turn][slot]}.json"
            path.write_text(json.dumps({"id": mapping[turn][slot], **rows[slot]}, indent=2, ensure_ascii=False) + "\n")
    return hints


def judge_where():
    return judge_gemini.Where(PRIVATE, also=(), seeds=SEEDS)


def judge(n: int, count: int | None):
    from experiments.gemini import vertex_client
    from .run import limits
    jw = judge_where()
    p = judge_gemini.prices()
    judge_gemini.may_start(jw, limits())
    print(json.dumps(asyncio.run(judge_gemini.run(jw, n, vertex_client(), p, count, limits=limits())), indent=2))
    path = judge_gemini.sheet(jw, n, judge_gemini.plan(jw.cards(n), SEEDS[n]))
    print(f"score sheet: {path}" if path else "score sheet: not yet (groups left or failed)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["seal", "pack", "judge", "adjudicate", "finish"])
    ap.add_argument("pass_", nargs="?", type=int, choices=[1, 2, 3])
    ap.add_argument("count", nargs="?", type=int)
    a = ap.parse_args(argv)
    if a.command == "seal":
        print(seal())
        return
    from .freeze import verify
    verify()
    if a.command == "pack":
        n = packets()
        (PRIVATE / "cues.json").write_text(json.dumps({"answers_with_cues": n}) + "\n")
        print("Cards written:", {f"pass-{i}": len(list((PRIVATE / f"pass-{i}").glob("*.json"))) for i in (1, 2)})
    elif a.command == "judge":
        judge(a.pass_, a.count)
    elif a.command == "adjudicate":
        _, _, _, hashes, disputes = coordinator.score_inputs(PRIVATE, expected())
        packets(passes=(2,), only=disputes)
        (PRIVATE / "scores-receipt.json").write_text(json.dumps(hashes) + "\n")
        print("Answers for third pass:", len(disputes))
    else:
        print("Judged answers:", coordinator.finish(PRIVATE, expected()))


if __name__ == "__main__":
    main()
