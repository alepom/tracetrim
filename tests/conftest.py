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



# ---- No test may touch the user's real files (tracetrim init rewrites host configurations).
REAL_HOME = Path.home()
_HOME_VARS = ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME")


def _real_places():
    """What `tracetrim init --write` would leave under the real home: a backup next
    to a host's file, and a servers file in ~/.tracetrim. Names and times only, never
    content. (Not the hosts' files themselves: a running host rewrites them on
    its own, ~/.claude.json above all, and the check would fail for no reason.)"""
    from tracetrim.init import BACKUP_MARK, HOSTS
    found = set()
    for h in HOSTS.values():
        if h.user:
            f = h.user_path()
            found |= {str(b) for b in f.parent.glob(f.name + BACKUP_MARK + "*")} if f.parent.is_dir() else set()
    # servers.json files and their times: a proxy already running from ~/.tracetrim
    # writes traces and reports there, never its servers file.
    work = REAL_HOME / ".tracetrim"
    found |= {f"{q}@{q.stat().st_mtime_ns}" for q in work.rglob("servers.json")} if work.is_dir() else set()
    return found


@pytest.fixture(scope="session", autouse=True)
def real_home_untouched():
    before = _real_places()
    yield
    assert _real_places() == before, "a test changed a file under the real home"


@pytest.fixture(autouse=True)
def temp_home(tmp_path, monkeypatch):
    """Every test runs with a home of its own."""
    home = tmp_path / "home"
    home.mkdir()
    for var in _HOME_VARS:
        monkeypatch.setenv(var, str(home / ".config") if var == "XDG_CONFIG_HOME" else
                           str(home / "AppData" / "Roaming") if var == "APPDATA" else str(home))
    return home


@pytest.fixture(autouse=True)
def no_init_check(request, monkeypatch):
    """`tracetrim init --write` starts the entry it wrote, once, to see that it answers.
    Tests about writing files do not need it; those about the check are marked `real_check`."""
    if request.node.get_closest_marker("real_check"):
        return
    from tracetrim import init
    monkeypatch.setattr(init, "check", lambda p, timeout=None: (True, ""))
