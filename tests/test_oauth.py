"""OAuth towards upstream servers: login once, then tokens reused and renewed.

Against a local OAuth-protected MCP server (experiments/simulated/oauth_server.py)
that approves at once: the "browser" is a script that follows the redirect,
so the whole flow (discovery, registration, PKCE, code, refresh) runs here.
"""
import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def oauth_server(tmp_path):
    port = _free_port()
    log = (tmp_path / "server.log").open("w")
    p = subprocess.Popen([PY, "-m", "experiments.simulated.oauth_server", "--port", str(port), "--ttl", "2"],
                         cwd=ROOT, stderr=log, stdout=log)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.2)
    yield f"http://127.0.0.1:{port}/mcp", tmp_path / "server.log"
    p.terminate()
    p.wait(10)


def _env(tmp_path, browser_follows: bool):
    browser = tmp_path / ("browser.sh" if browser_follows else "no-browser.sh")
    browser.write_text("#!/bin/sh\n" + ('curl -s -L -o /dev/null "$1"\n' if browser_follows else "exit 0\n"))
    browser.chmod(0o755)
    return {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "BROWSER": str(browser),
            "TRACETRIM_LOGIN_TIMEOUT": "15"}


def _tracetrim(args, env):
    return subprocess.run([PY, "-m", "tracetrim.cli", *args], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=60)


def test_login_then_the_proxy_renews_the_token_without_a_browser(tmp_path, oauth_server):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    url, server_log = oauth_server
    r = _tracetrim(["login", "--upstream-url", url], _env(tmp_path, browser_follows=True))
    assert r.returncode == 0, r.stderr
    assert "logged in, 1 tools" in r.stdout
    token_file = next((tmp_path / "config" / "tracetrim" / "oauth").glob("upstream-*.json"))
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert json.loads(token_file.read_text())["tokens"]["refresh_token"]

    time.sleep(3)   # the access token (2 s) has expired

    # Now the "browser" does nothing: a new login would time out. The proxy must
    # renew the token on its own and call the tool.
    params = StdioServerParameters(command=PY, cwd=str(ROOT), env=_env(tmp_path, browser_follows=False), args=[
        "-m", "tracetrim.cli", "proxy", "--upstream-url", url, "--auto", str(tmp_path / "auto"), "--quiet"])

    async def call():
        async with stdio_client(params) as (r_, w_), ClientSession(r_, w_) as s:
            await s.initialize()
            return (await s.call_tool("whoami", {})).content[0].text

    out = asyncio.run(call())
    assert "demo" in out
    log = server_log.read_text()
    assert log.count("authorization approved") == 1
    assert "token renewed (refresh token)" in log

    r = _tracetrim(["logout", "--upstream-url", url], _env(tmp_path, browser_follows=False))
    assert "token removed" in r.stdout and not token_file.exists()
