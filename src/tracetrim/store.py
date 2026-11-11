"""Files in the work directory are replaced whole, never written in place.

A process can die at any moment (a proxy is killed with its client). Writing
to a temporary file and renaming it means a reader finds either the previous
content or the new one, never half of a file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_json(path: str | Path, data) -> None:
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def clean(directory: str | Path) -> None:
    """Remove what an interrupted write left behind."""
    for tmp in Path(directory).glob(".*.tmp"):
        tmp.unlink(missing_ok=True)
