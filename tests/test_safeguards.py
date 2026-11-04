"""The two safeguards of 0.2, each pinned to the failure that motivated it."""
import json

from tracetrim import Event, Trace, analyse, learn
from tracetrim.projection import identifying
from tracetrim.usage import transformed_keys, words


def one_call(payload, answer, tool="t"):
    return Trace("x", [Event("prompt", text="go"),
                       Event("call", tool=tool, text="{}", output=json.dumps(payload)),
                       Event("message", text=answer)])


def test_timestamp_written_as_a_date_is_seen_with_normalize():
    # C4: the period end arrived as a Unix timestamp, the agent wrote "20 novembre 2026".
    t = one_call({"items": [{"current_period_end": 1795168800, "noise": "x" * 10}]},
                 "Potrai usare il servizio fino al 20 novembre 2026.")
    [plain] = analyse(t, normalize=False)
    [norm] = analyse(t, normalize=True)
    assert "items[].current_period_end" not in plain.used
    assert "items[].current_period_end" in norm.used


def test_amount_in_cents_is_seen_as_euros_only_for_money_fields():
    t = one_call({"amount": 2900, "quantity_total_count": 2900}, "Ti abbiamo addebitato 29,00 € due volte.")
    [u] = analyse(t, normalize=True)
    assert "amount" in u.used
    assert ("29,00",) in transformed_keys(2900, "amount")
    assert ("29,00",) not in transformed_keys(2900, "quantity")


def test_url_written_as_its_short_id_is_seen():
    # C3: "https://openalex.org/W4389519226" was written as "W4389519226".
    t = one_call({"results": [{"id": "https://openalex.org/W4389519226"}]}, '{"openalex_id": "W4389519226"}')
    [u] = analyse(t, normalize=True)
    assert "results[].id" in u.used


def test_identifying_fields_are_kept_even_when_never_seen_used():
    # C3 invented ids, C5 lost an issue title: both were "unused" fields.
    t = one_call({"items": [{"id": "abc123", "title": "Staging deploy times out", "body": "long text here",
                             "html_url": "https://x.test/1", "followers_url": "https://x.test/f"}]}, "nothing")
    [u] = analyse(t)
    p = learn([[u]], "aggressive", protect_ids=True)
    kept = p.keep["t"]
    assert {"items[].id", "items[].title", "items[].html_url"} <= kept
    assert "items[].body" not in kept and "items[].followers_url" not in kept
    assert not learn([[u]], "aggressive", protect_ids=False).keep["t"] & {"items[].id", "items[].title"}


def test_identifying_pattern():
    assert all(identifying(p) for p in ("id", "data[].customer_id", "items[].number", "results[].doi", "user.login_id"))
    assert not any(identifying(p) for p in ("body", "items[].followers_url", "data[].amount", "hidden"))


def test_month_names_tokenise_as_the_answer_does():
    assert ("20", "novembre") in transformed_keys(1795168800) and words("20 novembre") == ["20", "novembre"]
