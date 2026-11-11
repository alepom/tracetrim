"""Does a run do what its prompt explicitly asks? The checks fixed in protocol-v04.md.

  python -m experiments.coverage traces/C4/E1-R-1.jsonl ...

Each check is a rule on the final answer or on the arguments of the calls,
true or false; a run's coverage is the share of its checks that pass. Rules,
not a reading: they say whether a list has ten to fifteen items, not whether
the items are good. The checks on argument values (`limit`, `depth`) are
there on purpose: instructions on how to organise calls must not override
what the prompt prescribes.

`requirement` is the test of E2's variants: does the answer carry what the
added requirement asks, taken from that run's tool outputs?
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tracetrim.traces import load
from tracetrim.usage import leaves, parse_output

from .checks import STRIPE_ID, final_json
from .tools import to_args


def calls_of(path: Path) -> list[dict]:
    """The tool calls of a run (not `expand`): tool, arguments, the tool's original output."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        e = json.loads(line) if line.strip() else {}
        if e.get("kind") == "call" and e.get("tool") != "expand":
            args = e.get("arguments")
            out.append({"tool": e["tool"], "arguments": args if isinstance(args, dict) else to_args(args or ""),
                        "output": e.get("output") or ""})
    return out


def _c1(ans, calls) -> dict:
    short = ans.get("piano_editoriale_brevissimo_termine") if isinstance(ans, dict) else None
    medium = ans.get("piano_editoriale_medio_periodo") if isinstance(ans, dict) else None
    sized = lambda plan: isinstance(plan, list) and 10 <= len(plan) <= 15     # noqa: E731
    limits = [c["arguments"]["limit"] for c in calls if "limit" in c["arguments"]]
    depths = [c["arguments"].get("depth") for c in calls if c["tool"] == "serp_organic_live_advanced"]
    trends = [c["arguments"].get("keywords") for c in calls if c["tool"] == "kw_data_google_trends_explore"]
    return {"short-term plan has 10 to 15 items": sized(short), "medium-term plan has 10 to 15 items": sized(medium),
            "at most 50 tool calls": len(calls) <= 50,
            "every limit is 10": all(v == 10 for v in limits),
            "every SERP depth is 10": all(v == 10 for v in depths),
            "at most 5 keywords per Trends call": all(isinstance(k, list) and len(k) <= 5 for k in trends)}


def _c2(ans, calls) -> dict:
    themes = ans.get("temi") if isinstance(ans, dict) else None
    themes = themes if isinstance(themes, list) else []
    every = lambda rule: bool(themes) and all(rule(t) for t in themes if isinstance(t, dict))    # noqa: E731
    serp = [c["arguments"] for c in calls if c["tool"] == "serp_organic_live_advanced"]
    return {"three themes": len(themes) == 3,
            "2 or 3 queries analysed per theme": every(lambda t: 2 <= len(t.get("query_analizzate") or []) <= 3),
            "a page of the site per theme": every(lambda t: len(t.get("pagine_del_sito") or []) >= 1),
            "every gap has an evidence URL": every(lambda t: bool(t.get("gap")) and all(
                len(g.get("evidenze") or []) >= 1 for g in t["gap"])),
            "every SERP call asks Italy, Italian, depth 10": bool(serp) and all(
                (a.get("location_name"), a.get("language_code"), a.get("depth")) == ("Italy", "it", 10) for a in serp),
            "proposals for every theme": every(lambda t: len(t.get("proposte") or []) >= 1)}


def _c4(ans, calls) -> dict:
    tickets = ans.get("ticket") if isinstance(ans, dict) else None
    tickets = [t for t in tickets if isinstance(t, dict)] if isinstance(tickets, list) else []
    every = lambda rule: bool(tickets) and all(rule(t) for t in tickets)     # noqa: E731
    return {"five tickets": len(tickets) == 5,
            "a customer id per ticket": every(lambda t: str(t.get("customer_id", "")).startswith("cus_")),
            "a diagnosis per ticket": every(lambda t: bool(str(t.get("diagnosi", "")).strip())),
            "evidence per ticket": every(lambda t: len(t.get("evidenze") or []) >= 1),
            "a reply per ticket": every(lambda t: bool(str(t.get("risposta_al_cliente", "")).strip())),
            "no technical identifier in the replies": every(
                lambda t: not STRIPE_ID.search(str(t.get("risposta_al_cliente", ""))))}


CHECKS = {"C1": _c1, "C2": _c2, "C4": _c4}


def coverage(path: Path, case: str) -> dict:
    """`{"checks": {name: bool}, "passed": n, "of": m, "share": n/m}` for one run."""
    ans = final_json(load(path))
    checks = CHECKS[case](ans if ans is not None else {}, calls_of(path))
    passed = sum(bool(v) for v in checks.values())
    return {"checks": {k: bool(v) for k, v in checks.items()}, "passed": passed, "of": len(checks),
            "share": round(passed / len(checks), 4)}


COLLECTED = re.compile(r"automatic|addebit\w* in automatico|inviat\w*|da pagare|bonifico", re.I)


def requirement(path: Path, case: str) -> dict:
    """E2: is the requirement the variant adds met? `{"met": bool, "items": n, "items_met": m}`.

    C1: the answer names a YouTube channel that this run's YouTube outputs contain.
    C4: every ticket that cites an invoice gives, in its reply, a link found in this
    run's invoice outputs, and says how the invoice is collected. The second part
    is a keyword test: it accepts any sentence with one of the words."""
    trace = load(path)
    answer = [e.text for e in trace.events if e.kind == "message"]
    answer = answer[-1] if answer else ""
    calls = calls_of(path)

    def values(tool: str, field: str) -> set:
        found = set()
        for c in calls:
            payload = parse_output(c["output"]) if c["tool"] == tool else None
            if isinstance(payload, (dict, list)):
                found |= {str(v) for p, v in leaves(payload) if p == field and v not in (None, "")}
        return found

    if case == "C1":
        named = [v for v in values("serp_youtube_organic_live_advanced", "items[].channel_name") if v in answer]
        return {"met": bool(named), "items": 1, "items_met": int(bool(named))}
    ans = final_json(trace) or {}
    links = values("stripe_list_invoices", "data[].hosted_invoice_url")
    about = [t for t in ans.get("ticket") or [] if isinstance(t, dict) and re.search(r"\bin_\w+", json.dumps(t))]
    met = [any(u in str(t.get("risposta_al_cliente", "")) for u in links)
           and bool(COLLECTED.search(str(t.get("risposta_al_cliente", "")))) for t in about]
    return {"met": bool(met) and all(met), "items": len(met), "items_met": sum(met)}


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        p = Path(arg)
        r = coverage(p, p.parent.name)
        print(f"{p.parent.name}/{p.stem}: {r['passed']}/{r['of']}")
        for name, ok in r["checks"].items():
            print(f"   {'ok  ' if ok else 'FAIL'} {name}")
