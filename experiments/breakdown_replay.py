"""Where the saving of a set of recorded runs comes from: re-serialisation and fields.

  python -m experiments.breakdown_replay WORKDIR/traces --min-runs 3 [--timing] [--json]

Offline, no model and no tool call: the runs of an automatic-mode work
directory (`traces/run-*.jsonl`) are played again, in order, through `AutoTrim`,
as if the same calls arrived at a new proxy. For every call the engine gives the
three counts of `tracetrim.contract.Sizes` (tokens, o200k_base):

  R  the output as the tool sent it
  W  the same output as tracetrim writes it with nothing removed
  S  what the model receives, the `[trimmed]` line included

and the script checks, call by call, that they are what the definitions say
(each counted again from scratch) and that R - W (re-serialisation) plus W - S
(fields) is R - S. It prints one line per run.

What it reads stays where it is. The replay needs a work directory, which
`AutoTrim` fills with a copy of the runs it is given: it is a temporary
directory, removed before the script ends. Nothing else is written: the output
is counts and percentages, never a tool name's payload, an argument or an answer.

The ids in the `[trimmed]` lines are random: S can differ by a token or two per
filtered call from one replay to the next.

`--timing` measures what the three counts cost per call, on the same outputs:
the time of `Engine.sizes` (best of several repetitions), next to the time of
the two counts the proxy's log already made before 0.4.1 (R and S), which it no
longer makes itself.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

from tracetrim.auto import AutoTrim
from tracetrim.contract import FILTERED, Sizes, ToolCall, ToolResult
from tracetrim.measure import tokenizer


def load(path: Path) -> tuple[list[dict], str | None]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    answer = next((e["text"] for e in events if e["kind"] == "message"), None)
    return [e for e in events if e["kind"] == "call"], answer


def best(f, repeat: int) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        f()
        times.append(time.perf_counter() - t)
    return min(times)


def replay(traces: Path, min_runs: int, timing: int = 0) -> dict:
    paths = sorted(traces.glob("run-*.jsonl"))
    if not paths:
        raise SystemExit(f"no run-*.jsonl in {traces}")
    count = tokenizer()
    runs, costs = [], []
    with tempfile.TemporaryDirectory(prefix="tracetrim-replay-") as work:
        tt = AutoTrim(work, min_runs=min_runs)
        engine = tt.engine
        for number, path in enumerate(paths, 1):
            calls, answer = load(path)
            row = {"run": number, "calls": 0, "filtered": 0, "raw": 0, "whole": 0, "sent": 0}
            for e in calls:
                arguments = e.get("arguments") if isinstance(e.get("arguments"), dict) else {}
                result = ToolResult(e["output"])
                obs = engine.observe(ToolCall(e["tool"], arguments, e.get("turn")), result)
                s = obs.sizes
                # The definitions, counted again from scratch.
                whole = engine.policy.apply(e["tool"], e["output"], project=False)
                expected = Sizes(count(e["output"]), count(whole), count(obs.text))
                if s != expected:
                    raise SystemExit(f"run {number}, call {row['calls'] + 1}: counted {s}, by definition {expected}")
                if s.reserialisation + s.fields != s.raw - s.sent:
                    raise SystemExit(f"run {number}, call {row['calls'] + 1}: the parts do not add up")
                if timing:
                    costs.append({"tokens": s.raw,
                                  "sizes": best(lambda: engine.sizes(e["tool"], result, obs), timing),
                                  "log_before": best(lambda: (count(e["output"]), count(obs.text)), timing)})
                row["calls"] += 1
                row["filtered"] += obs.exposure == FILTERED
                for k, v in (("raw", s.raw), ("whole", s.whole), ("sent", s.sent)):
                    row[k] += v
            row["trimming_at_start"] = bool(calls) and row["filtered"] > 0
            tt.done(answer)
            runs.append(row)
    out = {"min_runs": min_runs, "unit": "tokens (o200k_base)", "runs": runs}
    if costs:
        ms = lambda xs: round(statistics.mean(xs) * 1000, 3)             # noqa: E731
        sizes, before = [c["sizes"] for c in costs], [c["log_before"] for c in costs]
        out["timing"] = {
            "calls": len(costs), "repetitions": timing,
            "mean_output_tokens": round(statistics.mean(c["tokens"] for c in costs)),
            "sizes_ms_per_call": {"mean": ms(sizes), "median": round(statistics.median(sizes) * 1000, 3),
                                  "max": round(max(sizes) * 1000, 3)},
            "two_counts_of_the_log_ms_per_call": {"mean": ms(before)},
            "net_for_the_proxy_ms_per_call": {"mean": ms([a - b for a, b in zip(sizes, before)])},
            "sizes_microseconds_per_1000_output_tokens": round(sum(sizes) / sum(c["tokens"] for c in costs) * 1e9),
        }
    return out


def share(part: int, raw: int) -> str:
    if not raw:
        return "—"
    return "0.0%" if part == 0 else f"{-part / raw * 100:+.1f}%"


def table(out: dict) -> str:
    lines = [f"min-runs {out['min_runs']} · {out['unit']} · total = re-serialisation + fields, checked on every call",
             f"{'run':>3} {'calls':>5} {'filtered':>8} {'R sent by tools':>16} {'W whole':>9} {'S received':>11} "
             f"{'total':>7} {'re-serial.':>10} {'fields':>7}"]
    for r in out["runs"]:
        lines.append(f"{r['run']:>3} {r['calls']:>5} {r['filtered']:>8} {r['raw']:>16,} {r['whole']:>9,} {r['sent']:>11,} "
                     f"{share(r['raw'] - r['sent'], r['raw']):>7} {share(r['raw'] - r['whole'], r['raw']):>10} "
                     f"{share(r['whole'] - r['sent'], r['raw']):>7}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("traces", type=Path, help="a directory of run-*.jsonl (read only)")
    ap.add_argument("--min-runs", type=int, default=5)
    ap.add_argument("--timing", type=int, nargs="?", const=7, default=0, metavar="REPETITIONS",
                    help="also time the three counts per call (best of REPETITIONS, default 7)")
    ap.add_argument("--json", action="store_true", help="print the numbers as JSON")
    a = ap.parse_args(argv)
    out = replay(a.traces, a.min_runs, a.timing)
    if a.json:
        print(json.dumps(out, indent=2))
        return
    print(table(out))
    if "timing" in out:
        t = out["timing"]
        print(f"\ncost of the three counts, {t['calls']} calls of {t['mean_output_tokens']:,} tokens on average "
              f"(best of {t['repetitions']}):\n"
              f"  Engine.sizes            {t['sizes_ms_per_call']['mean']} ms per call on average "
              f"(median {t['sizes_ms_per_call']['median']}, max {t['sizes_ms_per_call']['max']})\n"
              f"  the log's two counts    {t['two_counts_of_the_log_ms_per_call']['mean']} ms per call (made before 0.4.1, "
              f"no longer made by the log)\n"
              f"  net, for the proxy      {t['net_for_the_proxy_ms_per_call']['mean']} ms per call\n"
              f"  Engine.sizes            {t['sizes_microseconds_per_1000_output_tokens']} µs per 1,000 output tokens",
              file=sys.stdout)


if __name__ == "__main__":
    main()
