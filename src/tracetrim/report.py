"""Telemetry report: what the tools sent, what the agent used, what to do.

  tracetrim report traces/*.jsonl -o report.html        (also writes report.json)

Every suggestion comes from a deterministic rule and quotes the number that
triggered it, in English, Italian, Spanish, French and Chinese (tracetrim.i18n). No model writes the report: the same
traces always give the same report, and every sentence can be checked
against the JSON next to it.

Two projections are involved, on purpose:
  - learned on ALL traces: the one you would deploy, used to show which
    fields are kept or dropped and why;
  - learned leave-one-out: the honest estimate of savings and misses on a
    run the projection has never seen.

Field tokens are the tokens of each field's own fragment (`"key": value`),
summed over calls. JSON punctuation between fields is not attributed, so the
fragments of a tool add up to less than its total.
"""
from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import about, i18n, measure
from .projection import FoldTraining, learn
from .usage import leaves

# Thresholds of the rules. Changing one changes which suggestions appear,
# never the numbers they quote.
MIN_TRACES = 5            # below this, learned projections are unstable
TRIM_MIN_SHARE = 0.05     # tool must carry >= 5% of all tool-output tokens
TRIM_MIN_SAVING = 0.20    # and its held-out saving must be >= 20%
MISS_RATE = 0.10          # misses in >= 10% of trimmed held-out calls
PROSE_MIN_SHARE = 0.05    # text outputs >= 5% of all tool-output tokens
UNJUDGED_SHARE = 0.10     # non-judgeable kept fields >= 10% of a tool's field tokens
ESCAPE_MIN_SAVING = 0.01  # re-serialising without escapes saves >= 1%
MIN_CALLS = 5             # risk and review rules need enough calls to mean something
REVIEW_MIN_SHARE = 0.01   # and a tool worth reviewing: >= 1% of all tool-output tokens
# The rule on tool definitions has its thresholds in tracetrim.definitions.
from .definitions import LIST_MAX as DEFINITIONS_LIST_MAX  # noqa: E402


def _is_error(c) -> bool:
    if c.payload is None:
        return c.output.strip().lower().startswith("error")
    # `"error": null` is how many APIs say there was none (until 1.0 it counted as an error).
    return isinstance(c.payload, dict) and c.payload.get("error") not in (None, False, "", [], {})


def compute(names, usages, unseen="keep", protect_ids=True, runtime: bool = False, compact: bool = True,
            keep_positions: bool = True, *, training=None) -> dict:
    """`runtime` (learning 1.1): measure the projections as run time writes them (compact JSON, empty containers
    kept, list positions kept since 1.1.2), which is what the generated config deploys. Up to 1.0 they were measured with the spaces of
    `json.dumps`: on outputs that were already compact the saving came out negative, and a tool could stay
    untrimmed for it. `compact=False` measures run time with `--no-compact`, `keep_positions=False` with
    `--no-keep-positions`."""
    count = measure.tokenizer()
    if training is None:
        training = FoldTraining(usages, unseen=unseen, protect_ids=protect_ids)
    deployed_p = training.project("prudent")
    deployed_a = training.project("aggressive")
    rows = measure.leave_one_out(names, usages, unseen=unseen, protect_ids=protect_ids,
                                 compact=runtime and compact, keep_empty=runtime,
                                 keep_positions=runtime and keep_positions, training=training)
    seps = (",", ":") if runtime and compact else None

    tools = defaultdict(lambda: {
        "calls": 0, "raw": 0, "unescaped": 0, "prose_calls": 0, "prose_tokens": 0,
        "errors": 0, "duplicates": 0, "loo_raw": 0, "loo_prudent": 0, "loo_aggressive": 0,
        "loo_trimmed_calls": 0, "loo_misses": 0, "error_examples": [], "fields": {}})
    field_stats = defaultdict(lambda: defaultdict(lambda: {"tokens": 0, "seen": 0, "used": 0, "echo": 0}))
    judgeable = defaultdict(set)
    missed_fields = defaultdict(Counter)

    for calls in usages:
        seen_args = set()
        for c in calls:
            t = tools[c.tool]
            raw = count(c.output)
            t["calls"] += 1
            t["raw"] += raw
            if _is_error(c):
                t["errors"] += 1
                # The provider's own message usually names the bad parameter:
                # it is what the tool description needs to prevent.
                msg = " ".join(c.output.split())[:200]
                if msg not in t["error_examples"] and len(t["error_examples"]) < 3:
                    t["error_examples"].append(msg)
            key = (c.tool, c.arguments)
            t["duplicates"] += key in seen_args
            seen_args.add(key)
            if c.payload is None or not c.structured:
                if not _is_error(c):
                    t["prose_calls"] += 1
                    t["prose_tokens"] += raw
                t["unescaped"] += raw if c.payload is None else count(json.dumps(c.payload, ensure_ascii=False, separators=seps))
                continue
            t["unescaped"] += count(json.dumps(c.payload, ensure_ascii=False, separators=seps))
            judgeable[c.tool] |= c.judgeable
            per_call = defaultdict(int)
            for path, value in leaves(c.payload):
                key_name = path.rsplit(".", 1)[-1].removesuffix("[]")
                per_call[path] += count(f'"{key_name}": ' + json.dumps(value, ensure_ascii=False))
            for path, tok in per_call.items():
                f = field_stats[c.tool][path]
                f["tokens"] += tok
                f["seen"] += 1
                f["used"] += path in c.used
                f["echo"] += path in c.echo

    for r in rows:
        for tool, (n, raw, pr, ag, tr, ms) in r.per_tool.items():
            t = tools[tool]
            t["loo_raw"] += raw
            t["loo_prudent"] += pr
            t["loo_aggressive"] += ag
            t["loo_trimmed_calls"] += tr
            t["loo_misses"] += ms

    # Which fields a held-out run used and the projection removed, by name: counted by the leave-one-out measure
    # with the projection it learned without that run (up to 1.1.4 learned a second time here).
    for r in rows:
        for tool, fields in r.missed_fields.items():
            for f, n in fields.items():
                missed_fields[tool][f] += n

    for tool, fields in field_stats.items():
        for path, f in fields.items():
            if path in deployed_a.keep.get(tool, ()):
                status = "used" if f["used"] else "key"
            elif path in deployed_p.keep.get(tool, ()):
                status = "unjudged"
            else:
                status = "dropped"
            tools[tool]["fields"][path] = dict(f, status=status, missed=missed_fields[tool][path])

    total_raw = sum(t["raw"] for t in tools.values())
    return {
        "format": "tracetrim/report@1",
        "generator": about.as_dict(),
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unseen": unseen,
        "protect_ids": protect_ids,
        "traces": names,
        "totals": {
            "traces": len(names), "calls": sum(t["calls"] for t in tools.values()),
            "raw": total_raw, "unescaped": sum(t["unescaped"] for t in tools.values()),
            "loo_prudent": sum(r.prudent for r in rows), "loo_aggressive": sum(r.aggressive for r in rows),
            "loo_trimmed_calls": sum(r.trimmed_calls for r in rows), "loo_misses": sum(r.misses for r in rows),
            "loo_misses_prudent": sum(r.misses_prudent for r in rows),
            "errors": sum(t["errors"] for t in tools.values()),
            "duplicates": sum(t["duplicates"] for t in tools.values()),
        },
        "tools": dict(sorted(tools.items(), key=lambda x: -x[1]["raw"])),
    }


def savings(data: dict) -> dict:
    """The run-time counts of automatic mode (`savings.json`: per run and tool, the
    tokens the tools sent, the same outputs re-written whole, what the model received)
    as the report shows them: per run, per tool and in total, with the saving split in
    two. `reserialisation` + `fields` = `raw` - `sent`, in every row."""
    def row(raw, whole, sent, **more):
        return {**more, "raw": raw, "whole": whole, "sent": sent,
                "reserialisation": raw - whole, "fields": whole - sent}

    def add(into, t):
        for k in ("calls", "raw", "whole", "sent"):
            into[k] = into.get(k, 0) + t[k]

    runs, tools, total, expand = [], {}, {}, {"calls": 0, "tokens": 0}
    for run, entry in sorted(data["runs"].items()):
        one = {"calls": 0, "raw": 0, "whole": 0, "sent": 0}
        for tool, t in entry["tools"].items():
            add(one, t)
            add(tools.setdefault(tool, {}), t)
            add(total, t)
        e = entry.get("expand", {})
        expand["calls"] += e.get("calls", 0)
        expand["tokens"] += e.get("tokens", 0)
        runs.append(row(one["raw"], one["whole"], one["sent"], run=run, calls=one["calls"],
                        expand_calls=e.get("calls", 0), expand_tokens=e.get("tokens", 0)))
    return {
        "unit": data.get("unit", "tokens"),
        "runs": runs,
        "tools": {tool: row(t["raw"], t["whole"], t["sent"], calls=t["calls"])
                  for tool, t in sorted(tools.items(), key=lambda x: -x[1]["raw"])},
        "total": row(total.get("raw", 0), total.get("whole", 0), total.get("sent", 0), calls=total.get("calls", 0),
                     expand_calls=expand["calls"], expand_tokens=expand["tokens"]),
    }


def _n(x, lang):
    return i18n.num(x, lang)


def _p(x, lang=None):
    return f"{x * 100:.0f}%"


def _il(x) -> str:
    """Italian article before a percentage: l'8%, l'11%, l'80%, il 41%."""
    n = round(x * 100)
    return (f"l'{n}%" if n in (1, 8, 11) or str(n).startswith("8") else f"il {n}%")


def _calls(n, lang):
    if lang == "it":
        return f"{n} chiamata" if n == 1 else f"{n} chiamate"
    return f"{n} call" if n == 1 else f"{n} calls"


def suggestions(d: dict) -> list[dict]:
    """Deterministic rules. Each gives its id, level and text in every language of tracetrim.i18n.LANGS."""
    out = []
    T = d["totals"]
    total = T["raw"] or 1

    def add(rule, level, tool, v, en, it):
        # English and Italian are written here; the other languages come from the same values (tracetrim.i18n).
        out.append({"rule": rule, "level": level, "tool": tool, "en": en, "it": it,
                    **{lang: i18n.suggestion(rule, lang, v) for lang in i18n.EXTRA}})

    if T["traces"] < MIN_TRACES:
        add("few-traces", "warning", None,
            {"traces": T["traces"], "min": MIN_TRACES},
            f"Only {T['traces']} trace{'' if T['traces'] == 1 else 's'}: learned projections are unstable. Collect at least {MIN_TRACES}-10 runs of the same task before applying one.",
            f"Solo {T['traces']} tracc{'ia' if T['traces'] == 1 else 'e'}: le proiezioni apprese sono instabili. Raccogli almeno {MIN_TRACES}-10 esecuzioni dello stesso compito prima di applicarne una.")

    esc = 1 - T["unescaped"] / T["raw"] if T["raw"] else 0.0       # no tool output: nothing to re-serialise
    if esc >= ESCAPE_MIN_SAVING:
        add("escapes", "quick-win", None,
            {"tokens": T["raw"] - T["unescaped"], "share": esc},
            f"Tool outputs are indented or contain ASCII escapes (\\uXXXX). Writing them as compact UTF-8 JSON saves {_n(T['raw'] - T['unescaped'], 'en')} tokens ({_p(esc, 'en')}), with no loss of information.",
            f"Gli output dei tool sono indentati o contengono escape ASCII (\\uXXXX). Riscriverli come JSON UTF-8 compatto fa risparmiare {_n(T['raw'] - T['unescaped'], 'it')} token ({_p(esc, 'it')}), senza perdita di informazione.")

    if T["duplicates"]:
        add("duplicates", "quick-win", None,
            {"duplicates": T["duplicates"]},
            f"{_calls(T['duplicates'], 'en')} repeat an earlier call of the same run with identical arguments. Cache tool results within a run.",
            f"{_calls(T['duplicates'], 'it')} {'ripete' if T['duplicates'] == 1 else 'ripetono'} una chiamata precedente della stessa esecuzione con argomenti identici. Metti in cache i risultati dei tool all'interno di un'esecuzione.")

    for tool, t in d["tools"].items():
        share = t["raw"] / total
        if t["loo_raw"] and t["loo_trimmed_calls"]:
            saving = 1 - t["loo_prudent"] / t["loo_raw"]
            # Only tools the generated config will trim: a miss on a tool
            # left untouched is not a risk, and `expand` would have nothing to do.
            trimmed_by_config = share >= TRIM_MIN_SHARE and saving >= TRIM_MIN_SAVING
            if trimmed_by_config:
                add("trim", "saving", tool,
            {"tool": tool, "share": share, "saving": saving, "tokens": t["loo_raw"] - t["loo_prudent"], "calls": t["calls"]},
                    f"`{tool}` carries {_p(share, 'en')} of all tool-output tokens. On runs it was not learned from, the prudent projection removes {_p(saving, 'en')} of them ({_n(t['loo_raw'] - t['loo_prudent'], 'en')} tokens over {t['calls']} calls).",
                    f"`{tool}` porta {_il(share)} di tutti i token restituiti dai tool. Sulle esecuzioni da cui non ha imparato, la proiezione prudente ne toglie {_il(saving)} ({_n(t['loo_raw'] - t['loo_prudent'], 'it')} token su {t['calls']} chiamate).")
            rate = t["loo_misses"] / t["loo_trimmed_calls"]
            if trimmed_by_config and rate >= MISS_RATE and t["loo_trimmed_calls"] >= MIN_CALLS:
                top = [f for f, v in sorted(t["fields"].items(), key=lambda x: -x[1]["missed"]) if v["missed"]][:4]
                add("misses", "risk", tool,
            {"tool": tool, "misses": t["loo_misses"], "trimmed": t["loo_trimmed_calls"], "fields": top},
                    f"The aggressive projection of `{tool}` hid a field that a later step used in {t['loo_misses']} of {t['loo_trimmed_calls']} held-out calls ({', '.join(f'`{f}`' for f in top)}). Use the prudent projection, enable `expand`, and collect more traces before trimming aggressively.",
                    f"La proiezione aggressiva di `{tool}` ha nascosto un campo usato più avanti in {_calls(t['loo_misses'], 'it')} su {t['loo_trimmed_calls']} ({', '.join(f'`{f}`' for f in top)}). Usa la proiezione prudente, attiva `expand` e raccogli più tracce prima di tagliare in modo aggressivo.")
        if t["prose_tokens"] / total >= PROSE_MIN_SHARE:
            add("prose", "limit", tool,
            {"tool": tool, "tokens": t["prose_tokens"], "share": t["prose_tokens"] / total},
                f"`{tool}` returns text, not structured data: {_n(t['prose_tokens'], 'en')} tokens ({_p(t['prose_tokens'] / total, 'en')} of all tool output). Field projection cannot reduce it: call it less often, ask for shorter outputs, or use a compressor for prose.",
                f"`{tool}` restituisce testo, non dati strutturati: {_n(t['prose_tokens'], 'it')} token ({_p(t['prose_tokens'] / total, 'it')} di tutto l'output dei tool). La proiezione dei campi non può ridurlo: chiamalo meno spesso, chiedi output più brevi o usa un compressore per la prosa.")
        fields = t["fields"]
        ftot = sum(v["tokens"] for v in fields.values()) or 1
        unj = {f: v for f, v in fields.items() if v["status"] == "unjudged"}
        unj_tok = sum(v["tokens"] for v in unj.values())
        if unj_tok / ftot >= UNJUDGED_SHARE and t["calls"] >= MIN_CALLS and share >= REVIEW_MIN_SHARE:
            top = [f for f, _ in sorted(unj.items(), key=lambda x: -x[1]["tokens"])][:5]
            add("unjudged", "review", tool,
            {"tool": tool, "share": unj_tok / ftot, "fields": top},
                f"In `{tool}`, {_p(unj_tok / ftot, 'en')} of field tokens belong to fields the usage detector cannot judge (small numbers, booleans, short labels), e.g. {', '.join(f'`{f}`' for f in top)}. The prudent projection keeps them: review them, and drop explicitly those the task does not need.",
                f"In `{tool}`, {_il(unj_tok / ftot)} dei token dei campi appartiene a campi che il rilevamento d'uso non sa giudicare (numeri piccoli, booleani, etichette brevi), ad esempio {', '.join(f'`{f}`' for f in top)}. La proiezione prudente li tiene: rivedili, ed elimina esplicitamente quelli che al compito non servono.")
        if t["errors"]:
            add("errors", "quick-win", tool,
            {"tool": tool, "errors": t["errors"], "calls": t["calls"]},
                f"{t['errors']} of {_calls(t['calls'], 'en')} to `{tool}` returned an error. Each one costs a full turn: check the arguments the agent sends, and state the constraint in the tool description.",
                f"{_calls(t['errors'], 'it')} su {t['calls']} a `{tool}` {'ha' if t['errors'] == 1 else 'hanno'} restituito un errore. Ognuna costa un turno intero: controlla gli argomenti che l'agente invia e scrivi il vincolo nella descrizione del tool.")

    turns = d.get("turns") or {}
    if turns.get("suggest_strategy"):
        several, earlier = turns["share_of_turns_with_several_calls"], turns["share_of_calls_that_could_have_been_made_earlier"]
        add("strategy", "review", None,
            {"runs": turns["runs"], "several": several, "earlier": earlier},
            f"In the {turns['runs']} recorded runs the agent nearly always makes one tool call per turn ({_p(several, 'en')} of the turns with calls make several), and about {_p(earlier, 'en')} of its calls had their arguments available before the turn in which they were made (an estimate: identifiers only). The strategy level of the tracetrim skill might reduce the turns: on one model that works this way it took a task from a median of 17 turns to 6 (`docs/measures-v04.md`, E3); on a model that already groups its calls it changed nothing.",
            f"Nelle {turns['runs']} esecuzioni registrate l'agente fa quasi sempre una chiamata per turno ({_il(several)} dei turni con chiamate ne fa più di una), e circa {_il(earlier)} delle chiamate aveva gli argomenti disponibili prima del turno in cui è partita (una stima: solo identificativi). La strategia della skill di tracetrim potrebbe ridurre i turni: su un modello che lavora così ha portato un compito da una mediana di 17 turni a 6 (`docs/measures-v04.md`, E3); su un modello che raggruppa già le chiamate non ha cambiato nulla.")

    g = d.get("grounding")
    if g and g.get("suggest"):
        bad = [r for r in g["runs"] if r.get("suspect")]
        n = sum(len(r["suspect"]) for r in bad)
        tools = sorted({t for r in bad for t in r["filtered_tools"]})
        names = ", ".join(f"`{t}`" for t in tools)
        add("grounding", "risk", None,
            {"bad": len(bad), "checked": g["runs_checked"], "values": n, "tools": tools},
            f"In {len(bad)} of {g['runs_checked']} runs with an answer, the answer cites {n} value{'' if n == 1 else 's'} "
            f"(numbers, codes) that no tool output of its run contains, while results of {names} reached the model "
            "filtered. An agent that no longer sees a field can make one up: check those answers. A value may also be "
            "a computation of the agent, or come from the user's request, which tracetrim does not see; values found "
            "unsupported also in runs where nothing was filtered are not counted here.",
            f"In {len(bad)} esecuzioni con risposta su {g['runs_checked']}, la risposta cita {n} "
            f"{'valore' if n == 1 else 'valori'} (numeri, codici) che nessun output dei tool della sua esecuzione contiene, "
            f"mentre i risultati di {names} arrivavano al modello filtrati. Un agente che non vede più un campo può "
            "inventarlo: controlla quelle risposte. Un valore può anche essere un calcolo dell'agente, o venire dalla "
            "richiesta dell'utente, che tracetrim non vede; i valori non trovati anche in esecuzioni senza filtro qui "
            "non sono contati.")

    defs = d.get("definitions")
    if defs and defs.get("suggest"):
        k, n, runs = len(defs["never_called"]), defs["offered"], defs["runs"]
        shown = min(k, DEFINITIONS_LIST_MAX)
        listed_en = f"the first {shown} of {k} below" if k > shown else "below"
        listed_it = f"i primi {shown} di {k} qui sotto" if k > shown else "qui sotto"
        add("definitions", "review", None,
            {"tokens_per_turn": defs["tokens_per_turn"], "never": k, "offered": n, "runs": runs, "shown": shown, "never_tokens": defs["never_called_tokens"], "never_share": defs["never_called_share"]},
            f"The tool definitions weigh {_n(defs['tokens_per_turn'], 'en')} tokens, and the model receives them on every turn. "
            f"{k} of the {n} tools were never called in {runs} complete runs and weigh {_n(defs['never_called_tokens'], 'en')} "
            f"of those tokens ({_p(defs['never_called_share'], 'en')}); heaviest first, {listed_en}. If this agent does not need "
            "them, disable them in the host; or use native tool search where the host or the API has it, which loads a "
            "definition only when it is needed (Claude Code: on by default for MCP tools; OpenAI Responses API: `tool_search` "
            f"with `defer_loading`). Never called in {runs} runs does not mean never needed: a different request may need one "
            "of them. tracetrim hides no tool by itself.",
            f"Le definizioni dei tool pesano {_n(defs['tokens_per_turn'], 'it')} token, e il modello le riceve a ogni turno. "
            f"{k} dei {n} tool non sono mai stati chiamati in {runs} esecuzioni complete e ne pesano {_n(defs['never_called_tokens'], 'it')} "
            f"({_il(defs['never_called_share'])}); dal più pesante, {listed_it}. Se a questo agente non servono, disattivali "
            "nell'host; oppure usa la ricerca nativa dei tool dove l'host o l'API ce l'ha, che carica una definizione solo quando "
            "serve (Claude Code: attiva per default sui tool MCP; Responses API di OpenAI: `tool_search` con `defer_loading`). "
            f"«Mai chiamato in {runs} esecuzioni» non vuol dire «mai necessario»: una richiesta diversa può averne bisogno. "
            "tracetrim non nasconde nessun tool da solo.")

    for x in out:
        x["action"] = _action(x, d)
    order = {"warning": 0, "risk": 1, "saving": 2, "quick-win": 3, "review": 4, "limit": 5}
    return sorted(out, key=lambda s: order[s["level"]])


def _action(s: dict, d: dict) -> dict:
    """What to do about a suggestion. `auto` actions go into the generated
    config; `manual` ones need a person, and carry what that person needs."""
    tool = s["tool"]
    t = d["tools"].get(tool, {}) if tool else {}
    if s["rule"] == "trim":
        return {"kind": "auto", "type": "trim", "tool": tool, "mode": "prudent"}
    if s["rule"] == "misses":
        return {"kind": "auto", "type": "expand", "tool": tool}
    if s["rule"] == "escapes":
        return {"kind": "auto", "type": "unescape"}
    if s["rule"] == "duplicates":
        # Manual: a repeated call is not always a wasted one (prices, statuses
        # and logs change between calls), and only a person can tell.
        return {"kind": "manual", "type": "cache"}
    if s["rule"] == "errors":
        return {"kind": "manual", "type": "fix_tool_description", "tool": tool,
                "examples": t.get("error_examples", [])}
    if s["rule"] == "unjudged":
        unj = [f for f, v in sorted(t["fields"].items(), key=lambda x: -x[1]["tokens"]) if v["status"] == "unjudged"]
        return {"kind": "manual", "type": "review_fields", "tool": tool, "fields": unj[:10]}
    if s["rule"] == "prose":
        return {"kind": "manual", "type": "limit_text_output", "tool": tool}
    if s["rule"] == "definitions":
        # Manual, always: only a person knows whether a tool never called is a tool never needed.
        never = d["definitions"]["never_called"][:DEFINITIONS_LIST_MAX]
        return {"kind": "manual", "type": "unused_tools",
                "tools": [{"name": x["name"], "tokens": x["tokens"]} for x in never]}
    if s["rule"] == "grounding":
        # Manual, always: only a person can tell a made-up value from a computed one.
        runs = [r for r in d["grounding"]["runs"] if r.get("suspect")]
        return {"kind": "manual", "type": "check_values",
                "runs": [f"{r['run']}: {', '.join(r['suspect'])}" for r in runs[:DEFINITIONS_LIST_MAX]]}
    if s["rule"] == "strategy":
        # Manual, always: the strategy is instructions for the model, and whether
        # to give them is the choice of who writes the agent's prompts.
        return {"kind": "manual", "type": "skill_strategy"}
    return {"kind": "manual", "type": "collect_traces", "min": MIN_TRACES}


ACTION_TEXT = {
    "trim": ("The generated config applies the prudent projection to this tool.",
             "La configurazione generata applica la proiezione prudente a questo tool."),
    "expand": ("The generated config enables `expand` for this tool, so a hidden field can be fetched back.",
               "La configurazione generata attiva `expand` per questo tool, così un campo nascosto si può recuperare."),
    "unescape": ("The generated config re-serialises every JSON output as UTF-8.",
                 "La configurazione generata riserializza ogni output JSON in UTF-8."),
    "cache": ("If these results do not change within a run, cache them in the agent or the tool: tracetrim does not.",
              "Se questi risultati non cambiano all'interno di un'esecuzione, mettili in cache nell'agente o nel tool: tracetrim non lo fa."),
    "fix_tool_description": ("Rewrite the tool description with the constraint these errors reveal:",
                             "Riscrivi la descrizione del tool con il vincolo che questi errori rivelano:"),
    "review_fields": ("Review these fields and add the ones the task does not need to the projection's dropped list:",
                      "Rivedi questi campi e aggiungi alla lista dei campi tagliati quelli che al compito non servono:"),
    "limit_text_output": ("Reduce how often this tool is called, or how much text it returns.",
                          "Riduci quante volte viene chiamato questo tool, o quanto testo restituisce."),
    "skill_strategy": ("Try the strategy level of the skill and compare the turns: `--skill-strategy` on the proxy, `AutoTrim(strategy=True)` for function calling, `tracetrim skill --strategy` to print the text. tracetrim never turns it on by itself.",
                       "Prova la strategia della skill e confronta i turni: `--skill-strategy` sul proxy, `AutoTrim(strategy=True)` con il function calling, `tracetrim skill --strategy` per stampare il testo. tracetrim non la accende mai da solo."),
    "unused_tools": ("Tools never called, with the tokens of their definition. Disable those this agent does not need, in the host's settings; tracetrim does not:",
                     "Tool mai chiamati, con i token della loro definizione. Disattiva nell'host quelli che a questo agente non servono; tracetrim non lo fa:"),
    "check_values": ("Read these answers against the tool outputs of their run. If a value was made up, stop trimming the tool it should have come from (remove it from the config) or keep that field: tracetrim does not change anything by itself:",
                     "Rileggi queste risposte confrontandole con gli output dei tool della loro esecuzione. Se un valore è inventato, smetti di filtrare il tool da cui sarebbe dovuto arrivare (toglilo dalla configurazione) o tieni quel campo: tracetrim non cambia nulla da solo:"),
    "collect_traces": ("Collect more runs of the same task, then generate the report again.",
                       "Raccogli altre esecuzioni dello stesso compito, poi rigenera il report."),
}


def build_config(d: dict, sugg: list[dict], usages, projection_file: str, *, training=None):
    """The runtime config and the projection it points to, from `auto` actions only."""
    trim = {s["action"]["tool"] for s in sugg if s["action"]["type"] == "trim"}
    expand = {s["action"]["tool"] for s in sugg if s["action"]["type"] == "expand"} & trim
    p = (training.project("prudent") if training is not None else
         learn(usages, "prudent", unseen=d["unseen"], protect_ids=d.get("protect_ids", True)))
    p.keep = {t: v for t, v in p.keep.items() if t in trim}
    p.seen = {t: v for t, v in p.seen.items() if t in trim}
    config = {
        "format": "tracetrim/config@1",
        "generated": d["generated"],
        "projection": projection_file,
        # Lossless, so on by default even when the saving is below the
        # report's threshold.
        "unescape": True,
        # Also lossless: no spaces after separators (since 0.4).
        "compact": True,
        # expand on for every trimmed tool (safeguard of 0.2): a wrong cut costs
        # one round trip instead of a wrong answer.
        "tools": {t: {"trim": "prudent", "expand": True} for t in sorted(trim)},
    }
    return config, p


L = {
    "title": ("Tool-output telemetry", "Telemetria degli output dei tool"),
    "nav_overview": ("Overview", "Panoramica"), "nav_tokens": ("Tokens", "Token"), "nav_saving": ("Saving", "Risparmio"),
    "nav_sugg": ("Suggestions", "Suggerimenti"), "nav_tools": ("Tools", "Tool"), "nav_fields": ("Fields", "Campi"),
    "nav_defs": ("Definitions", "Definizioni"), "nav_answers": ("Answers", "Risposte"),
    "nav_adaptive": ("Learning", "Apprendimento"), "nav_measures": ("Measures", "Misure"),
    "nav_config": ("Config", "Configurazione"), "nav_about": ("About", "Informazioni"),
    "theme": ("Theme: automatic, light, dark", "Tema: automatico, chiaro, scuro"),
    "theme_auto": ("Auto", "Auto"), "theme_light": ("Light", "Chiaro"), "theme_dark": ("Dark", "Scuro"),
    "status_on": ("Trimming on", "Filtro attivo"), "status_learning": ("Learning", "In apprendimento"),
    "status_offline": ("Offline report", "Report offline"), "mode": ("Mode", "Modalità"),
    "status_offline_text": ("Made with `tracetrim report` from recorded traces: nothing is applied.",
                            "Generato con `tracetrim report` da tracce registrate: nulla viene applicato."),
    "updated": ("updated", "aggiornato"), "status_trimmed": ("tools trimmed", "tool filtrati"),
    "status_runs": ("complete runs", "esecuzioni complete"), "status_promoted": ("promoted fields", "campi promossi"),
    "config_json": ("Runtime config (JSON)", "Configurazione di runtime (JSON)"),
    "copy": ("Copy", "Copia"), "copied": ("Copied", "Copiato"),
    "more": ("more", "altri"),
    "subtitle": ("What the tools sent, what the agent used, and what to change.",
                 "Cosa hanno restituito i tool, cosa ha usato l'agente, cosa cambiare."),
    "traces": ("traces", "tracce"), "calls": ("tool calls", "chiamate ai tool"),
    "raw": ("tool-output tokens", "token restituiti dai tool"),
    "prudent": ("after prudent projection", "dopo la proiezione prudente"),
    "aggressive": ("after aggressive projection", "dopo la proiezione aggressiva"),
    "misses": ("held-out misses", "mancati sulle esecuzioni escluse"),
    "suggestions": ("Suggestions", "Suggerimenti"), "none": ("No suggestion.", "Nessun suggerimento."),
    "auto_title": ("Automatic mode", "Modalità automatica"),
    "auto_applied": ("trimming is on, with the automatic actions below",
                     "il filtro è attivo, con le azioni automatiche qui sotto"),
    "auto_learning": ("learning: complete runs", "in apprendimento: esecuzioni complete"),
    "auto_off": ("switched off after too many expand requests", "spenti dopo troppe richieste di expand"),
    "mixed_runs": ("warning: runs with calls from several HTTP sessions",
                   "avviso: esecuzioni con chiamate da più sessioni HTTP"),
    "mixed_occurrences": ("HTTP sessions counted across those runs (once per session per run)",
                          "sessioni HTTP in quelle esecuzioni (una volta per sessione ed esecuzione)"),
    "auto_refused": ("closings refused for lack of an answer (the run stayed open)",
                     "chiusure rifiutate perché senza risposta (l'esecuzione è rimasta aperta)"),
    "auto_followed": ("runs closed by an answer and followed by calls within the idle pause (see the note below)",
                      "esecuzioni chiuse con risposta e seguite da chiamate entro la pausa di inattività (vedi la "
                      "nota qui sotto)"),
    "savings": ("Where the saving comes from", "Da dove viene il risparmio"),
    "savings_sub": (
        "Counted at run time, call by call: what the tools sent against what the model received. "
        "Re-serialisation is what writing the same data as compact JSON saves, with nothing removed. "
        "Fields is what the projection removes on top of that, net of the line that lists what was removed. "
        "The two add up to the total. While learning, no field is removed: only re-serialisation saves. "
        "What expand gave back is counted apart, and is not subtracted from the saving.",
        "Contato a runtime, chiamata per chiamata: ciò che hanno inviato i tool rispetto a ciò che ha ricevuto il "
        "modello. La riserializzazione è ciò che si risparmia riscrivendo gli stessi dati come JSON compatto, senza "
        "togliere nulla. I campi sono ciò che la proiezione toglie in più, al netto della riga che elenca i campi "
        "tolti. Le due voci sommate danno il totale. Durante l'apprendimento nessun campo viene tolto: risparmia "
        "solo la riserializzazione. Ciò che ha restituito expand è contato a parte, e non è sottratto dal risparmio."),
    "sent_by_tools": ("sent by the tools", "inviati dai tool"),
    "received_by_model": ("received by the model", "ricevuti dal modello"),
    "saving_total": ("total", "totale"),
    "saving_reserialisation": ("re-serialisation", "riserializzazione"),
    "saving_fields": ("fields", "campi"),
    "expand_back": ("given back by expand (calls, size)", "restituiti da expand (chiamate, dimensione)"),
    "all_runs": ("all runs", "tutte le esecuzioni"),
    "savings_per_tool": ("Per tool, all runs", "Per tool, tutte le esecuzioni"),
    "project": ("Project and author", "Progetto e autore"),
    "hero": ("of tool-output tokens removed by the prudent projection, on runs it never saw",
             "dei token restituiti dai tool tolti dalla proiezione prudente, su esecuzioni mai viste"),
    "misses_prudent": ("held-out misses, prudent projection (the one applied)",
                       "mancati sulle esecuzioni escluse, proiezione prudente (quella applicata)"),
    "hero_sub": ("aggressive projection", "proiezione aggressiva"),
    # With one run there is nothing to hold out: the saving is re-serialisation only.
    "hero_one": ("of tool-output tokens saved so far by writing compact JSON alone, no field removed: "
                 "learning and testing a projection needs at least two runs",
                 "dei token restituiti dai tool risparmiati finora solo riscrivendo il JSON compatto, senza togliere "
                 "campi: per imparare e verificare una proiezione servono almeno due esecuzioni"),
    "where": ("Where the tokens go", "Dove vanno i token"),
    "where_sub": ("Tokens returned by each tool, and the part the prudent projection removes on held-out runs.",
                  "Token restituiti da ogni tool, e la parte che la proiezione prudente toglie sulle esecuzioni escluse."),
    "kept": ("reaches the agent", "arriva all'agente"), "removed": ("removed", "tolto"),
    "others": ("other tools", "altri tool"),
    "fields_bar": ("field tokens", "token dei campi"),
    "n_fields": ("fields", "campi"),
    "project_text": ("Report generated by an open-source tool that learns, from an agent's traces, which fields of tool outputs the agent visibly reuses.",
                     "Report generato da uno strumento open source che impara, dalle tracce di un agente, quali campi degli output dei tool l'agente riusa in modo visibile."),
    "auto": ("AUTOMATIC", "AUTOMATICA"), "manual": ("MANUAL", "MANUALE"),
    "actions": ("Automatic actions", "Azioni automatiche"),
    "config_text": ("The automatic actions are collected in a runtime config, written next to this report together with "
                    "the projection it uses. Apply it as it is, or edit it first: it is plain JSON.",
                    "Le azioni automatiche sono raccolte in una configurazione di runtime, scritta accanto a questo report "
                    "insieme alla proiezione che usa. Puoi applicarla così com'è o modificarla prima: è un semplice JSON."),
    "per_tool": ("Per tool", "Per tool"), "tool": ("tool", "tool"), "share": ("share", "quota"),
    "errors": ("errors", "errori"), "prose": ("text tokens", "token di testo"),
    "fields": ("Fields", "Campi"), "field": ("field", "campo"), "tokens": ("tokens", "token"),
    "seen": ("in calls", "in chiamate"), "used": ("used in", "usato in"),
    "status": ("decision", "decisione"), "missed": ("missed", "mancato"),
    "used_s": ("kept: used", "tenuto: usato"), "key_s": ("kept: record key", "tenuto: chiave del record"),
    "unjudged_s": ("kept: not judgeable", "tenuto: non giudicabile"), "dropped_s": ("dropped", "tagliato"),
    "promoted_s": ("kept: promoted on evidence", "tenuto: promosso per evidenza"),
    "adaptive": ("Adaptive learning", "Apprendimento adattivo"),
    "adaptive_sub": (
        "What the agent asked back with expand, what it used when a result was passed whole, and the fields kept "
        "because of it. Evidence counts runs, not requests. A field seen whole and not seen reused is not proven "
        "useless: nothing is removed on that ground.",
        "Cosa l'agente ha richiesto con expand, cosa ha usato quando un risultato è passato intero, e i campi tenuti "
        "di conseguenza. Le evidenze contano le esecuzioni, non le richieste. Un campo visto intero e non visto "
        "riusato non è dimostrato inutile: nulla viene tolto per questo."),
    "promotions": ("Promoted fields", "Campi promossi"), "no_promotions": ("No field promoted.", "Nessun campo promosso."),
    "resumed": ("Fields recovered with expand and then kept by ordinary learning (the tool's switch-off count restarted)",
                "Campi recuperati con expand e poi tenuti dall'apprendimento ordinario (il conteggio di sospensione del tool è ripartito)"),
    "promote_off": ("promotion is off: evidence is recorded, nothing is promoted",
                    "la promozione è spenta: le evidenze sono registrate, nulla viene promosso"),
    "reason": ("reason", "motivo"), "runs": ("runs", "esecuzioni"), "state": ("state", "stato"),
    "reason_requested": ("requested by name", "richiesto per nome"),
    "reason_reused": ("reused after recovery", "riusato dopo il recupero"),
    "reason_used_in_full": ("used in an unfiltered result", "usato in un risultato non filtrato"),
    "status_active": ("active", "attiva"), "status_reverted": ("reverted", "annullata"),
    "effect": ("Effect of promotions on tokens (recorded calls of the tool)",
               "Effetto delle promozioni sui token (chiamate registrate del tool)"),
    "without_p": ("without promotions", "senza promozioni"), "with_p": ("with promotions", "con promozioni"),
    "evidence": ("Evidence per field (runs)", "Evidenze per campo (esecuzioni)"),
    "ev_requested": ("requested", "richiesto"), "ev_recovered": ("recovered", "recuperato"),
    "ev_reused": ("reused after recovery", "riusato dopo il recupero"),
    "ev_seen_full": ("seen unfiltered", "visto non filtrato"),
    "ev_used_in_full": ("used when unfiltered", "usato quando non filtrato"),
    "exposure": ("Filtered and sampled results", "Risultati filtrati e campionati"),
    "filtered": ("filtered", "filtrati"), "expanded": ("of which expanded", "di cui espansi"),
    "broad": ("runs asking for everything", "esecuzioni con richiesta di tutto"),
    "sampled": ("sampled (passed whole)", "campionati (passati interi)"),
    "cheaper": ("passed whole: filtering would not have been shorter",
                "passati interi: il filtro non li avrebbe accorciati"),
    "conflicts": ("To review: reverted fields that ordinary learning would keep (they stay removed)",
                  "Da rivedere: campi annullati che l'apprendimento ordinario terrebbe (restano tolti)"),
    "compared_in": ("run-time comparisons are in", "i confronti a runtime sono in"),
    "eligible": ("would be filtered", "da filtrare"),
    "removed_u": ("removed by filtering", "tolto dal filtro"), "added_u": ("added by sampling", "aggiunto dal campionamento"),
    "lists": ("List elements reused (a measure: rows are never cut)",
              "Elementi di lista riusati (una misura: le righe non vengono mai tagliate)"),
    "list": ("list", "lista"), "size_args": ("size asked (argument: median, max)", "dimensione richiesta (argomento: mediana, massimo)"),
    "received": ("received (median, max)", "ricevuti (mediana, massimo)"),
    "reused_n": ("reused (median, max)", "riusati (mediana, massimo)"),
    "from_top": ("calls where the reused ones are the first", "chiamate in cui i riusati sono i primi"),
    "deepest": ("deepest reused (median, max)", "riusato più in basso (mediana, massimo)"),
    "arguments": ("Arguments: distinct values observed", "Argomenti: valori distinti osservati"),
    "turns": ("Tool calls per turn (a measure)", "Chiamate per turno (una misura)"),
    "turns_sub": ("Per run. The calls that could have been made earlier are an estimate: a call is taken to depend on "
                  "an earlier result only when one of its arguments is an identifier that result returned.",
                  "Per esecuzione. Le chiamate che potevano partire prima sono una stima: una chiamata si considera "
                  "dipendente da un risultato precedente solo se uno dei suoi argomenti è un identificativo restituito da quel risultato."),
    "run": ("run", "esecuzione"), "turns_n": ("turns", "turni"), "fewest": ("fewest turns needed", "minimo di turni necessari"),
    "several": ("turns with several calls", "turni con più chiamate"),
    "could_earlier": ("calls that could have been made earlier", "chiamate che potevano partire prima"),
    "argument": ("argument", "argomento"), "distinct": ("distinct values", "valori distinti"),
    "definitions": ("Tool definitions (a measure)", "Definizioni dei tool (una misura)"),
    "defs_per_turn": ("tokens of definitions per turn", "token di definizioni per turno"),
    "defs_offered": ("tools offered", "tool offerti"),
    "defs_called": ("called in at least one complete run", "chiamati in almeno un'esecuzione completa"),
    "defs_never": ("never called", "mai chiamati"),
    "defs_never_tokens": ("tokens of the tools never called", "token dei tool mai chiamati"),
    "defs_own": ("of which tracetrim's own tools", "di cui tool di tracetrim"),
    "defs_note": (
        "Counted on the JSON of each definition as published, with the tokenizer of this report: the provider bills its own "
        "rendering of the definitions, usually somewhat less. The model receives them on every turn, so a run carries this "
        "figure times its turns. tracetrim does not see the model's turns, so the weight per run is not estimated here.",
        "Contati sul JSON di ogni definizione come viene pubblicata, con il tokenizer di questo report: il fornitore fattura "
        "una sua resa delle definizioni, di solito un po' meno. Il modello le riceve a ogni turno, quindi un'esecuzione ne "
        "porta questo numero per i suoi turni. tracetrim non vede i turni del modello, quindi il peso per esecuzione qui "
        "non è stimato."),
    "defs_list": ("Tools never called, heaviest first", "Tool mai chiamati, dal più pesante"),
    "grounding": ("Values of the answers that no tool returned", "Valori delle risposte che nessun tool ha restituito"),
    "gr_runs": ("runs with an answer checked", "esecuzioni con risposta controllate"),
    "gr_values": ("values checked (numbers, codes)", "valori controllati (numeri, codici)"),
    "gr_bad": ("runs with a value no output contains", "esecuzioni con un valore che nessun output contiene"),
    "gr_run": ("run", "esecuzione"), "gr_found": ("values not found", "valori non trovati"),
    "gr_filtered": ("filtered in the run", "filtrati nell'esecuzione"),
    "gr_none": ("Every value checked appears in a tool output of its run.",
                "Ogni valore controllato compare in un output dei tool della sua esecuzione."),
    "gr_note": (
        "A deterministic check, with no model: numbers of three digits or more and codes containing them, searched in every "
        "tool output of the run as the tool returned it (amounts in minor units, dates from timestamps and grouped numbers "
        "included, and percentages and roundings of a value) and in the arguments the agent wrote. Years are not checked, "
        "so neither is any 4-digit number from 1900 to 2099. It does not see a true value attributed to "
        "the wrong item; a value computed by the agent, or taken from the user's request, is listed and may be right. In "
        "bold, values of runs with filtered results that are not also unsupported in runs with nothing filtered: the only "
        "ones trimming can account for.",
        "Un controllo deterministico, senza modelli: numeri di almeno tre cifre e codici che li contengono, cercati in ogni "
        "output dei tool dell'esecuzione così come il tool l'ha restituito (con importi in centesimi, date da timestamp e "
        "numeri con separatori, oltre a percentuali e arrotondamenti di un valore) e negli argomenti scritti dall'agente. "
        "Gli anni non sono controllati, quindi nemmeno un numero di quattro cifre tra 1900 e 2099. Non vede un valore vero "
        "attribuito all'elemento sbagliato; un valore calcolato dall'agente, o preso dalla richiesta dell'utente, compare "
        "nell'elenco e può essere giusto. In grassetto i valori di esecuzioni con risultati filtrati che non compaiono "
        "anche in esecuzioni senza filtro: gli unici che possono dipendere dal filtro."),
    "method": ("How to read this report", "Come leggere questo report"),
    "method_text": (
        "Savings and misses are measured leave-one-out: each run is trimmed with a projection learned from the other runs. "
        "A miss is a call where the aggressive projection removed a field that the run used later. Usage is a lower bound: "
        "a field read only while reasoning is invisible. Field tokens are the tokens of each field's fragment, summed over calls. "
        "Suggestions come from fixed rules; the numbers are in report.json.",
        "Risparmi e mancati sono misurati lasciando fuori un'esecuzione alla volta: ogni esecuzione è tagliata con una proiezione imparata dalle altre. "
        "Un mancato è una chiamata in cui la proiezione aggressiva ha tolto un campo che l'esecuzione ha usato più avanti. L'uso è un limite inferiore: "
        "un campo letto solo nel ragionamento non si vede. I token di un campo sono quelli del suo frammento, sommati sulle chiamate. "
        "I suggerimenti vengono da regole fisse; i numeri sono in report.json."),
    "level": {"warning": ("warning", "attenzione"), "risk": ("risk", "rischio"), "saving": ("saving", "risparmio"),
              "quick-win": ("quick win", "intervento rapido"), "review": ("review", "da rivedere"),
              "limit": ("limit", "limite")},
}


def _text(key, lang, sub=None) -> str:
    """A label in one language: English and Italian from `L`, the others from tracetrim.i18n (English if missing)."""
    if lang in ("en", "it"):
        v = L[key][sub] if sub else L[key]
        return v[0 if lang == "en" else 1]
    return i18n.label(key, lang, sub) or _text(key, "en", sub)


def _langs(render) -> str:
    """One span per language; the page shows the one chosen."""
    return "".join(f'<span lang="{lang}">{render(lang)}</span>' for lang in i18n.LANGS)


def _t(key, sub=None):
    return _langs(lambda lang: html.escape(_text(key, lang, sub)))


def _tmd(key):
    """A label with `code` in it."""
    return _langs(lambda lang: _md(_text(key, lang)))


def _num(x):
    return _langs(lambda lang: _n(x, lang))


def _md(s):
    # Backticks become code; everything else is escaped.
    parts = html.escape(s).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))


def _compact(x, lang):
    """Axis-style compact figures: 593K, 1.8M (it: 1,8M)."""
    for div, suf in ((1e6, "M"), (1e3, "K")):
        if x >= div:
            v = f"{x / div:.1f}".rstrip("0").rstrip(".") if x < 10 * div else f"{x / div:.0f}"
            return i18n.decimal(v, lang) + suf
    return _n(x, lang)


def _tip(value, label) -> str:
    """Tooltip payload, one per language: the value leads, the label follows. `value` and `label` take the
    language. JSON in data attributes, read with textContent: names come from tool outputs."""
    a = lambda v: html.escape(json.dumps(v, ensure_ascii=False), quote=True)   # noqa: E731
    return "".join(f' data-tip-{lang}="{a([value(lang), label(lang)])}"' for lang in i18n.LANGS)


def _segments(parts) -> str:
    """Touching fills separated by the 2px surface gap; zero parts draw nothing.
    Each fill sits in a taller transparent hit target."""
    return "".join(
        f'<span class="hit" style="flex:{v} 1 0"{tip}><span class="fill {kind}"></span></span>'
        for v, kind, tip in parts if v > 0)


def _chart_tools(d) -> str:
    tools = list(d["tools"].items())
    rows = [(f"<code>{html.escape(n)}</code>", n, t["raw"], t["loo_prudent"] if t["loo_raw"] else t["raw"])
            for n, t in tools[:7]]
    if len(tools) > 7:
        rest = tools[7:]
        rows.append((_t("others") + f" ({len(rest)})", f"other tools ({len(rest)})",
                     sum(t["raw"] for _, t in rest),
                     sum(t["loo_prudent"] if t["loo_raw"] else t["raw"] for _, t in rest)))
    top = max((r[2] for r in rows), default=1) or 1
    out = []
    for label, name, raw, kept in rows:
        kept = min(kept, raw)
        cut = raw - kept
        share = cut / raw if raw else 0.0          # a tool that returned nothing removes nothing
        parts = [
            (kept, "kept", _tip(lambda lg, k=kept: f"{_n(k, lg)} {_text('tokens', lg)}",
                                lambda lg, nm=name: f"{nm} · {_text('kept', lg)}")),
            (cut, "removed", _tip(lambda lg, c=cut, sh=share: f"{_n(c, lg)} {_text('tokens', lg)} ({i18n.pct(sh, lg)})",
                                  lambda lg, nm=name: f"{nm} · {_text('removed', lg)}")),
        ]
        pct = f" · −{share:.0%}" if share >= 0.005 else ""
        out.append(
            f'<div class="brow"><div class="bname">{label}</div>'
            f'<div class="btrack"><div class="bbar" style="width:{raw / top * 82:.2f}%">{_segments(parts)}</div>'
            f'<span class="bval">{_langs(lambda lg, r=raw: _compact(r, lg))}{pct}</span></div></div>')
    legend = (f'<div class="legend"><span><i class="sw kept"></i>{_t("kept")}</span>'
              f'<span><i class="sw removed"></i>{_t("removed")}</span></div>')
    return (f'<h2>{_t("where")}</h2><p class="sub">{_t("where_sub")}</p>{legend}'
            f'<div class="chart" role="img" aria-label="Tokens per tool, kept and removed">{"".join(out)}</div>')


FIELD_KINDS = ("used", "key", "unjudged", "promoted", "dropped")


def _fields_bar(t) -> str:
    tok = {k: sum(v["tokens"] for v in t["fields"].values() if v["status"] == k) for k in FIELD_KINDS}
    total = sum(tok.values())
    if not total:
        return ""
    parts = [(tok[k], k, _tip(lambda lg, k=k: f"{_n(tok[k], lg)} {_text('tokens', lg)} ({i18n.pct(tok[k] / total, lg)})",
                              lambda lg, k=k: _text(k + "_s", lg))) for k in FIELD_KINDS]
    return f'<div class="thin">{_segments(parts)}</div>'


def _table(head: list[str], rows: list[list[str]]) -> str:
    return ('<div class="scroll"><table><thead><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table></div>")


def _pair(m: dict) -> str:
    return "—" if m["median"] is None else f'{m["median"]:g}, {m["max"]:g}'


def _savings_html(d: dict) -> str:
    """Where the saving comes from: present in reports written by automatic mode since 0.4.1."""
    s = d.get("savings")
    if not s or not s["runs"]:
        return ""

    def cells(r):
        raw = r["raw"] or 1
        share = lambda x: "—" if not r["raw"] else f"{-x / raw:+.0%}"        # noqa: E731
        return [_num(r["raw"]), _num(r["sent"]), f'<b>{share(r["raw"] - r["sent"])}</b>',
                f'{share(r["reserialisation"])} ({_num(r["reserialisation"])})',
                f'{share(r["fields"])} ({_num(r["fields"])})']

    def expand(r):
        return f'{r["expand_calls"]}, {_num(r["expand_tokens"])}' if r["expand_calls"] else "—"

    head = [_t("calls"), f'{_t("sent_by_tools")} ({html.escape(s["unit"])})', _t("received_by_model"),
            _t("saving_total"), _t("saving_reserialisation"), _t("saving_fields")]
    runs = [[f'<code>{html.escape(r["run"])}</code>', str(r["calls"])] + cells(r) + [expand(r)] for r in s["runs"]]
    runs.append([f'<b>{_t("all_runs")}</b>', str(s["total"]["calls"])] + cells(s["total"]) + [expand(s["total"])])
    tools = [[f"<code>{html.escape(t)}</code>", str(r["calls"])] + cells(r) for t, r in s["tools"].items()]
    return (f'<h2>{_t("savings")}</h2><p class="method">{_t("savings_sub")}</p>'
            + _table([_t("run")] + head + [_t("expand_back")], runs)
            + f'<details><summary>{_t("savings_per_tool")} · {len(tools)}</summary>'
            + _table([_t("tool")] + head, tools) + "</details>")


def _followed_note(auto: dict) -> str:
    """What the count of runs followed by calls does and does not say."""
    n = auto.get("answered_runs_followed_by_calls")
    if not n:
        return ""
    pause = auto.get("idle_seconds")
    en_pause = f" ({pause:g} seconds)" if pause else ""
    it_pause = f" ({pause:g} secondi)" if pause else ""
    en = (f"Runs closed by an answer and followed by calls: {n}. This count adds up two cases that tracetrim cannot "
          f"tell apart: the agent closed the run too early and went on with the same task, or the next task began "
          f"within the idle pause{en_pause}. On a host where the user opens the next conversation right away, it "
          f"grows at every run: by itself it is not a sign that something is wrong. In both cases the runs are "
          f"kept apart, never merged.")
    it = (f"Esecuzioni chiuse con risposta e seguite da chiamate: {n}. Il contatore somma due casi che tracetrim non "
          f"sa distinguere: l'agente ha chiuso l'esecuzione troppo presto e ha proseguito lo stesso compito, oppure "
          f"il compito successivo è iniziato entro la pausa di inattività{it_pause}. Con un host in cui l'utente "
          f"apre subito la conversazione successiva, cresce a ogni esecuzione: da solo non indica un problema. "
          f"In entrambi i casi le esecuzioni restano separate, mai fuse.")
    texts = {"en": en, "it": it} | {lang: i18n.followed(n, pause, lang) for lang in i18n.EXTRA}
    return f'<p class="method">{_langs(lambda lang: html.escape(texts[lang]))}</p>'


def _adaptive_html(d: dict) -> str:
    """Evidence, promotions and sampling: present only in reports written by automatic mode."""
    code = lambda x: f"<code>{html.escape(str(x))}</code>"   # noqa: E731
    out = []
    a = d.get("adaptive")
    if a:
        out.append(f'<h2>{_t("adaptive")}</h2><p class="method">{_t("adaptive_sub")}</p>')
        if not a["settings"]["promote"]:
            out.append(f'<p class="method">{_t("promote_off")}</p>')
        if a["promotions"]:
            out.append(f'<h3>{_t("promotions")}</h3>' + _table(
                [_t("tool"), _t("field"), _t("reason"), _t("runs"), _t("tokens"), _t("state")],
                [[code(p["tool"]), code(p["field"]), f'{_t("reason_" + p["reason"])} (≥ {p["threshold"]})',
                  html.escape(", ".join(p["runs"])), _num(p["field_tokens"]) if p["field_tokens"] is not None else "—",
                  _t("status_" + p["status"])] for p in a["promotions"]]))
        elif a["settings"]["promote"]:
            out.append(f'<p class="method">{_t("no_promotions")}</p>')
        if a["effect"]:
            out.append(f'<h3>{_t("effect")}</h3>' + _table(
                [_t("tool"), _t("calls"), _t("raw"), _t("without_p"), _t("with_p")],
                [[code(t), str(e["calls"]), _num(e["raw"]),
                  f'{_num(e["without_promotions"])} ({e["without_promotions"] / (e["raw"] or 1) - 1:+.0%})',
                  f'{_num(e["with_promotions"])} ({e["with_promotions"] / (e["raw"] or 1) - 1:+.0%})']
                 for t, e in a["effect"].items()]))
        kinds = ("requested", "recovered", "reused", "seen_full", "used_in_full")
        ev = [[code(t), code(f)] + [str(n[k]) for k in kinds]
              for t, e in a["evidence"].items() for f, n in e["fields"].items()]
        if ev:
            out.append(f'<details><summary>{_t("evidence")} · {len(ev)}</summary>'
                       + _table([_t("tool"), _t("field")] + [_t("ev_" + k) for k in kinds], ev) + "</details>")
        if a.get("resumed"):
            out.append(f'<h3>{_t("resumed")}</h3>' + _table(
                [_t("tool"), _t("field"), _t("runs")],
                [[code(r["tool"]), code(r["field"]), html.escape(", ".join(r["recovered_in"]))] for r in a["resumed"]]))
        if a.get("conflicts"):
            out.append(f'<h3>{_t("conflicts")}</h3>' + _table(
                [_t("tool"), _t("field")], [[code(c["tool"]), code(c["field"])] for c in a["conflicts"]]))
        tools = sorted(set(a["expand"]) | set(a["sampling"]) | set(a.get("full_cheaper", {})))
        if tools:
            unit = a["sampling_unit"]
            rows = []
            for t in tools:
                e, s = a["expand"].get(t, {}), a["sampling"].get(t, {})
                rows.append([code(t), str(e.get("filtered", 0)), str(e.get("expanded", 0)),
                             str(a["evidence"].get(t, {}).get("broad_requests", 0)), str(s.get("eligible", "—")),
                             str(s.get("sampled", "—")), _num(s["removed"]) if s else "—", _num(s["added"]) if s else "—",
                             str(a.get("full_cheaper", {}).get(t, 0))])
            out.append(f'<h3>{_t("exposure")}</h3>' + _table(
                [_t("tool"), _t("filtered"), _t("expanded"), _t("broad"), _t("eligible"), _t("sampled"),
                 f'{_t("removed_u")} ({unit})', f'{_t("added_u")} ({unit})', _t("cheaper")], rows)
                + f'<p class="method">{_t("compared_in")} {unit}</p>')
    return "".join(out)


def _measures_html(d: dict) -> str:
    """List elements, turns and arguments: measures that change nothing, in any report."""
    code = lambda x: f"<code>{html.escape(str(x))}</code>"   # noqa: E731
    out = []
    lists = [(t, v) for t, v in (d.get("lists") or {}).items() if v["lists"]]
    if lists:
        rows = [[code(t), code(path),
                 "; ".join(f'{html.escape(n)}: {s["median"]:g}, {s["max"]:g}' for n, s in v["size_arguments"].items()) or "—",
                 str(m["calls"]), _pair(m["received"]), _pair(m["reused"]),
                 f'{m["calls_reused_from_top"]}/{m["calls_with_reuse"]}', _pair(m["deepest_reused"])]
                for t, v in lists for path, m in v["lists"].items()]
        out.append(f'<h2>{_t("lists")}</h2>' + _table(
            [_t("tool"), _t("list"), _t("size_args"), _t("calls"), _t("received"), _t("reused_n"), _t("from_top"),
             _t("deepest")], rows))
    turns = d.get("turns") or {}
    if turns.get("per_run"):
        rows = [[code(r["run"]), str(r["turns"]), str(r["fewest_turns"]), str(r["calls"]),
                 f'{r["turns_with_several_calls"]}/{r["turns_with_calls"]} ({r["turns_with_several_calls"] / r["turns_with_calls"]:.0%})',
                 f'{r["calls_that_could_have_been_made_earlier"]}/{r["calls"]} ({r["calls_that_could_have_been_made_earlier"] / r["calls"]:.0%})']
                for r in turns["per_run"]]
        out.append(f'<details><summary>{_t("turns")} · {len(rows)}</summary><p class="sub">{_t("turns_sub")}</p>'
                   + _table([_t("run"), _t("turns_n"), _t("fewest"), _t("calls"), _t("several"), _t("could_earlier")], rows)
                   + "</details>")
    args = [[code(t), code(p), str(m["calls"]), str(m["distinct"])]
            for t, v in (d.get("arguments") or {}).items() for p, m in v["arguments"].items()]
    if args:
        out.append(f'<details><summary>{_t("arguments")} · {len(args)}</summary>'
                   + _table([_t("tool"), _t("argument"), _t("calls"), _t("distinct")], args) + "</details>")
    return "".join(out)


def _definitions_html(d: dict) -> str:
    """The section on tool definitions: only when the tool list is known."""
    defs = d.get("definitions")
    if not defs:
        return ""
    tiles = [("defs_per_turn", defs["tokens_per_turn"]), ("defs_offered", defs["offered"]),
             ("defs_called", len(defs["called"])), ("defs_never", len(defs["never_called"])),
             ("defs_never_tokens", defs["never_called_tokens"])]
    tiles_html = "".join(f'<div class="tile"><div class="v">{_num(v)}</div><div class="k">{_t(k)}</div></div>'
                         for k, v in tiles)
    own = (f' · {_t("defs_own")}: {_num(defs["own_tokens"])} '
           + ", ".join(f"<code>{html.escape(n)}</code>" for n in defs["own_tools"]) if defs["own_tools"] else "")
    rows = "".join(f"<tr><td><code>{html.escape(x['name'])}</code></td><td>{_num(x['tokens'])}</td></tr>"
                   for x in defs["never_called"])
    table = (f"<details><summary>{_t('defs_list')} · {len(defs['never_called'])}</summary><div class=\"scroll\"><table><thead><tr>"
             f"<th>{_t('tool')}</th><th>{_t('tokens')}</th></tr></thead><tbody>{rows}</tbody></table></div></details>"
             if rows else "")
    return (f'<h2>{_t("definitions")}</h2><div class="tiles">{tiles_html}</div>'
            f'<p class="method">{_t("defs_note")}{own}</p>{table}')


def _grounding_html(d: dict) -> str:
    """The section on values of the answers that no tool returned (since 1.1): only when runs were checked."""
    g = d.get("grounding")
    if not g or not g["runs_checked"]:
        return ""
    tiles = [("gr_runs", g["runs_checked"]), ("gr_values", g["values_checked"]), ("gr_bad", len(g["runs"]))]
    tiles_html = "".join(f'<div class="tile"><div class="v">{_num(v)}</div><div class="k">{_t(k)}</div></div>'
                         for k, v in tiles)
    rows = "".join(
        f"<tr><td><code>{html.escape(r['run'])}</code></td>"
        f"<td class=\"vals\">{', '.join((f'<b><code>{html.escape(v)}</code></b>' if v in r.get('suspect', []) else f'<code>{html.escape(v)}</code>') for v in r['unsupported'])}"
        f"{' …' if r['count'] > len(r['unsupported']) else ''}</td>"
        f"<td class=\"vals\">{', '.join(f'<code>{html.escape(t)}</code>' for t in r['filtered_tools']) or '—'}</td></tr>"
        for r in g["runs"])
    body = (f"<div class=\"scroll\"><table class=\"gr\"><thead><tr><th>{_t('gr_run')}</th><th>{_t('gr_found')}</th>"
            f"<th>{_t('gr_filtered')}</th></tr></thead><tbody>{rows}</tbody></table></div>" if rows else f"<p>{_t('gr_none')}</p>")
    return f'<h2>{_t("grounding")}</h2><div class="tiles">{tiles_html}</div><p class="method">{_t("gr_note")}</p>{body}'


def _about_html(g: dict) -> str:
    """Project and author, from about.py: only identifiers that exist."""
    a = html.escape
    items = [f'<a href="{a(g["website"])}">{a(g["author"])}</a>, {a(g.get("author_role", ""))}'
             if g.get("website") else a(g["author"])]
    if g.get("orcid"):
        items.append(f'ORCID <a href="https://orcid.org/{a(g["orcid"])}">{a(g["orcid"])}</a>')
    if g.get("repository"):
        items.append(f'<a href="{a(g["repository"])}">{a(g["repository"])}</a>')
    for k, label in (("paper_doi", "paper DOI"), ("software_doi", "software DOI")):
        if g.get(k):
            items.append(f'{label} <a href="https://doi.org/{a(g[k])}">{a(g[k])}</a>')
    if g.get("license"):
        items.append(a(g["license"]))
    return (f'<p class="method"><b>{a(g["name"])} {a(g["version"])}</b> · {_t("project_text")}</p>'
            f'<p class="method">{" · ".join(items)}</p>')


# -- the page -----------------------------------------------------------------------
# One self-contained file: no network, no font or script from outside (the report is opened from disk, or served
# by the proxy behind a token). Colours are roles, defined once for light and once for dark; the data colours are
# those validated for the field chart (dataviz validator: light and dark, adjacent pairs).

_CSS = """
:root { color-scheme: light;
  --page:#f6f6f3; --surface:#ffffff; --surface-2:#fbfbf9; --ink:#0b0b0b; --ink-2:#52514e; --muted:#7d7b75;
  --grid:#e7e6e0; --ring:rgba(11,11,11,.09); --ring-2:rgba(11,11,11,.16); --shadow:0 1px 2px rgba(11,11,11,.04),0 4px 16px rgba(11,11,11,.04);
  --accent:#2a78d6; --accent-ink:#1c5cab; --accent-soft:rgba(42,120,214,.10);
  --good:#0ca30c; --good-ink:#006300; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --violet:#6250d6; --violet-ink:#4a3aa7;
  --d-used:#2a78d6; --d-key:#1baf7a; --d-unjudged:#eda100; --d-promoted:#6250d6; --d-removed:#c9c8bf;
  --on-removed:#0b0b0b; --code-bg:rgba(11,11,11,.045); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --surface-2:#161615; --ink:#f0efec; --ink-2:#c3c2b7; --muted:#8f8d87;
  --grid:#2c2c2a; --ring:rgba(255,255,255,.09); --ring-2:rgba(255,255,255,.16); --shadow:none;
  --accent:#3987e5; --accent-ink:#86b6ef; --accent-soft:rgba(57,135,229,.16);
  --good-ink:#0ca30c; --violet:#9085e9; --violet-ink:#b3aaf2;
  --d-used:#3987e5; --d-key:#199e70; --d-unjudged:#c98500; --d-promoted:#9085e9; --d-removed:#4a4a46;
  --on-removed:#ffffff; --code-bg:rgba(255,255,255,.06); } }
:root[data-theme="dark"] { color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --surface-2:#161615; --ink:#f0efec; --ink-2:#c3c2b7; --muted:#8f8d87;
  --grid:#2c2c2a; --ring:rgba(255,255,255,.09); --ring-2:rgba(255,255,255,.16); --shadow:none;
  --accent:#3987e5; --accent-ink:#86b6ef; --accent-soft:rgba(57,135,229,.16);
  --good-ink:#0ca30c; --violet:#9085e9; --violet-ink:#b3aaf2;
  --d-used:#3987e5; --d-key:#199e70; --d-unjudged:#c98500; --d-promoted:#9085e9; --d-removed:#4a4a46;
  --on-removed:#ffffff; --code-bg:rgba(255,255,255,.06); }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; scroll-padding-top:116px; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior:auto; } * { transition:none !important; } }
body { margin:0; background:var(--page); color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; -webkit-font-smoothing:antialiased; }
body:not(.en) [lang="en"], body:not(.it) [lang="it"], body:not(.es) [lang="es"], body:not(.fr) [lang="fr"],
body:not(.zh) [lang="zh"] { display:none; }
body.zh { font-family:system-ui,-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",
  "Noto Sans SC",sans-serif; }
a { color:var(--accent-ink); text-decoration-thickness:1px; text-underline-offset:2px; }
code { font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:var(--code-bg);
  padding:1px 5px; border-radius:4px; }
pre code { background:none; padding:0; }
h1 { font-size:28px; line-height:1.15; letter-spacing:-.02em; margin:0 0 6px; }
h2 { font-size:19px; letter-spacing:-.01em; margin:0 0 4px; }
h3 { font-size:15px; margin:22px 0 8px; }
.sub, .method { color:var(--ink-2); margin:0 0 12px; }
.method { font-size:13.5px; color:var(--muted); }
/* top bar */
.top { position:sticky; top:0; z-index:20; background:color-mix(in srgb, var(--page) 86%, transparent);
  -webkit-backdrop-filter:saturate(1.4) blur(10px); backdrop-filter:saturate(1.4) blur(10px);
  border-bottom:1px solid var(--ring); }
.top-in, .nav-in, main { max-width:1160px; margin:0 auto; padding-left:20px; padding-right:20px; }
.top-in { display:flex; align-items:center; justify-content:space-between; gap:12px; height:56px; }
.brand { display:flex; align-items:center; gap:10px; min-width:0; }
.brand b { font-size:16px; letter-spacing:-.01em; }
.brand .crumb { color:var(--muted); font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.brand .crumb::before { content:"/"; margin:0 8px 0 2px; color:var(--ring-2); }
.logo { flex:none; width:26px; height:26px; border-radius:7px; background:var(--ink); color:var(--page);
  display:grid; place-items:center; }
.ctl { display:flex; align-items:center; gap:8px; flex:none; }
.lsel { display:inline-flex; align-items:center; gap:6px; color:var(--ink-2); border:1px solid var(--ring);
  background:var(--surface); border-radius:8px; padding:0 4px 0 9px; }
.lsel select { font:inherit; font-size:12.5px; font-weight:600; color:var(--ink); background:transparent; border:0;
  padding:6px 4px; cursor:pointer; }
.lsel:focus-within { outline:2px solid var(--accent); outline-offset:2px; }
.lsel select:focus { outline:none; }
.tbtn { font:inherit; font-size:12.5px; font-weight:600; color:var(--ink-2); border-radius:8px; cursor:pointer; }
.live { display:inline-flex; align-items:center; gap:7px; font-size:12px; font-weight:650; color:var(--ink-2);
  text-transform:uppercase; letter-spacing:.06em; }
.live[hidden] { display:none; }
.live i { width:8px; height:8px; border-radius:50%; background:var(--good); box-shadow:0 0 0 0 rgba(12,163,12,.5);
  animation:pulse 2s infinite; }
.live[data-state="off"] i { background:var(--muted); animation:none; }
.live #live-time { font-weight:500; text-transform:none; letter-spacing:0; color:var(--muted); font-variant-numeric:tabular-nums; }
@keyframes pulse { 0% { box-shadow:0 0 0 0 rgba(12,163,12,.45); } 70% { box-shadow:0 0 0 7px rgba(12,163,12,0); } 100% { box-shadow:0 0 0 0 rgba(12,163,12,0); } }
@media (prefers-reduced-motion: reduce) { .live i { animation:none; } }
@media (max-width:640px) { .live #live-time { display:none; } }
.tbtn { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--ring); background:var(--surface); padding:5px 10px; }
.tbtn:focus-visible, .secnav a:focus-visible, summary:focus-visible, .copy:focus-visible {
  outline:2px solid var(--accent); outline-offset:2px; }
.secnav { border-top:1px solid var(--ring); }
.nav-in { display:flex; gap:2px; overflow-x:auto; scrollbar-width:none; height:44px; align-items:center; }
.nav-in::-webkit-scrollbar { display:none; }
.secnav a { flex:none; font-size:13.5px; font-weight:500; color:var(--ink-2); text-decoration:none; padding:6px 11px;
  border-radius:8px; }
.secnav a:hover { background:var(--code-bg); color:var(--ink); }
.secnav a.on { background:var(--surface); color:var(--ink); box-shadow:inset 0 0 0 1px var(--ring); }
/* layout */
main { padding-top:28px; padding-bottom:72px; overflow-wrap:break-word; }
.sec { margin:0 0 40px; }
.sec-h { display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:20px; box-shadow:var(--shadow); }
.intro { margin-bottom:20px; }
.byline { color:var(--muted); font-size:13px; margin:0; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.pill { display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600; padding:2px 9px;
  border-radius:999px; border:1px solid var(--ring); background:var(--surface); color:var(--ink-2); }
.ov { display:grid; grid-template-columns:minmax(0,1.65fr) minmax(0,1fr); gap:16px; margin-bottom:16px; }
.label { font-size:12px; font-weight:650; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; }
.hero .hv { font-size:64px; font-weight:700; letter-spacing:-.035em; line-height:1; margin:10px 0 8px; }
.hero .hk { color:var(--ink-2); max-width:56ch; }
.hero .hs { color:var(--muted); font-size:13px; margin-top:6px; }
.tall { display:flex; gap:2px; height:22px; margin:18px 0 10px; }
.tall > span { display:block; border-radius:4px; min-width:0; }
.tall .kept { background:var(--d-used); } .tall .removed { background:var(--d-removed); }
.hero .legend { margin:0 0 6px; } .hero .legend b { color:var(--ink); font-weight:650; }
.status .st { display:flex; align-items:center; gap:10px; font-size:17px; font-weight:650; margin:2px 0 6px; }
.dot { width:10px; height:10px; border-radius:50%; flex:none; box-shadow:0 0 0 4px color-mix(in srgb, currentColor 18%, transparent); }
.dot.on { color:var(--good); background:var(--good); } .dot.learning { color:var(--warning); background:var(--warning); }
.dot.offline { color:var(--muted); background:var(--muted); }
.meter { height:8px; border-radius:999px; background:var(--accent-soft); overflow:hidden; margin:10px 0 6px; }
.meter > span { display:block; height:100%; background:var(--accent); border-radius:999px; }
.status .kv { font-size:13.5px; color:var(--ink-2); margin:8px 0 0; }
.status .kv div { display:flex; justify-content:space-between; gap:12px; padding:6px 0; border-top:1px solid var(--grid); }
.status .kv div span:last-child { text-align:right; color:var(--ink); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }
.tile { background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:14px 16px; box-shadow:var(--shadow); }
.tile .v { font-size:24px; font-weight:650; letter-spacing:-.02em; }
.tile .k { color:var(--muted); font-size:12.5px; line-height:1.35; margin-top:2px; }
.followed { margin-top:12px; }
.tiles + p, .tiles + .method { margin-top:14px; }
.act details > summary { padding:4px 0; font-weight:500; }
.act details > :not(summary) { margin-left:0; margin-right:0; }
/* charts */
.legend { display:flex; flex-wrap:wrap; gap:6px 16px; font-size:12.5px; color:var(--ink-2); margin:0 0 12px; }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.sw { display:inline-block; width:10px; height:10px; border-radius:3px; }
.chart { display:grid; gap:4px; }
.brow { display:grid; grid-template-columns:minmax(120px,260px) 1fr; gap:14px; align-items:center; }
.bname { font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bname code { background:none; padding:0; }
.btrack { display:flex; align-items:center; gap:10px; min-width:0; border-left:1px solid var(--grid); }
.bbar { display:flex; gap:2px; min-width:2px; }
.bval { font-size:12px; color:var(--ink-2); white-space:nowrap; font-variant-numeric:tabular-nums; }
.hit { display:flex; align-items:center; padding:6px 0; cursor:default; outline:none; }
.bbar .fill { display:block; width:100%; height:16px; }
.bbar .hit:last-child .fill { border-radius:0 4px 4px 0; }
.thin { display:flex; gap:2px; width:150px; }
.thin .fill { display:block; width:100%; height:8px; }
.thin .hit:first-child .fill { border-radius:4px 0 0 4px; } .thin .hit:last-child .fill { border-radius:0 4px 4px 0; }
.thin .hit:only-child .fill { border-radius:4px; }
.fill.kept, .fill.used, .sw.kept, .sw.used { background:var(--d-used); }
.fill.key, .sw.key { background:var(--d-key); } .fill.unjudged, .sw.unjudged { background:var(--d-unjudged); }
.fill.removed, .fill.dropped, .sw.removed, .sw.dropped { background:var(--d-removed); }
.fill.promoted, .sw.promoted { background:var(--d-promoted); }
.hit:hover .fill, .hit:focus-visible .fill { filter:brightness(1.1); box-shadow:0 0 0 2px var(--surface), 0 0 0 3px var(--ink-2); }
/* suggestions */
.counts { display:flex; gap:6px; flex-wrap:wrap; }
ul.sugg { list-style:none; padding:0; margin:0; display:grid; gap:10px; }
li.s { display:grid; grid-template-columns:34px 1fr; gap:12px; background:var(--surface); border:1px solid var(--ring);
  border-radius:12px; padding:14px 16px; box-shadow:var(--shadow); }
li.s .ic { width:34px; height:34px; border-radius:9px; display:grid; place-items:center; }
li.s .body { min-width:0; }
.lvl { display:block; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.07em; margin-bottom:3px; }
li.risk .ic { background:color-mix(in srgb, var(--critical) 14%, transparent); color:var(--critical); } li.risk .lvl { color:var(--critical); }
li.warning .ic { background:color-mix(in srgb, var(--warning) 20%, transparent); color:#a86f00; } li.warning .lvl { color:#a86f00; }
li.saving .ic, li.quick-win .ic { background:color-mix(in srgb, var(--good) 14%, transparent); color:var(--good-ink); }
li.saving .lvl, li.quick-win .lvl { color:var(--good-ink); }
li.review .ic { background:color-mix(in srgb, var(--violet) 14%, transparent); color:var(--violet-ink); } li.review .lvl { color:var(--violet-ink); }
li.limit .ic { background:var(--code-bg); color:var(--ink-2); } li.limit .lvl { color:var(--ink-2); }
li.s code, .act code, .method code { overflow-wrap:anywhere; }
.act { margin-top:10px; padding-top:10px; border-top:1px solid var(--grid); font-size:14px; color:var(--ink-2); }
.chips { display:flex; flex-wrap:wrap; gap:6px; list-style:none; padding:0; margin:8px 0 0; }
.chips li code { display:inline-block; }
.act details { border:0; padding:0; margin:6px 0 0; background:none; box-shadow:none; }
.act details summary { font-size:13px; color:var(--accent-ink); }
.badge { display:inline-block; font-size:10.5px; font-weight:700; letter-spacing:.06em; padding:2px 7px; border-radius:5px;
  margin-right:8px; vertical-align:1px; }
.act.auto .badge { background:color-mix(in srgb, var(--good) 16%, transparent); color:var(--good-ink); }
.act.manual .badge { background:color-mix(in srgb, var(--violet) 16%, transparent); color:var(--violet-ink); }
.cnt { display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600; padding:3px 10px;
  border-radius:999px; background:var(--surface); border:1px solid var(--ring); color:var(--ink-2); }
.cnt i { width:7px; height:7px; border-radius:50%; display:inline-block; }
/* tables */
.scroll { overflow-x:auto; border:1px solid var(--ring); border-radius:12px; background:var(--surface); box-shadow:var(--shadow); }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; font-size:13.5px; }
th, td { border-bottom:1px solid var(--grid); padding:9px 12px; text-align:right; white-space:nowrap; }
td:has(> code:only-child) { text-align:left; }
tbody tr:last-child td { border-bottom:0; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--muted); font-weight:600; font-size:12px; line-height:1.3; background:var(--surface-2); white-space:normal;
  vertical-align:bottom; min-width:64px; }
tbody tr:hover td { background:var(--surface-2); }
tr.dropped td { color:var(--muted); } tr.unjudged td:nth-child(5) { color:#a86f00; }
tr.promoted td:nth-child(5) { color:var(--violet-ink); }
td.fb { text-align:left; }
td.vals { white-space:normal; text-align:left; line-height:1.9; min-width:220px; }
table.gr th { text-align:left; }
details { background:var(--surface); border:1px solid var(--ring); border-radius:12px; margin:10px 0; box-shadow:var(--shadow); }
details > summary { cursor:pointer; padding:12px 16px; list-style:none; display:flex; align-items:center; gap:8px; font-weight:500; }
details > summary::-webkit-details-marker { display:none; }
details > summary::before { content:""; width:7px; height:7px; border-right:2px solid var(--muted); border-bottom:2px solid var(--muted);
  transform:rotate(-45deg); transition:transform .15s; flex:none; margin-right:4px; }
details[open] > summary::before { transform:rotate(45deg); }
details > :not(summary) { margin-left:16px; margin-right:16px; }
details > .scroll { margin-bottom:16px; box-shadow:none; }
details > p { margin-top:0; }
pre { background:var(--surface-2); border:1px solid var(--ring); border-radius:10px; padding:14px 16px; overflow-x:auto;
  font-size:12.5px; line-height:1.5; margin:0 0 12px; }
.cfg { position:relative; }
.copy { position:absolute; top:8px; right:8px; font:inherit; font-size:12px; font-weight:600; color:var(--ink-2);
  background:var(--surface); border:1px solid var(--ring); border-radius:7px; padding:3px 9px; cursor:pointer; }
footer.foot { border-top:1px solid var(--ring); padding-top:24px; margin-top:8px; }
#tip { position:fixed; z-index:30; pointer-events:none; background:var(--surface); border:1px solid var(--ring-2);
  border-radius:8px; padding:7px 10px; font-size:12px; box-shadow:0 8px 24px rgba(0,0,0,.16); max-width:320px; }
#tip .tv { font-weight:650; font-size:13px; color:var(--ink); } #tip div + div { color:var(--ink-2); }
@media (max-width:860px) { .ov { grid-template-columns:1fr; } }
@media (max-width:640px) {
  .top-in, .nav-in, main { padding-left:16px; padding-right:16px; }
  .brand .crumb { display:none; } .tbtn .tl { display:none; }
  .brow { grid-template-columns:1fr; gap:0; margin-bottom:6px; }
  .hero .hv { font-size:52px; } h1 { font-size:24px; }
  li.s { grid-template-columns:1fr; } li.s .ic { display:none; }
  .card { padding:16px; } }
@media print { .top, #tip, .copy { display:none; } body { background:#fff; } .card, .tile, li.s, .scroll, details { box-shadow:none; } }
"""

# Before the page draws: the chosen theme, so that a dark page never flashes white.
_HEAD_JS = """(function(){try{var t=localStorage.getItem('tracetrim-theme');if(t==='light'||t==='dark')
document.documentElement.setAttribute('data-theme',t);}catch(e){}})();"""

_JS = """
// The chosen language survives the live report's automatic refresh: kept in the
// URL fragment (a refresh reloads the same URL) and, where allowed, in storage.
// With no choice yet, the browser's language when the report has it, else English.
var LANGS = ['en', 'it', 'es', 'fr', 'zh'];
function applyLang(l) {
  document.body.className = l;
  document.documentElement.lang = l === 'zh' ? 'zh-Hans' : l;
  var s = document.getElementById('lang'); if (s) s.value = l;
}
function setLang(l) {
  if (LANGS.indexOf(l) < 0) return;
  applyLang(l);
  try { history.replaceState(null, '', '#' + l); } catch (e) {}
  try { localStorage.setItem('tracetrim-lang', l); } catch (e) {}
}
(function () {
  var l = (location.hash || '').slice(1);
  if (LANGS.indexOf(l) < 0) { try { l = localStorage.getItem('tracetrim-lang'); } catch (e) { l = null; } }
  if (LANGS.indexOf(l) < 0) { l = ((navigator.language || 'en').slice(0, 2)).toLowerCase(); }
  applyLang(LANGS.indexOf(l) < 0 ? 'en' : l);
})();
// Theme: automatic (the system's), light or dark; the choice is kept where storage allows it.
var THEMES = ['auto', 'light', 'dark'];
function showTheme(t) {
  document.querySelectorAll('[data-theme-label]').forEach(function (el) { el.hidden = el.getAttribute('data-theme-label') !== t; });
}
function cycleTheme() {
  var now = document.documentElement.getAttribute('data-theme') || 'auto';
  var next = THEMES[(THEMES.indexOf(now) + 1) % THEMES.length];
  if (next === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', next);
  try { if (next === 'auto') localStorage.removeItem('tracetrim-theme'); else localStorage.setItem('tracetrim-theme', next); } catch (e) {}
  showTheme(next);
}
showTheme(document.documentElement.getAttribute('data-theme') || 'auto');
// The section in view is marked in the navigation (started again when the live report swaps the content).
var navObserver = null;
function watchNav() {
  if (navObserver) navObserver.disconnect();
  var links = {};
  document.querySelectorAll('.secnav a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
  if (!('IntersectionObserver' in window)) return;
  var io = navObserver = new IntersectionObserver(function (es) {
    es.forEach(function (e) {
      if (!e.isIntersecting) return;
      Object.keys(links).forEach(function (k) { links[k].classList.toggle('on', k === e.target.id); });
      var a = links[e.target.id]; if (a && a.scrollIntoView && a.parentNode.scrollWidth > a.parentNode.clientWidth) {
        a.parentNode.scrollLeft = a.offsetLeft - 40; }
    });
  }, { rootMargin: '-120px 0px -60% 0px' });
  Object.keys(links).forEach(function (k) { var s = document.getElementById(k); if (s) io.observe(s); });
}
watchNav();
// Copy the runtime config.
function copyCfg(btn) {
  var text = document.getElementById('cfg-json').textContent;
  function flip(copied) {
    btn.querySelectorAll('[data-copy]').forEach(function (x) { x.hidden = copied; });
    btn.querySelectorAll('[data-copied]').forEach(function (x) { x.hidden = !copied; });
  }
  try { navigator.clipboard.writeText(text).then(function () { flip(true); setTimeout(function () { flip(false); }, 1500); }); }
  catch (e) {}
}
var tip = document.getElementById('tip');
function bindTips(root) {
  function show(el, x, y) {
    var lines = JSON.parse(el.getAttribute('data-tip-' + document.body.className) || el.getAttribute('data-tip-en'));
    tip.replaceChildren();
    lines.forEach(function (t, i) { var d = document.createElement('div'); d.textContent = t; if (i === 0) d.className = 'tv'; tip.appendChild(d); });
    tip.hidden = false;
    var w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(x + 12, window.innerWidth - w - 8)) + 'px';
    tip.style.top = Math.max(8, y - h - 12) + 'px';
  }
  root.querySelectorAll('[data-tip-en]').forEach(function (el) {
    el.tabIndex = 0;
    el.setAttribute('aria-label', JSON.parse(el.getAttribute('data-tip-en')).slice().reverse().join(': '));
    el.addEventListener('pointermove', function (e) { show(el, e.clientX, e.clientY); });
    el.addEventListener('pointerleave', function () { tip.hidden = true; });
    el.addEventListener('focus', function () { var r = el.getBoundingClientRect(); show(el, r.left + r.width / 2, r.top); });
    el.addEventListener('blur', function () { tip.hidden = true; });
  });
}
bindTips(document);
// The live report (served by the proxy, which adds the tracetrim-live meta): every few seconds it asks for the
// report's stamp, and when it changed fetches the page and swaps the content in place. Scroll position, open
// sections, language and theme stay as they are. Paused while the tab is hidden; slower after a failure.
(function () {
  var m = document.querySelector('meta[name="tracetrim-live"]');
  if (!m || !window.fetch || !window.DOMParser) return;
  var every = Math.max(1, +m.content || 5) * 1000, fails = 0, timer = null;
  var stamp = (document.querySelector('meta[name="tracetrim-stamp"]') || {}).content || '';
  var base = location.pathname.replace(/[/]$/, ''), q = location.search;
  var pill = document.getElementById('live');
  if (pill) pill.hidden = false;
  function state(s) { if (pill) pill.setAttribute('data-state', s); }
  function later(ms) { clearTimeout(timer); timer = setTimeout(tick, ms); }
  function text(r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); }
  function swap(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var main = doc.querySelector('main'), nav = doc.querySelector('.secnav .nav-in');
    if (!main) return;
    var open = {};
    document.querySelectorAll('main details[open] > summary').forEach(function (s) { open[s.textContent] = true; });
    var y = window.scrollY;
    document.querySelector('main').replaceWith(document.adoptNode(main));
    if (nav) document.querySelector('.secnav .nav-in').replaceWith(document.adoptNode(nav));
    document.querySelectorAll('main details > summary').forEach(function (s) { if (open[s.textContent]) s.parentNode.open = true; });
    window.scrollTo(0, y);
    bindTips(document.querySelector('main'));
    watchNav();
    var t = document.getElementById('live-time');
    if (t) t.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function tick() {
    if (document.hidden) return;
    fetch(base + '/stamp' + q, { cache: 'no-store' }).then(text).then(function (now) {
      fails = 0; state('on');
      if (now === stamp || now === 'none') return later(every);
      return fetch(base + q, { cache: 'no-store' }).then(text).then(function (html) { swap(html); stamp = now; later(every); });
    }).catch(function () { fails += 1; state('off'); later(Math.min(every * Math.pow(2, fails), 60000)); });
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) later(0); });
  later(every);
})();
"""

# Small line icons (24px grid, stroke 2): drawn here, no icon font.
_ICONS = {
    "risk": '<path d="M12 4 2.5 20h19L12 4Z"/><path d="M12 10v4.5M12 17.5v.01"/>',
    "warning": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>',
    "saving": '<path d="M12 4v14M6 12l6 6 6-6"/>',
    "quick-win": '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6L13 3Z"/>',
    "review": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.8"/>',
    "limit": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5v.01"/>',
}
_LOGO = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" '
         'stroke-linecap="round" aria-hidden="true"><path d="M4 6.5h16M4 12h10M4 17.5h6"/></svg>')
_THEME_ICON = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
               'aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17A8.5 8.5 0 0 0 12 3.5Z" '
               'fill="currentColor"/></svg>')
_GLOBE = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
          'aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9'
          'c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z"/></svg>')
CHIPS_SHOWN = 8           # items of a manual action shown before "N more"


def _icon(level: str) -> str:
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS.get(level, _ICONS["limit"])}</svg>')


def _sec(sid: str, body: str) -> str:
    return f'<section class="sec" id="{sid}">{body}</section>' if body else ""


def _signed(saving: float) -> str:
    """A saving as the page writes it: −55% for tokens removed, +12% when the output grew (never "−-12%")."""
    return f"{'−' if saving > 0 else '+' if saving < 0 else ''}{abs(saving):.0%}"


def _hero(d: dict) -> str:
    T = d["totals"]
    if not T["raw"]:
        # No tool output yet: no saving to show (until 1.0 the page said −100%).
        return f'<div class="card hero"><div class="label">{_t("nav_saving")}</div><div class="hv">—</div></div>'
    saving = 1 - T["loo_prudent"] / T["raw"]
    agg = 1 - T["loo_aggressive"] / T["raw"]
    if T["traces"] < 2:
        return (f'<div class="card hero"><div class="label">{_t("nav_saving")}</div><div class="hv">{_signed(saving)}</div>'
                f'<div class="hk">{_t("hero_one")}</div></div>')
    # The tall bar carries the proportion; its legend, under it, carries names and shares. A split of the whole:
    # when the measure says the output grew, nothing is removed and the bar is all "reaches the agent".
    cut = min(1.0, max(0.0, saving))
    kept = 1.0 - cut
    split = ((kept, "kept"), (cut, "removed"))
    parts = "".join(f'<span class="{k}" style="flex:{v:.4f} 1 0"></span>' for v, k in split if v > 0)
    legend = "".join(f'<span><i class="sw {k}"></i>{_t(k)} · <b>{round(v * 100)}%</b></span>' for v, k in split)
    return (f'<div class="card hero"><div class="label">{_t("nav_saving")}</div><div class="hv">{_signed(saving)}</div>'
            f'<div class="hk">{_t("hero")}</div>'
            f'<div class="tall" role="img" aria-label="{kept:.0%} reaches the agent, {cut:.0%} removed">{parts}</div>'
            f'<div class="legend">{legend}</div>'
            f'<div class="hs">{_t("hero_sub")}: {_signed(agg)}, {_t("misses")}: {T["loo_misses"]}/{T["loo_trimmed_calls"]}</div></div>')


def _status(d: dict) -> str:
    auto = d.get("auto")
    if not auto:
        return (f'<div class="card status"><div class="label">{_t("mode")}</div>'
                f'<div class="st"><i class="dot offline"></i>{_t("status_offline")}</div>'
                f'<p class="method">{_tmd("status_offline_text")}</p></div>')
    if auto["applied"]:
        head = f'<div class="st"><i class="dot on"></i>{_t("status_on")}</div><p class="method">{_t("auto_applied")}</p>'
    else:
        done = min(auto["complete_runs"], auto["min_runs"])
        head = (f'<div class="st"><i class="dot learning"></i>{_t("status_learning")} · {done}/{auto["min_runs"]}</div>'
                f'<div class="meter" title="{html.escape(L["auto_learning"][0])}"><span style="width:{100 * done / (auto["min_runs"] or 1):.0f}%"></span></div>')
    rows = [(_t("status_runs"), f'{auto["complete_runs"]} <span class="method">(≥ {auto["min_runs"]})</span>')]
    trimmed = len((d.get("config") or {}).get("tools") or {})
    if auto["applied"]:
        rows.append((_t("status_trimmed"), str(trimmed)))
    promoted = sum(len(v) for v in (auto.get("promoted") or {}).values())
    if promoted:
        rows.append((_t("status_promoted"), str(promoted)))
    rows.append((_t("updated"), html.escape(auto["updated"].replace("T", " ").replace("+00:00", " UTC"))))
    if auto["switched_off"]:
        rows.append((_t("auto_off"), ", ".join(f"<code>{html.escape(x)}</code>" for x in auto["switched_off"])))
    # Shown only when it happened: where the boundary of a run may be wrong.
    rows += [(_t(key), str(auto[field])) for key, field in (("auto_refused", "refused_closings"),
                                                           ("auto_followed", "answered_runs_followed_by_calls"))
             if auto.get(field)]
    if mixed := d.get("mixed_sessions"):
        rows.extend([(_t("mixed_runs"), _num(mixed["runs"])),
                     (_t("mixed_occurrences"), _num(mixed["session_occurrences"]))])
    kv = "".join(f"<div><span>{k}</span><span>{v}</span></div>" for k, v in rows)
    return (f'<div class="card status"><div class="label">{_t("auto_title")}</div>{head}'
            f'<div class="kv">{kv}</div></div>')


def _suggestions_html(d: dict, sugg: list[dict]) -> str:
    def _item(x):
        return f"{x['name']} · {x['tokens']}" if isinstance(x, dict) else str(x)

    def chips(items):
        li = [f"<li><code>{html.escape(_item(x))}</code></li>" for x in items]
        if len(li) <= CHIPS_SHOWN:
            return f'<ul class="chips">{"".join(li)}</ul>'
        return (f'<ul class="chips">{"".join(li[:CHIPS_SHOWN])}</ul><details><summary>+{len(li) - CHIPS_SHOWN} '
                f'{_t("more")}</summary><ul class="chips">{"".join(li[CHIPS_SHOWN:])}</ul></details>')

    def act(a):
        en, it = ACTION_TEXT[a["type"]]
        texts = {"en": en, "it": it} | {lang: i18n.ACTIONS[lang].get(a["type"], en) for lang in i18n.EXTRA}
        extra = a.get("examples") or a.get("fields") or a.get("tools") or a.get("runs") or []
        badge = _t("auto") if a["kind"] == "auto" else _t("manual")
        return (f'<div class="act {a["kind"]}"><span class="badge">{badge}</span>'
                f'{_langs(lambda lang: _md(texts[lang]))}{chips(extra) if extra else ""}</div>')

    items = "".join(
        f'<li class="s {s["level"]}"><div class="ic">{_icon(s["level"])}</div><div class="body">'
        f'<span class="lvl">{_t("level", s["level"])}</span>'
        f'{_langs(lambda lang, s=s: _md(s.get(lang) or s["en"]))}{act(s["action"])}</div></li>'
        for s in sugg) or f'<li class="s limit"><div class="ic">{_icon("limit")}</div><div class="body">{_t("none")}</div></li>'
    colours = {"warning": "var(--warning)", "risk": "var(--critical)", "saving": "var(--good)",
               "quick-win": "var(--good)", "review": "var(--violet)", "limit": "var(--muted)"}
    levels = [lv for lv in colours if any(s["level"] == lv for s in sugg)]
    counts = "".join(f'<span class="cnt"><i style="background:{colours[lv]}"></i>'
                     f'{sum(s["level"] == lv for s in sugg)} {_t("level", lv)}</span>' for lv in levels)
    return (f'<div class="sec-h"><h2>{_t("suggestions")}</h2><div class="counts">{counts}</div></div>'
            f'<ul class="sugg">{items}</ul>')


def _tools_html(d: dict) -> tuple[str, str]:
    T = d["totals"]
    total = T["raw"] or 1
    rows, details = [], []
    for tool, t in d["tools"].items():
        sav = (lambda a: f"{a / t['loo_raw'] - 1:+.0%}" if t["loo_raw"] and t["loo_trimmed_calls"] else "—")
        rows.append(
            f"<tr><td><code>{html.escape(tool)}</code></td><td class=\"fb\">{_fields_bar(t)}</td><td>{t['calls']}</td><td>{_num(t['raw'])}</td>"
            f"<td>{t['raw'] / total:.0%}</td><td>{sav(t['loo_prudent'])}</td><td>{sav(t['loo_aggressive'])}</td>"
            f"<td>{t['loo_misses']}/{t['loo_trimmed_calls']}</td><td>{t['errors']}</td><td>{_num(t['prose_tokens'])}</td></tr>")
        if not t["fields"]:
            continue
        frows = "".join(
            f'<tr class="{v["status"]}"><td><code>{html.escape(f)}</code></td><td>{_num(v["tokens"])}</td>'
            f'<td>{v["seen"]}</td><td>{v["used"]}</td><td>{_t(v["status"] + "_s")}</td>'
            f'<td>{v["missed"] or ""}</td></tr>'
            for f, v in sorted(t["fields"].items(), key=lambda x: -x[1]["tokens"]))
        details.append(
            f"<details><summary><code>{html.escape(tool)}</code> · {len(t['fields'])} {_t('n_fields')}</summary>"
            f"<div class=\"scroll\"><table><thead><tr><th>{_t('field')}</th><th>{_t('tokens')}</th><th>{_t('seen')}</th>"
            f"<th>{_t('used')}</th><th>{_t('status')}</th><th>{_t('missed')}</th></tr></thead>"
            f"<tbody>{frows}</tbody></table></div></details>")
    legend = "".join(f'<span><i class="sw {k}"></i>{_t(k + "_s")}</span>' for k in FIELD_KINDS)
    table = (f'<h2>{_t("per_tool")}</h2><div class="legend" style="margin-top:8px">{legend}</div>'
             f'<div class="scroll"><table><thead><tr><th>{_t("tool")}</th><th>{_t("fields_bar")}</th><th>{_t("calls")}</th>'
             f'<th>{_t("tokens")}</th><th>{_t("share")}</th><th>{_t("prudent")}</th><th>{_t("aggressive")}</th>'
             f'<th>{_t("misses")}</th><th>{_t("errors")}</th><th>{_t("prose")}</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    fields = f'<h2>{_t("fields")}</h2>{"".join(details)}' if details else ""
    return table, fields


def _config_html(d: dict) -> str:
    cfg = d.get("config")
    if not cfg:
        return ""
    return (f'<h2>{_t("actions")}</h2><p class="sub">{_t("config_text")}</p>'
            f'<details><summary>{_t("config_json")}</summary><div class="cfg">'
            f'<button class="copy" type="button" onclick="copyCfg(this)"><span data-copy>{_t("copy")}</span>'
            f'<span data-copied hidden>{_t("copied")}</span></button>'
            f'<pre><code id="cfg-json">{html.escape(json.dumps(cfg, indent=2, ensure_ascii=False))}</code></pre></div></details>'
            f'<pre><code>tracetrim apply {html.escape(d.get("config_file", "report.config.json"))} TOOL &lt; output.json</code></pre>')


def render_html(d: dict, sugg: list[dict]) -> str:
    T = d["totals"]
    tiles = [("traces", _num(T["traces"])), ("calls", _num(T["calls"])), ("raw", _num(T["raw"])),
             ("prudent", _num(T["loo_prudent"])),
             # The tile is about the projection the config applies (prudent); the aggressive one's misses are
             # on the line under the headline.
             ("misses_prudent", f'{T.get("loo_misses_prudent", 0)}/{T["loo_trimmed_calls"]}')]
    tiles_html = "".join(f'<div class="tile"><div class="v">{v}</div><div class="k">{_t(k)}</div></div>' for k, v in tiles)
    g = d["generator"]
    followed = _followed_note(d["auto"]) if d.get("auto") else ""
    overview = (f'<div class="intro"><h1>{_t("title")}</h1><p class="sub">{_t("subtitle")}</p>'
                f'<p class="byline"><span class="pill">{html.escape(g["name"])} {html.escape(g["version"])}</span>'
                f'<span>{html.escape(g["author"])}</span></p></div>'
                f'<div class="ov">{_hero(d)}{_status(d)}</div><div class="tiles">{tiles_html}</div>'
                + (f'<div class="followed">{followed}</div>' if followed else ""))
    chart = _chart_tools(d)
    tools, fields = _tools_html(d)
    about = (f'<h2>{_t("method")}</h2><p class="method">{_t("method_text")}</p>'
             f'<h2 style="margin-top:20px">{_t("project")}</h2>{_about_html(g)}'
             f'<p class="method">{html.escape(d["generated"])} · unseen={html.escape(d["unseen"])}</p>')
    sections = [
        ("overview", "nav_overview", overview),
        ("tokens", "nav_tokens", f'<div class="card">{chart}</div>' if chart else ""),
        ("saving", "nav_saving", _savings_html(d)),
        ("suggestions", "nav_sugg", _suggestions_html(d, sugg)),
        ("tools", "nav_tools", tools),
        ("fields", "nav_fields", fields),
        ("definitions", "nav_defs", _definitions_html(d)),
        ("answers", "nav_answers", _grounding_html(d)),
        ("adaptive", "nav_adaptive", _adaptive_html(d)),
        ("measures", "nav_measures", _measures_html(d)),
        ("config", "nav_config", _config_html(d)),
    ]
    present = [(sid, key, body) for sid, key, body in sections if body]
    nav = "".join(f'<a href="#{sid}">{_t(key)}</a>' for sid, key, _ in present) + f'<a href="#about">{_t("nav_about")}</a>'
    body = "".join(_sec(sid, b) for sid, _, b in present)
    langs = "".join(f'<option value="{lg}">{html.escape(i18n.NAMES[lg])}</option>' for lg in i18n.LANGS)
    theme = "".join(f'<span class="tl" data-theme-label="{t}"{"" if t == "auto" else " hidden"}>{_t("theme_" + t)}</span>'
                    for t in ("auto", "light", "dark"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tracetrim · Tool-output telemetry</title>
<script>{_HEAD_JS}</script>
<style>{_CSS}</style></head>
<body class="en">
<header class="top"><div class="top-in">
<div class="brand"><span class="logo">{_LOGO}</span><b>tracetrim</b><span class="crumb">{_t("title")}</span></div>
<div class="ctl"><span class="live" id="live" data-state="on" hidden><i></i>live<span id="live-time"></span></span>
<label class="lsel" title="Language">{_GLOBE}<select id="lang" aria-label="Language" onchange="setLang(this.value)">{langs}</select></label>
<button class="tbtn" type="button" onclick="cycleTheme()" title="{html.escape(L['theme'][0])}" aria-label="{html.escape(L['theme'][0])}">{_THEME_ICON}{theme}</button></div>
</div><nav class="secnav" aria-label="Sections"><div class="nav-in">{nav}</div></nav></header>
<main>
{body}
<footer class="foot sec" id="about">{about}</footer>
</main><div id="tip" hidden></div>
<script>{_JS}</script></body></html>
"""
