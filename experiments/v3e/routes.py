"""How the hidden facts of protocol v3e reached the agent, by arm (no spend, no model; written after the analysis).

  python -m experiments.v3e.routes          # results/v3e/routes.json; reads the private traces in out/v3e/

The preregistered analysis (analysis.py, frozen by protocol-v3e.lock) records whether a hidden fact reached the agent
in a tool result, not by which call. This adds the route, with the check of protocol v3b (`experiments.v3b.analysis
.reached`, as `via` in protocol v3c): `list` (a trimmed list), `detail` (a call for one record), `expand`. A fact can
reach the agent by more than one route. The replacement link is left out, as in v3c (the check confuses it).
"""
from __future__ import annotations

import json

from experiments.paths import ROOT
from experiments.v3b import analysis as v3b

from .analysis import hidden_facts
from .run import ARMS, slots, task_data, where


def routes() -> dict:
    w = where()
    out: dict = {}
    for s in slots():
        if not s.startswith("C4b-") or not w.trace(s).exists():
            continue
        arm = s.rsplit("/", 1)[1]
        key = task_data(s.split("/")[0])["key"]
        for row in hidden_facts(w.trace(s), key):
            if row["kind"].startswith("replacement"):
                continue
            k = out.setdefault(row["kind"], {a: {"facts": 0, "reached": 0, "via": {}} for a in ARMS})[arm]
            k["facts"] += 1
            k["reached"] += bool(row["reached"])
            for h in row["reached"]:
                k["via"][h] = k["via"].get(h, 0) + 1
    return {"protocol": "protocol-v3e.md", "written_after_the_analysis": True, "hidden_fields": out}


def main():
    res = routes()
    (ROOT / "results/v3e/routes.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    for kind, arms in res["hidden_fields"].items():
        print(kind, {a: (v["reached"], v["facts"], v["via"]) for a, v in arms.items()})


if __name__ == "__main__":
    main()
