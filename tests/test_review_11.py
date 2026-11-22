"""Regression tests for the review of 1.1: one test (or more) per finding, each failing before its fix."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
import billing  # noqa: E402


# -- proxy -------------------------------------------------------------------------------

def _http(handler, path="/mcp", headers=()):
    sent, called = [], []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}

    scope = {"type": "http", "path": path, "query_string": b"", "headers": list(headers)}
    asyncio.run(handler(scope, receive, send))
    return sent


def test_an_unauthorised_request_gets_401_not_a_crash():
    from tracetrim.proxy import guard
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["path"])
    h = guard(app, None, "s3cret")
    sent = _http(h)
    assert sent[0]["status"] == 401 and not reached
    _http(h, headers=[(b"authorization", b"Bearer s3cret")])
    assert reached == ["/mcp"]


def test_the_live_report_accepts_a_host_with_any_port(tmp_path):
    from tracetrim.proxy import live_report
    (tmp_path / "report.html").write_text("<html><head></head><body></body></html>")
    h = live_report(tmp_path, ["tunnel.example:*"], None)
    sent = []

    async def send(m):
        sent.append(m)
    asyncio.run(h({"type": "http", "path": "/report/stamp", "query_string": b"",
                   "headers": [(b"host", b"tunnel.example:443")]}, send))
    assert sent[0]["status"] == 200


def test_a_tool_text_that_looks_like_our_line_does_not_break_the_log():
    from tracetrim.proxy import Live
    text = "log line 1\n[trimmed] by the upstream server\n"
    Live(quiet=True).call("t", text, text)              # raised IndexError before


def test_a_new_policy_is_installed_only_when_asked(tmp_path):
    from tracetrim.auto import AutoTrim
    tt = AutoTrim(tmp_path, min_runs=3)
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.learner.done(billing.answer(i))              # closings without relearning
    eng = tt.engine
    before = eng.policy
    new = eng.learn_policy()
    assert new is not None and new is not before and eng.policy is before     # learnt, not yet in place
    assert eng.install(new) and eng.policy is new
    assert not eng.install(None) and eng.policy is new


# -- learning rules (1.1, `--learning 1.0` to restore) ------------------------------------

def _trace(prompt, output, answer):
    from tracetrim.traces import Event, Trace
    t = Trace("r.jsonl")
    t.events = [Event("prompt", text=prompt), Event("call", text="{}", tool="t", output=json.dumps(output)),
                Event("message", text=answer)]
    return t


def test_negative_numbers_are_found_with_the_rules_of_1_1():
    from tracetrim.usage import analyse, learning_flags
    t = _trace("", {"change": -1234, "delta": -0.4567, "pos": 1234}, "change was -1234 and delta -0.4567, pos 1234")
    assert analyse(t, **learning_flags("1.1"))[0].used == {"change", "delta", "pos"}
    assert analyse(t, **learning_flags("1.0"))[0].used == {"pos"}          # the rules of 1.0, kept for the studies
    assert analyse(t)[0].used == {"pos"}                                    # and the default of `analyse`


def test_a_long_text_quoted_in_part_is_reused_even_if_a_piece_was_in_the_request():
    from tracetrim.usage import analyse, learning_flags
    snippet = ("Best coffee shops in Rome: Sciascia Caffe on Via Fabio Massimo roasts its own beans since 1919 "
               "and serves the city's best espresso")
    t = _trace("List the best coffee shops in Rome", {"snippet": snippet},
               "Sciascia Caffe on Via Fabio Massimo roasts its own beans.")
    assert analyse(t, **learning_flags("1.1"))[0].used == {"snippet"}
    assert analyse(t, **learning_flags("1.0"))[0].used == set()


def test_the_report_measures_as_run_time_writes():
    from tracetrim import report as rp
    from tracetrim.traces import Event, Trace
    from tracetrim.usage import analyse, learning_flags
    traces = []
    for i in range(2):
        # Fields that are all kept (an id and a title): nothing to remove, the JSON already compact.
        items = [{"id": f"x{i}{n}", "title": f"title number {n} of run {i}"} for n in range(30)]
        t = Trace(f"r{i}.jsonl")
        t.events = [Event("call", text="{}", tool="t", output=json.dumps(items, separators=(",", ":"))),
                    Event("message", text="ok")]
        traces.append(t)
    usages = [analyse(t, **learning_flags("1.1")) for t in traces]
    d_10 = rp.compute([t.name for t in traces], usages)
    d_11 = rp.compute([t.name for t in traces], usages, runtime=True)
    assert d_10["totals"]["loo_prudent"] > d_10["totals"]["raw"]           # 1.0: spaces added, a "saving" below zero
    assert d_11["totals"]["loo_prudent"] <= d_11["totals"]["raw"]
    page = rp.render_html(d_10, rp.suggestions(d_10))
    assert "−-" not in page and "+" in page.split('class="hv">', 1)[1][:3]


def test_the_learning_switch_is_recorded_and_checked(tmp_path):
    from tracetrim.auto import AutoTrim, Learner
    from tracetrim.cli import main
    tt = AutoTrim(tmp_path, min_runs=3, learning="1.0")
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    assert json.loads((tmp_path / "report.config.json").read_text())["auto"]["learning"] == "1.0"
    with pytest.raises(ValueError):
        Learner(tmp_path / "x", learning="0.9")
    with pytest.raises(SystemExit):
        main(["proxy", "--upstream-cmd", "true", "--learning", "1.0"])


# -- the learner -------------------------------------------------------------------------------

@pytest.mark.parametrize("durable", [True, False])
def test_a_failure_after_the_trace_is_written_keeps_the_counts(tmp_path, monkeypatch, durable):
    """File after file (`durable_closes=False`, the writes of 1.3): the run is recorded with what did not fail. Through
    the journal (1.4, the default), an I/O error on the evidence fails the closing as a whole: the run stays open with
    its counts and notes, and the retry records all of them (REVISIONE-14b-decies: recorded without its evidence,
    the run lost it for good)."""
    from tracetrim import evidence
    from tracetrim.auto import AutoTrim
    tt = AutoTrim(tmp_path, min_runs=3)
    tt.learner.durable_closes = durable
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    sent = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    tt.expand({"call_id": sent.rsplit("call_id=", 1)[1].split()[0], "fields": ["data[].footer"]})

    def full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(evidence, "save", full)
    with pytest.raises(OSError):
        tt.done(billing.answer(3))
    st = json.loads((tmp_path / "state.json").read_text())
    if durable:
        assert st["runs"] == 3 and tt.learner.events                     # still open
        monkeypatch.undo()
        tt.done(billing.answer(3))                                       # the retry
        st = json.loads((tmp_path / "state.json").read_text())
        fields = json.loads((tmp_path / "evidence.json").read_text())["tools"][billing.TOOL]["fields"]
        assert any("run-0004" in f.get("requested", []) for f in fields.values())
    assert st["runs"] == 4 and st["complete_runs"] == 4
    assert "run-0004" in json.loads((tmp_path / "savings.json").read_text())["runs"]


def test_unreadable_files_of_an_old_work_directory_do_not_stop_learning(tmp_path):
    from tracetrim.auto import AutoTrim
    tt = AutoTrim(tmp_path, min_runs=3)
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    (tmp_path / "traces" / "run-0002.jsonl").write_text('{"kind": "call", "tool": "x", "outp')     # cut short
    tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    tt.done(billing.answer(3))                                                   # relearns, does not raise
    assert (tmp_path / "traces" / "run-0002.jsonl.corrupt").exists()
    (tmp_path / "report.config.json").write_text('{"format": "tracetrim/con')
    AutoTrim(tmp_path, min_runs=3)                                               # starts


def test_an_expand_after_the_closing_counts_for_the_run_just_closed(tmp_path):
    from tracetrim.auto import AutoTrim
    tt = AutoTrim(tmp_path, min_runs=3)
    for i in range(3):
        tt.output(billing.TOOL, billing.arguments(i), billing.output(i))
        tt.done(billing.answer(i))
    a = tt.output(billing.TOOL, billing.arguments(3), billing.output(3))
    b = tt.output(billing.TOOL, billing.arguments(2), billing.output(2))
    cid = lambda s: s.rsplit("call_id=", 1)[1].split()[0]                      # noqa: E731
    tt.expand({"call_id": cid(a), "fields": ["data[].footer"]})
    tt.done(billing.answer(3))                                                   # run 4 closed (too early)
    tt.expand({"call_id": cid(b), "fields": ["data[].footer"]})                  # same task, after the closing
    tt.output(billing.TOOL, billing.arguments(1), billing.output(1))
    tt.done(billing.answer(1))                                                   # run 5: no expand in it
    assert tt.learner.state["expand_runs"].get(billing.TOOL) == ["run-0004"]


def test_turns_numbered_from_one_read_like_turns_from_zero():
    from tracetrim.traces import Event, Trace
    from tracetrim.turns import organisation

    def chain(start):
        t = Trace("r.jsonl")
        t.events = [Event("prompt", text="find the invoice of Ada", turn=-1),
                    Event("call", text='{"q": "Ada"}', tool="search", output='{"id": "cus_X9k2"}', turn=start),
                    Event("call", text='{"customer": "cus_X9k2"}', tool="customer", output='{"inv": "in_77Qz"}',
                          turn=start + 1),
                    Event("call", text='{"invoice": "in_77Qz"}', tool="invoice", output='{"total": 2900}',
                          turn=start + 2)]
        return organisation(t)
    assert chain(0) == chain(1) and chain(1)["calls_that_could_have_been_made_earlier"] == 0


# -- the check on the answer -------------------------------------------------------------------

@pytest.mark.parametrize("output, answer", [
    ({"cost": 29.456}, "29,46 EUR"), ({"ctr": 0.04567}, "CTR 4.57%"), ({"ctr": 0.1234}, "CTR 12.3%"),
    ({"avg_position": 3.14159}, "position 3.14"), ({"v": 12130456}, "about 12.000.000"),
    ({"v": 1213456}, "about 1.200.000"), ({"c": -1234}, "-1234 EUR"), ({"c": -1234.5}, "-1234,5"),
])
def test_roundings_and_negatives_are_supported(output, answer):
    from tracetrim.grounding import unsupported
    assert unsupported(_trace("", output, answer))[1] == []


# -- report, traces, command line --------------------------------------------------------------

def test_a_report_with_no_tool_output_shows_no_saving(tmp_path):
    from tracetrim.cli import main
    for i in range(2):
        (tmp_path / f"r{i}.jsonl").write_text(json.dumps({"kind": "message", "text": "hello"}) + "\n")
    main(["report", str(tmp_path / "r0.jsonl"), str(tmp_path / "r1.jsonl"), "-o", str(tmp_path / "o.html")])
    d = json.loads((tmp_path / "o.json").read_text())
    assert not [s for s in d["suggestions"] if s["rule"] == "escapes"]
    assert '<div class="hv">—</div>' in (tmp_path / "o.html").read_text()


def test_error_null_is_not_an_error():
    from tracetrim.report import _is_error
    from tracetrim.usage import CallUsage
    assert not _is_error(CallUsage("t", '{"result": 5, "error": null}', {"result": 5, "error": None}))
    assert _is_error(CallUsage("t", '{"error": "bad key"}', {"error": "bad key"}))


def test_an_output_written_as_json_is_read(tmp_path):
    from tracetrim.traces import load
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps({"kind": "call", "tool": "t", "arguments": {}, "output": {"a": 12345}}) + "\n")
    assert json.loads(load(p).calls[0].output) == {"a": 12345}


def test_identifiers_in_lists_of_lists_are_identifiers():
    from tracetrim.projection import identifying, record_level
    assert identifying("ids[][]") is False and identifying("id[][]") and identifying("rows[][].id")
    assert record_level("names[][]") is False and record_level("x[][].name")


def test_report_refuses_an_html_name_ending_in_json(tmp_path):
    from tracetrim.cli import main
    (tmp_path / "r.jsonl").write_text(json.dumps({"kind": "message", "text": "x"}) + "\n")
    with pytest.raises(SystemExit):
        main(["report", str(tmp_path / "r.jsonl"), "-o", str(tmp_path / "report.json")])


# -- tracetrim init ------------------------------------------------------------------------------

def _host(tmp_path, servers) -> Path:
    f = tmp_path / "proj" / ".cursor" / "mcp.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")
    return f


def _init(*argv):
    from tracetrim.cli import main
    try:
        main(["init", "--host", "cursor", *map(str, argv)])
        return None
    except SystemExit as e:
        return str(e.code)


@pytest.mark.parametrize("command, args", [
    ("uvx", ["tracetrim", "proxy", "--servers", "/abs/servers.json"]),
    ("cmd", ["/c", "tracetrim", "proxy", "--servers", "/abs/servers.json"]),
    ("/usr/bin/python3", ["/venv/bin/tracetrim", "proxy", "--servers", "/abs/servers.json"]),
    ("python", ["-m", "tracetrim.cli", "proxy", "--servers", "/abs/servers.json"]),
    ("uvx", ["tracetrim@1.1.0", "proxy"]),
])
def test_a_tracetrim_entry_behind_a_launcher_is_recognised(command, args):
    from tracetrim.init import _ours
    assert _ours({"command": command, "args": args}) is not None
    assert _ours({"command": "uvx", "args": ["other-server", "proxy"]}) is None


def test_an_existing_entry_with_a_relative_servers_path_is_refused(tmp_path, capsys):
    f = _host(tmp_path, {"tracetrim": {"command": "tracetrim", "args": ["proxy", "--servers", "servers.json",
                                                                        "--auto", "tt-data"]},
                         "b": {"command": "node", "args": ["b.js"]}})
    msg = _init("--file", f, "--write")
    assert msg and "not absolute" in msg and not (Path.cwd() / "servers.json").exists()


def test_a_url_ending_in_sse_with_no_type_stays_in_the_host(tmp_path, capsys):
    f = _host(tmp_path, {"live": {"url": "https://x.example/sse", "headers": {"A": "b"}},
                         "b": {"command": "node", "args": ["b.js"]}})
    assert _init("--file", f, "--write") is None
    servers = json.loads(f.read_text())["mcpServers"]
    assert "live" in servers and "b" not in servers


@pytest.mark.parametrize("bad", [{"env": []}, {"args": False}, {"args": {}}])
def test_a_field_of_the_wrong_kind_leaves_the_server_in_the_host(tmp_path, capsys, bad):
    f = _host(tmp_path, {"odd": {"command": "node", "args": ["x.js"], **bad}, "b": {"command": "node", "args": ["b.js"]}})
    assert _init("--file", f, "--write") is None
    assert "odd" in json.loads(f.read_text())["mcpServers"]


def test_if_the_host_file_cannot_be_written_the_servers_file_goes_back(tmp_path, monkeypatch, capsys):
    from tracetrim import init
    f = _host(tmp_path, {"b": {"command": "node", "args": ["b.js"]}})
    real = init._write

    def write(path, data, mode):
        if Path(path) == f:
            raise PermissionError("locked")
        return real(path, data, mode)
    monkeypatch.setattr(init, "_write", write)
    msg = _init("--file", f, "--write", "--workdir", tmp_path / "wd")
    assert msg and "unexpected error" in msg
    assert not (tmp_path / "wd" / "servers.json").exists()                      # no server both here and there
    assert "b" in json.loads(f.read_text())["mcpServers"]


def test_stopping_the_check_still_says_how_to_undo(tmp_path, monkeypatch, capsys):
    from tracetrim import init
    f = _host(tmp_path, {"b": {"command": "node", "args": ["b.js"]}})

    def stopped(p, timeout=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(init, "check", stopped)
    msg = _init("--file", f, "--write", "--workdir", tmp_path / "wd")
    out = capsys.readouterr().out
    assert msg and "interrupted" in msg and "--undo" in msg
    assert "Backup" in out or "backup" in out.lower()                            # the summary came before the check


@pytest.mark.real_check
def test_the_check_does_not_start_an_oauth_login(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    f = _host(tmp_path, {"remote": {"url": "https://mcp.example/mcp"}})
    msg = _init("--file", f, "--write", "--workdir", tmp_path / "wd")
    out = capsys.readouterr().out
    assert msg is None and "skipped" in out and "OAuth" in out


# -- the proxy stops its upstream servers on SIGTERM ------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_stops_the_upstream_servers(tmp_path):
    import os
    import signal
    import subprocess
    import time
    pidfile = tmp_path / "upstream.pid"
    upstream = tmp_path / "upstream.py"
    # An upstream still stuck in its start (waiting on a network, a database): it does not read its input, so
    # the proxy's end alone does not stop it. Until 1.0 it outlived a proxy stopped with SIGTERM.
    upstream.write_text(
        "import os, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n")
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    proxy = subprocess.Popen([sys.executable, "-m", "tracetrim.cli", "proxy", "--quiet",
                              "--upstream-cmd", f"{sys.executable} {upstream}"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        init_msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
        proxy.stdin.write((json.dumps(init_msg) + "\n").encode())
        proxy.stdin.flush()
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.1)
        pid = int(pidfile.read_text())
        proxy.send_signal(signal.SIGTERM)
        proxy.wait(timeout=20)
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
            pytest.fail("the upstream server outlived the proxy")
    finally:
        if proxy.poll() is None:
            proxy.kill()


def test_every_table_scrolls_inside_its_card_and_long_lists_wrap(tmp_path):
    """Found after 1.1.0: the table of values no tool returned sat outside the scroll container and kept every
    value on one line, so ten identifiers widened the whole page. Every table is now inside `.scroll`."""
    import re
    from tracetrim.cli import main
    ids = " ".join(f"W43912345{n:02d}" for n in range(12))
    for i in range(3):
        lines = [{"kind": "call", "tool": billing.TOOL, "arguments": billing.arguments(i), "output": billing.output(i)},
                 {"kind": "message", "text": billing.answer(i) + (f" Sources: {ids}." if i == 2 else "")}]
        (tmp_path / f"r{i}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    out = tmp_path / "report.html"
    main(["report", *map(str, sorted(tmp_path.glob("r*.jsonl"))), "-o", str(out)])
    page = out.read_text()
    assert page.count("<table") == len(re.findall(r'<div class="scroll"><table', page)) > 0
    assert '<td class="vals">' in page and "td.vals { white-space:normal" in page
