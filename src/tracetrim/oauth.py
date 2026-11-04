"""OAuth for upstream MCP servers that ask for a login (MCP authorization spec).

  tracetrim login  --servers mcp.json        # once: a browser login per OAuth server
  tracetrim proxy  --servers mcp.json ...    # then: tokens reused and renewed on their own
  tracetrim logout --servers mcp.json        # forget the tokens

A remote server in `mcp.json` with a `url` and no `headers` is treated as an
OAuth server, like Claude Code and Cursor do (`"auth": "oauth"` forces it,
`"auth": "none"` disables it). The protocol itself — discovery, dynamic client
registration, PKCE, refresh — is the MCP SDK's; this module adds what a proxy
needs around it:

  - tokens kept on disk, one file per server, readable only by the user, in
    ~/.config/tracetrim/oauth/ (not in the work directory: that one holds
    traces and may be shared; tokens are credentials);
  - the login: the authorization URL is printed (and opened in the browser
    when possible), and a one-shot local listener receives the redirect;
  - the token expiry kept with the token, so that after a restart an expired
    token is renewed with the refresh token instead of asking for a new login.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

CALLBACK_PORT = 33418
# The SDK logs a full traceback when a login does not complete; the cause is
# already in tracetrim's own message.
logging.getLogger("mcp.client.auth").setLevel(logging.CRITICAL)
LOGIN_TIMEOUT = int(os.environ.get("TRACETRIM_LOGIN_TIMEOUT", "300"))


def wants_oauth(spec: dict) -> bool:
    if spec.get("auth") == "none" or "url" not in spec:
        return False
    return spec.get("auth") == "oauth" or not spec.get("headers")


def token_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "tracetrim" / "oauth"


def token_path(name: str, url: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return token_dir() / f"{safe}-{hashlib.sha256(url.encode()).hexdigest()[:8]}.json"


class FileStorage:
    """The SDK's TokenStorage, on one JSON file (mode 600)."""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {}

    @property
    def expires_at(self) -> float | None:
        return self.data.get("expires_at")

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        # Created readable by the owner only, never even briefly by others.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(self.data, indent=2))
        tmp.replace(self.path)

    async def get_tokens(self) -> OAuthToken | None:
        t = self.data.get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self.data["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in else None
        self._save()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        c = self.data.get("client")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.data["client"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save()


class Provider(OAuthClientProvider):
    async def _initialize(self) -> None:
        await super()._initialize()
        # The SDK does not know when a token loaded from storage expires, and
        # would treat it as valid: an expired one would then cost a new login.
        if self.context.current_tokens and self.context.storage.expires_at:
            self.context.token_expiry_time = self.context.storage.expires_at


def _say(line: str) -> None:
    print(f"[tracetrim] {line}", file=sys.stderr, flush=True)


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


class Login:
    """The browser half of the flow. The listener for the redirect starts
    BEFORE the browser opens: a browser where the user is already logged in
    can come back at once."""

    def __init__(self, name: str, port: int):
        self.name, self.port = name, port
        self.server = None
        self.got: asyncio.Future | None = None

    async def open(self, url: str) -> None:
        self.got = asyncio.get_running_loop().create_future()
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        _say(f"login required for '{self.name}': open this address in your browser\n\n  {url}\n")
        # In a thread: some browser commands only return when the page is
        # done, and the listener above must keep answering meanwhile.
        asyncio.get_running_loop().run_in_executor(None, _open_browser, url)

    async def wait(self) -> AuthorizationCodeResult:
        try:
            return await asyncio.wait_for(self.got, LOGIN_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError(f"no login within {LOGIN_TIMEOUT}s: run `tracetrim login` first") from None
        finally:
            self.server.close()

    async def _handle(self, reader, writer):
        line = (await reader.readline()).decode(errors="replace")
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        q = parse_qs(urlparse(line.split(" ")[1] if " " in line else "/").query)
        ok = "code" in q
        body = ("<!doctype html><meta charset=utf-8><body style='font:16px system-ui;padding:24px'>"
                + ("tracetrim: login complete, you can close this tab." if ok
                   else f"tracetrim: login failed ({html.escape(q.get('error', ['no code'])[0])}).") + "</body>")
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
                     f"{len(body.encode())}\r\nConnection: close\r\n\r\n{body}".encode())
        await writer.drain()
        writer.close()
        if not self.got.done():
            if ok:
                self.got.set_result(AuthorizationCodeResult(code=q["code"][0], state=q.get("state", [None])[0],
                                                            iss=q.get("iss", [None])[0]))
            else:
                self.got.set_exception(RuntimeError(f"login failed: {q.get('error', ['no code'])[0]}"))


def provider(name: str, url: str, port: int = CALLBACK_PORT) -> Provider:
    metadata = OAuthClientMetadata(
        client_name="tracetrim", redirect_uris=[f"http://127.0.0.1:{port}/callback"],
        grant_types=["authorization_code", "refresh_token"], response_types=["code"],
        token_endpoint_auth_method="none")
    login = Login(name, port)
    return Provider(server_url=url, client_metadata=metadata, storage=FileStorage(token_path(name, url)),
                    redirect_handler=login.open, callback_handler=login.wait)
