"""`tracetrim compare ... --html FILE` (1.4): the numbers of tracetrim/compare@1, on one page, offline."""
import json
import re

import pytest

from tracetrim import compare, compare_html, i18n


@pytest.fixture
def result(traces):
    paths = sorted((traces / "C4").glob("A*.jsonl"))
    return compare.compute(paths, compare.recipe("protect_ids=all"), compare.recipe("protect_ids=record"))


def english(cell: str) -> str:
    m = re.search(r'<span lang="en">(.*?)</span>', cell)
    return re.sub(r"<[^>]+>", "", m.group(1) if m else cell)


def rows(page: str, section: str) -> list[list[str]]:
    body = page.split(f'id="{section}"', 1)[1].split("</section>", 1)[0]
    return [[english(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr)] for tr in re.findall(r"<tr>(.*?)</tr>", body)
            if "<td" in tr]


def test_the_numbers_are_those_of_the_json(result):
    page = compare_html.render(json.loads(json.dumps(result)))
    table = {r[0]: r[1:] for r in rows(page, "tools")}
    for tool, row in [*result["tools"].items(), ("Total", result["totals"])]:
        calls, ta, tb, delta, miss, cheaper = table[tool]
        assert calls == f"{row['calls']:,}" and ta == f"{row['a']['tokens']:,}" and tb == f"{row['b']['tokens']:,}"
        d = row["delta"]["tokens"]
        assert delta == ("+" if d > 0 else "−" if d < 0 else "") + f"{abs(d):,}"
        assert miss == f"{row['a']['miss_calls']}/{row['b']['miss_calls']}"
        assert cheaper == f"{row['a']['full_cheaper']}/{row['b']['full_cheaper']}"
    missed = [(t, f, n["a"], n["b"]) for t, r in result["tools"].items() for f, n in r["missed_fields"].items()]
    assert [(r[0], r[1], int(r[2]), int(r[3])) for r in rows(page, "missed")] == missed
    assert [(r[1], int(r[2]), r[3]) for r in rows(page, "whole")] == \
           [(w["run"], w["call"], w["tool"]) for w in result["full_cheaper_calls"]]
    assert [(r[0], int(r[1]), r[2], r[3], r[4]) for r in rows(page, "divergences")] == \
           [(d["run"], d["call"], d["tool"], d["field"], d["hidden_by"].upper()) for d in result["divergences"]]
    assert f'<div class="v">{_n(result["observed_expand"])}</div>' in page


def _n(x):
    return "".join(f'<span lang="{lang}">{i18n.num(x, lang)}</span>' for lang in i18n.LANGS)


def test_nothing_is_loaded_from_the_network_and_tables_scroll_in_their_card(result):
    page = compare_html.render(result)
    assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/|<link\b|\bsrc=|@import|url\(|fetch\(|XMLHttpRequest", page, re.I)
    tables = page.count("<table")
    assert tables >= 2 and page.count('<div class="scroll"><table') == tables
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in page


def test_every_label_in_the_five_languages(result):
    page = compare_html.render(result)
    for key, texts in compare_html._LABELS.items():
        assert len(texts) == len(i18n.LANGS) and all(t.strip() for t in texts), key
    for lang in i18n.LANGS:
        assert f'<span lang="{lang}">{compare_html.label("title", lang)}</span>' in page


def test_the_cli_writes_the_page_and_keeps_its_output(traces, tmp_path, capsys):
    from tracetrim.cli import main
    page = tmp_path / "compare.html"
    paths = [str(p) for p in sorted((traces / "C4").glob("A*.jsonl"))]
    main(["compare", *paths, "--a", "protect_ids=all", "--b", "protect_ids=record", "--json", "--html", str(page)])
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "tracetrim/compare@1"
    assert page.read_text(encoding="utf-8") == compare_html.render(data)


def test_a_document_of_another_format_is_refused():
    with pytest.raises(ValueError):
        compare_html.render({"format": "tracetrim/report@1"})
