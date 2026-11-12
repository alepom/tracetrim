#!/usr/bin/env python3
"""The publishable dataset, following the providers' terms (protocol, deviation 12).

  .venv/bin/python -m experiments.export_data          # writes dataset/

  dataset/traces/C3|C4|C5/   full traces and metadata of Phases A, B, C and Study 2
                             (CC0 catalogue; simulated servers whose payloads are ours)
  dataset/derived/C1|C2/     one record per tool call, no text and no values:
                             tool, raw token count, field paths used / used by
                             later calls / echoed / judgeable, every field path seen
                             (analysed as in Study 1)
  dataset/MANIFEST.json      every file with its sha256

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

from .paths import ROOT, TRACES

OUT = ROOT / "dataset"
FULL = ("C3", "C4", "C5")
DERIVED = ("C1", "C2")
PHASES = ("A*", "B-*", "C-*", "D-*")



def without_amounts(meta: dict) -> dict:
    """A run's metadata without what it cost: every key that holds US dollars goes."""
    return {k: v for k, v in meta.items() if "usd" not in k.lower()}

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
            meta = json.loads(p.with_suffix(".meta.json").read_text())
            meta.pop("tool_failures", None)
            (d / p.with_suffix(".meta.json").name).write_text(json.dumps(without_amounts(meta), indent=2) + "\n")
    files = sorted(f for f in OUT.rglob("*") if f.is_file())
    manifest = {str(f.relative_to(OUT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    size = sum(f.stat().st_size for f in files)
    print(f"dataset/: {len(files)} files, {size / 1e6:.1f} MB "
          f"(full traces: {', '.join(FULL)}; derived records: {', '.join(DERIVED)})")


if __name__ == "__main__":
    main()
