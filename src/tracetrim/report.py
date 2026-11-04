"""Telemetry report: what the tools sent, what the agent used, what to do.

  tracetrim report traces/*.jsonl -o report.html        (also writes report.json)

Every suggestion comes from a deterministic rule and quotes the number that
triggered it, in English and Italian. No model writes the report: the same
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

from . import about, measure
from .projection import learn
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


def _is_error(c) -> bool:
    if c.payload is None:
        return c.output.strip().lower().startswith("error")
    return isinstance(c.payload, dict) and "error" in c.payload


def compute(names, usages, unseen="keep") -> dict:
    count = measure.tokenizer()
    deployed_p = learn(usages, "prudent", unseen=unseen)
    deployed_a = learn(usages, "aggressive", unseen=unseen)
    rows = measure.leave_one_out(names, usages, unseen=unseen)

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
                t["unescaped"] += raw if c.payload is None else count(json.dumps(c.payload, ensure_ascii=False))
                continue
            t["unescaped"] += count(json.dumps(c.payload, ensure_ascii=False))
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

    # Which fields a held-out run used and the projection removed, by name.
    for i, calls in enumerate(usages):
        agg = learn(usages[:i] + usages[i + 1:], "aggressive", unseen=unseen)
        for c in calls:
            if c.structured and c.tool in agg.keep:
                for f in c.used:
                    if measure.removes(agg, c.tool, f):
                        missed_fields[c.tool][f] += 1

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
        "traces": names,
        "totals": {
            "traces": len(names), "calls": sum(t["calls"] for t in tools.values()),
            "raw": total_raw, "unescaped": sum(t["unescaped"] for t in tools.values()),
            "loo_prudent": sum(r.prudent for r in rows), "loo_aggressive": sum(r.aggressive for r in rows),
            "loo_trimmed_calls": sum(r.trimmed_calls for r in rows), "loo_misses": sum(r.misses for r in rows),
            "errors": sum(t["errors"] for t in tools.values()),
            "duplicates": sum(t["duplicates"] for t in tools.values()),
        },
        "tools": dict(sorted(tools.items(), key=lambda x: -x[1]["raw"])),
    }


def _n(x, lang):
    s = f"{int(round(x)):,}"
    return s.replace(",", ".") if lang == "it" else s


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
    """Deterministic rules. Each returns (id, level, en, it)."""
    out = []
    T = d["totals"]
    total = T["raw"] or 1

    def add(rule, level, tool, en, it):
        out.append({"rule": rule, "level": level, "tool": tool, "en": en, "it": it})

    if T["traces"] < MIN_TRACES:
        add("few-traces", "warning", None,
            f"Only {T['traces']} traces: learned projections are unstable. Collect at least {MIN_TRACES}-10 runs of the same task before applying one.",
            f"Solo {T['traces']} tracce: le proiezioni apprese sono instabili. Raccogli almeno {MIN_TRACES}-10 esecuzioni dello stesso compito prima di applicarne una.")

    esc = 1 - T["unescaped"] / total
    if esc >= ESCAPE_MIN_SAVING:
        add("escapes", "quick-win", None,
            f"Tool outputs contain ASCII escapes (\\uXXXX). Re-serialising them as UTF-8 saves {_n(T['raw'] - T['unescaped'], 'en')} tokens ({_p(esc, 'en')}), with no loss of information.",
            f"Gli output dei tool contengono escape ASCII (\\uXXXX). Riserializzarli in UTF-8 fa risparmiare {_n(T['raw'] - T['unescaped'], 'it')} token ({_p(esc, 'it')}), senza perdita di informazione.")

    if T["duplicates"]:
        add("duplicates", "quick-win", None,
            f"{_calls(T['duplicates'], 'en')} repeat an earlier call of the same run with identical arguments. Cache tool results within a run.",
            f"{_calls(T['duplicates'], 'it')} {'ripete' if T['duplicates'] == 1 else 'ripetono'} una chiamata precedente dello stesso run con argomenti identici. Metti in cache i risultati dei tool all'interno di un run.")

    for tool, t in d["tools"].items():
        share = t["raw"] / total
        if t["loo_raw"] and t["loo_trimmed_calls"]:
            saving = 1 - t["loo_prudent"] / t["loo_raw"]
            # Only tools the generated config will trim: a miss on a tool
            # left untouched is not a risk, and `expand` would have nothing to do.
            trimmed_by_config = share >= TRIM_MIN_SHARE and saving >= TRIM_MIN_SAVING
            if trimmed_by_config:
                add("trim", "saving", tool,
                    f"`{tool}` carries {_p(share, 'en')} of all tool-output tokens. On runs it was not learned from, the prudent projection removes {_p(saving, 'en')} of them ({_n(t['loo_raw'] - t['loo_prudent'], 'en')} tokens over {t['calls']} calls).",
                    f"`{tool}` porta {_il(share)} di tutti i token restituiti dai tool. Sui run da cui non ha imparato, la proiezione prudente ne toglie {_il(saving)} ({_n(t['loo_raw'] - t['loo_prudent'], 'it')} token su {t['calls']} chiamate).")
            rate = t["loo_misses"] / t["loo_trimmed_calls"]
            if trimmed_by_config and rate >= MISS_RATE and t["loo_trimmed_calls"] >= MIN_CALLS:
                top = [f for f, v in sorted(t["fields"].items(), key=lambda x: -x[1]["missed"]) if v["missed"]][:4]
                add("misses", "risk", tool,
                    f"The aggressive projection of `{tool}` hid a field that a later step used in {t['loo_misses']} of {_calls(t['loo_trimmed_calls'], 'en')} held-out ({', '.join(f'`{f}`' for f in top)}). Use the prudent projection, enable `expand`, and collect more traces before trimming aggressively.",
                    f"La proiezione aggressiva di `{tool}` ha nascosto un campo usato più avanti in {_calls(t['loo_misses'], 'it')} su {t['loo_trimmed_calls']} ({', '.join(f'`{f}`' for f in top)}). Usa la proiezione prudente, attiva `expand` e raccogli più tracce prima di tagliare in modo aggressivo.")
        if t["prose_tokens"] / total >= PROSE_MIN_SHARE:
            add("prose", "limit", tool,
                f"`{tool}` returns text, not structured data: {_n(t['prose_tokens'], 'en')} tokens ({_p(t['prose_tokens'] / total, 'en')} of all tool output). Field projection cannot reduce it: call it less often, ask for shorter outputs, or use a compressor for prose.",
                f"`{tool}` restituisce testo, non dati strutturati: {_n(t['prose_tokens'], 'it')} token ({_p(t['prose_tokens'] / total, 'it')} di tutto l'output dei tool). La proiezione dei campi non può ridurlo: chiamalo meno spesso, chiedi output più brevi o usa un compressore per la prosa.")
        fields = t["fields"]
        ftot = sum(v["tokens"] for v in fields.values()) or 1
        unj = {f: v for f, v in fields.items() if v["status"] == "unjudged"}
        unj_tok = sum(v["tokens"] for v in unj.values())
        if unj_tok / ftot >= UNJUDGED_SHARE and t["calls"] >= MIN_CALLS and share >= REVIEW_MIN_SHARE:
            top = [f for f, _ in sorted(unj.items(), key=lambda x: -x[1]["tokens"])][:5]
            add("unjudged", "review", tool,
                f"In `{tool}`, {_p(unj_tok / ftot, 'en')} of field tokens belong to fields the usage detector cannot judge (small numbers, booleans, short labels), e.g. {', '.join(f'`{f}`' for f in top)}. The prudent projection keeps them: review them, and drop explicitly those the task does not need.",
                f"In `{tool}`, {_il(unj_tok / ftot)} dei token dei campi appartiene a campi che il rilevamento d'uso non sa giudicare (numeri piccoli, booleani, etichette brevi), ad esempio {', '.join(f'`{f}`' for f in top)}. La proiezione prudente li tiene: rivedili, ed elimina esplicitamente quelli che al compito non servono.")
        if t["errors"]:
            add("errors", "quick-win", tool,
                f"{t['errors']} of {_calls(t['calls'], 'en')} to `{tool}` returned an error. Each one costs a full turn: check the arguments the agent sends, and state the constraint in the tool description.",
                f"{_calls(t['errors'], 'it')} su {t['calls']} a `{tool}` {'ha' if t['errors'] == 1 else 'hanno'} restituito un errore. Ognuna costa un turno intero: controlla gli argomenti che l'agente invia e scrivi il vincolo nella descrizione del tool.")

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
        return {"kind": "auto", "type": "cache"}
    if s["rule"] == "errors":
        return {"kind": "manual", "type": "fix_tool_description", "tool": tool,
                "examples": t.get("error_examples", [])}
    if s["rule"] == "unjudged":
        unj = [f for f, v in sorted(t["fields"].items(), key=lambda x: -x[1]["tokens"]) if v["status"] == "unjudged"]
        return {"kind": "manual", "type": "review_fields", "tool": tool, "fields": unj[:10]}
    if s["rule"] == "prose":
        return {"kind": "manual", "type": "limit_text_output", "tool": tool}
    return {"kind": "manual", "type": "collect_traces", "min": MIN_TRACES}


ACTION_TEXT = {
    "trim": ("The generated config applies the prudent projection to this tool.",
             "La configurazione generata applica la proiezione prudente a questo tool."),
    "expand": ("The generated config enables `expand` for this tool, so a hidden field can be fetched back.",
               "La configurazione generata attiva `expand` per questo tool, così un campo nascosto si può recuperare."),
    "unescape": ("The generated config re-serialises every JSON output as UTF-8.",
                 "La configurazione generata riserializza ogni output JSON in UTF-8."),
    "cache": ("The generated config caches identical calls within a run.",
              "La configurazione generata mette in cache le chiamate identiche all'interno di un run."),
    "fix_tool_description": ("Rewrite the tool description with the constraint these errors reveal:",
                             "Riscrivi la descrizione del tool con il vincolo che questi errori rivelano:"),
    "review_fields": ("Review these fields and add the ones the task does not need to the projection's dropped list:",
                      "Rivedi questi campi e aggiungi alla lista dei campi tagliati quelli che al compito non servono:"),
    "limit_text_output": ("Reduce how often this tool is called, or how much text it returns.",
                          "Riduci quante volte viene chiamato questo tool, o quanto testo restituisce."),
    "collect_traces": ("Collect more runs of the same task, then generate the report again.",
                       "Raccogli altre esecuzioni dello stesso compito, poi rigenera il report."),
}


def build_config(d: dict, sugg: list[dict], usages, projection_file: str):
    """The runtime config and the projection it points to, from `auto` actions only."""
    trim = {s["action"]["tool"] for s in sugg if s["action"]["type"] == "trim"}
    expand = {s["action"]["tool"] for s in sugg if s["action"]["type"] == "expand"} & trim
    p = learn(usages, "prudent", unseen=d["unseen"])
    p.keep = {t: v for t, v in p.keep.items() if t in trim}
    p.seen = {t: v for t, v in p.seen.items() if t in trim}
    config = {
        "format": "tracetrim/config@1",
        "generated": d["generated"],
        "projection": projection_file,
        # Lossless, so on by default even when the saving is below the
        # report's threshold.
        "unescape": True,
        "cache_identical_calls": any(s["action"]["type"] == "cache" for s in sugg),
        "tools": {t: {"trim": "prudent", "expand": t in expand} for t in sorted(trim)},
    }
    return config, p


L = {
    "title": ("Tool-output telemetry", "Telemetria degli output dei tool"),
    "subtitle": ("What the tools sent, what the agent used, and what to change.",
                 "Cosa hanno restituito i tool, cosa ha usato l'agente, cosa cambiare."),
    "traces": ("traces", "tracce"), "calls": ("tool calls", "chiamate ai tool"),
    "raw": ("tool-output tokens", "token restituiti dai tool"),
    "prudent": ("after prudent projection", "dopo la proiezione prudente"),
    "aggressive": ("after aggressive projection", "dopo la proiezione aggressiva"),
    "misses": ("held-out misses", "mancati sui run esclusi"),
    "suggestions": ("Suggestions", "Suggerimenti"), "none": ("No suggestion.", "Nessun suggerimento."),
    "project": ("Project and author", "Progetto e autore"),
    "project_text": ("Report generated by an open-source tool that learns, from an agent's traces, which fields of tool outputs the agent actually uses.",
                     "Report generato da uno strumento open source che impara, dalle tracce di un agente, quali campi degli output dei tool l'agente usa davvero."),
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
    "method": ("How to read this report", "Come leggere questo report"),
    "method_text": (
        "Savings and misses are measured leave-one-out: each run is trimmed with a projection learned from the other runs. "
        "A miss is a call where the aggressive projection removed a field that the run used later. Usage is a lower bound: "
        "a field read only while reasoning is invisible. Field tokens are the tokens of each field's fragment, summed over calls. "
        "Suggestions come from fixed rules; the numbers are in report.json.",
        "Risparmi e mancati sono misurati lasciando fuori un run alla volta: ogni run è tagliato con una proiezione imparata dagli altri. "
        "Un mancato è una chiamata in cui la proiezione aggressiva ha tolto un campo che il run ha usato più avanti. L'uso è un limite inferiore: "
        "un campo letto solo nel ragionamento non si vede. I token di un campo sono quelli del suo frammento, sommati sulle chiamate. "
        "I suggerimenti vengono da regole fisse; i numeri sono in report.json."),
    "level": {"warning": ("warning", "attenzione"), "risk": ("risk", "rischio"), "saving": ("saving", "risparmio"),
              "quick-win": ("quick win", "intervento rapido"), "review": ("review", "da rivedere"),
              "limit": ("limit", "limite")},
}


def _t(key, sub=None):
    en, it = L[key][sub] if sub else L[key]
    return f'<span lang="en">{html.escape(en)}</span><span lang="it">{html.escape(it)}</span>'


def _num(x):
    return f'<span lang="en">{_n(x, "en")}</span><span lang="it">{_n(x, "it")}</span>'


def _md(s):
    # Backticks become code; everything else is escaped.
    parts = html.escape(s).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))


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


def render_html(d: dict, sugg: list[dict]) -> str:
    T = d["totals"]
    tiles = [("traces", T["traces"], None), ("calls", T["calls"], None), ("raw", T["raw"], None),
             ("prudent", T["loo_prudent"], T["loo_prudent"] / (T["raw"] or 1) - 1),
             ("aggressive", T["loo_aggressive"], T["loo_aggressive"] / (T["raw"] or 1) - 1)]
    tiles_html = "".join(
        f'<div class="tile"><div class="v">{_num(v)}</div><div class="k">{_t(k)}'
        f'{f" <b>{d_:+.0%}</b>" if d_ is not None else ""}</div></div>' for k, v, d_ in tiles)
    tiles_html += (f'<div class="tile"><div class="v">{T["loo_misses"]}/{T["loo_trimmed_calls"]}</div>'
                   f'<div class="k">{_t("misses")}</div></div>')

    def act(a):
        en, it = ACTION_TEXT[a["type"]]
        extra = a.get("examples") or a.get("fields") or []
        items = "".join(f"<li><code>{html.escape(x)}</code></li>" for x in extra)
        badge = _t("auto") if a["kind"] == "auto" else _t("manual")
        return (f'<div class="act {a["kind"]}"><span class="badge">{badge}</span>'
                f'<span lang="en">{_md(en)}</span><span lang="it">{_md(it)}</span>'
                f'{f"<ul>{items}</ul>" if items else ""}</div>')

    sugg_html = "".join(
        f'<li class="s {s["level"]}"><span class="lvl">{_t("level", s["level"])}</span>'
        f'<span lang="en">{_md(s["en"])}</span><span lang="it">{_md(s["it"])}</span>{act(s["action"])}</li>'
        for s in sugg
    ) or f"<li>{_t('none')}</li>"
    cfg = d.get("config")
    cfg_html = (f'<p>{_t("config_text")}</p><pre><code>{html.escape(json.dumps(cfg, indent=2, ensure_ascii=False))}</code></pre>'
                f'<pre><code>tracetrim apply {html.escape(d.get("config_file", "report.config.json"))} TOOL &lt; output.json</code></pre>'
                if cfg else "")

    total = T["raw"] or 1
    rows, details = [], []
    for tool, t in d["tools"].items():
        sav = (lambda a: f"{a / t['loo_raw'] - 1:+.0%}" if t["loo_raw"] and t["loo_trimmed_calls"] else "—")
        rows.append(
            f"<tr><td><code>{html.escape(tool)}</code></td><td>{t['calls']}</td><td>{_num(t['raw'])}</td>"
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
            f"<details><summary><code>{html.escape(tool)}</code> · {len(t['fields'])} {_t('fields')}</summary>"
            f"<table><thead><tr><th>{_t('field')}</th><th>{_t('tokens')}</th><th>{_t('seen')}</th>"
            f"<th>{_t('used')}</th><th>{_t('status')}</th><th>{_t('missed')}</th></tr></thead>"
            f"<tbody>{frows}</tbody></table></details>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tool-output telemetry</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d1d1b; --mut:#6b6a66; --line:#e3e1db; --card:#fff; --acc:#2d5bd7;
  --risk:#b4232a; --warn:#a15c00; --ok:#1f7a45; --rev:#6a4bb3; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --fg:#ecebe6; --mut:#a3a29c; --line:#2e2d2a;
  --card:#1c1c1a; --acc:#8fb0ff; --risk:#ff8a8f; --warn:#f0b35c; --ok:#6fd39b; --rev:#c3a8ff; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:32px 0 10px; }}
.sub {{ color:var(--mut); margin:0 0 4px; }}
.byline {{ color:var(--mut); font-size:13px; margin:0 0 16px; }}
a {{ color:var(--acc); }}
.bar {{ display:flex; gap:8px; margin:0 0 20px; }}
.bar button {{ border:1px solid var(--line); background:var(--card); color:var(--fg); padding:4px 12px; border-radius:6px; cursor:pointer; }}
body.en .bar .en, body.it .bar .it {{ border-color:var(--acc); color:var(--acc); }}
body.en [lang="it"], body.it [lang="en"] {{ display:none; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; }}
.tile .v {{ font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; }}
.tile .k {{ color:var(--mut); font-size:13px; }}
ul.sugg {{ list-style:none; padding:0; margin:0; display:grid; gap:8px; }}
li.s {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--mut); border-radius:6px; padding:10px 12px; }}
li.risk {{ border-left-color:var(--risk); }} li.warning {{ border-left-color:var(--warn); }}
li.saving, li.quick-win {{ border-left-color:var(--ok); }} li.review {{ border-left-color:var(--rev); }}
.lvl {{ display:inline-block; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--mut); margin-right:8px; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; font-size:14px; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:right; white-space:nowrap; }}
th:first-child, td:first-child {{ text-align:left; }}
th {{ color:var(--mut); font-weight:500; }}
tr.dropped td {{ color:var(--mut); }} tr.unjudged td:nth-child(5) {{ color:var(--rev); }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:6px; padding:8px 12px; margin:8px 0; overflow-x:auto; }}
summary {{ cursor:pointer; }}
code {{ font:13px ui-monospace,SFMono-Regular,Menlo,monospace; }}
.method {{ color:var(--mut); font-size:14px; }}
.act {{ margin-top:8px; padding-top:8px; border-top:1px dashed var(--line); font-size:14px; }}
.act ul {{ margin:6px 0 0; padding-left:18px; }}
.badge {{ display:inline-block; font-size:11px; font-weight:600; letter-spacing:.04em; padding:1px 6px; border-radius:4px; margin-right:8px; }}
.act.auto .badge {{ background:var(--ok); color:var(--card); }}
.act.manual .badge {{ background:var(--rev); color:var(--card); }}
pre {{ background:var(--card); border:1px solid var(--line); border-radius:6px; padding:10px 12px; overflow-x:auto; }}
</style></head>
<body class="en"><main>
<div class="bar"><button class="en" onclick="document.body.className='en'">EN</button>
<button class="it" onclick="document.body.className='it'">IT</button></div>
<h1>{_t("title")}</h1><p class="sub">{_t("subtitle")}</p>
<p class="byline">{html.escape(d["generator"]["name"])} {html.escape(d["generator"]["version"])} · {html.escape(d["generator"]["author"])}</p>
<div class="tiles">{tiles_html}</div>
<h2>{_t("suggestions")}</h2><ul class="sugg">{sugg_html}</ul>
<h2>{_t("actions")}</h2>{cfg_html}
<h2>{_t("per_tool")}</h2>
<div class="scroll"><table><thead><tr><th>{_t("tool")}</th><th>{_t("calls")}</th><th>{_t("tokens")}</th><th>{_t("share")}</th>
<th>{_t("prudent")}</th><th>{_t("aggressive")}</th><th>{_t("misses")}</th><th>{_t("errors")}</th><th>{_t("prose")}</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h2>{_t("fields")}</h2>{"".join(details)}
<h2>{_t("method")}</h2><p class="method">{_t("method_text")}</p>
<h2>{_t("project")}</h2>{_about_html(d["generator"])}
<p class="method">{html.escape(d["generated"])} · unseen={html.escape(d["unseen"])}</p>
</main></body></html>
"""
