"""Spend caps of the paid experiments, kept out of the repository.

The author's decision (2026-11-12): what the tests cost is never published, so
the caps and the warning thresholds live in a git-ignored file next to this
one, `experiments/caps.local.json`:

  {"study": {"warn": .., "stop": ..}, "v04": {...}, "e3": {...}}

in USD, at the providers' list prices. Without the file every cap is 0: a paid
run is refused before it starts, while dry runs, estimates and analyses work.
$TRACETRIM_CAPS points to another file (the tests use caps of their own).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LOCAL = Path(os.environ.get("TRACETRIM_CAPS") or Path(__file__).resolve().parent / "caps.local.json")


def caps(name: str, key: str | None = None):
    """(warning, stop) for one campaign: "study" (the preprint), "v04" or "e3". With
    `key`, one other local amount of that campaign (e.g. "tuning", an allowance)."""
    c = (json.loads(LOCAL.read_text()).get(name) or {}) if LOCAL.exists() else {}
    if key is not None:
        return float(c.get(key, 0.0))
    return float(c.get("warn", 0.0)), float(c.get("stop", 0.0))
