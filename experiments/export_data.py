#!/usr/bin/env python3
"""The publishable dataset, following the providers' terms (protocol, deviation 12).

  .venv/bin/python -m experiments.export_data          # writes dataset/

  dataset/traces/C3|C4|C5/   full traces and metadata of Phases A, B, C and Study 2
                             (CC0 catalogue; simulated servers whose payloads are ours)
  dataset/derived/C1|C2/     one record per tool call, no text and no values:
                             tool, raw token count, field paths used / used by
                             later calls / echoed / judgeable, every field path seen
                             (analysed as in Study 1)
  dataset/study3/traces/C4|C5/   the runs of Study 3 (protocol-v04.md, protocol-v04-e3.md) on the
                             simulated servers: traces, metadata, the tool definitions sent
  dataset/study3/derived/C1|C2/  the runs of Study 3 on the search-data API, or replaying its
                             recorded outputs (E3): derived records only, as in Study 1
  dataset/MANIFEST.json      every file with its sha256

The runs of Study 3 are read from $TRACETRIM_V04_TRACES (default: traces/, where they were
recorded); they are skipped, with a note, when that directory does not hold them.

Pilot runs are excluded, as in every analysis. Metadata files keep the token
usage and never the cost of a run (no amount spent is published: the author's
decision, 2026-11-12). The C1/C2 metadata files also drop the tool-failure arguments, which can contain
keywords and URLs typed by the agent.
"""
from __future__ import annotations

import hashlib
import json
import shutil

from tracetrim.measure import tokenizer
from tracetrim.traces import load
from tracetrim.usage import analyse, leaves

import os
from pathlib import Path

from .paths import ROOT, TRACES

OUT = ROOT / "dataset"
FULL = ("C3", "C4", "C5")
DERIVED = ("C1", "C2")
PHASES = ("A*", "B-*", "C-*", "D-*")



def without_amounts(meta: dict) -> dict:
    """A run's metadata without what it cost: every key that holds US dollars goes."""
    return {k: v for k, v in meta.items() if "usd" not in k.lower()}

V04 = Path(os.environ.get("TRACETRIM_V04_TRACES") or ROOT / "traces")
V04_PHASES = ("E1-*", "E2-*", "E3-R-*", "E3-S-*")      # pilots excluded, as in every analysis


def v04_runs(case):
    for pat in V04_PHASES:
        yield from sorted((V04 / case).glob(f"{pat}.jsonl"))


def derived_record(p, count, fh) -> None:
    """One line per tool call: no text and no values, only paths and counts."""
    for i, u in enumerate(analyse(load(p), normalize=False, comma_grouping=False)):
        fh.write(json.dumps({
            "call": i + 1, "tool": u.tool, "structured": u.structured,
            "raw_tokens": count(u.output),
            "used": sorted(u.used), "used_by_calls": sorted(u.used_by_calls),
            "echo": sorted(u.echo), "judgeable": sorted(u.judgeable),
            # Every field path of the output (paths only, no values): the
            # projection keeps unseen fields, so misses depend on what was seen.
            "seen": sorted({q for q, _ in leaves(u.payload)}) if u.structured else [],
        }) + "\n")


def public_meta(meta: dict, derived: bool) -> dict:
    """What a run's metadata may publish: never an amount; for the search-data cases neither
    the failed calls' arguments nor rejected domains, which hold what the agent typed."""
    meta = without_amounts(meta)
    if derived:
        for k in ("tool_failures", "domain_rejections", "replay"):
            meta.pop(k, None)
    return meta


def runs(case):
    for pat in PHASES:
        yield from sorted((TRACES / case).glob(f"{pat}.jsonl"))


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    count = tokenizer()
    for case in FULL:
        d = OUT / "traces" / case
        d.mkdir(parents=True)
        for p in runs(case):
            shutil.copy2(p, d / p.name)
            meta = json.loads(p.with_suffix(".meta.json").read_text())
            (d / p.with_suffix(".meta.json").name).write_text(json.dumps(without_amounts(meta), indent=2) + "\n")
    for case in DERIVED:
        d = OUT / "derived" / case
        d.mkdir(parents=True)
        for p in runs(case):
            if not p.stem.startswith("A"):
                continue            # replays repeat the A3 calls: their records add nothing
            with (d / p.name).open("w", encoding="utf-8") as fh:
                derived_record(p, count, fh)
            meta = json.loads(p.with_suffix(".meta.json").read_text())
            meta.pop("tool_failures", None)
            (d / p.with_suffix(".meta.json").name).write_text(json.dumps(without_amounts(meta), indent=2) + "\n")
    study3 = 0
    for case in FULL + DERIVED:
        derived = case in DERIVED
        found = list(v04_runs(case))
        if not found:
            continue
        d = OUT / "study3" / ("derived" if derived else "traces") / case
        d.mkdir(parents=True)
        for p in found:
            if derived:
                with (d / p.name).open("w", encoding="utf-8") as fh:
                    derived_record(p, count, fh)
            else:
                shutil.copy2(p, d / p.name)
                tools = p.with_suffix(".tools.json")
                if tools.exists():
                    shutil.copy2(tools, d / tools.name)
            meta = json.loads(p.with_suffix(".meta.json").read_text())
            (d / p.with_suffix(".meta.json").name).write_text(json.dumps(public_meta(meta, derived), indent=2) + "\n")
            study3 += 1
    if not study3:
        print(f"Study 3: no run under {V04}; set TRACETRIM_V04_TRACES to include them")
    files = sorted(f for f in OUT.rglob("*") if f.is_file())
    manifest = {str(f.relative_to(OUT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    size = sum(f.stat().st_size for f in files)
    print(f"dataset/: {len(files)} files, {size / 1e6:.1f} MB "
          f"(full traces: {', '.join(FULL)}; derived records: {', '.join(DERIVED)})")


if __name__ == "__main__":
    main()
