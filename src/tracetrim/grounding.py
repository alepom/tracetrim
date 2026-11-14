"""Values in an answer that no tool output of its run contains (since 1.1).

tracetrim keeps the original of every tool output and receives the agent's answer when a run is closed, so it
can check, deterministically and at no cost, whether the values the answer cites came from somewhere. It looks
at the values a reader could attribute to a source:

  numbers of three digits or more, and codes containing them (ids, invoice numbers, volumes, amounts);
  not the years (the agent writes them itself: "a plan for 2027"), which means no 4-digit number from 1900 to
  2099 is checked, whatever it stands for; not zeros (`0,00`).

Each is searched in every tool output of the run, as the tool returned it, under the same forms usage
detection recognises (amounts in minor units, dates from timestamps, grouped numbers), and in the arguments
the agent wrote. A number is also supported when, written as a percentage, it is a value of the outputs times
100 (`12.5%` for 0.125), or when it is a rounding of one (`1.600` or `1600` for 1612: at most two significant digits, within 5%). A value
found nowhere is reported as *unsupported*.

What it is for: the worst failure the study found was an agent writing plausible identifiers it had not been
given, once a projection had removed them. On the study's recorded runs this check flags the 22 identifiers
made up that way (Study 1, OpenAlex) and none in the arms without trimming (`docs/measures-v1.md`).

What it does not see:
- a true value attributed to the wrong item (the volume of one keyword given to another): both exist;
- a value the agent computed (a sum, a difference): it is reported, and may be right;
- a value given only in the user's request, which tracetrim does not see: it is reported too.
A value also found unsupported in a run where nothing was filtered is not counted against trimming (see
`summarise`). So it is a signal to look at, never an action: the report shows it as a MANUAL suggestion.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from .engine import MARK
from .traces import Trace
from .usage import _WORD, keys_of, leaves, parse_output, transformed_keys, words

_YEAR = re.compile(r"(19|20)\d\d")
MAX_LISTED = 10          # values listed per run in the report


def attributable(word: str) -> bool:
    """A value a reader could attribute to a source: three digits or more, not all zeros, not a year."""
    digits = [ch for ch in word if ch.isdigit()]
    if len(digits) < 3 or all(ch == "0" for ch in digits):
        return False
    return not _YEAR.fullmatch(word)


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A number written as a percentage: only then is it compared with the outputs' values times 100. Without the
# sign, `600` would match any 6 of the outputs (it did, on C5, during the review).
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)*)\s?(?:%|per ?cento|percent)", re.I)
TOLERANCE = 0.05          # a rounding: within 5% of a value of the outputs


def _body(text: str) -> str:
    """A filtered result is the JSON followed by its `[trimmed]` line (engine.MARK): split them, or it does not
    parse, and amounts and dates would be matched only as plain words."""
    return text.split(MARK, 1)[0]


def support(texts) -> tuple[set[str], list[float]]:
    """Every word under which a value of these outputs or arguments can reappear, and their numbers."""
    out: set[str] = set()
    numbers: list[float] = []
    for text in texts:
        if text is None:
            continue
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        text = _body(text)
        p = parse_output(text)
        if isinstance(p, (dict, list)):
            for path, v in leaves(p):
                for k in keys_of(v, unsigned=True) + transformed_keys(v, path):
                    out.update(k)
                if isinstance(v, str):
                    out.update(words(v))
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    numbers.append(float(v))
        else:
            out.update(words(text))
    return out, numbers


def readings(word: str) -> list[tuple[float, int]]:
    """The numbers a word can stand for, each with the decimals it was written with: `1.600` is 1600 (Italian
    grouping) or 1.6 (one decimal), `29,00` is 29.00 or 2900."""
    if not _NUMBER.fullmatch(word):
        return []
    found = []
    for dec, group in ((".", ","), (",", ".")):
        t = word.replace(group, "")
        if t.count(dec) <= 1:
            found.append((float(t.replace(dec, ".")), len(t.split(dec, 1)[1]) if dec in t else 0))
    return found


def _rounded(v: float) -> bool:
    """At most two significant digits: how a value is written when it is rounded (1600, 13, 0.25, 12000000)."""
    if not v:
        return False
    return len(Decimal(repr(abs(v))).normalize().as_tuple().digits) <= 2


def close(word: str, numbers: list[float], percent: bool = False) -> bool:
    """A value of the outputs, rounded, or (when the answer writes it as a percentage) times 100. Signs are
    compared apart: the answer's words never carry one. Rounded means to the decimals written (29,46 for 29.456;
    3.14 for 3.14159), or to two significant digits within TOLERANCE (1.600 for 1612)."""
    for v, decimals in readings(word):
        for x in numbers:
            for y in ((abs(x), abs(x) * 100) if percent else (abs(x),)):
                if abs(y - v) <= 1e-9 * max(1.0, v):
                    return True
                if abs(round(y, decimals) - v) <= 1e-9 * max(1.0, v):
                    return True
                if y and _rounded(v) and abs(y - v) <= TOLERANCE * y:
                    return True
    return False


def answer_of(trace: Trace) -> str:
    return next((e.text for e in reversed(trace.events) if e.kind == "message"), "")


def unsupported(trace: Trace) -> tuple[int, list[str]]:
    """(attributable values in the answer, those no output or argument of the run contains), in order, once each."""
    # Compared as usage detection compares (lower case), listed as the agent wrote them.
    values = [w for w in _WORD.findall(answer_of(trace)) if attributable(w)]
    if not values:
        return 0, []
    known, numbers = support([e.output for e in trace.calls] + [e.text for e in trace.events if e.kind == "call"])
    percents = set(_PERCENT.findall(answer_of(trace)))
    missing = list(dict.fromkeys(w for w in values
                                 if w.lower() not in known and not close(w, numbers, percent=w in percents)))
    return len(values), missing


def filtered_tools(workdir: Path, run: str) -> list[str]:
    """Tools whose results reached the model filtered in this run (from the run's notes, evidence/)."""
    log = workdir / "evidence" / f"{run}.jsonl"
    if not log.exists():
        return []
    tools = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            n = json.loads(line)
        except ValueError:
            continue
        if n.get("kind") == "observation" and n.get("exposure") == "filtered" and n.get("tool") not in tools:
            tools.append(n["tool"])
    return tools


def summarise(traces: list[Trace], workdir: Path | None = None) -> dict:
    """The report's section: runs checked, values checked, and the runs with unsupported values.

    `suspect`, per run: the unsupported values of a run whose results were filtered, minus those that were also
    unsupported in a run where nothing was filtered. A value the agent writes without any trimming (a sum it
    computes, a default it knows, a number from the user's request) says nothing about trimming: on the study's
    runs, `58,00` (a sum) and `600` (a deployment's default timeout) were of this kind. The suggestion is given
    only when a suspect value remains."""
    runs, checked, values = [], 0, 0
    for t in traces:
        if not answer_of(t):
            continue
        n, missing = unsupported(t)
        checked += 1
        values += n
        if missing:
            run = t.name.removesuffix(".jsonl")
            runs.append({"run": run, "values": n, "unsupported": missing, "count": len(missing),
                         "filtered_tools": filtered_tools(workdir, run) if workdir else []})
    unfiltered = {v.lower() for r in runs if not r["filtered_tools"] for v in r["unsupported"]}
    for r in runs:
        r["suspect"] = [v for v in r["unsupported"] if v.lower() not in unfiltered] if r["filtered_tools"] else []
        r["unsupported"] = r["unsupported"][:MAX_LISTED]
        r["suspect"] = r["suspect"][:MAX_LISTED]
    return {"runs_checked": checked, "values_checked": values, "runs": runs,
            "suggest": any(r["suspect"] for r in runs)}
