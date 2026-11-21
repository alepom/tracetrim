"""Files in the work directory are replaced whole, never written in place.

A process can die at any moment (a proxy is killed with its client). Writing
to a temporary file and renaming it means a reader finds either the previous
content or the new one, never half of a file.
"""
from __future__ import annotations

import json
import os
import threading
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
    # A closing: one trace and its files. A relearning's commit (1.4): the state and the evidence, no trace.
    closing = len(traces.intersection(files)) == 1
    commit = not traces.intersection(files) and files.keys() <= {"state.json", "evidence.json"}
    if not (closing or commit) or files.keys() - allowed:
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


# -- between processes (1.4) ---------------------------------------------------------------------------------------
# A work directory has one owner, the process whose Learner runs on it (a proxy, an AutoTrim loop); `tracetrim
# promotions` may edit its evidence from another process meanwhile. Two locks, each on a file of its own:
#   - WRITE_LOCK: exclusive, held for every read-modify-write of evidence.json and for the journal of a closing, by
#     every process. Reentrant within a process (an RLock in front of the file lock).
#   - OPEN_LOCK: shared, held by every Learner for its life; `tracetrim promotions` tries it exclusively, without
#     waiting, to learn whether another process owns the directory. A POSIX record lock: it belongs to the process,
#     so Learners of one process never see each other.
# Where neither fcntl nor msvcrt exists the locks are not taken, as up to 1.3.
WRITE_LOCK = ".tracetrim-write.lock"
OPEN_LOCK = ".tracetrim-open.lock"
_guard = threading.Lock()
_writers: dict[str, dict] = {}


def _lock(fh) -> None:
    try:
        import fcntl
    except ImportError:
        try:
            import msvcrt
        except ImportError:
            return
        fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)    # gives up after about 10 s: try again
                return
            except OSError:
                continue
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)


def _unlock(fh) -> None:
    try:
        import fcntl
    except ImportError:
        try:
            import msvcrt
        except ImportError:
            return
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def write_lock(directory: str | Path):
    """Exclusive between processes and between threads, reentrant within a thread: see WRITE_LOCK."""
    directory = Path(directory)
    key = str(directory.resolve())
    with _guard:
        entry = _writers.setdefault(key, {"rlock": threading.RLock(), "depth": 0, "file": None})
    with entry["rlock"]:
        if entry["depth"] == 0:
            fh = open(directory / WRITE_LOCK, "a+b")
            try:
                _lock(fh)
            except BaseException:
                fh.close()
                raise
            entry["file"] = fh
        entry["depth"] += 1
        try:
            yield
        finally:
            entry["depth"] -= 1
            if entry["depth"] == 0:
                fh, entry["file"] = entry["file"], None
                try:
                    _unlock(fh)
                finally:
                    fh.close()


# Directories this context already holds exclusively (`alone`): a Learner made inside does not take the shared lock,
# which for a POSIX record lock of the same process would replace the exclusive one.
_alone: ContextVar[frozenset] = ContextVar("tracetrim_alone", default=frozenset())


_held: dict[str, dict] = {}                # directory -> {"file", "count"}: the OPEN_LOCK, once per process
_held_guard = threading.Lock()


def hold_open(directory: str | Path):
    """For the life of a Learner: the shared OPEN_LOCK, so that another process knows the directory is in use. Waits
    while `tracetrim promotions` relearns it. Returns the function that gives it back (None without fcntl).

    Taken once per process and directory, and counted: a POSIX record lock belongs to the process, and closing any
    descriptor of the file would give it up for every Learner of the process (REVISIONE-14b-quater)."""
    try:
        import fcntl
    except ImportError:
        return None
    key = str(Path(directory).resolve())
    if key in _alone.get():
        return None
    with _held_guard:
        entry = _held.get(key)
        if entry is None:
            fh = open(Path(directory) / OPEN_LOCK, "a+b")
            try:
                fcntl.lockf(fh.fileno(), fcntl.LOCK_SH)
            except OSError:
                fh.close()
                return None
            entry = _held[key] = {"file": fh, "count": 0}
        entry["count"] += 1

    def give_back():
        with _held_guard:
            entry = _held.get(key)
            if entry is None:
                return
            entry["count"] -= 1
            if entry["count"] == 0:
                del _held[key]
                entry["file"].close()        # the process's lock goes with its last holder
    return give_back


@contextmanager
def alone(directory: str | Path):
    """True when no other process owns the directory (and none can start owning it until the block ends); False
    when one does. True where it cannot be known (no fcntl), as up to 1.3."""
    try:
        import fcntl
    except ImportError:
        yield True
        return
    if str(Path(directory).resolve()) in _held:
        # This process owns it (a Learner of the same process: tests, or an embedding program): no other process
        # can, and touching the lock here would give up the process's own.
        yield True
        return
    fh = open(Path(directory) / OPEN_LOCK, "a+b")
    try:
        try:
            fcntl.lockf(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        token = _alone.set(_alone.get() | {str(Path(directory).resolve())})
        try:
            yield True
        finally:
            _alone.reset(token)
            fcntl.lockf(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


# Left by `tracetrim promotions` when it changed the evidence of a directory a proxy owns: the next relearning, in
# that proxy or at the next start, applies the change and removes it.
CHANGED = ".tracetrim-evidence-changed"


def stamp(path: str | Path):
    """What changes when a file is replaced (atomically: another inode) or written: None when missing."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_ino, st.st_mtime_ns, st.st_size
