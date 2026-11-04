#!/usr/bin/env python3
"""Phase B: replay one Phase A trace, changing only the tool messages.

  .venv/bin/python -m experiments.replay C1 --dry      # tokens per arm, no API call
  .venv/bin/python -m experiments.replay C1            # 2 runs per arm, interleaved

The conversation of trace A3 is rebuilt up to the final answer: same system
prompt, same user prompt, same calls in the same order. Only the content of
the tool messages changes, by arm:

  raw          as the tool returned it
  prudent      learned projection, prudent, from the other Phase A traces of the case
  aggressive   learned projection, aggressive, same source
  structural   prudent projection, then clean-up and TOON

Each replay is written as a trace whose tool outputs are the RAW ones and
whose final message is the replayed answer: the gates in checks.py then judge
it against what the tools really said, whatever the model was shown.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from openai import OpenAI

from tracetrim.measure import tokenizer
from tracetrim.projection import learn
from tracetrim.structural import structural
from tracetrim.traces import load
from tracetrim.usage import analyse

from .run import LEDGER, PRICES, TRACES, cost, spent

TRACE = "A3"            # chosen before looking at any Phase A content (protocol, Phase B)
ARMS = ("raw", "prudent", "aggressive", "structural")       # Phase B, tracetrim 0.1
ARMS_D = ("raw", "v02", "v02-structural")                   # Study 2, safeguards S1 + S2
PREFIX = {"B": ARMS, "D": ARMS_D}
CLOSE = {
    "it": "Hai raccolto tutti i dati che ti servono. Non chiamare altri strumenti: produci ora la risposta finale nel formato richiesto.",
    "en": "You have collected all the data you need. Do not call any more tools: produce the final answer now, in the required format.",
}
LANG = {"C1": "it", "C2": "it", "C3": "en", "C4": "it", "C5": "en"}


def arms_for(case: str):
    """The tool-message transform of each arm, learned without the replayed trace.

    Phase B arms pin the 0.1 behaviour explicitly, so they stay reproducible
    whatever the library defaults become; Study 2 arms switch on S1 and S2.
    """
    others = sorted(p for p in (TRACES / case).glob("A*.jsonl") if p.stem != TRACE)
    if len(others) < 2:
        sys.exit(f"{case}: need Phase A traces besides {TRACE}, found {len(others)}")
    loaded = [load(p) for p in others]
    usages = [analyse(t, normalize=False) for t in loaded]
    prudent = learn(usages, "prudent", protect_ids=False)
    aggressive = learn(usages, "aggressive", protect_ids=False)
    v02 = learn([analyse(t, normalize=True) for t in loaded], "prudent", protect_ids=True)
    return {
        "raw": lambda tool, out: out,
        "prudent": prudent.apply,
        "aggressive": aggressive.apply,
        "structural": lambda tool, out: structural(prudent.apply(tool, out)),
        "v02": v02.apply,
        "v02-structural": lambda tool, out: structural(v02.apply(tool, out)),
    }, [p.stem for p in others]


def conversation(trace, transform):
    prompts = [e.text for e in trace.events if e.kind == "prompt"]
    msgs = [{"role": "system", "content": prompts[0]}, {"role": "user", "content": prompts[1]}]
    n = 0
    for e in trace.events:
        if e.kind != "call" or e.output is None:
            continue
        n += 1
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{n}", "type": "function", "function": {"name": e.tool, "arguments": e.text or "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{n}", "content": transform(e.tool, e.output)})
    return msgs


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("case", choices=list(LANG))
    a.add_argument("--runs", type=int, default=2)
    a.add_argument("--dry", action="store_true")
    a.add_argument("--model", default=PRICES["model"])
    a.add_argument("--study", choices=list(PREFIX), default="B", help="B = Phase B, D = Study 2")
    o = a.parse_args()
    arms = PREFIX[o.study]

    trace = load(TRACES / o.case / f"{TRACE}.jsonl")
    transforms, learned_from = arms_for(o.case)
    count = tokenizer()
    convs = {}
    print(f"\n  {o.case} {TRACE}, projections learned from {', '.join(learned_from)}\n")
    for arm in arms:
        msgs = conversation(trace, transforms[arm]) + [{"role": "user", "content": CLOSE[LANG[o.case]]}]
        tool_tok = sum(count(m["content"]) for m in msgs if m["role"] == "tool")
        convs[arm] = (msgs, tool_tok)
        print(f"  {arm:<11} tool messages {tool_tok:>9,} tokens")
    if o.dry:
        return

    client = OpenAI(timeout=900.0, max_retries=1)
    base_events = [e for e in trace.events if e.kind != "message"]
    for i in range(1, o.runs + 1):
        for arm in arms:              # interleaved: drifts hit every arm alike
            if spent() >= PRICES["stop_usd"]:
                sys.exit(f"stop rule: {spent():.2f} USD spent")
            dest = TRACES / o.case / f"{o.study}-{arm}-{i}.jsonl"
            if dest.exists():
                continue
            msgs, tool_tok = convs[arm]
            t0 = time.time()
            r = client.chat.completions.create(model=o.model, messages=msgs)
            u = r.usage
            usage = {"input": u.prompt_tokens, "output": u.completion_tokens,
                     "cached": getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0}
            with dest.open("w", encoding="utf-8") as fh:
                for e in base_events:
                    d = {"kind": e.kind, "text": e.text} if e.kind != "call" else \
                        {"kind": "call", "tool": e.tool, "arguments": e.text, "output": e.output}
                    fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                fh.write(json.dumps({"kind": "message", "text": r.choices[0].message.content or ""},
                                    ensure_ascii=False) + "\n")
            meta = {"case": o.case, "run": dest.stem, "phase": o.study, "arm": arm, "replay_of": TRACE,
                    "learned_from": learned_from, "model": o.model, "tool_message_tokens": tool_tok,
                    "usage": usage, "cost_usd": round(cost(usage), 4), "seconds": round(time.time() - t0, 1)}
            dest.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            with LEDGER.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: meta[k] for k in ("case", "run", "model", "cost_usd")} | {"usage": usage}) + "\n")
            print(f"  {arm:<11} {i}/{o.runs}: input {usage['input']:,} (cached {usage['cached']:,}), "
                  f"output {usage['output']:,}, {meta['cost_usd']:.3f} USD", flush=True)


if __name__ == "__main__":
    main()
