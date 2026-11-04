"""Structural compression: same information, fewer tokens.

Applied AFTER the projection, it never decides what to keep, only how to
write it:

  clean   drop null, empty strings, empty lists and empty objects. Near
          lossless: "absent" and "null" become the same thing, which is how
          most APIs already treat optional fields.
  TOON    Token-Oriented Object Notation: objects as indented `key: value`
          lines, arrays of uniform flat objects as a table whose field names
          are written once. Arrays that are not uniform fall back to a list.

The model has to READ this format: whether it reads it as well as JSON is
an empirical question, measured in Phase B, not assumed.
"""
from __future__ import annotations

import json
import re

from .usage import parse_output

_EMPTY = (None, "", [], {})
_SAFE = re.compile(r"^[^,:\n\"\[\]{}#-][^,:\n\"\[\]{}]*$")


def clean(v):
    if isinstance(v, dict):
        out = {k: clean(x) for k, x in v.items()}
        return {k: x for k, x in out.items() if x not in _EMPTY}
    if isinstance(v, list):
        out = [clean(x) for x in v]
        return [x for x in out if x not in _EMPTY]
    return v


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    s = str(v)
    # Quote whatever a reader could mistake for structure, a number or a literal.
    if (not s or s != s.strip() or not _SAFE.match(s) or s in ("true", "false", "null")
            or re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s)):
        return json.dumps(s, ensure_ascii=False)
    return s


def _is_table(items) -> list[str] | None:
    """Field names, when every item is a flat object with the same keys."""
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    keys = list(items[0].keys())
    if any(list(x.keys()) != keys for x in items):
        return None
    if any(isinstance(x[k], (dict, list)) for x in items for k in keys):
        return None
    return keys


def _encode(v, key: str | None, ind: int, out: list[str]) -> None:
    pad = "  " * ind
    label = f"{_scalar(key)}" if key is not None else ""
    if isinstance(v, dict):
        if key is not None:
            out.append(f"{pad}{label}:")
            ind += 1
        for k, x in v.items():
            _encode(x, k, ind, out)
        return
    if isinstance(v, list):
        if all(not isinstance(x, (dict, list)) for x in v):
            out.append(f"{pad}{label}[{len(v)}]: " + ",".join(_scalar(x) for x in v))
            return
        fields = _is_table(v)
        if fields:
            out.append(f"{pad}{label}[{len(v)}]{{{','.join(_scalar(f) for f in fields)}}}:")
            for x in v:
                out.append("  " * (ind + 1) + ",".join(_scalar(x[f]) for f in fields))
            return
        out.append(f"{pad}{label}[{len(v)}]:")
        for x in v:
            sub: list[str] = []
            _encode(x, None, 0, sub)
            first, *rest = sub or [""]
            out.append("  " * (ind + 1) + "- " + first)
            out.extend("  " * (ind + 2) + line for line in rest)
        return
    out.append(f"{pad}{label}: {_scalar(v)}" if key is not None else f"{pad}{_scalar(v)}")


def to_toon(v) -> str:
    out: list[str] = []
    _encode(v, None, 0, out)
    return "\n".join(out)


def structural(output: str) -> str:
    """Clean and TOON-encode a JSON output; anything else is returned as is."""
    payload = parse_output(output)
    if not isinstance(payload, (dict, list)):
        return output
    return to_toon(clean(payload))


def headroom(output: str, tool: str = "tool") -> str:
    """Compress one tool output with Headroom (Apache-2.0), if installed.

    EXPERIMENTAL in tracetrim: Headroom was measured as a token baseline in
    the study behind this library, not validated for answer quality on top of
    a projection. It needs `pip install tracetrim[headroom]` (torch included).
    Its text compressor needs a model download; without it, Headroom
    compresses structured content only.
    """
    try:
        from headroom import CompressConfig, compress
    except ImportError as e:
        raise RuntimeError("Headroom is not installed: pip install 'tracetrim[headroom]'") from e
    msgs = [{"role": "assistant", "content": None, "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": tool, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t1", "name": tool, "content": output}]
    r = compress(msgs, model="gpt-4o", model_limit=400000,
                 config=CompressConfig(protect_recent=0, protect_analysis_context=False))
    return next((m["content"] for m in r.messages if m.get("role") == "tool"), output)
