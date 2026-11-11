"""protocol-v04.md states numbers; these tests keep them equal to what the scripts compute.

They need the original study's traces and meta files (see conftest: skipped without them)."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Compared without regard to line breaks: the protocol is wrapped prose.
PROTOCOL = " ".join((ROOT / "protocol-v04.md").read_text().split())


@pytest.fixture
def study(traces, monkeypatch):
    from experiments import budget, paths, v04_preconditions
    for module in (budget, paths, v04_preconditions):
        monkeypatch.setattr(module, "STUDY_TRACES", traces)
    return budget, v04_preconditions


def test_the_estimate_in_the_protocol_is_the_one_the_budget_script_computes(study):
    budget, _ = study
    r = budget.v04()
    t = r["totals"]
    e1 = {c: sum(x["usd"] for x in r["e1"] if x["case"] == c) for c in ("C1", "C2", "C4")}
    e2 = {c: sum(x["usd"] for x in r["e2"] if x["case"] == c) for c in ("C1", "C4")}
    for row in (f"| 20 | {e1['C1']:.2f} |", f"| 10 | {e1['C2']:.2f} |", f"| 20 | {e1['C4']:.2f} |",
                f"| 40 | {e2['C1']:.2f} |", f"| 40 | {e2['C4']:.2f} |",
                f"| **{t['runs']}** | **{t['all']:.2f}** |",
                f"maximum seen for its case: {t['all_if_every_run_costs_the_maximum_seen']:.2f} USD"):
        assert row in PROTOCOL, row
    assert [x["arm"] for x in r["e1"] if x["case"] == "C2"] == ["R", "S"]          # C2 has no trimmed arm
    m = r["measured_per_run"]
    assert (f"C1 {m['C1']['cost']:.3f} (maximum {m['C1']['cost_max']:.3f}), C2 {m['C2']['cost']:.3f} "
            f"({m['C2']['cost_max']:.3f}), C4 {m['C4']['cost']:.3f} ({m['C4']['cost_max']:.3f})") in PROTOCOL
    d = r["dataforseo_calls"]
    assert f"C1 {d['C1']['calls']:,} (60 runs × {m['C1']['calls']:g}; {d['C1']['calls_max']:,} at the" in PROTOCOL
    assert f"C2 {d['C2']['calls']:g} (10 runs × {m['C2']['calls']:g}; {d['C2']['calls_max']:g})" in PROTOCOL
    # Above the warning, below the cap, and still below it if every run cost the maximum seen.
    assert r["design"]["warn_usd"] < t["all"] < t["all_if_every_run_costs_the_maximum_seen"] < r["design"]["cap_usd"] == 50
    assert f"After the first {r['design']['dataforseo_checkpoint_runs']} runs that call DataForSEO" in PROTOCOL
    assert f"rate {r['design']['e2']['sample_rate']}, cost share {r['design']['e2']['sample_max_share']}" in PROTOCOL


def test_the_preconditions_in_the_protocol_hold_on_the_recorded_traces(study):
    _, pre = study
    cases = {c: pre.case(c) for c in pre.CASES}
    saved = json.loads((ROOT / "results" / "v04" / "preconditions.json").read_text())["cases"]
    assert cases == saved                                              # the committed file is what the script writes
    for c, saving, trimmed in (("C1", "58%", 4), ("C2", "9%", 0), ("C4", "57%", 4), ("C4v", "57%", 4)):
        assert f"| {c} | {saving} |" in PROTOCOL or c == "C4v"
        assert f"{cases[c]['held_out_saving']:.0%}" == saving and len(cases[c]["tools_trimmed"]) == trimmed
    assert cases["C4v"]["tools_trimmed"] == cases["C4"]["tools_trimmed"]
    # Every field a variant needs is removed today, has a value in every recorded call, and was never used.
    for c in ("C1", "C4v"):
        for tool, n in cases[c]["fields_needed_by_the_variant"].items():
            assert tool in cases[c]["tools_trimmed"] and n["calls"] == 16
            for f, v in n["fields"].items():
                assert (v["removed_by_the_projection"], v["with_a_value_in_calls"], v["used_in_runs"]) == (True, 16, 0), f
                assert f"`{f}`" in PROTOCOL
    # On the copy of the world the reworded field cannot be guessed: two values, nearly even.
    method = cases["C4v"]["fields_needed_by_the_variant"]["stripe_list_invoices"]["fields"]["data[].collection_method"]
    assert method["distinct_values"] == 2 and method["share_of_the_commonest_value"] <= 0.55
    assert f"the commoner in {method['share_of_the_commonest_value']:.0%} of the invoices read" in PROTOCOL


def test_the_copy_of_the_simulated_world_changes_two_invoice_fields_and_nothing_else():
    from experiments.simulated import v04
    from experiments.simulated.worlds import stripe_world
    original, copy = stripe_world(), v04.world()
    assert json.dumps(stripe_world(), sort_keys=True) == json.dumps(original, sort_keys=True)     # untouched
    for kind in original:
        for a, b in zip(original[kind], copy[kind]):
            assert {k for k in a if a[k] != b[k]} <= ({"collection_method", "hosted_invoice_url"} if kind == "invoices" else set())
    assert all(i["hosted_invoice_url"] for i in copy["invoices"])
    for cus in copy["customers"]:
        methods = [i["collection_method"] for i in sorted((i for i in copy["invoices"] if i["customer"] == cus["id"]),
                                                          key=lambda i: -i["created"])]
        assert all(a != b for a, b in zip(methods, methods[1:]))                      # alternating, newest first
    listing = json.dumps({"object": "list", "data": original["invoices"][:3]}, indent=2, ensure_ascii=False)
    assert json.loads(v04.rewrite("stripe_list_invoices", listing))["data"] == copy["invoices"][:3]
    assert v04.rewrite("stripe_list_charges", listing) == listing and v04.rewrite("stripe_list_invoices", "Error: x") == "Error: x"


def test_the_variant_prompts_add_one_requirement_to_the_original_ones():
    prompts = ROOT / "experiments" / "prompts"
    for case in ("c1", "c4"):
        original, variant = ((prompts / f"{case}{v}-user.md").read_text() for v in ("", "v"))
        assert variant.startswith(original.rstrip()) and len(variant) > len(original)
        assert len(variant[len(original.rstrip()):].strip().splitlines()) == 1
        assert f"{case}v-user.md" in PROTOCOL
    assert not re.search(r"\d", (prompts / "c4v-user.md").read_text().splitlines()[-1])
