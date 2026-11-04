#!/usr/bin/env python3
"""Phase B token baseline: what Headroom does to the same conversations.

Headroom needs torch (~1.8 GB), so it is NOT a dependency of tracetrim: this
script runs with the course project's environment, where it is installed,
and imports tracetrim from source.

  ~/progetti/accademia-server-mcp/.venv/bin/python experiments/headroom_baseline.py

Same configuration as the pilot study (everything compressed, no protection):
the most favourable to Headroom, so the comparison does not depend on a
cautious default. Token-only: its effect on quality was replayed in the pilot.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import tiktoken  # noqa: E402
from headroom import CompressConfig, compress  # noqa: E402

from tracetrim.traces import load  # noqa: E402

from experiments.paths import TRACES  # noqa: E402

CONFIG = CompressConfig(protect_recent=0, protect_analysis_context=False, target_ratio=0.2,
                        compress_user_messages=True)
enc = tiktoken.get_encoding("o200k_base")


def tok(s):
    return len(enc.encode(s or ""))


def main():
    from importlib.metadata import version
    res = {"headroom_version": version("headroom-ai"), "cases": {}}
    for case in ("C1", "C2", "C3", "C4", "C5"):
        p = TRACES / case / "A3.jsonl"
        if not p.exists():
            print(f"  {case}: traces not available here")
            continue
        t = load(p)
        prompts = [e.text for e in t.events if e.kind == "prompt"]
        msgs, n = [{"role": "user", "content": prompts[1]}], 0
        for e in t.events:
            if e.kind == "call" and e.output is not None:
                n += 1
                msgs.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": f"c{n}", "type": "function", "function": {"name": e.tool, "arguments": e.text or "{}"}}]})
                msgs.append({"role": "tool", "tool_call_id": f"c{n}", "name": e.tool, "content": e.output})
        before = sum(tok(m["content"]) for m in msgs if m["role"] == "tool")
        r = compress(list(msgs), model="gpt-4o", model_limit=400000, config=CONFIG)
        after = sum(tok(m["content"]) for m in r.messages if m.get("role") == "tool")
        res["cases"][case] = {"tool_tokens": before, "headroom": after, "saving": 1 - after / before if before else 0}
        print(f"  {case}: {before:>9,} -> {after:>9,} tokens ({after / before - 1:+.1%})")
    if len(res["cases"]) < 5:
        # Never shrink the published baseline: C1 and C2 are not released.
        print("  not every case is available: results/headroom_baseline.json left as it is")
        return
    out = ROOT / "results" / "headroom_baseline.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=2) + "\n")
    print(f"  written: {out.relative_to(ROOT)} (headroom {res['headroom_version']})")


if __name__ == "__main__":
    main()
