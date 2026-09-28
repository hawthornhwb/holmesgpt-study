"""Check trace association, interrupted journals, offline escaping and local routes."""

import json
import stat
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from study.trace_viewer import build_trace, export_run, load_trace, read_events, render_html
from study.trace_viewer.__main__ import available_runs, make_handler


def event(kind: str, data: dict, loop: int | None = None) -> dict:
    return {"event": kind, "data": data, "loop": loop, "seq": 1, "elapsed_s": 1}


def call(call_id: str) -> dict:
    return {"id": call_id, "function": {"name": "bash", "arguments": '{"command":"pwd"}'}}


def test_parallel_tools_reused_ids_and_auxiliary_requests():
    entries = [
        event("llm.request", {"request": 1, "messages": [{"role": "user", "content": "Find evidence"}]}, 1),
        event("llm.response", {"request": 1, "tool_calls": [call("a"), call("b")]}, 1),
        event("tool.end", {"tool_call_id": "b", "result": {"status": "success", "data": "B"}}, 1),
        event("tool.end", {"tool_call_id": "a", "result": {"status": "approval_required", "data": "raw"}}, 1),
        event("loop.tool_result", {"tool_call_id": "a", "result": {"status": "error", "data": "denied"}}, 1),
        event("llm.request", {"request": 2, "kwargs": {"messages": []}}),
        event("llm.response", {"request": 2, "tool_calls": []}),
        event("llm.request", {"request": 3, "kwargs": {"messages": []}}, 2),
        event("llm.response", {"request": 3, "tool_calls": [call("a")]}, 2),
        event("loop.tool_result", {"tool_call_id": "a", "result": {"status": "success", "data": "new"}}, 2),
    ]
    trace = build_trace(entries, "test")
    first = trace["rounds"][0]["tools"]
    assert first[0]["raw"]["data"]["result"]["data"] == "raw"
    assert first[0]["final"]["data"]["result"]["data"] == "denied"
    assert first[1]["raw"]["data"]["result"]["data"] == "B"
    assert trace["rounds"][2]["tools"][0]["final"]["data"]["result"]["data"] == "new"
    assert trace["summary"]["round_count"] == 2
    assert trace["summary"]["auxiliary_count"] == 1
    assert trace["summary"]["tool_errors"] == 1
    assert trace["summary"]["question"] == "Find evidence"
    assert trace["summary"]["status"] == "incomplete"
    assert sum(len(row["events"]) for row in trace["rounds"]) == len(entries)


def test_truncated_and_invalid_lines_keep_prior_events(tmp_path):
    path = tmp_path / "events.jsonl"
    entries = [event("llm.request", {"request": 1}, 1), event("llm.error", {"request": 1, "error": "timed out"}, 1)]
    path.write_text(json.dumps(entries[0]) + '\n{"bad":"shape"}\n' + json.dumps(entries[1]) + '\n{"unfinished":', encoding="utf-8")
    events, warnings = read_events(path)
    assert events == entries
    assert len(warnings) == 2
    trace = load_trace(path)
    assert trace["rounds"][0]["response"] is None
    assert trace["rounds"][0]["error"]["data"]["error"] == "timed out"
    assert trace["warnings"] == warnings


@pytest.mark.parametrize("end,status", [(None, "incomplete"), (0, "finished"), (1, "failed"), (130, "failed")])
def test_exit_status_is_separate_from_answer(end, status):
    entries = [event("result", {"result": "answer"})]
    if end is not None:
        entries.append(event("run.end", {"exit_code": end}))
    trace = build_trace(entries, "test")
    assert trace["answer"] == "answer"
    assert trace["summary"]["status"] == status


def test_tool_exception_and_pending_call_are_visible():
    entries = [event("llm.response", {"request": 1, "tool_calls": [call("a"), call("b")]}, 1),
               event("tool.error", {"tool_call_id": "a", "error": "boom"}, 1)]
    trace = build_trace(entries, "test")
    assert [tool["status"] for tool in trace["rounds"][0]["tools"]] == ["error", "pending"]
    assert trace["errors"] == [entries[1]]


def test_offline_html_escapes_untrusted_content_and_never_overwrites(tmp_path):
    malicious = '</script><script>window.TRACE_INJECTED=1</script><img src="https://example.com/secret">'
    entries = [event("result", {"result": malicious})]
    (tmp_path / "events.jsonl").write_text(json.dumps(entries[0]) + "\n", encoding="utf-8")
    target = export_run(tmp_path)
    document = target.read_text(encoding="utf-8")
    assert malicious not in document
    payload = document.split('<script id="trace-data" type="application/json">', 1)[1].split('</script>', 1)[0]
    assert json.loads(payload)["answer"] == malicious
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "/* VIEWER_JS */" not in document
    assert "/* VIEWER_CSS */" not in document
    with pytest.raises(FileExistsError):
        export_run(tmp_path)
    with pytest.raises(FileExistsError):
        export_run(tmp_path, tmp_path / "events.jsonl")
    assert json.loads((tmp_path / "events.jsonl").read_text()) == entries[0]


def test_empty_journal_and_export_failure(tmp_path):
    assert build_trace([], "empty")["summary"]["round_count"] == 0
    with pytest.raises(FileNotFoundError):
        export_run(tmp_path)
    assert not (tmp_path / "trace.html").exists()
    assert "TRACE_DATA_PLACEHOLDER" not in render_html()


def test_run_listing_excludes_symlinks_outside_root(tmp_path):
    root = tmp_path / "runs"
    run = root / "good"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text("", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "events.jsonl").write_text("secret", encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    assert list(available_runs(root)) == ["good"]
    assert list(available_runs(run)) == ["good"]


def test_server_routes_are_read_only_and_scoped(tmp_path):
    run = tmp_path / "record"
    run.mkdir()
    (run / "events.jsonl").write_text(json.dumps(event("run.end", {"exit_code": 0})), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def fetch(path: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    try:
        status, body, headers = fetch("/")
        assert status == 200
        assert b"TRACE_DATA_PLACEHOLDER" not in body
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert json.loads(fetch("/api/runs")[1])[0]["name"] == "record"
        assert json.loads(fetch("/api/trace?run=record")[1])["summary"]["status"] == "finished"
        assert fetch("/export?run=record")[2]["Content-Disposition"].startswith("attachment;")
        assert fetch("/api/trace?run=../outside")[0] == 404
        assert fetch("/../../pyproject.toml")[0] == 404
        assert fetch("/", {"Host": "untrusted.example"})[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
