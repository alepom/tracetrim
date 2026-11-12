"""`tracetrim init`: regressions of the review of 0.7. Same rules as test_init.py:
every value of the user's files is a recognisable secret, and no output may show one."""
import io
import json
import ntpath
import os
import stat
import sys
from pathlib import Path

import pytest

from tracetrim import init
from tracetrim.cli import main
from test_init import EXE, S, backups, cursor_config, cursor_file, fixed_executable, git, run, tree, write  # noqa: F401

HOST_FILES = {"claude-desktop": "claude_desktop_config.json", "claude-code": ".mcp.json",
              "cursor": ".cursor/mcp.json", "vscode": ".vscode/mcp.json"}


def host_file(tmp_path, host, servers) -> Path:
    return write(tmp_path / "proj" / HOST_FILES[host], {init.HOSTS[host].key: servers})


# ---------------------------------------------------------------- 1. an existing entry is shown without its values

@pytest.mark.parametrize("host", list(HOST_FILES))
def test_an_existing_entry_shows_only_what_init_writes(temp_home, tmp_path, run, host):
    work = tmp_path / "work"
    entry = {"type": "stdio", "command": f"/opt/{S['PATH']}/bin/tracetrim",
             "args": ["proxy", "--servers", str(work / "servers.json"), "--auto", str(work),
                      "--log-client", S["ARG"], f"--token={S['ARG']}"],
             "env": {"OLD": S["ENV"], "R": "${V:-" + S["DEFAULT"] + "}"},
             "cwd": f"/srv/{S['CWD']}", "envFile": f"/{S['PATH']}/.env",
             "auth": {"CLIENT_SECRET": S["HEADER"], "nested": [{"deep": S["QUERY"]}]}}
    file = host_file(tmp_path, host, {"tracetrim": entry, "new": {"command": "srv", "env": {"K": S["DISK"]}}})
    failure, text = run("--host", host, "--file", file)
    assert failure is None and "Host entry" in text
    shown = text[text.index("Host entry"):]
    for key in ("cwd", "envFile", "auth", "OLD", "--log-client"):
        assert key in shown
    assert f'"command": "{init.MASK}/tracetrim"' in shown and "${V:-" + init.MASK + "}" in shown
    failure, text = run("--host", host, "--file", file, "--write")
    assert failure is None
    after = json.loads(file.read_text())[init.HOSTS[host].key]["tracetrim"]
    assert {k: v for k, v in after.items() if k != "env"} == {k: v for k, v in entry.items() if k != "env"}


# ---------------------------------------------------------------- 2. a terminal that is not UTF-8

def test_a_terminal_that_is_not_utf8_still_gets_the_backup(temp_home, monkeypatch):
    config = cursor_config()
    config["mcpServers"]["asks"] = {"command": "x", "env": {"K": "${input:k}"}}       # a reason with '…'
    file = write(temp_home / ".cursor" / "mcp.json", config)
    out = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    main(["init", "--host", "cursor", "--write"])
    out.flush()
    text = out.buffer.getvalue().decode("ascii")
    (backup,) = backups(file)
    assert str(backup) in text and "Authorization=****" in text and "--undo" in text
    assert not any(v in text for v in S.values())


def test_a_failure_after_writing_still_names_the_backup(temp_home, run, monkeypatch):
    file = cursor_file(temp_home)
    monkeypatch.setattr(init, "closing", lambda *a: (_ for _ in ()).throw(OSError(S["ENV"])))
    failure, _ = run("--host", "cursor", "--write")
    (backup,) = backups(file)
    assert str(backup) in failure and "--undo" in failure


# ---------------------------------------------------------------- 3. a servers file that is a whole client's file

def test_a_servers_file_with_more_than_servers_is_refused(temp_home, tmp_path, run):
    client = write(tmp_path / "client.json", {"mcpServers": {"old": {"command": "o", "env": {"K": S["DISK"]}}},
                                              "theme": S["ENV"], "telemetry": {"id": S["ARG"]}})
    client.chmod(0o644)
    file = host_file(tmp_path, "cursor", {
        "tracetrim": {"command": EXE, "args": ["proxy", "--servers", str(client), "--auto", str(tmp_path / "w")]},
        "new": {"command": "n", "env": {"K": S["ENV"]}}})
    before = tree(tmp_path)
    failure, _ = run("--host", "cursor", "--file", file, "--write")
    assert "more than an 'mcpServers'" in failure and "telemetry" in failure and "theme" in failure
    assert tree(tmp_path) == before and stat.S_IMODE(client.stat().st_mode) == 0o644


# ---------------------------------------------------------------- 4. re-init after undo

def test_reinit_after_undo_does_not_bring_back_removed_servers(temp_home, run):
    config = cursor_config()
    config["mcpServers"]["billing"]["headers"] = {"Authorization": "Bearer ${env:BILL_TOKEN}"}
    file = write(temp_home / ".cursor" / "mcp.json", config)
    assert run("--host", "cursor", "--write")[0] is None
    (backup,) = backups(file)
    assert run("--undo", backup)[0] is None
    data = json.loads(file.read_text())
    del data["mcpServers"]["billing"]                    # the user removes a server from the host
    write(file, data)
    before = tree(temp_home)
    failure, text = run("--host", "cursor", "--write")
    assert "nothing was written" in failure and "billing" in text and "--force" in text
    assert tree(temp_home) == before

    assert run("--host", "cursor", "--write", "--force")[0] is None      # kept on purpose: its reference is passed
    entry = json.loads(file.read_text())["mcpServers"]["tracetrim"]
    assert entry["env"] == {"BILL_TOKEN": "${env:BILL_TOKEN}"}
    servers = json.loads((temp_home / ".tracetrim" / "cursor" / "servers.json").read_text())["mcpServers"]
    assert set(servers) == {"billing", "code"}


# ---------------------------------------------------------------- 5. other forms of a tracetrim entry

@pytest.mark.parametrize("args", [
    lambda s, w: ["proxy", f"--servers={s}", f"--auto={w}"],
    lambda s, w: ["-m", "tracetrim.cli", "proxy", f"--servers={s}", "--auto", str(w)]])
def test_a_servers_equals_entry_is_not_wrapped_twice(temp_home, tmp_path, run, args):
    servers, work = tmp_path / "w" / "servers.json", tmp_path / "w"
    command = EXE if args(servers, work)[0] == "proxy" else sys.executable
    file = host_file(tmp_path, "cursor", {"tt": {"command": command, "args": args(servers, work)},
                                          "new": {"command": "n", "env": {"K": S["ENV"]}}})
    failure, text = run("--host", "cursor", "--file", file, "--write")
    assert failure is None and "not wrapped a second time" in text
    assert list(json.loads(file.read_text())["mcpServers"]) == ["tt"]
    assert set(json.loads(servers.read_text())["mcpServers"]) == {"new"}


def test_a_tracetrim_proxy_without_servers_stays_as_it_is(temp_home, tmp_path, run):
    readme = {"command": "tracetrim", "args": ["proxy", "--upstream-url", f"https://x.example/{S['PATH']}"]}
    file = host_file(tmp_path, "cursor", {"tt-remote": readme, "new": {"command": "n", "env": {"K": S["ENV"]}}})
    failure, text = run("--host", "cursor", "--file", file, "--write")
    assert failure is None and "without --servers" in next(l for l in text.splitlines() if "tt-remote" in l)
    after = json.loads(file.read_text())["mcpServers"]
    assert after["tt-remote"] == readme and list(after) == ["tt-remote", "tracetrim"]
    servers = Path(after["tracetrim"]["args"][after["tracetrim"]["args"].index("--servers") + 1])
    assert set(json.loads(servers.read_text())["mcpServers"]) == {"new"}


# ---------------------------------------------------------------- 6. the git check looks at the file written

def test_the_git_check_looks_at_the_servers_file_written(temp_home, tmp_path, run):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    file = host_file(tmp_path, "cursor", {
        "tracetrim": {"command": EXE, "args": ["proxy", "--servers", str(repo / "servers.json"),
                                               "--auto", str(tmp_path / "work")]},
        "new": {"command": "n", "env": {"K": S["ENV"]}}})
    before = tree(tmp_path)
    failure, text = run("--host", "cursor", "--file", file, "--write")
    assert "nothing was written" in failure and "git repository" in text and str(repo / "servers.json") in text
    assert tree(tmp_path) == before
    (repo / ".gitignore").write_text("servers.json\n")
    assert run("--host", "cursor", "--file", file, "--write")[0] is None
    assert (repo / "servers.json").exists()


# ---------------------------------------------------------------- 7. Windows: what ntpath.expandvars would change

NT_VALUES = ["plain", "pa$$", "p@$$!", "100%%", "a%%b", "x$$-y", "$-x", "%X%", "a%20b%20c", "it's", "$", "x$",
             "100%", "${X}", "pre-${X}-post", "'${X}'", "a$${X}", "%a${X}b%", "${X}%", "$X", "${X:-d}"]


@pytest.mark.parametrize("host", ["claude-desktop", "claude-code", "cursor"])
def test_on_windows_every_accepted_value_survives_expansion(monkeypatch, host):
    monkeypatch.setattr(init, "_NT", True)
    env = {"X": "val", "-x": "dash", "20b": "space", "a${X}b": "q"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    h, accepted = init.HOSTS[host], []
    for raw in NT_VALUES:
        value = raw.replace("${X}", "${env:X}") if host == "cursor" else raw
        try:
            out, _ = init._translate(h, {"command": "x", "args": [value]}, False)
        except init.Refuse:
            continue
        accepted.append(raw)
        # What the host passes (it resolves its own references), then what the proxy makes of it.
        meant = (init._CC.sub(lambda m: env[m.group(1)], value) if host == "claude-code" else
                 init._ENV.sub(lambda m: env[m.group(1)], value) if host == "cursor" else value)
        got = ntpath.expandvars(out["args"][0])
        assert got == meant, (raw, got, meant)
    assert "plain" in accepted and "100%" in accepted and "it's" in accepted
    assert not {"pa$$", "a%%b", "x$$-y", "a$${X}", "'${X}'"} & set(accepted)
    if host != "claude-desktop":
        assert "${X}" in accepted and "pre-${X}-post" in accepted


# ---------------------------------------------------------------- 8. a file saved while init runs

@pytest.mark.parametrize("which", ["host", "servers"])
def test_a_file_saved_while_init_runs_stops_everything(temp_home, run, monkeypatch, which):
    file = cursor_file(temp_home)
    servers = temp_home / ".tracetrim" / "cursor" / "servers.json"
    real = init.git_problems

    def meanwhile(*a, **k):                              # after the plan has read the files, before apply
        if which == "host":
            data = json.loads(file.read_text())
            data["preferences"] = {"edited": "concurrently"}
            write(file, data)
        else:
            write(servers, {"mcpServers": {"other": {"command": "o", "env": {"K": S["DISK"]}}}})
        return real(*a, **k)
    monkeypatch.setattr(init, "git_problems", meanwhile)
    failure, _ = run("--host", "cursor", "--write")
    assert "changed while init was running" in failure and "Nothing was written" in failure
    monkeypatch.setattr(init, "git_problems", real)
    assert not backups(file)
    if which == "host":
        assert "preferences" in json.loads(file.read_text()) and not servers.exists()
    else:
        assert set(json.loads(servers.read_text())["mcpServers"]) == {"other"}
        assert "tracetrim" not in json.loads(file.read_text())["mcpServers"]


# ---------------------------------------------------------------- 9. a corrupt note and --undo --force

def test_undo_force_with_a_corrupt_note(temp_home, run):
    file = cursor_file(temp_home)
    original = file.read_bytes()
    run("--host", "cursor", "--write")
    (backup,) = backups(file)
    backup.with_name(backup.name + ".json").write_text('{"written_sha256": "' + S["DISK"])
    failure, _ = run("--undo", backup)
    assert "note of the backup" in failure and file.read_bytes() != original
    assert run("--undo", backup, "--force")[0] is None and file.read_bytes() == original


# ---------------------------------------------------------------- 10. --http 0

@pytest.mark.parametrize("port", ["0", "65536", "-1", "x"])
def test_http_takes_a_real_port_only(temp_home, run, port):
    cursor_file(temp_home)
    before = tree(temp_home)
    failure, text = run("--host", "cursor", "--http", port, "--write")
    assert failure == "2" and "between 1 and 65535" in text and tree(temp_home) == before


# ---------------------------------------------------------------- 11. null fields

def test_null_fields_leave_that_server_in_the_host(temp_home, run):
    config = cursor_config()
    config["mcpServers"].update({"n-env": {"command": "x", "env": None}, "n-args": {"command": "y", "args": None},
                                 "n-head": {"url": "https://x.example/mcp", "headers": None},
                                 "n-type": {"type": None, "command": "z"}})
    file = write(temp_home / ".cursor" / "mcp.json", config)
    failure, text = run("--host", "cursor", "--write")
    assert failure is None and "Left in the host (5)" in text
    for n in ("n-env", "n-args", "n-head", "n-type"):
        assert "null" in next(l for l in text.splitlines() if l.strip().startswith(n + " "))
    after = json.loads(file.read_text())["mcpServers"]
    assert {n: after[n] for n in ("n-env", "n-args", "n-head", "n-type")} == \
        {n: config["mcpServers"][n] for n in ("n-env", "n-args", "n-head", "n-type")}
    servers = json.loads((temp_home / ".tracetrim" / "cursor" / "servers.json").read_text())["mcpServers"]
    assert set(servers) == {"billing", "code"}


# ---------------------------------------------------------------- 12. ~/.claude.json with --file

@pytest.mark.parametrize("host, link", [("claude-code", False), ("cursor", False), ("claude-code", True)])
def test_claude_json_is_never_opened_even_with_file(temp_home, run, monkeypatch, host, link):
    state = temp_home / ".claude.json"
    state.write_text('{"mcpServers": {"x": {"command": "x", "env": {"K": "%s"}}}}' % S["ENV"])
    given = state
    if link:
        given = temp_home / "servers-link.json"
        given.symlink_to(state)
    opened = []
    real_open, real_read = os.open, Path.read_bytes
    monkeypatch.setattr(os, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    monkeypatch.setattr(Path, "read_bytes", lambda self: (opened.append(str(self)), real_read(self))[1])
    before = tree(temp_home)
    opened.clear()
    failure, _ = run("--host", host, "--file", given, "--write")
    assert "Claude Code's own state file" in failure and "claude mcp" in failure
    assert not opened and tree(temp_home) == before


# ---------------------------------------------------------------- 13. the temporary file of _write

def test_write_never_follows_or_reuses_a_temporary_name(tmp_path, monkeypatch):
    target, victim = tmp_path / "servers.json", tmp_path / "victim"
    victim.write_text("untouched")
    monkeypatch.setattr(init.secrets, "token_hex", lambda n: "fixed")
    trap = tmp_path / ".servers.json.tracetrim-tmp-fixed"
    trap.symlink_to(victim)
    with pytest.raises(FileExistsError):
        init._write(target, b"{}", 0o600)
    assert victim.read_text() == "untouched" and trap.is_symlink() and not target.exists()


def test_write_is_atomic_with_the_mode_asked_for(tmp_path):
    tmp_path = tmp_path / "d"
    tmp_path.mkdir()
    target = tmp_path / "f.json"
    old = os.umask(0o077)
    try:
        init._write(target, b"one", 0o644)
        init._write(target, b"two", 0o600)
        init._write(tmp_path / "g.json", b"x", 0o644)
    finally:
        os.umask(old)
    assert target.read_bytes() == b"two" and stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "g.json").stat().st_mode) == 0o644
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.json", "g.json"]
