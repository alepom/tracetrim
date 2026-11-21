"""Protocol v3c without spend: arms, schedule and the preregistered estimator."""
from collections import Counter

from experiments.v3c import analysis, run


def test_arms_differ_only_in_the_skill_text():
    assert run.ARMS["P1"]["policy"] == run.ARMS["P2"]["policy"] == "P"
    assert (run.ARMS["P1"]["skill_version"], run.ARMS["P2"]["skill_version"]) == ("1", "2")


def test_schedule_blocks_both_arms_and_is_reproducible():
    s = run.schedule()
    assert s == run.schedule() and len(s) == 92 and len(set(s)) == 92
    pairs = [s[i:i + 2] for i in range(0, 92, 2)]
    assert all(a.rsplit("/", 1)[0] == b.rsplit("/", 1)[0] and {a[-2:], b[-2:]} == {"P1", "P2"} for a, b in pairs)
    assert Counter(a[-2:] for a, _ in pairs) == {"P1": 23, "P2": 23}
    tasks = Counter(x.split("/")[0] for x in s)
    assert all(tasks[t] == 6 for t in run.HARD) and all(tasks[t] == 2 for t in run.ORDINARY)


def test_estimator_and_verdicts():
    values = {}
    for i in range(1, 13):
        for r in (1, 2, 3):
            values[f"C4b-{i:02}/{r}/P1"] = 0.8
            values[f"C4b-{i:02}/{r}/P2"] = 0.8 + (0.1 if i % 2 else 0.05)
    c = analysis.contrast(analysis.paired(values))
    assert abs(c["point"] - 0.075) < 1e-9 and c["verdict"] == "improved" and c["tasks"] == 12
    flat = {k: 0.8 for k in values}
    assert analysis.contrast(analysis.paired(flat))["verdict"] == "no improvement shown"
    worse = {k: (0.7 if k.endswith("P2") else 0.8) for k in values}
    assert analysis.contrast(analysis.paired(worse))["verdict"] == "worse"
