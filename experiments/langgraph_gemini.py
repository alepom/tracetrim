"""Smoke test: tracetrim.langgraph.TraceTrim in a real LangGraph graph with a real model, gemini-3.8-flash.

    .venv-lg-gemini/bin/python -m experiments.langgraph_gemini              # 7 conversations, a few cents
    .venv-lg-gemini/bin/python -m experiments.langgraph_gemini --promote    # the same, with promotion on

Not a study: no hypothesis and no arms. It checks that the adapter works end to end with a model that is not
scripted (the unit tests and examples/langgraph_agent.py use scripted ones):
  - the model calls the tools and closes every run with tracetrim_done (no closing from code);
  - after min_runs closed runs the policy is learned and later results carry the [trimmed] line;
  - when a question needs a field the policy hides, the model calls expand and the answer has the value;
  - the async path (ainvoke, awrap_tool_call) gives the same.
With --promote (tracetrim.evidence.Adaptive(promote=True)), a field the model asks for by name in two runs is
added to the projection, and the later runs no longer need to expand it.
The tools are local and their data invented: no external service apart from the model. The summary in
out/langgraph-gemini/summary.json has tokens, no amounts; the cost estimate is printed only (CLAUDE.md).
Its own venv (.venv-lg-gemini): langchain-google-genai needs a newer google-genai than the one that ran v3d.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from tracetrim.evidence import Adaptive
from tracetrim.langgraph import TraceTrim

from .tools import env

MODEL = "gemini-3.8-flash"
PROMOTE = "--promote" in sys.argv[1:]
OUT = Path("out/langgraph-gemini" + ("-promote" if PROMOTE else ""))
PRICES = json.loads(Path("experiments/prices-e3.json").read_text())
assert PRICES["model"] == MODEL

# Data invented for the test. Each invoice has the fields a billing agent needs (number, amount, status, due date)
# and many it rarely needs; the memo is free text that only the last question asks about.
CUSTOMERS = {
    "Rossi Arredamenti": "cus_r01", "Bianchi Viaggi": "cus_b02", "Verdi Ottica": "cus_v03",
    "Neri Ceramiche": "cus_n04", "Gallo Sport": "cus_g05", "Costa Vini": "cus_c06", "Fontana Libri": "cus_f07",
}
MEMO = {"cus_c06": "Nota di credito parziale: 2 casse restituite per tappo difettoso, accordo con M. Costa del 3 marzo."}


def invoices(cid: str) -> list[dict]:
    seed = sum(map(ord, cid))
    out = []
    for i in range(4):
        n = seed % 97 + i
        out.append({
            "id": f"in_{cid[4:]}{i}", "number": f"FT-2027-{n:04d}", "amount_due": (seed * (i + 3)) % 90000 + 1200,
            "currency": "eur", "status": ["paid", "open", "open", "void"][(seed + i) % 4],
            "due_date": f"2027-0{1 + (i + seed) % 8}-{10 + i * 4}",
            "memo": MEMO.get(cid, "Fattura emessa da gestionale.") if i == 1 else "Fattura emessa da gestionale.",
            "hosted_invoice_url": f"https://pay.example.invalid/i/{cid}/{i}", "invoice_pdf": f"https://files.example.invalid/{cid}/{i}.pdf",
            "account_country": "IT", "account_tax_ids": None, "attempt_count": i % 2, "auto_advance": True,
            "billing_reason": "subscription_cycle", "collection_method": "send_invoice", "created": 1798000000 + i * 86400,
            "footer": "Pagamento a 30 giorni data fattura. IBAN su richiesta.", "livemode": False,
            "metadata": {"source": "gestionale", "batch": f"b{seed % 13}"}, "period_start": 1797000000, "period_end": 1798000000,
            "lines": {"object": "list", "data": [{"description": "Abbonamento servizio", "quantity": 1 + i}], "has_more": False},
        })
    return out


@tool
def find_customer(name: str) -> str:
    """Find a customer by company name. Returns its id and account details."""
    cid = CUSTOMERS.get(name.strip())
    if not cid:
        return json.dumps({"data": [], "has_more": False})
    return json.dumps({"data": [{"id": cid, "name": name, "email": f"amministrazione@{cid}.example.invalid",
                                 "currency": "eur", "balance": 0, "delinquent": False, "created": 1790000000,
                                 "invoice_prefix": cid[4:].upper(), "preferred_locales": ["it"], "tax_exempt": "none",
                                 "metadata": {"crm": f"crm-{cid}"}, "address": {"country": "IT", "city": "Bologna"}}],
                       "has_more": False})


@tool
def list_invoices(customer_id: str) -> str:
    """List the invoices of a customer id (cus_...)."""
    return json.dumps({"object": "list", "data": invoices(customer_id), "has_more": False})


SYSTEM = ("You are a billing assistant for an Italian company. Answer in Italian, briefly, using only data from the "
          "tools. First find the customer, then list the invoices.\n\n")

QUESTIONS = [  # (customer, question): the first five need only number, amount, status, due date
    ("Rossi Arredamenti", "Quanto deve ancora pagare Rossi Arredamenti? Elenca le fatture aperte con numero e importo."),
    ("Bianchi Viaggi", "Quali fatture di Bianchi Viaggi sono aperte, e con quale scadenza?"),
    ("Verdi Ottica", "Totale delle fatture aperte di Verdi Ottica, con i numeri delle fatture."),
    ("Neri Ceramiche", "Quante fatture aperte ha Neri Ceramiche e per quale importo totale?"),
    ("Gallo Sport", "Elenca le fatture aperte di Gallo Sport con importo e scadenza."),
    ("Costa Vini", "Costa Vini chiede perché una delle sue fatture ha una nota: che cosa dice la nota, e su quale fattura?"),
    ("Fontana Libri", "Quanto deve ancora pagare Fontana Libri? Elenca le fatture aperte con numero e importo."),
]
EXPAND_RUN, ASYNC_RUN = 6, 7      # 1-based: the memo question, and the conversation run with ainvoke


def model_client():
    from langchain_google_genai import ChatGoogleGenerativeAI
    # As in experiments/gemini.py: the service account comes from .env and its file is read only by the SDK.
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and env("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = env("GOOGLE_APPLICATION_CREDENTIALS")
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS is needed in .env")
    import google.auth
    _, project = google.auth.default()
    return ChatGoogleGenerativeAI(model=MODEL, vertexai=True, project=project, location="global", max_retries=2)


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    tt = TraceTrim(OUT / "workdir", min_runs=3, **({"adaptive": Adaptive(promote=True)} if PROMOTE else {}))
    tools = [find_customer, list_invoices, *tt.tools]
    llm = model_client().bind_tools(tools)
    usage = {"input": 0, "cached": 0, "output": 0, "model_calls": 0}

    def count(msg: AIMessage) -> None:
        u = msg.usage_metadata or {}
        usage["input"] += u.get("input_tokens", 0)
        usage["cached"] += (u.get("input_token_details") or {}).get("cache_read", 0)
        usage["output"] += u.get("output_tokens", 0)
        usage["model_calls"] += 1

    def prompt(state):
        return [SystemMessage(SYSTEM + tt.instructions), *state["messages"]]

    def model(state: MessagesState):
        msg = llm.invoke(prompt(state))
        count(msg)
        return {"messages": [msg]}

    async def amodel(state: MessagesState):
        msg = await llm.ainvoke(prompt(state))
        count(msg)
        return {"messages": [msg]}

    def build(node):
        g = StateGraph(MessagesState)
        g.add_node("model", node)
        g.add_node("tools", ToolNode(tools, wrap_tool_call=tt.wrap_tool_call, awrap_tool_call=tt.awrap_tool_call,
                                     handle_tool_errors=True))
        g.add_edge(START, "model")
        g.add_conditional_edges("model", tools_condition)
        g.add_edge("tools", "model")
        return g.compile()

    app, aapp = build(model), build(amodel)
    rows = []
    for n, (customer, question) in enumerate(QUESTIONS, start=1):
        cfg = {"configurable": {"thread_id": f"conv-{n}"}, "recursion_limit": 40}
        state = {"messages": [HumanMessage(question)]}
        out = asyncio.run(aapp.ainvoke(state, cfg)) if n == ASYNC_RUN else app.invoke(state, cfg)
        msgs = out["messages"]
        calls = [c for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls]
        results = [m for m in msgs if isinstance(m, ToolMessage)]
        done = [c for c in calls if c["name"] == "tracetrim_done"]
        answer = done[-1]["args"].get("answer", "") if done else ""
        rows.append({
            "run": n, "customer": customer, "async": n == ASYNC_RUN,
            "tools": [c["name"] for c in calls],
            "trimmed_results": sum("[trimmed]" in str(m.content) for m in results if m.name != "expand"),
            "expand_calls": [c["args"] for c in calls if c["name"] == "expand"],
            "errors": [str(m.content)[:200] for m in results if m.status == "error"],
            "closed_by_model": bool(done) and not any(m.name == "tracetrim_done" and m.status == "error"
                                                       for m in results[-1:]),
            "answer": answer,
        })
        r = rows[-1]
        print(f"run {n}{' (async)' if r['async'] else ''}: calls {len(r['tools'])}, trimmed results "
              f"{r['trimmed_results']}, expand {len(r['expand_calls'])}, errors {len(r['errors'])}, "
              f"closed by model {r['closed_by_model']}")
        print(f"   answer: {answer[:160]}")

    memo_ok = "tappo difettoso" in rows[EXPAND_RUN - 1]["answer"] or "casse" in rows[EXPAND_RUN - 1]["answer"]
    gates = {
        "every run closed by the model": all(r["closed_by_model"] for r in rows),
        "no trimming before the policy (runs 1-3)": all(r["trimmed_results"] == 0 for r in rows[:3]),
        "trimmed after learning (runs 4-7)": all(r["trimmed_results"] > 0 for r in rows[3:]),
        "memo question: expand called": bool(rows[EXPAND_RUN - 1]["expand_calls"]),
        "memo question: memo in the answer": memo_ok,
        "async run closed": rows[ASYNC_RUN - 1]["closed_by_model"],
    }
    if PROMOTE:
        # The model asks for a field by name in two runs: from then on the projection keeps it.
        asked = {}
        for r in rows:
            for a in r["expand_calls"]:
                for f in a.get("fields") or []:
                    asked.setdefault(f, []).append(r["run"])
        twice = {f: runs[1] for f, runs in asked.items() if len(set(runs)) >= 2}
        later = {f: [r["run"] for r in rows if r["run"] > second and any(f in (a.get("fields") or [])
                                                                     for a in r["expand_calls"])]
                 for f, second in twice.items()}
        gates["promotion: a field asked for in two runs is not asked for again"] = bool(twice) and not any(later.values())
        print("asked for in two runs (field: second run):", twice, "· asked again later:", later)
    report = Path(tt.report).read_text() if Path(tt.report).exists() else None
    (OUT / "summary.json").write_text(json.dumps({"model": MODEL, "runs": rows, "gates": gates, "usage": usage,
                                                  "report": report}, indent=1, ensure_ascii=False))
    print()
    for g, ok in gates.items():
        print(f"{'OK ' if ok else 'NO '} {g}")
    p = PRICES
    estimate = ((usage["input"] - usage["cached"]) * p["input_per_mtok"] + usage["cached"] * p["cached_input_per_mtok"]
                + usage["output"] * p["output_per_mtok"]) / 1e6
    print(f"\nmodel calls {usage['model_calls']}, input {usage['input']} (cached {usage['cached']}), "
          f"output {usage['output']} tokens; estimate at the assumed Vertex prices {estimate:.4f} USD (not saved)")
    sys.exit(0 if all(gates.values()) else 1)


if __name__ == "__main__":
    main()
