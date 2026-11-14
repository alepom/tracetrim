"""Parts with little or no coverage before 1.1: the OAuth storage and callback, the TOON encoder, the
OpenAI Playground export. No network, no browser: the callback is driven with a local socket."""
import asyncio
import json
import os
import stat
import sys

import pytest

from tracetrim import oauth
from tracetrim.structural import clean, structural, to_toon
from tracetrim.traces import load


# -- OAuth -----------------------------------------------------------------------------------------

def test_which_servers_sign_in_with_oauth():
    assert oauth.wants_oauth({"url": "https://x/mcp"})
    assert not oauth.wants_oauth({"url": "https://x/mcp", "headers": {"Authorization": "Bearer t"}})
    assert oauth.wants_oauth({"url": "https://x/mcp", "headers": {"A": "b"}, "auth": "oauth"})
    assert not oauth.wants_oauth({"url": "https://x/mcp", "auth": "none"})
    assert not oauth.wants_oauth({"command": "node"})


def test_token_files_are_named_safely_and_per_url(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    a, b = oauth.token_path("my server/../x", "https://a/mcp"), oauth.token_path("my server/../x", "https://b/mcp")
    assert a != b and a.parent == tmp_path / "tracetrim" / "oauth"
    assert "/" not in a.name and ".." not in a.name


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_tokens_are_stored_for_the_owner_only(tmp_path):
    from mcp.shared.auth import OAuthToken
    st = oauth.FileStorage(tmp_path / "oauth" / "s.json")
    asyncio.run(st.set_tokens(OAuthToken(access_token="A1", token_type="Bearer", expires_in=3600)))
    p = tmp_path / "oauth" / "s.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    again = oauth.FileStorage(p)
    assert asyncio.run(again.get_tokens()).access_token == "A1" and again.expires_at is not None
    assert not list(p.parent.glob("*.tmp"))                       # no temporary file left behind


def _callback(path):
    login = oauth.Login("srv", 0)

    async def go():
        login.got = asyncio.get_running_loop().create_future()
        server = await asyncio.start_server(login._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await w.drain()
        page = (await r.read()).decode()
        server.close()
        try:
            return page, await login.got
        except RuntimeError as e:
            return page, e
    return asyncio.run(go())


def test_the_login_callback_takes_the_code():
    page, got = _callback("/callback?code=abc&state=s1")
    assert "login complete" in page and got.code == "abc" and got.state == "s1"


def test_the_login_callback_reports_an_error_escaped():
    page, got = _callback("/callback?error=%3Cscript%3E")
    assert "<script>" not in page and "&lt;script&gt;" in page and isinstance(got, RuntimeError)


# -- TOON -------------------------------------------------------------------------------------------

def test_a_list_of_flat_records_becomes_a_table():
    out = to_toon({"items": [{"id": 1, "name": "a b"}, {"id": 2, "name": "c"}]})
    assert out.splitlines()[0] == "items[2]{id,name}:" and "1,a b" in out


def test_values_that_look_like_structure_are_quoted():
    out = to_toon({"x": ["true", "12", "a,b", " pad", ""]})
    for v in ('"true"', '"12"', '"a,b"', '" pad"', '""'):
        assert v in out


def test_nested_and_mixed_lists():
    out = to_toon({"a": [{"b": {"c": 1}}, {"b": {"c": 2}}], "m": [1, {"k": "v"}]})
    assert "a[2]:" in out and "m[2]:" in out and "- k: v" in out


def test_clean_drops_empty_values_and_structural_leaves_text_alone():
    assert clean({"a": None, "b": [], "c": {"d": ""}, "e": 0}) == {"e": 0}
    assert structural("plain text, not JSON") == "plain text, not JSON"
    assert structural('{"a": [1, 2]}') == "a[2]: 1,2"


# -- the OpenAI Playground export --------------------------------------------------------------------

EXPORT = '''from openai import OpenAI
client = OpenAI()
response = client.responses.create(
  model="gpt-x",
  input=[
    {"role": "developer", "content": [{"type": "input_text", "text": "You are an assistant."}]},
    {"role": "user", "content": "Find invoice in_1001"},
    {"type": "web_search_call", "action": {"query": "invoice"}},
    {"type": "mcp_call", "name": "list_invoices", "arguments": "{\\"customer\\": \\"cus_1\\"}",
     "output": "{\\"data\\": [{\\"id\\": \\"in_1001\\", \\"paid\\": true, \\"note\\": null}]}"},
    {"type": "function_call", "call_id": "fc_1", "name": "get_invoice", "arguments": "{\\"id\\": \\"in_1001\\"}"},
    {"type": "function_call_output", "call_id": "fc_1", "output": "{\\"id\\": \\"in_1001\\", \\"total\\": 2900}"},
    {"role": "assistant", "content": [{"type": "output_text", "text": "Invoice in_1001: 29.00 EUR."}]},
  ],
)
'''


def test_a_playground_export_with_mcp_and_function_tools(tmp_path):
    p = tmp_path / "export.py"
    p.write_text(EXPORT)
    t = load(p)
    kinds = [(e.kind, e.tool) for e in t.events]
    assert kinds == [("prompt", ""), ("prompt", ""), ("call", "web_search"), ("call", "list_invoices"),
                     ("call", "get_invoice"), ("message", "")]
    assert json.loads(t.events[4].output) == {"id": "in_1001", "total": 2900}
    assert [e.tool for e in t.calls] == ["list_invoices", "get_invoice"]     # web search: no visible output


def test_a_file_that_is_not_an_export_says_so(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("print('hello')\n")
    with pytest.raises(ValueError):
        load(p)
    with pytest.raises(ValueError):
        load(tmp_path / "trace.txt")


# -- the command line ---------------------------------------------------------------------------------

def test_the_version_is_the_package_version(capsys):
    from tracetrim.about import VERSION
    from tracetrim.cli import main
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and capsys.readouterr().out.strip() == f"tracetrim {VERSION}"
    tomllib = pytest.importorskip("tomllib")                  # Python 3.11+
    from pathlib import Path
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if pyproject.exists():                                       # in a checkout or the sdist: the two agree
        assert tomllib.loads(pyproject.read_text())["project"]["version"] == VERSION
