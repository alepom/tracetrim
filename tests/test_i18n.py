"""The report in five languages (tracetrim.i18n, since 1.1): nothing missing, nothing left in English by mistake."""
import json
import re
import sys
from pathlib import Path

import pytest

from tracetrim import i18n
from tracetrim import report as rp
from tracetrim.auto import AutoTrim

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402

RULES = sorted(set(re.findall(r'add\("([a-z-]+)"', Path(rp.__file__).read_text())))
SAMPLE = {"traces": 3, "min": 5, "tokens": 43543, "share": 0.17, "duplicates": 2, "tool": "list_invoices",
          "saving": 0.57, "calls": 26, "misses": 3, "trimmed": 29, "fields": ["data[].status", "data[].footer"],
          "errors": 1, "runs": 5, "several": 0.05, "earlier": 0.4, "bad": 1, "checked": 13, "values": 2,
          "tools": ["list_invoices"], "tokens_per_turn": 5200, "never": 12, "offered": 20, "shown": 10,
          "never_tokens": 3100, "never_share": 0.6}


@pytest.mark.parametrize("lang", i18n.EXTRA)
def test_every_label_is_translated(lang):
    missing = [k for k in rp.L if k != "level" and not i18n.label(k, lang)]
    missing += [f"level.{s}" for s in rp.L["level"] if not i18n.label("level", lang, s)]
    assert not missing, missing


@pytest.mark.parametrize("lang", i18n.EXTRA)
def test_no_label_is_left_over(lang):
    extra = set(i18n.LABELS[lang]) - set(rp.L)
    assert not extra, extra


@pytest.mark.parametrize("lang", i18n.EXTRA)
def test_every_action_is_translated(lang):
    assert set(i18n.ACTIONS[lang]) == set(rp.ACTION_TEXT)


@pytest.mark.parametrize("lang", i18n.EXTRA)
@pytest.mark.parametrize("rule", RULES)
def test_every_rule_has_a_text(rule, lang):
    text = i18n.suggestion(rule, lang, SAMPLE)
    assert text and "{" not in text and "}" not in text
    assert "list_invoices" in text or rule in ("few-traces", "escapes", "duplicates", "strategy", "definitions")


def test_numbers_as_each_language_writes_them():
    assert [i18n.num(1234567, lg) for lg in i18n.LANGS] == \
        ["1,234,567", "1.234.567", "1.234.567", "1 234 567", "1,234,567"]
    assert i18n.pct(0.48, "fr") == "48 %" and i18n.pct(0.48, "zh") == "48%"
    assert i18n.calls(1, "es") == "1 llamada" and i18n.calls(2, "fr") == "2 appels"


def test_the_page_carries_every_language(tmp_path):
    tt = AutoTrim(tmp_path, min_runs=3)
    for i in range(4):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    page = (tmp_path / "report.html").read_text()
    for lang, name in i18n.NAMES.items():
        assert f'<option value="{lang}">{name}</option>' in page
        assert page.count(f'<span lang="{lang}">') == page.count('<span lang="en">')
    assert "工具输出遥测" in page and "Télémétrie des sorties d&#x27;outils" in page and "Sugerencias" in page
    assert page.count("data-tip-zh=") == page.count("data-tip-en=") > 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert all(s.get(lang) for s in report["suggestions"] for lang in i18n.LANGS)
