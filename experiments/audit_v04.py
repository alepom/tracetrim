#!/usr/bin/env python3
"""Two stricter audits of the runs of protocol-v04.md, asked by a reviewer. POST HOC.

  PYTHONPATH=src python -m experiments.audit_v04 [--traces DIR]   # -> results/v04/audits.json

The runs are read from DIR, else $TRACETRIM_V04_TRACES, else this checkout's traces/
(files C1/, C4/ with E1-*.jsonl, E2-*.jsonl and their .meta.json). No model is called.

Both audits are post-hoc analyses: they were written on 2026-11-13, after the results of
E1 and E2 were known, and are not in the frozen protocol. The rules below were written
before the audits were run on the recorded runs, and were not changed after.

1. C4 AUDIT FOR E1
------------------
protocol-v04.md (E1, measure 1) asks, on C4, for the stricter audit of `experiments/c4_audit.py`
beside the keyword test. It had not been run on E1. It is run here, unchanged: `c4_audit.audit`
(rules T1-T5, FORBID, negation) on the final answer of every E1 run on C4, beside the frozen
keyword test (`quality.C4_TRUTH`, computed exactly as `c4_audit.main` does) and the gates of
`results/v04/e1.json`. E1's C4 runs use the original world and the original prompts (the system
prompt of arms S, T, ST has the skill text appended), so the facts of the audit are those of the
scenario it was written for. Adapted: only which files are read (the v0.4 runs, grouped by the
arm in their meta file, instead of the original study's traces/C4).

2. E2 REQUIREMENT, FULL ASSOCIATIONS
------------------------------------
`coverage.requirement` checks part of what the variants ask. These rules check the association.

C1 (the YouTube channel, its video, the views). The truth is the run's own YouTube outputs (the
`output` of every `serp_youtube_organic_live_advanced` call, as returned by the tool, before any
trimming): the list of `items[]` with `channel_name`, `title`, `views_count`, `video_id`, `url`.

  Unit checked: a pair (plan item, channel). Plan items are the elements of both plans of the
  answer; an item's text is all its string values joined. A channel of the outputs is *named* in an
  item when its `channel_name` (at least 3 characters) occurs in the item's text, case-insensitive,
  on word boundaries, and the item's text mentions "youtube", "canale" or "channel". Where one
  matched name lies inside a longer matched name at the same place, only the longer one counts.
  A pair is FULLY CORRECT when one output item O of that channel satisfies both:
    (a) its video is identified in the item's text: the normalised title of O occurs in the
        normalised text, or O's `video_id` or `url` occurs in the raw text, or a quoted span of the
        text (between “ ”, " " or « ») of at least 4 words, normalised, is a substring of O's
        normalised title (a shortened title). Normalised: NFKC, casefold, every run of
        non-alphanumeric characters made one space.
    (b) a view count stated in the item's text matches O's `views_count`. A view count is a number
        with "visualizzazion", "views"/"view" or "visual" within 30 characters after it, or before
        it with no other digit in between. It may carry a qualifier and a scale ("circa", "oltre",
        "più di", "quasi", "about", "over", "~", "mila", "k", "milioni", "M"). Its precision (unit)
        is the scale times 10^-decimals when it has decimals, else the scale times 10^(trailing
        zeros). Match: no qualifier or an approximate one, |views - N| <= unit/2 (exact when the
        unit is 1); a lower bound ("oltre", "più di", "almeno", "over", "more than", ">"),
        N <= views < N + unit; an upper bound ("quasi", "nearly", "almost"), N - unit < views <= N.
  Failure kinds, the first that applies:
    video_of_another_channel   no video of the channel identified, but a video of another channel
                               (not itself named in the item) is
    video_not_identified       no video of the channel identified
    no_view_count              a video identified, no view count stated in the item
    views_mismatch             a view count stated, none matches an identified video of the channel
                               (noted when it matches another video of the run instead)
  Also counted, outside the pairs: plan items that state a view count but name no channel of the
  outputs (a channel absent from the outputs, or written differently, cannot be told apart).
  SENSITIVITY, added after the first execution of this audit and reported beside the rule, not in
  place of it: the same check without the condition on "youtube"/"canale"/"channel" (one run wrote
  "Video analizzato: <channel>, <title>, <views>" with none of those words).

C4 (the invoice link and how it is collected). The truth is the world of E2
(`experiments/simulated/v04`): per invoice, `hosted_invoice_url`, `invoice_pdf` and
`collection_method` (`charge_automatically` | `send_invoice`). The run's invoice outputs are
checked to agree with it.

  Unit checked: a ticket of the answer that cites an invoice id (`\\bin_\\w+` anywhere in the
  ticket, the selection of `coverage.requirement`). Cited invoices: those ids. Only the reply to
  the customer (`risposta_al_cliente`) is read for the link and the statement, as the prompt asks.
  A ticket is FULLY CORRECT when:
    (a) the reply has at least one link that is some invoice's `hosted_invoice_url`, and every such
        link is the `hosted_invoice_url` of an invoice cited in the ticket;
    (b) the collection methods stated in the reply are exactly the set of `collection_method` of
        the linked invoices. Stated "automatic": "addebitat* [≤2 words] automaticamente / in
        automatico", "automaticamente [≤2 words] addebitat*", "addebito automatico", "pagament*
        automatic*", "riscoss*/incassat* automaticamente", "charge_automatically". Stated "sent":
        "inviat* ... per (il) pagament*" (same sentence, ≤60 characters), "send_invoice",
        "pagament* manual*", "pagar* manualmente". A match preceded by "non"/"not"/"no" within two
        words counts as the other method (there are two values).
  Failure kinds, the first that applies:
    no_link                    no link in the reply
    link_not_hosted_page       links, none is an invoice's hosted page (noted: the PDF link instead)
    link_of_uncited_invoice    a hosted page of an invoice the ticket does not cite
    no_collection_statement    no statement of either method
    wrong_collection_method    only the other method stated
    ambiguous_collection       both methods stated, one invoice linked
These are still rules on words, not a reading: a statement about another object of the same reply,
or a link put in the right reply for the wrong reason, passes; a paraphrase not in the lists fails.

The results file holds counts, kinds, ticket and invoice ids (synthetic): no value of a C1 output
(no channel name, title or view count).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path
from statistics import median

from tracetrim.traces import load
from tracetrim.usage import parse_output

from .c4_audit import RULES as C4_RULES, audit as c4_audit
from .checks import final_json
from .coverage import calls_of, requirement
from .paths import ROOT

OUT = ROOT / "results" / "v04" / "audits.json"
YOUTUBE = "serp_youtube_organic_live_advanced"
PLANS = ("piano_editoriale_brevissimo_termine", "piano_editoriale_medio_periodo")


def traces_dir(arg: str | None = None) -> Path:
    return Path(arg or os.environ.get("TRACETRIM_V04_TRACES") or ROOT / "traces")


def runs(traces: Path, case: str, experiment: str) -> list[tuple[dict, Path]]:
    """(meta, trace) of every run of an experiment on a case, in name order."""
    out = []
    for m in sorted((traces / case).glob(f"{experiment}-*.meta.json")):
        meta = json.loads(m.read_text())
        if meta.get("experiment") == experiment and not meta.get("dry_run") and not meta.get("pilot"):
            out.append((meta, m.with_name(m.name.replace(".meta.json", ".jsonl"))))
    return out


# ---------------------------------------------------------------- 1. C4 audit on E1

def frozen_test(ans) -> dict:
    """The frozen keyword test, as `c4_audit.main` computes it."""
    from .quality import C4_TRUTH
    tickets = {str(t.get("id")): t for t in (ans or {}).get("ticket") or [] if isinstance(t, dict)}
    return {k: bool(tickets.get(k)) and f(json.dumps(tickets[k], ensure_ascii=False).lower())
            for k, f in C4_TRUTH.items()}


def e1_c4(traces: Path) -> dict:
    gates = {r["run"]: r for r in json.loads((ROOT / "results" / "v04" / "e1.json").read_text())["per_run"]
             if r["case"] == "C4"}
    per_arm: dict[str, list] = {}
    for meta, path in runs(traces, "C4", "E1"):
        ans = final_json(load(path))
        strict, frozen = c4_audit(ans), frozen_test(ans)
        g = gates.get(meta["run"], {})
        per_arm.setdefault(meta["arm"], []).append({
            "run": meta["run"], "answer_parsed": ans is not None,
            "audit": sum(strict.values()), "audit_failed": [k for k in C4_RULES if not strict[k]],
            "keyword_test": sum(frozen.values()), "keyword_only": [k for k in C4_RULES if frozen[k] and not strict[k]],
            "gates_passed": g.get("gates_passed"), "gate_facts": g.get("facts")})
    arms = {}
    for arm in ("R", "S", "T", "ST"):
        rs = per_arm.get(arm, [])
        a = [r["audit"] for r in rs]
        arms[arm] = {"runs": len(rs), "audit_total": sum(a), "audit_of": 5 * len(rs),
                     "audit_per_run": {"median": median(a), "min": min(a), "max": max(a)} if a else None,
                     "keyword_test_total": sum(r["keyword_test"] for r in rs),
                     "gates_passed": sum(bool(r["gates_passed"]) for r in rs),
                     "gate_facts": [sum(r["gate_facts"][0] for r in rs), sum(r["gate_facts"][1] for r in rs)]
                     if all(r["gate_facts"] for r in rs) else None,
                     "audit_failed_by_ticket": dict(sorted(Counter(t for r in rs for t in r["audit_failed"]).items())),
                     "runs_detail": rs}
    return arms


# ---------------------------------------------------------------- 2a. C1: channel, video, views

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).casefold()
    return " ".join(re.sub(r"[^\w]+|_", " ", s).split())


def text_of(x) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return "\n".join(text_of(v) for v in x.values())
    if isinstance(x, list):
        return "\n".join(text_of(v) for v in x)
    return ""


def youtube_items(path: Path) -> list[dict]:
    """Every item of the run's YouTube outputs that has a channel name."""
    out = []
    for c in calls_of(path):
        if c["tool"] != YOUTUBE:
            continue
        payload = parse_output(c["output"])
        for it in (payload.get("items") if isinstance(payload, dict) else None) or []:
            if isinstance(it, dict) and str(it.get("channel_name") or "").strip():
                out.append(it)
    return out


def named_channels(text: str, channels) -> list[str]:
    """Channels of the outputs named in a text: whole words, case-insensitive; a name inside
    a longer matched name at the same place does not count."""
    t = unicodedata.normalize("NFKC", text).replace("’", "'")
    spans = []
    for ch in {c for c in channels if len(c.strip()) >= 3}:
        name = unicodedata.normalize("NFKC", ch).replace("’", "'").strip()
        for m in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", t, re.I):
            spans.append((m.start(), m.end(), ch))
    keep = {ch for s, e, ch in spans if not any(s2 <= s and e <= e2 and (e2 - s2) > (e - s) for s2, e2, _ in spans)}
    return sorted(keep)


QUOTED = re.compile(r"“([^”]+)”|\"([^\"]+)\"|«([^»]+)»")


def video_identified(item_text: str, o: dict) -> str | None:
    """How the video of output item `o` is identified in the text: 'title', 'id', 'short', or None."""
    title = norm(o.get("title") or "")
    nt = norm(item_text)
    if title and f" {title} " in f" {nt} ":
        return "title"
    for key in ("video_id", "url"):
        v = str(o.get(key) or "")
        if len(v) >= 6 and v in item_text:
            return "id"
    for m in QUOTED.finditer(item_text):
        q = norm(next(g for g in m.groups() if g is not None))
        if title and len(q.split()) >= 4 and f" {q} " in f" {title} ":
            return "short"
    return None


QUAL = (r"(?P<q>circa|oltre|più\s+di|piu\s+di|quasi|almeno|about|around|approx\.?|ca\.|over|more\s+than|"
        r"at\s+least|nearly|almost|~|≈|>)?")
NUM = re.compile(QUAL + r"\s*(?P<n>\d{1,3}(?:[.,   ]\d{3})+|\d+(?:[.,]\d+)?)\s*"
                 r"(?P<s>milioni|milione|mln|mila|[kKM])?(?!\w)", re.I)
VIEWS = re.compile(r"visualizzazion|\bviews?\b|visual", re.I)
LOWER = {"oltre", "più di", "piu di", "almeno", "over", "more than", "at least", ">"}
UPPER = {"quasi", "nearly", "almost"}


def view_counts(text: str) -> list[dict]:
    """The view counts a text states: value, unit (precision) and kind of qualifier."""
    out = []
    for m in NUM.finditer(text):
        after, before = text[m.end():m.end() + 30], text[max(0, m.start() - 30):m.start()]
        near_after = VIEWS.search(after) and not re.search(r"\d", after[:VIEWS.search(after).start()])
        vb = list(VIEWS.finditer(before))
        near_before = vb and not re.search(r"\d", before[vb[-1].end():])
        if not (near_after or near_before):
            continue
        raw, s = m.group("n"), (m.group("s") or "").lower()
        scale = {"milioni": 10**6, "milione": 10**6, "mln": 10**6, "m": 10**6, "mila": 1000, "k": 1000}.get(s, 1)
        seps = re.findall(r"[.,   ]", raw)
        if scale > 1 and len(seps) == 1:                       # "1,2 milioni", "1.5M": a decimal
            whole, dec = re.split(r"[.,   ]", raw)
            value, decimals = float(f"{whole}.{dec}"), len(dec)
        elif re.fullmatch(r"\d{1,3}(?:[.,   ]\d{3})+", raw):   # thousands separators
            value, decimals = float(re.sub(r"\D", "", raw)), 0
        else:
            value = float(raw.replace(",", "."))
            decimals = len(re.split(r"[.,]", raw)[1]) if re.search(r"[.,]", raw) else 0
        n = round(value * scale)
        if decimals:
            unit = scale / 10**decimals
        else:
            whole = int(value)
            zeros = len(str(whole)) - len(str(whole).rstrip("0")) if whole else 0
            unit = scale * 10**zeros
        q = re.sub(r"\s+", " ", (m.group("q") or "").lower())
        kind = "lower" if q in LOWER else "upper" if q in UPPER else "approx"
        out.append({"n": n, "unit": unit, "kind": kind})
    return out


def views_match(stated: dict, views) -> bool:
    try:
        v = int(views)
    except (TypeError, ValueError):
        return False
    n, unit = stated["n"], stated["unit"]
    if stated["kind"] == "lower":
        return n <= v < n + unit
    if stated["kind"] == "upper":
        return n - unit < v <= n
    return v == n if unit <= 1 else abs(v - n) <= unit / 2


def c1_items(ans, items: list[dict], context: bool = True) -> tuple[list[dict], int]:
    """The pairs (plan item, channel) checked, and the plan items with a view count and no channel.
    `context=False` drops the condition on "youtube"/"canale"/"channel": the sensitivity below."""
    channels = {str(o["channel_name"]) for o in items}
    checked, unattributed = [], 0
    for plan in PLANS:
        for i, entry in enumerate((ans or {}).get(plan) or [] if isinstance(ans, dict) else []):
            text = text_of(entry)
            counts = view_counts(text)
            names = named_channels(text, channels) if not context or re.search(r"youtube|canale|channel", text, re.I) else []
            if counts and not names:
                unattributed += 1
            for ch in names:
                own = [o for o in items if o["channel_name"] == ch]
                ident = [(o, how) for o in own if (how := video_identified(text, o))]
                rec = {"plan": plan, "item": i, "ok": False, "kind": None, "how": None}
                if not ident:
                    others = [o for o in items if o["channel_name"] != ch and o["channel_name"] not in names
                              and video_identified(text, o)]
                    rec["kind"] = "video_of_another_channel" if others else "video_not_identified"
                elif not counts:
                    rec["kind"] = "no_view_count"
                else:
                    hit = next(((o, how) for o, how in ident if any(views_match(c, o.get("views_count")) for c in counts)), None)
                    if hit:
                        rec.update(ok=True, how=hit[1],
                                   rounded=not any(views_match(dict(c, unit=1, kind="approx"), hit[0].get("views_count"))
                                                   for c in counts))
                    else:
                        rec["kind"] = "views_mismatch"
                        rec["views_of_another_video"] = any(views_match(c, o.get("views_count")) for c in counts
                                                            for o in items if all(o is not x for x, _ in ident))
                        own_v = [int(o["views_count"]) for o, _ in ident if isinstance(o.get("views_count"), (int, float))]
                        if own_v:   # how far off, as a share of the true value: no value itself
                            rec["relative_error"] = round(min(abs(c["n"] - v) / v for c in counts for v in own_v if v), 4) \
                                if any(own_v) else None
                checked.append(rec)
    return checked, unattributed


# ---------------------------------------------------------------- 2b. C4: link and collection method

URL = re.compile(r"https?://[^\s<>\"')\]]+")
CITED = re.compile(r"\bin_\w+")
AUTO = [r"\baddebitat\w*\s+(?:\S+\s+){0,2}?(?:automaticamente|in\s+automatico)",
        r"\bautomaticamente\s+(?:\S+\s+){0,2}?addebitat\w*", r"\baddebito\s+automatico",
        r"\bpagament\w*\s+automatic\w*", r"\b(?:riscoss|incassat)\w*\s+automaticamente", r"\bcharge_automatically\b"]
SEND = [r"\binviat\w*\b[^.;]{0,60}?\bper\s+(?:il\s+)?pagament\w*", r"\bsend_invoice\b",
        r"\bpagament\w*\s+manual\w*", r"\bpagar\w*\s+manualmente"]
NOT = re.compile(r"\b(?:non|not|no)\s+(?:\S+\s+){0,2}$", re.I)
METHOD = {"auto": "charge_automatically", "send": "send_invoice"}
OTHER = {"charge_automatically": "send_invoice", "send_invoice": "charge_automatically"}


def stated_methods(reply: str) -> set[str]:
    out = set()
    for key, pats in (("auto", AUTO), ("send", SEND)):
        for pat in pats:
            for m in re.finditer(pat, reply, re.I):
                negated = bool(NOT.search(reply[:m.start()]))
                out.add(OTHER[METHOD[key]] if negated else METHOD[key])
    return out


def invoice_truth() -> tuple[dict, dict, dict]:
    """From E2's world: hosted url -> invoice, pdf url -> invoice, invoice -> collection method."""
    from .simulated.v04 import world
    hosted, pdf, method = {}, {}, {}
    for inv in world()["invoices"]:
        hosted[inv["hosted_invoice_url"]] = inv["id"]
        if inv.get("invoice_pdf"):
            pdf[inv["invoice_pdf"]] = inv["id"]
        method[inv["id"]] = inv["collection_method"]
    return hosted, pdf, method


def c4_ticket(t: dict, truth) -> dict:
    hosted, pdf, method = truth
    cited = sorted(set(CITED.findall(json.dumps(t, ensure_ascii=False))))
    reply = str(t.get("risposta_al_cliente", ""))
    links = [u.rstrip(".,;:!?»") for u in URL.findall(reply)]
    linked = [hosted[u] for u in links if u in hosted]
    rec = {"ticket": str(t.get("id")), "cited": cited, "linked": linked, "ok": False, "kind": None}
    if not links:
        rec["kind"] = "no_link"
    elif not linked:
        rec["kind"] = "link_not_hosted_page"
        rec["pdf_instead"] = any(u in pdf for u in links)
    elif any(i not in cited for i in linked):
        rec["kind"] = "link_of_uncited_invoice"
    else:
        truth_m, said = {method[i] for i in linked}, stated_methods(reply)
        rec.update(true_methods=sorted(truth_m), stated=sorted(said))
        if not said:
            rec["kind"] = "no_collection_statement"
        elif said == truth_m:
            rec["ok"] = True
        elif not said & truth_m:
            rec["kind"] = "wrong_collection_method"
        else:
            rec["kind"] = "ambiguous_collection"
    return rec


# ---------------------------------------------------------------- 2. E2, per run and sequence

def e2(traces: Path) -> dict:
    truth = invoice_truth()
    old = {r["run"] + "|" + r["case"]: r["e2"]["requirement"]
           for r in json.loads((ROOT / "results" / "v04" / "e2.json").read_text())["per_run"]}
    checks = {"c4_invoice_outputs": 0, "c4_invoice_outputs_disagreeing_with_world": 0,
              "c1_runs_without_youtube_items": [], "requirement_recomputed_differs": []}
    seqs: dict[tuple, list] = {}
    for case in ("C1", "C4"):
        for meta, path in runs(traces, case, "E2"):
            ans = final_json(load(path))
            req = requirement(path, case)
            if req != old.get(meta["run"] + "|" + case):
                checks["requirement_recomputed_differs"].append(f"{case}/{meta['run']}")
            if case == "C1":
                items = youtube_items(path)
                if not items:
                    checks["c1_runs_without_youtube_items"].append(meta["run"])
                recs, unattributed = c1_items(ans, items)
                sens, sens_un = c1_items(ans, items, context=False)
                extra = {"youtube_items": len(items), "plan_items_with_views_and_no_channel": unattributed,
                         "how_matched": dict(Counter(r["how"] for r in recs if r["ok"])),
                         "rounded_views_accepted": sum(bool(r.get("rounded")) for r in recs if r["ok"]),
                         "sensitivity_without_context_words": {
                             "items": len(sens), "items_correct": sum(r["ok"] for r in sens),
                             "failures": dict(Counter(r["kind"] for r in sens if not r["ok"])),
                             "plan_items_with_views_and_no_channel": sens_un}}
            else:
                for c in calls_of(path):
                    if c["tool"] in ("stripe_list_invoices", "stripe_retrieve_invoice"):
                        p = parse_output(c["output"])
                        for inv in (p.get("data") if isinstance(p, dict) and isinstance(p.get("data"), list) else [p]):
                            if isinstance(inv, dict) and str(inv.get("id", "")).startswith("in_"):
                                checks["c4_invoice_outputs"] += 1
                                if (truth[2].get(inv["id"]) != inv.get("collection_method")
                                        or truth[0].get(inv.get("hosted_invoice_url")) != inv["id"]):
                                    checks["c4_invoice_outputs_disagreeing_with_world"] += 1
                tickets = [t for t in (ans or {}).get("ticket") or [] if isinstance(t, dict)
                           and CITED.search(json.dumps(t))]
                recs, extra = [c4_ticket(t, truth) for t in tickets], {}
            seqs.setdefault((case, meta["arm"], meta["sequence"]), []).append({
                "run": meta["run"], "n": meta["n"], "answer_parsed": ans is not None,
                "requirement": req, "items": len(recs), "items_correct": sum(r["ok"] for r in recs),
                "failures": dict(Counter(r["kind"] for r in recs if not r["ok"])), "detail": recs, **extra})
    out = []
    for (case, arm, seq), rs in sorted(seqs.items()):
        rs.sort(key=lambda r: r["n"])
        out.append({"case": case, "arm": arm, "sequence": seq, "runs": len(rs),
                    "requirement_met_runs": sum(r["requirement"]["met"] for r in rs),
                    "requirement_items": sum(r["requirement"]["items"] for r in rs),
                    "items": sum(r["items"] for r in rs), "items_correct": sum(r["items_correct"] for r in rs),
                    "runs_all_items_correct": sum(r["items"] > 0 and r["items"] == r["items_correct"] for r in rs),
                    "failures": dict(sum((Counter(r["failures"]) for r in rs), Counter())), "per_run": rs})
    return {"sequences": out, "input_checks": checks}


# ---------------------------------------------------------------- input checks, tables

def input_counts(e1_arms: dict, e2_res: dict) -> dict:
    """Runs read here against the runs the results files hold."""
    e1j = json.loads((ROOT / "results" / "v04" / "e1.json").read_text())
    e2j = json.loads((ROOT / "results" / "v04" / "e2.json").read_text())
    want1 = Counter(r["arm"] for r in e1j["per_run"] if r["case"] == "C4")
    got1 = Counter({a: v["runs"] for a, v in e1_arms.items() if v["runs"]})
    want2 = Counter(f"{r['case']}/{r['arm']}/s{r['sequence']}" for r in e2j["per_run"])
    got2 = Counter({f"{s['case']}/{s['arm']}/s{s['sequence']}": s["runs"] for s in e2_res["sequences"]})
    return {"e1_c4_runs_per_arm": dict(got1), "e1_json": dict(want1), "e1_match": got1 == want1,
            "e2_runs_per_sequence": dict(sorted(got2.items())), "e2_json": dict(sorted(want2.items())),
            "e2_match": got2 == want2}


def tables(res: dict) -> str:
    L = ["", "1. C4 audit on E1 (post hoc)",
         f"  {'arm':<4}{'runs':>5}{'gates':>7}{'gate facts':>12}{'keyword':>9}{'audit':>8}   audit per run median [min-max]   failed"]
    for arm, a in res["e1_c4"].items():
        s = a["audit_per_run"]
        gf = f"{a['gate_facts'][0]}/{a['gate_facts'][1]}" if a["gate_facts"] else "-"
        L.append(f"  {arm:<4}{a['runs']:>5}{a['gates_passed']:>5}/{a['runs']:<1}{gf:>12}"
                 f"{a['keyword_test_total']:>6}/{a['audit_of']:<3}{a['audit_total']:>4}/{a['audit_of']:<3}"
                 f"   {s['median']:g} [{s['min']}-{s['max']}]{'':<20}{a['audit_failed_by_ticket']}")
    L += ["", "2. E2 requirement, full associations (post hoc)",
          f"  {'case':<5}{'arm':<20}{'seq':>4}{'runs':>5}{'req. met':>10}{'items':>7}{'correct':>9}{'runs all ok':>13}   failures"]
    for s in res["e2"]["sequences"]:
        L.append(f"  {s['case']:<5}{s['arm']:<20}{s['sequence']:>4}{s['runs']:>5}{s['requirement_met_runs']:>8}/{s['runs']}"
                 f"{s['items']:>7}{s['items_correct']:>9}{s['runs_all_items_correct']:>11}/{s['runs']}   {s['failures']}")
    for case in ("C1", "C4"):
        ss = [s for s in res["e2"]["sequences"] if s["case"] == case]
        L.append(f"  {case} overall: requirement met {sum(s['requirement_met_runs'] for s in ss)}/{sum(s['runs'] for s in ss)} runs; "
                 f"full associations {sum(s['items_correct'] for s in ss)}/{sum(s['items'] for s in ss)} items, "
                 f"{sum(s['runs_all_items_correct'] for s in ss)}/{sum(s['runs'] for s in ss)} runs with every item correct")
    L += ["", f"  input: {res['input']}", f"  checks: {res['e2']['input_checks']}"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--traces", help="the v0.4 runs (default: $TRACETRIM_V04_TRACES, else traces/)")
    a = ap.parse_args()
    traces = traces_dir(a.traces)
    if not any((traces / "C4").glob("E1-*.jsonl")):
        raise SystemExit(f"{traces}: no E1 run on C4 (set $TRACETRIM_V04_TRACES or --traces)")
    e1_arms, e2_res = e1_c4(traces), e2(traces)
    res = {"post_hoc": True,
           "note": "Post-hoc analyses, asked by a reviewer on 2026-11-13 after the results of E1 and E2 were known; "
                   "not in protocol-v04.md. Rules in experiments/audit_v04.py, written before these audits were run.",
           "input": input_counts(e1_arms, e2_res), "e1_c4": e1_arms, "e2": e2_res}
    OUT.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    print(tables(res))
    print(f"\n  written: {OUT.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
