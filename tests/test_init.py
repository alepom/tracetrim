"""`tracetrim init`: the host's configuration rewritten so that its servers go
through tracetrim. Every file here is built by the test, in a temporary home;
the values to keep secret are recognisable, and no output may ever show one."""
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tracetrim import init
from tracetrim.cli import main

S = {k: f"SEGRETO-{k}-7f3a" for k in ("ENV", "HEADER", "ARG", "QUERY", "PATH", "USERINFO", "DEFAULT", "CWD", "DISK")}
EXE = "/opt/tt/bin/tracetrim"


@pytest.fixture(autouse=True)
def fixed_executable(monkeypatch):
    monkeypatch.setattr(init, "executable", lambda: [EXE])


@pytest.fixture
def run(capsys):
    """Run the CLI; return (exit message or None, everything printed). No secret may appear."""
    def go(*argv):
        failure = None
        try:
            main(["init", *map(str, argv)])
        except SystemExit as e:
            failure = str(e.code)
        cap = capsys.readouterr()
        text = cap.out + cap.err + (failure or "")
        for name, value in S.items():
            assert value not in text, f"the {name} value was printed"
        return failure, text
    return go


def write(path: Path, data, indent=2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent) + "\n", encoding="utf-8")
    return path


def cursor_config():
    return {"theme": "dark", "mcpServers": {
        "notes": {"command": "node", "args": ["notes.js"], "envFile": ".env"},
        "billing": {"url": f"https://u:{S['USERINFO']}@billing.example/{S['PATH']}/mcp?key={S['QUERY']}",
                    "headers": {"Authorization": f"Bearer {S['HEADER']}"}},
        "code": {"type": "stdio", "command": "/usr/bin/python3", "args": ["-m", "srv", "--api-key", S["ARG"]],
                 "env": {"API_KEY": S["ENV"], "REGION": "eu"}, "cwd": f"/srv/{S['CWD']}"},
    }, "other": [1, 2]}


def cursor_file(home) -> Path:
    return write(home / ".cursor" / "mcp.json", cursor_config())


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def backups(file: Path) -> list:
    return sorted(p for p in file.parent.iterdir() if init.BACKUP_MARK in p.name and not p.name.endswith(".json"))


# ---------------------------------------------------------------- the home of the tests

def test_every_test_runs_in_a_temporary_home(temp_home, tmp_path):
    from conftest import REAL_HOME
    assert Path.home() == temp_home != REAL_HOME
    assert tmp_path in temp_home.parents
    for host in init.HOSTS.values():
        if host.user:
            assert temp_home in host.user_path().parents
        assert temp_home in init.default_workdir(host, "user", None)[0].parents
        assert temp_home in init.default_workdir(host, "project", tmp_path / "p")[0].parents


@pytest.mark.parametrize("platform, tail", [
    ("linux", ".config/Claude/claude_desktop_config.json"),
    ("darwin", "Library/Application Support/Claude/claude_desktop_config.json"),
    ("win32", "AppData/Roaming/Claude/claude_desktop_config.json")])
def test_claude_desktop_paths(temp_home, monkeypatch, platform, tail):
    monkeypatch.setattr(sys, "platform", platform)
    assert init.HOSTS["claude-desktop"].user_path() == temp_home / tail


# ---------------------------------------------------------------- dry run, write, undo

def test_dry_run_writes_nothing_and_says_what_would_change(temp_home, run):
    cursor_file(temp_home)
    before = tree(temp_home)
    failure, text = run("--host", "cursor")
    assert failure is None and tree(temp_home) == before
    assert "Dry run" in text and "Behind tracetrim (2)" in text and "Left in the host (1)" in text
    assert "envFile" in text                                   # why notes stays
    assert "https://billing.example" in text and "Authorization=••••" in text
    assert "python3 (4 args)" in text and "API_KEY=••••" in text and "REGION=••••" in text
    assert EXE in text and str(temp_home / ".tracetrim" / "cursor" / "servers.json") in text
    assert "tracetrim_done" in text and "report.html" in text


def test_write_moves_the_servers_as_they_were(temp_home, run):
    file = cursor_file(temp_home)
    original, config = file.read_bytes(), cursor_config()
    failure, text = run("--host", "cursor", "--write")
    assert failure is None
    work = temp_home / ".tracetrim" / "cursor"

    (backup,) = backups(file)
    assert backup.read_bytes() == original and backup.parent == file.parent
    assert backup.name.startswith("mcp.json.tracetrim-backup-20") and str(backup) in text

    servers = work / "servers.json"
    assert stat.S_IMODE(servers.stat().st_mode) == 0o600
    assert json.loads(servers.read_text()) == {"mcpServers": {n: config["mcpServers"][n] for n in ("billing", "code")}}

    after = json.loads(file.read_text())
    assert list(after) == ["theme", "mcpServers", "other"] and after["theme"] == "dark" and after["other"] == [1, 2]
    assert list(after["mcpServers"]) == ["notes", "tracetrim"]          # one entry, where the first server was
    assert after["mcpServers"]["notes"] == config["mcpServers"]["notes"]
    assert after["mcpServers"]["tracetrim"] == {
        "type": "stdio", "command": EXE,
        "args": ["proxy", "--servers", str(servers), "--auto", str(work)]}
    assert stat.S_IMODE(file.stat().st_mode) == stat.S_IMODE(backup.stat().st_mode)

    note = json.loads(backup.with_name(backup.name + ".json").read_text())    # a fingerprint, no content
    assert set(note) == {"format", "file", "host", "written_sha256", "backup_sha256"}
    assert note["written_sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()


def test_the_servers_file_is_one_the_proxy_reads(temp_home, run):
    from tracetrim.proxy import load_servers
    cursor_file(temp_home)
    run("--host", "cursor", "--write")
    assert set(load_servers(temp_home / ".tracetrim" / "cursor" / "servers.json")) == {"billing", "code"}


def test_undo_restores_byte_for_byte(temp_home, run):
    file = cursor_file(temp_home)
    file.write_bytes(original := file.read_bytes().replace(b"\n", b"\r\n") + b"  \n")     # bytes no rewrite would keep
    run("--host", "cursor", "--write")
    assert file.read_bytes() != original
    (backup,) = backups(file)
    failure, text = run("--undo", backup)
    assert failure is None and file.read_bytes() == original and "byte for byte" in text
    assert (temp_home / ".tracetrim" / "cursor" / "servers.json").exists() and "servers.json" in text
    failure, text = run("--undo", backup)
    assert failure is None and "nothing to do" in text


def test_undo_refuses_when_the_host_file_changed_since(temp_home, run):
    file = cursor_file(temp_home)
    original = file.read_bytes()
    run("--host", "cursor", "--write")
    (backup,) = backups(file)
    data = json.loads(file.read_text())
    data["mcpServers"]["added"] = {"command": "later", "env": {"K": S["ENV"]}}
    write(file, data)
    changed = file.read_bytes()
    failure, _ = run("--undo", backup)
    assert "no longer the file" in failure and "--force" in failure and file.read_bytes() == changed
    failure, _ = run("--undo", backup, "--force")
    assert failure is None and file.read_bytes() == original


def test_undo_without_its_note_needs_force(temp_home, run):
    file = cursor_file(temp_home)
    original = file.read_bytes()
    run("--host", "cursor", "--write")
    (backup,) = backups(file)
    backup.with_name(backup.name + ".json").unlink()
    failure, _ = run("--undo", backup)
    assert "--force" in failure and file.read_bytes() != original
    assert "backup" in run("--undo", file)[0]                     # not a backup at all
    assert run("--undo", backup, "--force")[0] is None and file.read_bytes() == original


# ---------------------------------------------------------------- idempotence

def test_a_second_run_does_not_wrap_twice(temp_home, run):
    file = cursor_file(temp_home)
    run("--host", "cursor", "--write")
    before = tree(temp_home)
    failure, text = run("--host", "cursor", "--write")
    assert failure is None and tree(temp_home) == before
    assert "already goes through tracetrim" in text and "Nothing to do" in text


def test_servers_added_later_join_the_same_file(temp_home, run):
    file = cursor_file(temp_home)
    run("--host", "cursor", "--write")
    data = json.loads(file.read_text())
    code = cursor_config()["mcpServers"]["code"]
    data["mcpServers"].update({"late": {"command": "late", "env": {"K": S["ENV"]}}, "code": code})   # one new, one copy
    data["mcpServers"]["tracetrim"]["args"] += ["--promote", f"--token={S['ARG']}"]                   # the user's own edits
    write(file, data)
    failure, text = run("--host", "cursor", "--write")
    assert failure is None and "not wrapped a second time" in text and "not duplicated" in text
    after = json.loads(file.read_text())["mcpServers"]
    assert list(after) == ["notes", "tracetrim"]
    assert after["tracetrim"] == data["mcpServers"]["tracetrim"]                    # kept as the user left it
    servers = json.loads((temp_home / ".tracetrim" / "cursor" / "servers.json").read_text())["mcpServers"]
    assert list(servers) == ["billing", "code", "late"] and servers["code"] == code
    assert len(backups(file)) == 2


def test_same_name_with_another_definition_is_refused(temp_home, run):
    file = cursor_file(temp_home)
    run("--host", "cursor", "--write")
    data = json.loads(file.read_text())
    data["mcpServers"]["code"] = {"command": "other", "env": {"API_KEY": S["DISK"]}}
    write(file, data)
    before = tree(temp_home)
    failure, _ = run("--host", "cursor", "--write")
    assert "different definition" in failure and "code" in failure and tree(temp_home) == before


def test_a_server_called_tracetrim_that_is_not_ours(temp_home, run):
    config = cursor_config()
    config["mcpServers"]["tracetrim"] = {"command": "something-else", "env": {"K": S["ENV"]}}
    file = write(temp_home / ".cursor" / "mcp.json", config)
    before = tree(temp_home)
    failure, _ = run("--host", "cursor", "--write")
    assert "--name" in failure and tree(temp_home) == before
    assert run("--host", "cursor", "--write", "--name", "tt", "--exclude", "tracetrim")[0] is None
    assert list(json.loads(file.read_text())["mcpServers"]) == ["notes", "tt", "tracetrim"]


# ---------------------------------------------------------------- variables, host by host

def test_claude_code_references_are_passed_by_the_host(temp_home, tmp_path, run, monkeypatch):
    project = tmp_path / "shop"
    file = write(project / ".mcp.json", {"mcpServers": {
        "api": {"type": "http", "url": "${API_BASE:-https://" + S["DEFAULT"] + ".example}/mcp",
                "headers": {"Authorization": "Bearer ${API_TOKEN}"}},
        "db": {"command": "db-server", "args": ["--dsn", "${DSN}"], "env": {"TOKEN": "${API_TOKEN}", "MODE": "fast"}}}})
    monkeypatch.chdir(project)
    failure, text = run("--host", "claude-code", "--write")
    assert failure is None and "References translated" in text and "${API_BASE:-••••}" in text
    work = init.default_workdir(init.HOSTS["claude-code"], "project", project)[0]
    assert work.parent == temp_home / ".tracetrim" / "claude-code" and work.name.startswith("shop-")
    assert str(work) in text and "per project" in text
    entry = json.loads(file.read_text())["mcpServers"]["tracetrim"]
    assert entry["type"] == "stdio" and entry["env"] == {
        "API_BASE": "${API_BASE:-https://" + S["DEFAULT"] + ".example}", "API_TOKEN": "${API_TOKEN}", "DSN": "${DSN}"}
    servers = json.loads((work / "servers.json").read_text())["mcpServers"]
    assert servers["api"] == {"type": "http", "url": "${API_BASE}/mcp", "headers": {"Authorization": "Bearer ${API_TOKEN}"}}
    assert servers["db"]["args"] == ["--dsn", "${DSN}"] and servers["db"]["env"] == {"TOKEN": "${API_TOKEN}", "MODE": "fast"}

    # What the proxy then resolves, with the variables as the host passes them, is what the host resolved before.
    from tracetrim.proxy import Options, load_servers
    for var, value in {"API_BASE": "https://real.example", "API_TOKEN": "t0k", "DSN": "pg://x"}.items():
        monkeypatch.setenv(var, value)
    ups = Options(servers=load_servers(work / "servers.json")).upstreams()
    assert ups["api"]["url"] == "https://real.example/mcp" and ups["api"]["headers"] == {"Authorization": "Bearer t0k"}
    assert ups["db"]["args"] == ["--dsn", "pg://x"] and ups["db"]["env"]["TOKEN"] == "t0k"


@pytest.mark.parametrize("host, rel, key", [("cursor", ".cursor/mcp.json", "mcpServers"),
                                            ("vscode", ".vscode/mcp.json", "servers")])
def test_env_colon_references(temp_home, tmp_path, run, monkeypatch, host, rel, key):
    project = tmp_path / "site"
    inputs = [{"type": "promptString", "id": "k", "password": True}]
    file = write(project / rel, {key: {
        "search": {"type": "stdio", "command": "npx", "args": ["-y", "srv"], "env": {"KEY": "${env:SEARCH_KEY}"}},
        "remote": {"type": "http", "url": "https://r.example/mcp", "headers": {"X-Key": "k-${env:R_KEY}"}},
        "asks": {"type": "stdio", "command": "x", "env": {"KEY": "${input:k}"}}}, "inputs": inputs})
    monkeypatch.chdir(project)
    failure, text = run("--host", host, "--scope", "project", "--write")
    assert failure is None and "${input:…}" in text and "SEARCH_KEY" in text
    after = json.loads(file.read_text())
    assert after["inputs"] == inputs and list(after[key]) == ["tracetrim", "asks"]
    assert after[key]["tracetrim"]["type"] == "stdio"
    assert after[key]["tracetrim"]["env"] == {"SEARCH_KEY": "${env:SEARCH_KEY}", "R_KEY": "${env:R_KEY}"}
    work = init.default_workdir(init.HOSTS[host], "project", project)[0]
    servers = json.loads((work / "servers.json").read_text())["mcpServers"]
    assert servers["search"]["env"] == {"KEY": "${SEARCH_KEY}"} and servers["remote"]["headers"] == {"X-Key": "k-${R_KEY}"}


def test_claude_desktop_values_are_literal(temp_home, run):
    host = init.HOSTS["claude-desktop"]
    spec = {"command": "npx", "args": ["-y", "srv"], "env": {"KEY": S["ENV"]}}
    file = write(host.user_path(), {"mcpServers": {"plain": spec, "dollar": {"command": "x", "env": {"P": "pa$HOME" + S["ENV"]}},
                                                   "braces": {"command": "x", "args": ["${VAR}"]}}})
    failure, text = run("--host", "claude-desktop", "--write")
    assert failure is None and text.count("a '$' that tracetrim would expand") == 2
    after = json.loads(file.read_text())["mcpServers"]
    assert list(after) == ["tracetrim", "dollar", "braces"]
    assert "type" not in after["tracetrim"] and "env" not in after["tracetrim"]
    servers = temp_home / ".tracetrim" / "claude-desktop" / "servers.json"
    assert json.loads(servers.read_text()) == {"mcpServers": {"plain": spec}}


REFUSED = [
    ("cursor", {"command": "x", "envFile": ".env"}, "envFile"),
    ("cursor", {"url": "https://x.example/mcp", "auth": {"CLIENT_ID": "id", "CLIENT_SECRET": S["ENV"]}}, "auth"),
    ("cursor", {"command": "x", "args": ["${workspaceFolder}/s.py"]}, "${workspaceFolder}"),
    ("cursor", {"command": "x", "env": {"H": "${userHome}/" + S["ENV"]}}, "${userHome}"),
    ("cursor", {"command": "x", "env": {"K": "${" + "API_KEY}"}}, "does not document"),
    ("cursor", {"command": "x", "cwd": "${env:WHERE}"}, "does not document"),
    ("cursor", {"type": "sse", "url": "https://x.example/sse"}, "SSE"),
    ("cursor", {"command": "x", "url": "https://x.example/mcp"}, "exactly one"),
    ("cursor", {"type": "ws", "url": "wss://x.example"}, "'type'"),
    ("cursor", {"type": "http", "command": "x"}, "'type'"),
    ("cursor", {"command": "x", "headers": {"A": S["HEADER"]}}, "together"),
    ("cursor", [S["ENV"]], "not an object"),
    ("vscode", {"type": "stdio", "command": "x", "env": {"N": 3, "K": S["ENV"]}}, "not text"),
    ("vscode", {"type": "stdio", "command": "x", "sandboxEnabled": True}, "sandboxEnabled"),
    ("vscode", {"type": "stdio", "command": "x", "dev": {"watch": "src/**"}}, "dev"),
    ("vscode", {"type": "http", "url": "https://x.example", "oauth": {"clientId": S["ENV"]}}, "oauth"),
    ("vscode", {"type": "stdio", "command": "x", "env": {"K": "${input:" + S["ENV"] + "}"}}, "${input:…}"),
    ("claude-code", {"command": "x", "env": {"K": "$" + "BARE"}}, "does not document"),
    ("claude-code", {"command": "x", "env": {"K": "${env:NAME}"}}, "does not document"),
    ("claude-code", {"command": "x", "args": ["${A}", "${A:-" + S["DEFAULT"] + "}"]}, "different defaults"),
    ("claude-code", {"command": "x", "cwd": "${WHERE}"}, "does not document"),
    ("claude-code", {"type": "http", "url": "https://x.example", "headersHelper": "/bin/" + S["ARG"]}, "headersHelper"),
    ("claude-code", {"command": "x", "timeout": 5000}, "timeout"),
    ("claude-desktop", {"command": "x", "args": ["--pw", "a$b" + S["ARG"]]}, "leaves as it is"),
]


@pytest.mark.parametrize("host, spec, reason", REFUSED, ids=[f"{h}-{r}" for h, _, r in REFUSED])
def test_a_server_tracetrim_cannot_stand_in_for_stays(temp_home, tmp_path, run, host, spec, reason):
    h = init.HOSTS[host]
    config = {h.key: {"good": {"command": "ok", "env": {"K": S["ENV"]}}, "odd": spec}}
    file = write(tmp_path / "proj" / "host.json", config)
    failure, text = run("--host", host, "--file", file, "--write")
    assert failure is None and "Left in the host (1)" in text
    assert reason in next(line for line in text.splitlines() if line.strip().startswith("odd"))
    after = json.loads(file.read_text())[h.key]
    assert list(after) == ["tracetrim", "odd"] and after["odd"] == spec          # untouched


def test_two_servers_that_want_one_variable_differently(temp_home, tmp_path, run):
    file = write(tmp_path / "p" / ".mcp.json", {"mcpServers": {
        "a": {"command": "a", "env": {"K": "${TOKEN:-" + S["DEFAULT"] + "}"}},
        "b": {"command": "b", "env": {"K": "${TOKEN}"}}}})
    failure, text = run("--host", "claude-code", "--file", file, "--write")
    assert failure is None and "another expression" in text
    assert list(json.loads(file.read_text())["mcpServers"]) == ["tracetrim", "b"]


def test_exclude(temp_home, run):
    file = cursor_file(temp_home)
    failure, text = run("--host", "cursor", "--exclude", "billing", "--write")
    assert failure is None and "excluded with --exclude" in text
    assert list(json.loads(file.read_text())["mcpServers"]) == ["notes", "billing", "tracetrim"]
    assert "no server of the file" in run("--host", "cursor", "--exclude", "nobody")[0]


def test_nothing_to_move(temp_home, run):
    file = write(temp_home / ".cursor" / "mcp.json", {"mcpServers": {"notes": {"command": "n", "envFile": ".env"}}})
    before = tree(temp_home)
    failure, text = run("--host", "cursor", "--write")
    assert failure is None and "Nothing to do" in text and tree(temp_home) == before and not backups(file)


# ---------------------------------------------------------------- where the work directory is

def test_one_work_directory_per_project(temp_home, tmp_path):
    host = init.HOSTS["vscode"]
    a, why = init.default_workdir(host, "project", tmp_path / "one" / "app")
    b, _ = init.default_workdir(host, "project", tmp_path / "two" / "app")
    assert a != b and a.parent == b.parent == temp_home / ".tracetrim" / "vscode"
    assert a.name.startswith("app-") and len(a.name) == len("app-") + 8 and "per project" in why
    assert init.default_workdir(init.HOSTS["cursor"], "user", None)[0] == temp_home / ".tracetrim" / "cursor"


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_workdir_in_a_git_repository_is_refused(temp_home, tmp_path, run):
    file = cursor_file(temp_home)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    before = tree(temp_home)
    failure, text = run("--host", "cursor", "--workdir", repo / "tt")
    assert failure is None and "With --write this is refused" in text and "git repository" in text
    failure, text = run("--host", "cursor", "--workdir", repo / "tt", "--write")
    assert "nothing was written" in failure and "Refused" in text
    assert tree(temp_home) == before and not (repo / "tt").exists() and not backups(file)

    (repo / ".gitignore").write_text("ignored/\n")
    assert run("--host", "cursor", "--workdir", repo / "ignored" / "tt", "--write")[0] is None
    assert (repo / "ignored" / "tt" / "servers.json").exists()


def test_force_writes_in_a_git_repository_and_warns_about_a_versioned_mcp_json(temp_home, tmp_path, run):
    cursor_file(temp_home)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / ".mcp.json").write_text("{}\n")
    git(repo, "add", ".mcp.json")
    failure, text = run("--host", "cursor", "--workdir", repo / "tt", "--write", "--force")
    assert failure is None and ".mcp.json is under version control" in text
    assert stat.S_IMODE((repo / "tt" / "servers.json").stat().st_mode) == 0o600


# ---------------------------------------------------------------- HTTP, Claude Code's user scope

def test_http_does_not_rewrite_the_host(temp_home, run):
    file = cursor_file(temp_home)
    original = file.read_bytes()
    failure, text = run("--host", "cursor", "--http", 8799, "--write")
    assert failure is None and file.read_bytes() == original and not backups(file)
    servers = temp_home / ".tracetrim" / "cursor" / "servers.json"
    assert set(json.loads(servers.read_text())["mcpServers"]) == {"billing", "code"}
    assert "--http 8799" in text and "TRACETRIM_TOKEN=CHOOSE_A_SECRET" in text and "billing, code" in text


def test_http_asks_for_the_variables_in_the_shell(temp_home, tmp_path, run):
    file = write(tmp_path / "p" / ".mcp.json", {"mcpServers": {
        "a": {"command": "a", "env": {"K": "${TOKEN}"}},
        "b": {"command": "b", "env": {"K": "${OTHER:-" + S["DEFAULT"] + "}"}}}})
    failure, text = run("--host", "claude-code", "--file", file, "--http", 8799)
    assert failure is None and "set them in the shell" in text and "TOKEN" in text
    assert "no host is there to resolve the default" in text


def test_claude_code_user_scope_is_never_opened(temp_home, run, monkeypatch):
    (temp_home / ".claude.json").write_text('{"mcpServers": {"x": {"command": "x", "env": {"K": "%s"}}}}' % S["ENV"])
    opened = []
    real_open, real_read = os.open, Path.read_bytes
    monkeypatch.setattr(os, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    monkeypatch.setattr(Path, "read_bytes", lambda self: (opened.append(str(self)), real_read(self))[1])
    before = tree(temp_home)
    opened.clear()
    failure, text = run("--host", "claude-code", "--scope", "user", "--write")
    assert failure is None and not any(".claude.json" in p for p in opened)
    assert tree(temp_home) == before
    assert "claude mcp remove NAME --scope user" in text
    assert f"claude mcp add --transport stdio --scope user tracetrim -- {EXE} proxy --servers" in text


def test_scopes_a_host_does_not_have(temp_home, run):
    assert "no user file" in run("--host", "vscode", "--scope", "user")[0]
    assert "no project file" in run("--host", "claude-desktop", "--scope", "project")[0]
    assert "not found" in run("--host", "cursor")[0]
    assert "--host" in run()[0]


# ---------------------------------------------------------------- errors never show a value

def test_invalid_json_gives_a_position_not_the_text(temp_home, run):
    file = temp_home / ".cursor" / "mcp.json"
    file.parent.mkdir()
    file.write_text('{"mcpServers": {\n  // my servers\n  "a": {"command": "x", "env": {"K": "%s"}}}}\n' % S["ENV"])
    before = file.read_bytes()
    failure, _ = run("--host", "cursor", "--write")
    assert "not valid JSON (line 2, column 3)" in failure and file.read_bytes() == before


def test_a_broken_servers_file_and_a_file_of_another_kind(temp_home, run):
    cursor_file(temp_home)
    work = temp_home / ".tracetrim" / "cursor"
    work.mkdir(parents=True)
    (work / "servers.json").write_text('{"mcpServers": {"a": {"env": {"K": "%s"' % S["DISK"])
    assert "servers file" in run("--host", "cursor", "--write")[0]
    write(temp_home / "x.json", {"servers": {"a": {"command": "x", "env": {"K": S["ENV"]}}}})
    assert "'mcpServers'" in run("--host", "cursor", "--file", temp_home / "x.json")[0]


def test_an_unexpected_error_shows_only_its_kind(temp_home, run, monkeypatch):
    cursor_file(temp_home)
    def boom(*a, **k):
        raise ValueError(f"cannot handle {S['ENV']}")
    monkeypatch.setattr(init, "plan", boom)
    failure, _ = run("--host", "cursor")
    assert "unexpected error (ValueError)" in failure


def test_names_that_are_not_names_are_not_shown(temp_home, run):
    file = write(temp_home / ".cursor" / "mcp.json", {"mcpServers": {
        "ok": {"command": "x", "env": {f"K={S['ENV']};": "v"}, "weird\n" + S["ARG"]: 1},
        "remote": {"url": "https://${env:HOST}.example/" + S["PATH"], "headers": {"A": "b"}}}})
    failure, text = run("--host", "cursor")
    assert failure is None and "(name not shown)" in text and "remote" in text
    assert file.exists()


def test_the_real_executable_is_absolute(monkeypatch):
    monkeypatch.undo()
    exe = init.executable()
    assert Path(exe[0]).is_absolute()
