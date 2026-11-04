"""Shared fixtures. Some tests replay recorded runs of the simulated cases:
those live in traces/ where they were recorded, and in dataset/traces/ once
the Zenodo dataset is unzipped at the repository root. Without either, the
tests that need them are skipped, not failed."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TRACES = next((p for p in (ROOT / "traces", ROOT / "dataset" / "traces") if (p / "C4").exists()), None)


@pytest.fixture
def traces():
    if TRACES is None:
        pytest.skip("recorded traces not available: unzip the Zenodo dataset at the repository root")
    return TRACES
