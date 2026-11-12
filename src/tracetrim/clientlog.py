"""Diagnostics of the client: one JSON line per request it sends to the proxy.

  tracetrim proxy ... --log-client client.jsonl

Off by default. It answers questions the traces cannot: which host connected,
what it declared in the handshake, and whether it asked for the tracetrim
skill (`skills/list`, `skills/get`, `resources/read`) or only for the tools.

What a line holds is an allow-list, per method; whatever is not listed below
is never written:

  every request     time, an anonymous id of the session, the method
  initialize        clientInfo (name, version, title), protocolVersion, capabilities
                    (with the extensions the client declares)
  server/discover   what the client declares in the request's `_meta`, as above
  resources/read, skills/get        the uri asked for
  resources/list, skills/list       nothing more
  tools/list        nothing more
  tools/call        the name of the tool

Never: arguments, results, headers, tokens, upstream URLs, the client's
address. The session id is a salted hash, with a salt that lives only in the
memory of this process: it tells the sessions of one log apart and cannot be
turned back into the transport's id.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import weakref
from datetime import datetime, timezone

MAX_TEXT = 200               # a name or a uri: longer ones are cut
MAX_CAPABILITIES = 4000      # characters of JSON; beyond this only the names of the capabilities are kept
URI_METHODS = {"resources/read", "skills/get"}
META_PREFIX = "io.modelcontextprotocol/"
_DECLARED = {"clientInfo": "clientInfo", "protocolVersion": "protocolVersion", "capabilities": "capabilities",
             "clientCapabilities": "capabilities"}


def _text(value, limit: int = MAX_TEXT) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _declared(source) -> dict:
    """clientInfo, protocolVersion and capabilities, from the params of
    `initialize` or from a `_meta` that names them (with or without the
    `io.modelcontextprotocol/` prefix)."""
    out: dict = {}
    if not isinstance(source, dict):
        return out
    for key, value in source.items():
        name = _DECLARED.get(key.removeprefix(META_PREFIX)) if isinstance(key, str) else None
        if name == "clientInfo" and isinstance(value, dict):
            info = {k: _text(value.get(k)) for k in ("name", "version", "title")}
            out[name] = {k: v for k, v in info.items() if v is not None}
        elif name == "protocolVersion" and isinstance(value, str):
            out[name] = value[:40]
        elif name == "capabilities" and isinstance(value, dict):
            try:
                size = len(json.dumps(value, ensure_ascii=False))
            except (TypeError, ValueError):
                continue
            out[name] = value if size <= MAX_CAPABILITIES else {"truncated": sorted(map(str, value))[:50]}
    return out


def entry(method: str, params) -> dict:
    """What may be written about a request, besides time and session."""
    params = params if isinstance(params, dict) else {}
    out: dict = {"method": str(method)[:80]}
    if method == "initialize":
        out.update(_declared(params))
    elif method == "server/discover":
        out.update(_declared(params.get("_meta")))
    elif method in URI_METHODS:
        if (uri := _text(params.get("uri"))) is not None:
            out["uri"] = uri
    elif method == "tools/call":
        if (name := _text(params.get("name"))) is not None:
            out["tool"] = name
    return out


class ClientLog:
    def __init__(self, path: str):
        # O_APPEND, and one write per line: lines from several clients, or from
        # several proxies given the same file, do not mix.
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self.lock = threading.Lock()
        self._salt = secrets.token_bytes(16)
        self._tokens: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def session(self, connection) -> str:
        """An anonymous id for a connection: the salted hash of the transport's
        session id when there is one (HTTP), otherwise of a random token given
        to the connection (stdio; and HTTP without sessions, where each request
        is its own connection)."""
        real = getattr(connection, "session_id", None)
        if not isinstance(real, str) or not real:
            try:
                real = self._tokens.setdefault(connection, secrets.token_hex(16))
            except TypeError:                    # no connection to tell apart
                real = "-"
        return hashlib.sha256(self._salt + real.encode()).hexdigest()[:12]

    def record(self, connection, method: str, params) -> None:
        line = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "session": self.session(connection), **entry(method, params)}
        data = (json.dumps(line, ensure_ascii=False) + "\n").encode()
        with self.lock:
            os.write(self.fd, data)

    def close(self) -> None:
        os.close(self.fd)
