"""The preregistered estimator of protocol v3 on invented scores."""
import random

import pytest

from experiments.v3 import analysis as an
from experiments.v3.coordinator import schedule


def scores(delta_p, delta_i=0.0, noise=0.0, seed=1):
    rng = random.Random(seed)
    out = {}
    for slot in schedule():
        task, rep, arm = slot.split("/")
        base = 2 + (int(task[-2:]) % 3) * 0.5
        out[slot] = base + {"R": 0.0, "P": delta_p, "I": delta_i}[arm] + rng.uniform(-noise, noise)
    return out


def test_no_difference_is_preserved():
    r = an.contrast(scores(0.0), "P")
    assert r["point"]["combined"] == pytest.approx(0.0) and r["ci95"]["combined"] == pytest.approx([0.0, 0.0])
    assert r["verdict_0.5"] == "preserved" and r["tasks"] == {"C4": 10, "C5": 10}


def test_large_loss_and_boundary():
    assert an.contrast(scores(-1.0), "P")["verdict_0.5"] == "material loss"
    r = an.contrast(scores(-0.4, noise=0.6), "P")
    assert r["ci95"]["combined"][0] < r["point"]["combined"] < r["ci95"]["combined"][1]
    assert an.verdict([-0.6, -0.1], -0.5) == "inconclusive"
    assert an.verdict([-0.4, 0.1], -0.5) == "preserved" and an.verdict([-0.4, 0.1], -0.25) == "inconclusive"


def test_cases_weigh_equally_and_secondary_uses_same_estimator():
    s = scores(0.0, delta_i=-0.2)
    for slot in s:
        if slot.startswith("C4") and slot.endswith("/P"):
            s[slot] -= 1.0
    r = an.contrast(s, "P")
    assert r["point"]["by_case"] == pytest.approx({"C4": -1.0, "C5": 0.0})
    assert r["point"]["combined"] == pytest.approx(-0.5)
    assert an.contrast(s, "I")["point"]["combined"] == pytest.approx(-0.2)


def test_bootstrap_is_reproducible_and_unpaired_blocks_left_out():
    s = scores(-0.2, noise=1.0)
    assert an.bootstrap(an.units(s, "P")) == an.bootstrap(an.units(s, "P"))
    del s["C4-01/1/R"]
    assert an.units(s, "P")["C4"]["C4-01"] == pytest.approx(s["C4-01/2/P"] - s["C4-01/2/R"])
    s = {k: v for k, v in s.items() if not k.startswith("C5")}
    assert an.contrast(s, "P")["verdict_0.5"] == "unavailable"
