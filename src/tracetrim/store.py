"""Files in the work directory are replaced whole, never written in place.

A process can die at any moment (a proxy is killed with its client). Writing
to a temporary file and renaming it means a reader finds either the previous
content or the new one, never half of a file.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
import tempfile
from pathlib import Path


# During an isolated closing, collect the exact replacement contents before
# publishing any of them. The private journal is replayed by replacement,
# so a second interruption cannot add the same counts twice.
_writes: ContextVar[dict | None] = ContextVar("tracetrim_close_writes", default=None)
JOURNAL = ".tracetrim-pending-close.json"


@contextmanager
def collect_close():
    files = {}
    token = _writes.set(files)
    try:
        yield files
    finally:
        _writes.reset(token)


def recover_close(directory: Path) -> bool:
    journal = directory / JOURNAL
    if not journal.exists():
        return False
    files = json.loads(journal.read_text(encoding="utf-8"))
    # Accept only a single run and the known files owned by this coordinator.
    number = json.loads(files["state.json"])["runs"]
    if type(number) is not int or number < 1:
        raise ValueError("invalid run number in close journal")
    run = f"run-{number:04d}"
    traces = {f"traces/{run}.jsonl", f"traces/{run}-noanswer.jsonl"}
    allowed = traces | {f"evidence/{run}.jsonl", "state.json", "savings.json", "evidence.json"}
    if len(traces.intersection(files)) != 1 or files.keys() - allowed:
        raise ValueError("invalid files in close journal")
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(exist_ok=True)
        write_atomic(path, content)
    journal.unlink()
    return True


def write_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    if (files := _writes.get()) is not None:
        files[path] = text
        return
    # A temporary name of its own for every write: two threads writing the same
    # file (a closing and a report regenerated at shutdown) must not share it.
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
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
