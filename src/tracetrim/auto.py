"""Automatic mode: the proxy observes, learns and applies, on its own.

  tracetrim proxy --upstream-url ... --auto workdir/ [--min-runs 5]

How a run is observed. The proxy sees every tool call but not the agent's
answer, and the study behind this library found that learning without the
answer misses most of what agents use. So the proxy offers one more tool,
`tracetrim_done`, and the agent is told (one line in its instructions) to
pass its final answer to it. A run is closed by that call. A run that never
calls it is kept for telemetry only.

What is applied without asking. After every run the report is regenerated
(telemetry from the first run). Once `min_runs` complete runs exist, the
config is rebuilt and reloaded: only the report's automatic actions, which
in 0.2 are the prudent projection with identifying fields kept, transformed
values recognised, and `expand` on. Everything else stays in the report as a
manual action.

How it corrects itself. Every `expand` call is a sign that a cut was wrong.
A tool whose trimmed outputs are expanded too often stops being trimmed, and
the report says so.

Everything lives in the work directory, so learning survives restarts:

  workdir/traces/run-0001.jsonl   one file per run (calls, then the answer)
  workdir/report.html (+ .json, .config.json, .projection.json)
  workdir/state.json              runs, expand counts, tools switched off
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .runtime import Policy

DONE = {
    "name": "tracetrim_done",
    "description": ("REQUIRED last step: when your final answer is ready, call this once with the complete "
                    "answer, BEFORE you show it; then show the same answer. It lets tracetrim learn which data "
                    "you used, so that next time tool results are lighter. It does not change your answer."),
    "inputSchema": {"type": "object", "required": ["answer"],
                    "properties": {"answer": {"type": "string", "description": "Your final answer, verbatim."}}},
}
# A tool is switched off when, over at least EXPAND_MIN_CALLS trimmed calls,
# the agent asked to expand at least EXPAND_MAX_RATE of them.
EXPAND_MIN_CALLS = 4
EXPAND_MAX_RATE = 0.5


class Learner:
    def __init__(self, workdir: str | Path, min_runs: int = 5):
        self.dir = Path(workdir)
        (self.dir / "traces").mkdir(parents=True, exist_ok=True)
        self.min_runs = min_runs
        self.state_path = self.dir / "state.json"
        self.state = {"runs": 0, "complete_runs": 0, "trimmed": {}, "expanded": {}, "off": [],
                      "config_from_runs": 0}
        if self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text()))
        self.events: list[dict] = []

    # -- observation ------------------------------------------------------
    def call(self, tool: str, arguments: dict, output: str) -> None:
        self.events.append({"kind": "call", "tool": tool, "arguments": arguments, "output": output})

    def trimmed(self, tool: str) -> None:
        self.state["trimmed"][tool] = self.state["trimmed"].get(tool, 0) + 1

    def expanded(self, tool: str) -> None:
        self.state["expanded"][tool] = self.state["expanded"].get(tool, 0) + 1

    def done(self, answer: str | None) -> Path | None:
        """Close the run. Returns the trace written, or None if there was nothing to write."""
        if not self.events:
            return None
        self.state["runs"] += 1
        complete = bool(answer and answer.strip())
        name = f"run-{self.state['runs']:04d}{'' if complete else '-noanswer'}.jsonl"
        path = self.dir / "traces" / name
        with path.open("w", encoding="utf-8") as fh:
            for e in self.events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            if complete:
                fh.write(json.dumps({"kind": "message", "text": answer}, ensure_ascii=False) + "\n")
        self.events = []
        if complete:
            self.state["complete_runs"] += 1
        self._save()
        return path

    def flush(self) -> Path | None:
        """On shutdown: keep the calls of an unfinished run, for telemetry."""
        path = self.done(None)
        if path is not None:
            self.relearn()
        return path

    # -- learning ---------------------------------------------------------
    def relearn(self) -> Policy | None:
        """Regenerate the report; return a new policy when one should be applied."""
        from . import report as rp
        from .traces import load
        from .usage import analyse

        paths = sorted((self.dir / "traces").glob("run-*.jsonl"))
        if not paths:
            return None
        complete = [p for p in paths if "noanswer" not in p.name]
        # Telemetry from every run; learning only from runs with an answer.
        learn_from = complete or paths
        traces = [load(p) for p in learn_from]
        usages = [analyse(t) for t in traces]
        d = rp.compute([t.name for t in traces], usages)
        sugg = rp.suggestions(d)
        config, projection = rp.build_config(d, sugg, usages, "report.projection.json")

        self._switch_off_overexpanded()
        for tool in self.state["off"]:
            config["tools"].pop(tool, None)
        ready = len(complete) >= self.min_runs
        if not ready:
            config["tools"] = {}            # telemetry only until enough complete runs
        config["auto"] = {"complete_runs": len(complete), "min_runs": self.min_runs,
                          "applied": ready, "switched_off": self.state["off"],
                          "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}

        projection.save(self.dir / "report.projection.json")
        (self.dir / "report.config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        d["suggestions"], d["config"], d["config_file"] = sugg, config, "report.config.json"
        d["auto"] = config["auto"]
        (self.dir / "report.html").write_text(rp.render_html(d, sugg), encoding="utf-8")
        (self.dir / "report.json").write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        if ready:
            self.state["config_from_runs"] = len(complete)
        self._save()
        return Policy(config, projection) if ready else None

    def _switch_off_overexpanded(self) -> None:
        for tool, n in self.state["trimmed"].items():
            e = self.state["expanded"].get(tool, 0)
            if n >= EXPAND_MIN_CALLS and e / n >= EXPAND_MAX_RATE and tool not in self.state["off"]:
                self.state["off"].append(tool)

    def initial_policy(self) -> Policy | None:
        """The config learned in a previous session, if any."""
        p = self.dir / "report.config.json"
        if p.exists():
            config = json.loads(p.read_text())
            if config.get("auto", {}).get("applied") and config.get("tools"):
                return Policy.load(p)
        return None

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2) + "\n")
