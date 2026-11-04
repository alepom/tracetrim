"""Shared fixtures. Some tests replay recorded runs of the simulated cases:
those live in traces/ where they were recorded, and in dataset/traces/ once
the Zenodo dataset is unzipped at the repository root. Without either, the
tests that need them are skipped, not failed."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TRACES = next((p for p in (ROOT / "traces", ROOT / "dataset" / "traces") if (p / "C4").exists()), None)


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
