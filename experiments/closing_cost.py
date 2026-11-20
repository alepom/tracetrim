"""Time one complete automatic close on a prepared history (offline).

PYTHONPATH=src python -m experiments.closing_cost --dataset dataset/traces

Decision fixed before measurement: design a cache if any workload takes >1 s
median wall time at 100 runs, or if CPU/run increases by >15% on both doublings
from 50 to 100 to 200. Three serial repeats, with o200k_base already warm.
N includes the new run. Public histories cycle through copies of A1..A8.
"""
import argparse
import hashlib
import json
import platform
import sys
import time
from collections import Counter
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tracetrim import auto, grounding, lists, measure, report, store, traces, usage
from tracetrim.projection import Projection
from tracetrim.about import VERSION
from .paths import ROOT

SIZES = (5, 20, 50, 100, 200)
STAGES = ("read", "analyse", "loo_fit", "loo_evaluate", "report_fit", "report_other",
          "list_usage", "grounding", "render_html", "write_products", "other")


def synthetic():
    records = [{"id": f"record-{i:03}", "title": f"Search result number {i:03}",
                "description": "Detailed description of the example record. " * 2,
                "score": 1000 + i} for i in range(50)]
    output = json.dumps(records, ensure_ascii=False)
    events = [{"kind": "call", "tool": "search", "arguments": {}, "output": output},
              {"kind": "message", "text": "Selected record-017, Search result number 017."}]
    return ("\n".join(map(json.dumps, events)) + "\n").encode()


def inputs(case, dataset):
    if case == "synthetic":
        files = [("synthetic.jsonl", synthetic())]
    else:
        paths = sorted((dataset / case).glob("A*.jsonl"))
        if [p.stem for p in paths] != [f"A{i}" for i in range(1, 9)]:
            raise ValueError(f"{case}: expected exactly A1..A8")
        files = [(p.name, p.read_bytes()) for p in paths]
    samples, manifest = [], []
    for name, raw in files:
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        calls = [e for e in events if e["kind"] == "call"]
        if (not calls or any(not e["tool"] or not isinstance(e["output"], str) for e in calls)
                or events[-1]["kind"] != "message" or not events[-1]["text"].strip()):
            raise ValueError(f"{case}/{name}: expected original calls and a final answer")
        manifest.append({"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "calls": len(calls),
                         "file_bytes": len(raw), "output_bytes": sum(len(e["output"].encode()) for e in calls)})
        samples.append((raw, events))
    if len({f["sha256"] for f in manifest}) != len(files):
        raise ValueError(f"{case}: duplicate source file")
    return samples, manifest


def clocks():
    return time.process_time_ns(), time.perf_counter_ns()


class Breakdown:
    """Charge nested calls exclusively to the innermost measured stage."""
    def __init__(self):
        self.spans = []
        self.cpu, self.wall = Counter(), Counter()

    def wrap(self, stack, module, name, stage, when=None):
        original = getattr(module, name)

        def measured(*args, **kwargs):
            if when is not None and not when(*args, **kwargs):
                return original(*args, **kwargs)
            self.spans.append(stage)
            start_cpu, start_wall = clocks()
            try:
                return original(*args, **kwargs)
            finally:
                cpu, wall = clocks()
                cpu -= start_cpu
                wall -= start_wall
                self.cpu[stage] += cpu
                self.wall[stage] += wall
                self.spans.pop()
                if self.spans:
                    self.cpu[self.spans[-1]] -= cpu
                    self.wall[self.spans[-1]] -= wall
        stack.enter_context(patch.object(module, name, measured))

    def install(self, stack):
        for module, name, stage in (
            (traces, "load", "read"), (auto, "load_trace", "read"), (usage, "analyse", "analyse"),
            (report, "compute", "report_other"), (measure, "leave_one_out", "loo_evaluate"),
            (measure, "learn", "loo_fit"), (lists, "element_usage", "list_usage"),
            (grounding, "summarise", "grounding"), (report, "render_html", "render_html"),
            (Projection, "save", "write_products"),
        ):
            self.wrap(stack, module, name, stage)
        self.wrap(stack, report, "learn", "report_fit", lambda *a, **kw: "report_other" in self.spans)
        self.wrap(stack, store, "write_json", "write_products",
                  lambda path, data: path.name in ("report.json", "report.config.json"))
        self.wrap(stack, store, "write_atomic", "write_products", lambda path, text: path.name == "report.html")


def trial(work, samples, size, expected_calls):
    learner = auto.Learner(work)
    directory = work / "traces"
    for i in range(size - 1):
        (directory / f"run-{i + 1:04}.jsonl").write_bytes(samples[i % len(samples)][0])
    learner.state.update(runs=size - 1, complete_runs=size - 1)
    events = samples[(size - 1) % len(samples)][1]
    learner.events.extend(events[:-1])
    timer = Breakdown()
    with ExitStack() as stack:
        timer.install(stack)
        start_cpu, start_wall = clocks()
        closed = learner.done(events[-1]["text"])
        policy = learner.relearn()
        end_cpu, end_wall = clocks()
    if closed != directory / f"run-{size:04}.jsonl" or policy is None:
        raise RuntimeError("closing did not publish the expected run and policy")
    last = [json.loads(line) for line in closed.read_bytes().splitlines()]
    if last != events:
        raise RuntimeError("the closing changed the source events")
    data = json.loads((work / "report.json").read_text())
    if (data["auto"]["complete_runs"] != size or len(data["traces"]) != size
            or data["totals"]["calls"] != expected_calls or "grounding" not in data
            or not all((work / f).stat().st_size for f in ("report.html", "report.config.json", "report.projection.json"))):
        raise RuntimeError("incomplete report or incorrect input counts")
    result = {}
    for key, total, parts in (("cpu", end_cpu - start_cpu, timer.cpu), ("wall", end_wall - start_wall, timer.wall)):
        parts["other"] = total - sum(parts.values())
        if min(parts.values()) < 0:
            raise RuntimeError("overlapping phase timers")
        result[key] = {"total": total / 1e9, **{s: parts[s] / 1e9 for s in STAGES}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "dataset/traces")
    parser.add_argument("--output", type=Path, default=ROOT / "results/v1/closing_cost.json")
    args = parser.parse_args()
    scratch = ROOT / "out"
    scratch.mkdir(exist_ok=True)
    sources = {c: inputs(c, args.dataset) for c in ("synthetic", "C4", "C5")}
    measure.tokenizer()("warm o200k_base")
    data = {"format": "closing-cost@1", "python": platform.python_version(), "platform": sys.platform,
            "architecture": platform.machine(), "tracetrim": VERSION, "tiktoken": version("tiktoken"),
            "unit": "seconds", "repetitions": 3, "tokenizer": "o200k_base",
            "rule": "Median wall >1 s at 100, or CPU/run grows >15% at both 50->100 and 100->200, on any workload.",
            "sources": {c: m for c, (_, m) in sources.items()}, "results": {}}
    for case, (samples, manifest) in sources.items():
        rows = []
        for size in SIZES:
            calls = sum(manifest[i % len(samples)]["calls"] for i in range(size))
            repeats = []
            for repetition in range(3):
                with TemporaryDirectory(prefix="closing-cost-", dir=scratch) as directory:
                    repeats.append(trial(Path(directory), samples, size, calls))
                print(f"{case} {size} ({calls} calls) repeat {repetition + 1}/3 "
                      f"{repeats[-1]['wall']['total']:.3f}s", flush=True)
            med = {k: {s: median(r[k][s] for r in repeats) for s in ("total", *STAGES)} for k in ("cpu", "wall")}
            rows.append({"runs": size, "calls": calls, "median": med, "repetitions": repeats})
        data["results"][case] = rows
    args.output.parent.mkdir(parents=True, exist_ok=True)
    store.write_json(args.output, data)


if __name__ == "__main__":
    main()
