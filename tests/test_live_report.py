"""The live report over HTTP (tracetrim.proxy.live_report, since 1.1): updated in place, never reloaded."""
import asyncio
import os
import time

from tracetrim.proxy import LIVE_SECONDS, live_report, report_stamp

HOSTS = ["127.0.0.1:8000", "localhost:8000"]


def call(handle, path, query="", host="127.0.0.1:8000"):
    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "path": path, "query_string": query.encode(), "headers": [(b"host", host.encode())]}
    handled = asyncio.run(handle(scope, send))
    if not handled:
        return None
    start, body = sent
    return start["status"], dict(start["headers"]), body["body"]


def test_other_paths_are_not_answered(tmp_path):
    handle = live_report(tmp_path, HOSTS, None)
    assert call(handle, "/mcp") is None and call(handle, "/report/other") is None
    assert asyncio.run(handle({"type": "lifespan"}, None)) is False


def test_before_the_first_report_a_placeholder_waits_for_the_stamp(tmp_path):
    handle = live_report(tmp_path, HOSTS, None)
    assert call(handle, "/report/stamp")[2] == b"none"
    status, headers, body = call(handle, "/report")
    assert status == 200 and headers[b"cache-control"] == b"no-store"
    assert b'name="tracetrim-live"' in body and b"/stamp" in body and b"http-equiv" not in body


def test_the_page_carries_the_stamp_and_no_refresh(tmp_path):
    (tmp_path / "report.html").write_text("<!doctype html><html><head><title>t</title></head><body><main>A</main></body></html>")
    handle = live_report(tmp_path, HOSTS, None)
    stamp = call(handle, "/report/stamp")[2].decode()
    status, headers, body = call(handle, "/report")
    assert status == 200 and headers[b"content-type"].startswith(b"text/html")
    assert f'<meta name="tracetrim-live" content="{LIVE_SECONDS}">'.encode() in body
    assert f'<meta name="tracetrim-stamp" content="{stamp}">'.encode() in body
    assert b"refresh" not in body and b"<main>A</main>" in body


def test_the_stamp_changes_when_the_report_is_written_again(tmp_path):
    page = tmp_path / "report.html"
    page.write_text("<html><head></head><body>one</body></html>")
    first = report_stamp(page)
    page.write_text("<html><head></head><body>two, longer</body></html>")
    later = time.time() + 5
    os.utime(page, (later, later))
    assert report_stamp(page) not in (first, "none")
    assert report_stamp(tmp_path / "missing.html") == "none"


def test_host_and_token_are_checked_on_both_paths(tmp_path):
    (tmp_path / "report.html").write_text("<html><head></head><body></body></html>")
    handle = live_report(tmp_path, HOSTS, "s3cret")
    for path in ("/report", "/report/stamp"):
        assert call(handle, path, "token=s3cret", host="evil.example:8000")[0] == 421
        assert call(handle, path)[0] == 401
        assert call(handle, path, "token=wrong")[0] == 401
        assert call(handle, path, "token=s3cret")[0] == 200


def test_the_page_script_updates_in_place():
    """The script the proxy relies on is in every report: it swaps the content and never reloads the page."""
    from tracetrim.report import _JS
    assert "tracetrim-live" in _JS and "/stamp" in _JS and "DOMParser" in _JS and "replaceWith" in _JS
    assert "location.reload" not in _JS and "visibilitychange" in _JS
