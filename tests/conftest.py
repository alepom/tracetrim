"""Shared fixtures. Some tests replay recorded runs of the simulated cases:
those live in traces/ where they were recorded, and in dataset/traces/ once
the Zenodo dataset is unzipped at the repository root; $TRACETRIM_TRACES
points to them when they are elsewhere (a git worktree). Without any, the
tests that need them are skipped, not failed."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# The spend caps are local and never published (experiments/caps.py): the tests of the
# stop rules run with fixed caps of their own, whatever the author's local file says.
os.environ["TRACETRIM_CAPS"] = str(ROOT / "tests" / "fixtures" / "caps.json")
_PLACES = [Path(p) for p in [os.environ.get("TRACETRIM_TRACES")] if p] + [ROOT / "traces", ROOT / "dataset" / "traces"]
# A place counts when it holds the recorded runs of the original study (Phase A): a
# checkout's own traces/ can exist with only the runs of later measures in it.
TRACES = next((p for p in _PLACES if (p / "C4" / "A1.jsonl").exists()), None)


@pytest.fixture
def specs():
    """The simulated servers need their pinned OpenAPI descriptions: downloaded
    on first use; without network and without a copy, the test is skipped."""
    import sys
    sys.path.insert(0, str(ROOT))
    from experiments.simulated.synth import PINNED, spec_path
    try:
        for name in PINNED:
            spec_path(name)
    except Exception as e:                     # no network, proxy, ...
        pytest.skip(f"OpenAPI descriptions not available: {e}")


@pytest.fixture
def traces():
    if TRACES is None:
        pytest.skip("recorded traces not available: unzip the Zenodo dataset at the repository root")
    return TRACES

