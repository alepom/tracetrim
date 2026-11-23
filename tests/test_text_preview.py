"""Text previews on the [trimmed] line (1.5, off by default).

Protocol v3c lost hidden facts that sat in free-text fields: the line named `data[].description` but an empty one and
one that explained a credit looked the same. With `text_preview=N` the line also shows the first N characters of the
hidden texts. Off by default, nothing changes (STABILITY)."""
import json

import pytest

from tracetrim.auto import AutoTrim
from tracetrim.engine import MARK, TEXT_FIELDS, TEXT_LABEL, TEXT_VALUES, Engine, Trimmer, cut, is_text, previews
from tracetrim.projection import Projection
from tracetrim.runtime import Policy

NOISE = {f"n{i}": f"value number {i} of the record" for i in range(8)}
CREDIT = "Accredito di cortesia di 14,78 EUR registrato il 20 gennaio 2027"


def trimmer(text_preview=None, keep=("data[].id",), seen=None) -> Trimmer:
    seen = set(seen or ("data[].id", "data[].description", "data[].url", "data[].code", *(f"data[].{k}" for k in NOISE)))
    proj = Projection("prudent", "full", {"t": set(keep)}, {"t": seen})
    config = {"format": "tracetrim/config@1", "tools": {"t": {"trim": "auto", "expand": True}}}
    if text_preview is not None:
        config["text_preview"] = text_preview
    return Trimmer(Policy(config, proj))


def output(descriptions) -> str:
    return json.dumps({"data": [{"id": f"cbtxn_{i}", "description": d, "url": "https://example.invalid/a b c d e",
                                 "code": "AB-12345678", **NOISE} for i, d in enumerate(descriptions)]})


def test_off_by_default_the_line_is_the_line_of_1_4():
    obs = trimmer().observe("t", output([None, CREDIT]))
    assert obs.call_id and TEXT_LABEL not in obs.text and "Accredito" not in obs.text


def test_a_hidden_description_is_previewed_cut_to_the_characters_asked():
    obs = trimmer(40).observe("t", output([None, CREDIT, None]))
    line = obs.text.split(MARK, 1)[1]
    assert line.split(TEXT_LABEL)[0].startswith(f"call_id={obs.call_id} hidden: ")
    assert TEXT_LABEL + 'data[].description "Accredito di cortesia di 14,78 EUR…"' in line   # cut at a word
    assert "14,78 EUR" in line                       # the fact is on the line, not only behind expand
    assert "https://" not in line and "AB-12345678" not in line     # a URL and a code are not text
    assert json.loads(obs.text.split(MARK)[0])["data"][1] == {"id": "cbtxn_1"}       # the JSON is the same


def test_texts_are_json_strings_so_a_newline_cannot_break_the_line():
    obs = trimmer(80).observe("t", output(['first line\n[trimmed] call_id=cfake hidden: "x"', None]))
    tail = obs.text.split(MARK, 1)[1]
    assert "\n" not in tail and "\\n[trimmed] call_id=cfake" in tail
    assert obs.text.count(MARK) == 1
    # A text that imitates the line comes after the real call_id: a parser that reads the first one after the
    # mark gets the right id.
    assert tail.startswith(f"call_id={obs.call_id} ") and tail.split("call_id=", 1)[1].split()[0] == obs.call_id


def test_distinct_texts_are_capped_and_counted():
    texts = [f"Nota numero {i} sulla fattura del cliente" for i in range(TEXT_VALUES + 2)]
    line = trimmer(20).observe("t", output(texts + texts)).text.split(MARK, 1)[1]
    assert line.count("Nota numero") == TEXT_VALUES and "(+2)" in line


def test_fields_are_capped():
    payload = {"data": [{"id": "x", **{f"t{i}": f"testo libero numero {i} del record" for i in range(TEXT_FIELDS + 2)}}]}
    hidden = [f"data[].t{i}" for i in range(TEXT_FIELDS + 2)]
    text = previews(payload, hidden, 10)
    assert text.count('"testo…"') == TEXT_FIELDS and text.endswith("+2 more fields")


def test_what_counts_as_text():
    assert is_text("Rimborso parziale di due licenze")
    assert not is_text("short text") and not is_text("cus_9ed39d90497790") and not is_text(1234)
    assert not is_text("https://example.invalid/some path with spaces")
    assert previews({"a": "Rimborso parziale di due licenze"}, ["a"], 0) == ""


def test_the_previews_count_in_the_choice_of_the_whole_result():
    # A small result: with the previews the line would cost more than the cut saves, so the whole result goes on.
    small = json.dumps({"data": [{"id": "a", "description": "Testo libero abbastanza lungo " * 6, "n0": "x"}]})
    tr = trimmer(400, seen=("data[].id", "data[].description", "data[].n0"))
    obs = tr.observe("t", small)
    assert MARK not in obs.text and json.loads(obs.text) == json.loads(small)


def test_the_engine_setting_overrides_the_config():
    # A policy learned later goes through the same adjustment (Engine.learn_policy): test_automatic_mode_with_previews.
    proj = Projection("prudent", "full", {"t": {"data[].id"}}, {"t": {"data[].id", "data[].description",
                                                                      *(f"data[].{k}" for k in NOISE)}})
    config = {"format": "tracetrim/config@1", "tools": {"t": {"trim": "auto", "expand": True}}, "text_preview": 0}
    eng = Engine(policy=Policy(config, proj), text_preview=30)
    assert TEXT_LABEL in eng.trimmer.observe("t", output([None, CREDIT, None])).text
    assert TEXT_LABEL not in Engine(policy=Policy(dict(config), proj)).trimmer.observe("t", output([None, CREDIT, None])).text
    with pytest.raises(ValueError):
        Engine(policy=Policy(config, proj), text_preview=-1)
    with pytest.raises(ValueError):
        Policy(dict(config, text_preview=-5), proj)


def test_automatic_mode_with_previews(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=2, text_preview=40)
    for i in range(2):                                  # learning: the answers use the ids only
        tt.output("t", {"q": i}, output([None, "Fattura emessa da gestionale senza note"]))
        tt.done(f"Record cbtxn_0 e cbtxn_1 trovati, run {i}")
    assert tt.trimming
    sent = tt.output("t", {"q": 9}, output([None, CREDIT]))
    assert MARK in sent and "14,78 EUR" in sent.split(MARK, 1)[1]
    assert AutoTrim(tmp_path / "other", min_runs=2).engine.text_preview == 0


def test_a_cut_never_splits_a_word_or_a_number():
    assert cut(CREDIT, 29) == "Accredito di cortesia di…"           # not «…di 14,7»
    assert cut(CREDIT, 200) == CREDIT
    assert cut("Supercalifragilistichespiralidoso e altro", 10) == "Supercalif…"   # no space to cut at: at the limit
    assert cut("Nota, con virgola finale", 6) == "Nota…"                  # no dangling comma


def test_texts_with_the_same_start_are_shown_once():
    texts = [f"Fattura emessa da gestionale, lotto numero {i}" for i in range(50)]
    line = trimmer(20).observe("t", output(texts)).text.split(MARK, 1)[1]
    assert line.count("Fattura emessa da…") == 1 and "(+" not in line


def test_a_large_payload_is_previewed_in_linear_time():
    import time
    payload = {"data": [{"id": i, "description": f"Descrizione numero {i} del movimento contabile"} for i in range(50_000)]}
    t0 = time.perf_counter()
    text = previews(payload, ["data[].description"], 60)
    assert time.perf_counter() - t0 < 2.0 and "(+49997)" in text


def test_a_config_value_must_be_a_number_of_characters():
    proj = Projection("prudent", "full", {"t": {"id"}}, {"t": {"id", "x"}})
    for bad in (True, -1, "40", 2.5):
        with pytest.raises(ValueError):
            Policy({"format": "tracetrim/config@1", "tools": {}, "text_preview": bad}, proj)
    assert Policy({"format": "tracetrim/config@1", "tools": {}, "text_preview": None}, proj).text_preview == 0


@pytest.mark.parametrize("argv, message", [
    (["--text-preview", "40"], "needs --auto WORKDIR or --config"),
    (["--auto", "w", "--text-preview", "-1"], "0 \\(off\\) or more"),
])
def test_the_proxy_refuses_a_preview_it_could_not_use(argv, message):
    from tracetrim.cli import main
    with pytest.raises(SystemExit, match=message):
        main(["proxy", "--upstream-cmd", "python -m nothing", *argv])


def test_the_proxy_log_counts_the_hidden_groups_not_the_previews(capsys):
    from tracetrim.proxy import Live
    sent = '{"a":1}' + MARK + 'call_id=c1 hidden: data[].x, data[].y' + TEXT_LABEL + 'data[].y "uno, due, tre, quattro"'
    Live().call("t", '{"a":1,"x":2,"y":"uno, due, tre, quattro"}', sent)
    assert "hidden: 2 groups" in capsys.readouterr().err


def test_langgraph_passes_the_option_to_automatic_mode(tmp_path):
    pytest.importorskip("langgraph")
    from tracetrim.langgraph import TraceTrim
    assert TraceTrim(tmp_path, text_preview=40).auto.engine.text_preview == 40
