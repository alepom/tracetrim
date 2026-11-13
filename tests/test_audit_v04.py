"""The post-hoc audits of experiments/audit_v04.py, on small hand-built traces:
every rule passes what it should and fails what it should, with the failure kind."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.audit_v04 import (c1_items, c4_ticket, e1_c4, runs, stated_methods,  # noqa: E402
                                   view_counts, views_match, youtube_items)

SYSTEM = "system"


def write_run(d: Path, case: str, name: str, meta: dict, calls=(), answer=None):
    (d / case).mkdir(parents=True, exist_ok=True)
    events = [{"kind": "prompt", "text": SYSTEM}, {"kind": "prompt", "text": "user"}]
    events += [{"kind": "call", "tool": t, "arguments": {}, "output": json.dumps(o)} for t, o in calls]
    if answer is not None:
        events.append({"kind": "message", "text": json.dumps(answer, ensure_ascii=False)})
    (d / case / f"{name}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (d / case / f"{name}.meta.json").write_text(json.dumps({"run": name, "case": case, "dry_run": False, **meta}))
    return d / case / f"{name}.jsonl"


# ------------------------------------------------------------------ 1. C4 audit on E1

GOOD = {"ticket": [
    {"id": "T1", "diagnosi": "Due addebiti riusciti di 29,00 € a pochi secondi."},
    {"id": "T2", "diagnosi": "Il pagamento è stato rifiutato per fondi insufficienti."},
    {"id": "T3", "diagnosi": "Il periodo corrente termina il 20 novembre 2026."},
    {"id": "T4", "diagnosi": "Il rimborso di 49,00 € è ancora in elaborazione."},
    {"id": "T5", "diagnosi": "Fattura di 38,00 €: piano 29,00 € più 9,00 € di storage 50 GB."}]}


def test_e1_runs_are_read_by_arm_and_audited_unchanged(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    bad["ticket"][1]["diagnosi"] = "Errore di tipo carta; nuovo tentativo il 29 ottobre."   # no reason
    bad["ticket"][2]["diagnosi"] = "Il periodo termina il 21 dicembre 2026."                 # wrong date
    write_run(tmp_path, "C4", "E1-R-1", {"experiment": "E1", "arm": "R", "n": 1}, answer=GOOD)
    write_run(tmp_path, "C4", "E1-R-2", {"experiment": "E1", "arm": "R", "n": 2}, answer=bad)
    write_run(tmp_path, "C4", "E1-ST-1", {"experiment": "E1", "arm": "ST", "n": 1}, answer=None)   # no answer
    write_run(tmp_path, "C4", "E2-base-s1-r1", {"experiment": "E2", "arm": "base", "n": 1}, answer=GOOD)
    assert len(runs(tmp_path, "C4", "E1")) == 3
    arms = e1_c4(tmp_path)
    assert arms["R"]["runs"] == 2 and arms["R"]["audit_total"] == 8
    assert arms["R"]["audit_failed_by_ticket"] == {"T2": 1, "T3": 1}
    assert arms["ST"]["audit_total"] == 0 and arms["ST"]["runs_detail"][0]["answer_parsed"] is False
    assert arms["S"]["runs"] == 0


# ------------------------------------------------------------------ 2a. C1

def yt(channel, title, views, vid="abcdefghijk"):
    return {"type": "youtube_video", "channel_name": channel, "title": title, "views_count": views,
            "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}


ITEMS = [yt("Zeta Lab", "Agenti AI per il marketing spiegati bene (corso completo)", 123210, "vid00000001"),
         yt("Zeta Lab", "Un altro video della serie", 5000, "vid00000002"),
         yt("Altro Canale", "Come usare Claude per le campagne", 98765, "vid00000003"),
         yt("Zeta", "Video di un canale dal nome corto", 777, "vid00000004")]


def plan(*notes):
    return {"piano_editoriale_brevissimo_termine": [{"id": i, "note_di_redazione": n} for i, n in enumerate(notes)],
            "piano_editoriale_medio_periodo": []}


def check(note):
    recs, unattributed = c1_items(plan(note), ITEMS)
    return recs, unattributed


def test_c1_exact_triple_passes():
    recs, _ = check('YouTube: il canale più rilevante è Zeta Lab, con "Agenti AI per il marketing spiegati '
                    'bene (corso completo)", 123.210 visualizzazioni.')
    assert [(r["ok"], r["how"]) for r in recs] == [(True, "title")]


def test_c1_rounding_as_stated():
    base = "YouTube: Zeta Lab, video “Agenti AI per il marketing spiegati bene”, "
    assert check(base + "circa 123.000 visualizzazioni.")[0][0]["ok"]
    assert check(base + "about 123,000 views.")[0][0]["ok"]
    assert check(base + "oltre 120 mila visualizzazioni.")[0][0]["ok"]
    assert check(base + "123,2 mila visualizzazioni.")[0][0]["ok"]
    r = check(base + "circa 125.000 visualizzazioni.")[0][0]                # 125,000 ± 500: no
    assert not r["ok"] and r["kind"] == "views_mismatch"
    r = check(base + "123.000 visualizzazioni.")[0][0]                      # a round number, ± 500
    assert r["ok"] and r["rounded"] and r["how"] == "short"


def test_view_count_parsing():
    assert view_counts("1.529.378 visualizzazioni") == [{"n": 1529378, "unit": 1, "kind": "approx"}]
    assert view_counts("1,2 milioni di visualizzazioni")[0]["n"] == 1200000
    assert view_counts("views: 84281")[0]["n"] == 84281
    assert [c["n"] for c in view_counts("nel 2026: 84.281 visualizzazioni")] == [84281]   # the nearest number only
    assert views_match({"n": 1200000, "unit": 100000, "kind": "lower"}, 1250000)
    assert not views_match({"n": 1200000, "unit": 100000, "kind": "lower"}, 1190000)
    assert views_match({"n": 100000, "unit": 100000, "kind": "upper"}, 99000)


def test_c1_video_of_another_channel():
    r = check('YouTube, canale più rilevante: Zeta Lab, con "Come usare Claude per le campagne", '
              '98.765 visualizzazioni.')[0][0]
    assert not r["ok"] and r["kind"] == "video_of_another_channel"


def test_c1_video_not_identified_when_title_is_not_the_outputs():
    r = check('YouTube: Zeta Lab, video "AI agents for marketing, explained (full course)", '
              '123.210 visualizzazioni.')[0][0]
    assert not r["ok"] and r["kind"] == "video_not_identified"


def test_c1_no_view_count_and_wrong_views_of_another_video():
    r = check('YouTube: Zeta Lab, video "Un altro video della serie".')[0][0]
    assert r["kind"] == "no_view_count"
    r = check('YouTube: Zeta Lab, video "Agenti AI per il marketing spiegati bene (corso completo)", '
              '5.000 visualizzazioni.')[0][0]
    assert r["kind"] == "views_mismatch" and r["views_of_another_video"]


def test_c1_video_by_url_and_longer_name_wins():
    recs, _ = check("YouTube: Zeta Lab, https://www.youtube.com/watch?v=vid00000002, 5000 views.")
    assert [(r["ok"], r["how"]) for r in recs] == [(True, "id")]          # "Canale" inside "Zeta Lab": not counted


def test_c1_context_words_and_unattributed_counts():
    note = 'Video analizzato: Zeta Lab, "Un altro video della serie", 5.000 visualizzazioni.'
    assert check(note) == ([], 1)          # no "youtube"/"canale"/"channel": not checked, counted as unattributed
    recs, _ = c1_items(plan(note), ITEMS, context=False)            # the sensitivity checks it
    assert [r["ok"] for r in recs] == [True]
    recs, unattributed = c1_items(plan('YouTube: Altro, "Un video", 5.000 visualizzazioni.'), ITEMS)
    assert recs == [] and unattributed == 1


def test_c1_a_channel_named_like_a_common_word_is_matched_anyway():
    # A known limit of the rule: a channel whose name is a common word ("Google", "Canale") is
    # taken as named wherever the word occurs, and the pair fails for want of its video.
    items = ITEMS + [yt("Canale", "Il video del canale generico", 10)]
    recs, _ = c1_items(plan('YouTube: il canale più rilevante è Zeta Lab, con "Un altro video della serie", '
                            '5.000 visualizzazioni.'), items)
    assert sorted((r["ok"], r["kind"]) for r in recs) == [(False, "video_not_identified"), (True, None)]


def test_c1_youtube_items_are_read_from_the_trace(tmp_path):
    p = write_run(tmp_path, "C1", "E2-base-s1-r1", {"experiment": "E2", "arm": "base", "n": 1, "sequence": 1},
                  calls=[("serp_youtube_organic_live_advanced", {"items": ITEMS + [{"type": "x"}]}),
                         ("kw_data_google_trends_explore", {"items": [{"channel_name": "no"}]})], answer={})
    assert len(youtube_items(p)) == len(ITEMS)


# ------------------------------------------------------------------ 2b. C4

TRUTH = ({"https://inv.test/i/1": "in_A1", "https://inv.test/i/2": "in_B2"},
         {"https://inv.test/pdf/1": "in_A1"},
         {"in_A1": "charge_automatically", "in_B2": "send_invoice"})


def ticket(reply, cited=("in_A1",)):
    return {"id": "T5", "evidenze": [{"oggetto": c, "fatto": "x"} for c in cited], "risposta_al_cliente": reply}


def test_c4_pass():
    r = c4_ticket(ticket("La fattura viene addebitata automaticamente. Link: https://inv.test/i/1."), TRUTH)
    assert r["ok"] and r["linked"] == ["in_A1"]
    r = c4_ticket(ticket("La fattura le viene inviata per il pagamento: https://inv.test/i/2", ("in_B2",)), TRUTH)
    assert r["ok"]


def test_c4_negation_counts_as_the_other_method():
    assert stated_methods("La fattura non viene addebitata automaticamente.") == {"send_invoice"}
    r = c4_ticket(ticket("Non viene addebitata automaticamente: https://inv.test/i/2", ("in_B2",)), TRUTH)
    assert r["ok"]


def test_c4_link_failures():
    assert c4_ticket(ticket("Addebitata automaticamente."), TRUTH)["kind"] == "no_link"
    r = c4_ticket(ticket("Addebitata automaticamente: https://inv.test/pdf/1"), TRUTH)
    assert r["kind"] == "link_not_hosted_page" and r["pdf_instead"]
    r = c4_ticket(ticket("Addebitata automaticamente: https://inv.test/i/2"), TRUTH)     # in_B2 not cited
    assert r["kind"] == "link_of_uncited_invoice"


def test_c4_statement_failures():
    assert c4_ticket(ticket("Ecco la fattura: https://inv.test/i/1"), TRUTH)["kind"] == "no_collection_statement"
    r = c4_ticket(ticket("La fattura è inviata a lei per il pagamento: https://inv.test/i/1"), TRUTH)
    assert r["kind"] == "wrong_collection_method"
    r = c4_ticket(ticket("Viene addebitata automaticamente; è stata inviata per il pagamento: https://inv.test/i/1"), TRUTH)
    assert r["kind"] == "ambiguous_collection"
