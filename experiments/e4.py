#!/usr/bin/env python3
"""The runs of protocol-v11.md (E4): which identifiers to protect, `all` (1.0) or `record` (1.1), live.

  python -m experiments.run --e4 freeze             # record the digests of the protocol and of this file
  python -m experiments.run --e4 schedule           # every run, in order, and which are done
  python -m experiments.run --e4 next               # the next run not done yet (PAID: the model only)
  python -m experiments.run --e4 dry-run DIR        # one run of every arm and case, a stub for the model: no spend
  python -m experiments.run --e4 analyse            # the measures and hypotheses of the protocol

The loop, the traces and the meta files are those of experiments/v04.py (arms in v04.E4_ARMS). What E4 has
of its own: the cases (C3 live on OpenAlex, C4 and C5 on the simulated servers), its ledger
(out/ledger-e4.jsonl), its cap and warning (campaign `e4` in experiments/caps.local.json, never published),
and the freeze: no run starts unless the protocol and this file are those recorded by `freeze`.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from .caps import caps
from .paths import PRICES, ROOT, cost
from .v04 import E4_ARMS, HERE, Run, Where, execute, ledger, spent, stub_for

WARN, CAP = caps("e4")           # local, never published: experiments/caps.py
CASES = ("C4", "C5", "C3")        # rounds in this order: the cheap simulated cases first
ARMS = tuple(E4_ARMS)            # T, TR
RUNS = 5
PROTOCOL = ROOT / "protocol-v11.md"
FROZEN_FILES = (PROTOCOL, Path(__file__))


def ledger_path(where: Where) -> Path:
    return where.root / "out" / "ledger-e4.jsonl"


def frozen_path(where: Where) -> Path:
    return where.root / "out" / "e4" / "frozen.json"


def digests() -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in FROZEN_FILES}


def may_start(where: Where, run: Run) -> str | None:
    """Refuse (SystemExit) a run the stop rules forbid; return a warning to print, if any."""
    if not run.id.startswith("E4-"):
        raise SystemExit(f"{run.id}: not a run of E4")
    if where is HERE:
        f = frozen_path(where)
        if not f.exists():
            raise SystemExit("protocol-v11.md is not frozen: run `--e4 freeze` first")
        if json.loads(f.read_text())["digests"] != digests():
            raise SystemExit("protocol-v11.md or experiments/e4.py changed since the freeze: record a deviation, "
                             "and freeze again only with the author's agreement")
    total = spent(where, ledger_path(where))
    if total >= CAP:
        raise SystemExit(f"stop rule: {total:.2f} USD already spent, cap {CAP:.0f}")
    return f"WARNING: {total:.2f} USD spent, warning at {WARN:.0f}, cap {CAP:.0f}" if total >= WARN else None


def book(where: Where) -> dict:
    return {"ledger": ledger_path(where), "cap": CAP, "cost": cost, "model": PRICES["model"],
            "prices": "experiments/prices.json", "may_start": may_start, "protocol": "protocol-v11.md"}


def schedule() -> list[Run]:
    return [Run("E4", c, arm, n) for n in range(1, RUNS + 1) for c in CASES for arm in ARMS]


def done(where: Where, run: Run) -> bool:
    return (where.traces / run.case / f"{run.id}.meta.json").exists()


# -- measures ------------------------------------------------------------------------

def measures(where: Where, run: Run) -> dict:
    from tracetrim.grounding import unsupported
    from tracetrim.traces import load

    from .checks import check

    case, rid = run.case, run.id
    meta = json.loads((where.traces / case / f"{rid}.meta.json").read_text())
    trace = where.traces / case / f"{rid}.jsonl"
    t = meta["tokens"]
    original = t["outputs_original"]
    sent = sum(t["outputs_sent"].values())
    expand = t.get("expand_replies", 0)
    g = check(trace, case)
    n_values, missing = unsupported(load(trace))
    return {
        "run": rid, "arm": run.arm, "flags": meta["flags"],
        "saving": 1 - sent / original if original else None,
        "saving_with_expand": 1 - (sent + expand) / original if original else None,
        "original": original, "sent": sent, "expand_tokens": expand,
        "gates_passed": bool(g["json"] and g["structure"] and g["facts_ok"] == g["facts"]),
        "facts": [g["facts_ok"], g["facts"]],
        "values": n_values, "unsupported": missing,
        "cost": meta["cost_usd"], "turns": meta["turns"], "calls": meta["calls"],
        "expand_calls": sum(1 for line in trace.read_text().splitlines()
                            if line.strip() and json.loads(line).get("tool") == "expand"),
    }


def analyse(where: Where = HERE) -> dict:
    out = {}
    for case in CASES:
        arms = {}
        for arm in ARMS:
            rs = [measures(where, r) for r in schedule() if r.case == case and r.arm == arm and done(where, r)]
            ok = [r for r in rs if not r["flags"]]
            med = lambda k, rs=ok: median([r[k] for r in rs if r[k] is not None]) if rs else None  # noqa: E731
            arms[arm] = {"runs": len(rs), "flagged": len(rs) - len(ok), "saving": med("saving"),
                         "saving_with_expand": med("saving_with_expand"), "gates_passed": sum(r["gates_passed"] for r in rs),
                         "unsupported": sum(len(r["unsupported"]) for r in rs),
                         "unsupported_values": [v for r in rs for v in r["unsupported"]],
                         "cost": med("cost"), "turns": med("turns"), "calls": med("calls"),
                         "expand_calls": med("expand_calls"), "per_run": rs}
        t, tr = arms["T"], arms["TR"]
        diff = (tr["saving"] - t["saving"]) if t["saving"] is not None and tr["saving"] is not None else None
        out[case] = {"arms": arms, "saving_difference": diff,
                     "H1": (diff is not None and diff >= 0.05) if case in ("C3", "C5") else None,
                     "H2": tr["gates_passed"] >= t["gates_passed"] if t["runs"] and tr["runs"] else None,
                     "H3": tr["unsupported"] <= t["unsupported"] if t["runs"] and tr["runs"] else None,
                     "cost_TR_vs_T": (tr["cost"] / t["cost"] - 1) if t["cost"] and tr["cost"] else None}
    return out


def published(res: dict) -> dict:
    """What goes into results/: no amount spent (the project's rule), only the cost of TR relative to T."""
    out = json.loads(json.dumps(res))
    for d in out.values():
        for a in d["arms"].values():
            a.pop("cost", None)
            for r in a["per_run"]:
                r.pop("cost", None)
    return out


def print_analysis(res: dict) -> None:
    pct = lambda x: "—" if x is None else f"{100 * x:.1f}%"  # noqa: E731
    print(f"{'caso':<5}{'braccio':<8}{'esec.':>6}{'risparmio':>11}{'con expand':>12}{'cancelli':>10}"
          f"{'non trovati':>13}{'turni':>7}{'chiamate':>10}{'expand':>8}")
    for case, d in res.items():
        for arm, a in d["arms"].items():
            print(f"{case:<5}{arm:<8}{a['runs']:>6}{pct(a['saving']):>11}{pct(a['saving_with_expand']):>12}"
                  f"{a['gates_passed']:>6}/{a['runs']:<3}{a['unsupported']:>13}{a['turns'] or '—':>7}{a['calls'] or '—':>10}"
                  f"{a['expand_calls'] if a['expand_calls'] is not None else '—':>8}")
        c = d["cost_TR_vs_T"]
        print(f"      differenza di risparmio TR − T: {pct(d['saving_difference'])} · costo per esecuzione TR rispetto a T: "
              f"{'—' if c is None else f'{100 * c:+.1f}%'} · H1 {d['H1']} · H2 {d['H2']} · H3 {d['H3']}")


async def dry_run(directory: str | Path) -> list[dict]:
    where = Where(Path(directory))
    if where.root.exists() and any(where.root.iterdir()):
        raise SystemExit(f"{where.root} is not empty: a dry run starts from an empty directory")
    metas = []
    for run in schedule():
        if run.n != 1:
            continue
        print(f"{run.case} {run.id} (dry run)", flush=True)
        metas.append(await execute(run, where, client=stub_for(run), model="stub", dry=True))
    return metas


def main(argv=None):
    p = argparse.ArgumentParser(prog="experiments.run --e4", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("freeze", help="record the digests of protocol-v11.md and of this file")
    sub.add_parser("schedule", help="every run in order, and which are done")
    sub.add_parser("next", help="make the next run not done yet (paid)")
    s = sub.add_parser("dry-run", help="one run of every arm and case with a stub in place of the model (no spend)")
    s.add_argument("directory")
    s = sub.add_parser("analyse", help="the measures and hypotheses of the protocol")
    s.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "freeze":
        f = frozen_path(HERE)
        if f.exists():
            raise SystemExit(f"{f} exists: the protocol is frozen already")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"frozen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "digests": digests()}, indent=2) + "\n")
        print(f"frozen: {f}")
    elif a.cmd == "schedule":
        for n, r in enumerate(schedule(), 1):
            print(f"{n:>4}  {r.case}  {r.id:<12}{'done' if done(HERE, r) else ''}")
        print(f"\n{len(schedule())} runs · spent {spent(HERE, ledger_path(HERE)):.2f} USD of {CAP:.0f} (warning at {WARN:.0f})")
    elif a.cmd == "dry-run":
        metas = asyncio.run(dry_run(a.directory))
        print(f"\n{len(metas)} dry runs in {a.directory}: nothing spent")
    elif a.cmd == "analyse":
        res = analyse()
        if a.json:
            out = ROOT / "results" / "v1" / "e4.json"
            out.write_text(json.dumps(published(res), indent=2, ensure_ascii=False) + "\n")
            print(f"-> {out}")
        print_analysis(res)
    else:
        run = next((r for r in schedule() if not done(HERE, r)), None)
        if run is None:
            raise SystemExit("every run of the protocol is done")
        m = asyncio.run(execute(run))
        print(f"\n  {m['case']} {m['run']}: {m['turns']} turns, {m['calls']} calls, {m['cost_usd']:.3f} USD, flags {m['flags']}")


if __name__ == "__main__":
    main()
