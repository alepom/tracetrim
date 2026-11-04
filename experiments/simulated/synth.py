"""Synthetic objects generated from an OpenAPI schema.

Every property the schema declares is present, with a value of the declared
type and format; names and enumerations come from the schema itself. What
this module invents is only the values, deterministically from a seed, so
the same scenario always yields byte-identical payloads.

The goal is realism of SHAPE, not of content: the study measures how much of
a payload an agent uses, and the shape is what decides how much there is.
"""
from __future__ import annotations

import json
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPECS = Path(__file__).resolve().parent / "specs"
# The OpenAPI descriptions are not in git (22 MB): pinned to a commit and a
# hash (SOURCES.md), they are downloaded on first use and verified, so that a
# fresh clone runs the simulated servers, the demo and the tests as they ran.
PINNED = {
    "stripe-spec3.json": ("https://raw.githubusercontent.com/stripe/openapi/b213739d4879835a5af50f15daaa3a41ae267985/"
                          "openapi/spec3.json",
                          "7cff4cc46d0654101a36a3302f7544640f5f73e4f3cad6f264f0e23c19fa1776"),
    "github-api.json": ("https://raw.githubusercontent.com/github/rest-api-description/"
                        "99b1432b55b9a71d0881fd792a87f1807cf4c8f2/descriptions/api.github.com/api.github.com.json",
                        "25c06a5ada7843db70fc54f8888df8f616e8689206c5da4bc60badf9bc577594"),
}


def spec_path(name: str) -> Path:
    import hashlib
    import sys
    import urllib.request
    path = SPECS / name
    if not path.exists():
        url, digest = PINNED[name]
        print(f"[simulated] downloading the pinned OpenAPI description {name} (once)", file=sys.stderr, flush=True)
        data = urllib.request.urlopen(url, timeout=120).read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError(f"{name}: downloaded file does not match the pinned sha256 (SOURCES.md)")
        SPECS.mkdir(exist_ok=True)
        path.write_bytes(data)
    return path
BASE_TIME = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)   # scenarios live in late Oct 2026
WORDS = ("account", "update", "review", "request", "service", "release", "config", "payment",
         "billing", "monthly", "standard", "support", "checkout", "storage", "report", "access")


class Synth:
    def __init__(self, spec_file: str, seed: int, id_prefixes: dict[str, str] | None = None,
                 max_depth: int = 4, top_level_optional: str | set = "all"):
        self.spec = json.loads(spec_path(spec_file).read_text(encoding="utf-8"))
        self.schemas = self.spec["components"]["schemas"]
        self.rng = random.Random(seed)
        self.prefixes = id_prefixes or {}
        self.max_depth = max_depth
        # Which optional properties appear at the top level: "all" (Stripe
        # returns every field, null when unset) or an explicit set (GitHub
        # endpoints return a known subset by default). Below the top level
        # only required properties appear, plus the branch a `type` names.
        self.top_level_optional = top_level_optional
        self.counter = 0

    # -- schema plumbing -------------------------------------------------
    def resolve(self, s: dict) -> dict:
        while isinstance(s, dict) and "$ref" in s:
            s = self.schemas[s["$ref"].rsplit("/", 1)[-1]]
        return s

    def schema(self, name: str) -> dict:
        return self.schemas[name]

    # -- values ----------------------------------------------------------
    def _id(self, kind: str) -> str:
        self.counter += 1
        body = "".join(self.rng.choices(string.ascii_letters + string.digits, k=14))
        prefix = self.prefixes.get(kind)
        return f"{prefix}_{body}" if prefix else body

    def _string(self, name: str, s: dict, kind: str):
        if "enum" in s:
            return self.rng.choice([e for e in s["enum"] if e is not None] or [""])
        fmt = s.get("format", "")
        if fmt == "date-time" or name.endswith("_at"):
            return (BASE_TIME - timedelta(minutes=self.rng.randint(0, 60 * 24 * 10))).isoformat().replace("+00:00", "Z")
        if fmt == "date":
            return (BASE_TIME - timedelta(days=self.rng.randint(0, 30))).date().isoformat()
        if fmt in ("uri", "url") or name.endswith("url") or name == "href":
            return f"https://api.example.test/{kind or 'object'}/{self.rng.randint(1000, 99999)}"
        if fmt == "email" or name == "email":
            return f"user{self.rng.randint(1, 999)}@example.com"
        if name in ("id", "node_id") or name.endswith("_id"):
            return self._id(kind if name == "id" else name.removesuffix("_id"))
        if name in ("currency",):
            return "eur"
        if name in ("country",):
            return "IT"
        if name in ("sha", "head_sha"):
            return "".join(self.rng.choices("0123456789abcdef", k=40))
        n = self.rng.randint(1, 4)
        return " ".join(self.rng.choice(WORDS) for _ in range(n))

    def _integer(self, name: str, s: dict):
        if name in ("created", "date") or name.endswith(("_at", "_start", "_end", "_date", "due_by")) or name.startswith("period"):
            return int((BASE_TIME - timedelta(minutes=self.rng.randint(0, 60 * 24 * 10))).timestamp())
        if "amount" in name or name in ("total", "subtotal", "balance"):
            return self.rng.randint(5, 500) * 100
        if name == "id":
            return self.rng.randint(10**8, 10**10)
        return self.rng.randint(0, 50)

    def value(self, s: dict, name: str = "", depth: int = 0, kind: str = ""):
        s = self.resolve(s)
        if not isinstance(s, dict):
            return None
        if "allOf" in s:
            merged = {"type": "object", "properties": {}, "required": []}
            for part in s["allOf"]:
                p = self.resolve(part)
                merged["properties"].update(p.get("properties", {}))
            return self.value(merged, name, depth, kind)
        for key in ("anyOf", "oneOf"):
            if key in s:
                opts = [self.resolve(o) for o in s[key]]
                # Expandable references: the API returns the id unless asked to expand.
                strings = [o for o in opts if o.get("type") == "string"]
                if strings and any(o.get("type") == "object" or "properties" in o for o in opts):
                    return self._string(name or "id", strings[0], kind)
                non_null = [o for o in opts if o.get("type") != "null" and o != {}]
                return self.value(non_null[0] if non_null else opts[0], name, depth, kind)
        nullable = s.get("nullable") or (isinstance(s.get("type"), list) and "null" in s["type"])
        t = s.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), None)
        if nullable and (depth >= self.max_depth or self.rng.random() < 0.25):
            return None
        if t == "object" or "properties" in s:
            if depth >= self.max_depth:
                return None if nullable else {}
            props = s.get("properties", {})
            if not props:
                return {}
            obj_kind = s.get("x-resourceId", "") or kind
            required = set(s.get("required", []))
            if depth == 0:
                extra = set(props) if self.top_level_optional == "all" else set(self.top_level_optional)
                keep = [k for k in props if k in required or k in extra]
            else:
                keep = [k for k in props if k in required]
            obj = {k: self.value(props[k], k, depth + 1, obj_kind) for k in keep}
            # A free-text `type` beside optional object branches is a discriminator:
            # name one of the branches, as a real response would.
            branches = [k for k in props if k not in required and k != "type"
                        and self.resolve(props[k]).get("properties")]
            if "type" in obj and branches and "enum" not in self.resolve(props["type"]):
                obj["type"] = self.rng.choice(sorted(branches))
            # Discriminated objects (payment_method_details: {type, card, sepa_debit, ...})
            # carry only the branch their type names, as the API does.
            branch = obj.get("type")
            if isinstance(branch, str) and branch in props and branch not in obj:
                obj[branch] = self.value(props[branch], branch, depth + 1, obj_kind)
            return obj
        if t == "array":
            n = 0 if depth >= self.max_depth else self.rng.randint(0, 2)
            return [self.value(s.get("items", {}), name, depth + 1, kind) for _ in range(n)]
        if t == "string":
            return self._string(name, s, kind)
        if t == "integer":
            return self._integer(name, s)
        if t == "number":
            return round(self.rng.uniform(0, 100), 2)
        if t == "boolean":
            return self.rng.random() < 0.5
        return None

    def make(self, schema_name: str, overrides: dict | None = None, kind: str = "") -> dict:
        obj = self.value(self.schemas[schema_name], "", 0, kind or schema_name)
        for path, v in (overrides or {}).items():
            set_path(obj, path, v)
        return obj


def set_path(obj: dict, path: str, value) -> None:
    """Set a dotted path, creating intermediate objects when the generator
    left them null (nullable branches)."""
    keys = path.split(".")
    for k in keys[:-1]:
        if not isinstance(obj.get(k), dict):
            obj[k] = {}
        obj = obj[k]
    obj[keys[-1]] = value


def ts(days_ago: float, hour: int = 10) -> int:
    t = (BASE_TIME - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0)
    return int(t.timestamp())


def iso(days_ago: float, hour: int = 10) -> str:
    t = (BASE_TIME - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0)
    return t.isoformat().replace("+00:00", "Z")
