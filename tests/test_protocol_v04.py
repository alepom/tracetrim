"""protocol-v04.md states numbers; these tests keep them equal to what the scripts compute.

They need the original study's traces and meta files (see conftest: skipped without them)."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PROTOCOL = (ROOT / "protocol-v04.md").read_text()


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
    e2 = {c: sum(x["usd"] for x in r["e2"] if x["case"] == c) for c in ("C1", "C4")}
    for row in (f"| 60 | {t['e1']:.2f} |", f"| 20 | {e2['C1']:.2f} |", f"| 40 | {e2['C4']:.2f} |",
                f"| **{t['runs']}** | **{t['all']:.2f}** |",
                f"maximum seen for its case: {t['all_if_every_run_costs_the_maximum_seen']:.2f} USD"):
        assert row in PROTOCOL, row
    m = r["measured_per_run"]
    assert (f"C1 {m['C1']['cost']:.3f} (maximum\n{m['C1']['cost_max']:.3f}), C2 {m['C2']['cost']:.3f} "
            f"({m['C2']['cost_max']:.3f}), C4 {m['C4']['cost']:.3f} ({m['C4']['cost_max']:.3f})") in PROTOCOL
    d = r["dataforseo_calls"]
    assert f"C1 {d['C1']['calls']:g} (40 runs × {m['C1']['calls']:g}; {d['C1']['calls_max']:,} at the" in PROTOCOL
    assert f"C2 {d['C2']['calls']:g} (20 runs × {m['C2']['calls']:g}; {d['C2']['calls_max']:g})" in PROTOCOL
    assert t["all_if_every_run_costs_the_maximum_seen"] < r["design"]["warn_usd"] < r["design"]["cap_usd"] == 50
    assert f"rate {r['design']['e2']['sample_rate']}, cost share {r['design']['e2']['sample_max_share']}" in PROTOCOL


def test_the_preconditions_in_the_protocol_hold_on_the_recorded_traces(study):
    _, pre = study
    cases = {c: pre.case(c) for c in pre.CASES}
    saved = json.loads((ROOT / "results" / "v04" / "preconditions.json").read_text())["cases"]
    assert cases == saved                                              # the committed file is what the script writes
    for c, saving, trimmed in (("C1", "58%", 4), ("C2", "9%", 0), ("C4", "57%", 4)):
        assert f"| {c} | {saving} |" in PROTOCOL
        assert f"{cases[c]['held_out_saving']:.0%}" == saving and len(cases[c]["tools_trimmed"]) == trimmed
    # Every field a variant needs is removed today, present in every recorded call, and was never used.
    for c in ("C1", "C4"):
        for tool, n in cases[c]["fields_needed_by_the_variant"].items():
            assert tool in cases[c]["tools_trimmed"] and n["calls"] == 16
            for f, v in n["fields"].items():
                assert v == {"removed_by_the_projection": True, "present_in_calls": 16, "used_in_runs": 0}, f
                assert f"`{f}`" in PROTOCOL


def test_the_variant_prompts_add_one_requirement_to_the_original_ones():
    prompts = ROOT / "experiments" / "prompts"
    for case in ("c1", "c4"):
        original, variant = ((prompts / f"{case}{v}-user.md").read_text() for v in ("", "v"))
        assert variant.startswith(original.rstrip()) and len(variant) > len(original)
        assert len(variant[len(original.rstrip()):].strip().splitlines()) == 1
        assert f"{case}v-user.md" in PROTOCOL
    assert not re.search(r"\d", (prompts / "c4v-user.md").read_text().splitlines()[-1])
