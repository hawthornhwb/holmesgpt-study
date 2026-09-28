"""Dependency-free, local viewer for study trace journals."""

import json
import os
from pathlib import Path
from typing import Any


ASSETS = Path(__file__).parent
DEFAULT_RUNS = ASSETS.parent / "day1" / "runs"


def read_events(path: Path) -> tuple[list[dict], list[str]]:
    """Keep valid records, including when an interrupted writer leaves a partial line."""
    events: list[dict] = []
    warnings: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("event"), str) or not isinstance(event.get("data"), dict):
                raise ValueError("expected an event with an object data field")
        except (ValueError, TypeError) as error:
            warnings.append(f"第 {number} 行无法读取（可能尚未写完）：{error}")
            continue
        events.append(event)
    return events, warnings


def request_args(event: dict | None) -> dict:
    data = (event or {}).get("data", {})
    return data.get("kwargs", data)


def build_trace(events: list[dict], name: str, warnings: list[str] | None = None) -> dict:
    """Associate tools by call ID within each request, preserving raw/final results."""
    rounds: list[dict] = []
    requests: dict[Any, dict] = {}
    active_tools: dict[tuple[Any, Any], dict] = {}
    assigned: set[int] = set()

    def get_round(event: dict) -> dict:
        key = event["data"].get("request")
        if key not in requests:
            row = {"loop": event.get("loop"), "request_id": key, "request": None,
                   "response": None, "error": None, "tools": [], "events": []}
            requests[key] = row
            rounds.append(row)
        return requests[key]

    for index, event in enumerate(events):
        kind, data = event["event"], event["data"]
        if kind in {"llm.request", "llm.response", "llm.error"}:
            row = get_round(event)
            row[{"llm.request": "request", "llm.response": "response", "llm.error": "error"}[kind]] = event
            row["events"].append(event)
            assigned.add(index)
            if kind == "llm.response":
                for call in data.get("tool_calls") or []:
                    tool = {"call": call, "start": None, "raw": None, "final": None, "error": None}
                    row["tools"].append(tool)
                    active_tools[(event.get("loop"), call.get("id"))] = {"tool": tool, "round": row}
        elif kind in {"tool.start", "tool.end", "tool.error", "loop.tool_result"}:
            match = active_tools.get((event.get("loop"), data.get("tool_call_id")))
            if match:
                field = {"tool.start": "start", "tool.end": "raw", "tool.error": "error", "loop.tool_result": "final"}[kind]
                match["tool"][field] = event
                match["round"]["events"].append(event)
                assigned.add(index)

    def last_data(kind: str) -> dict:
        return next((e["data"] for e in reversed(events) if e["event"] == kind), {})

    start = last_data("run.start")
    argv = start.get("argv", [])
    question = argv[2] if len(argv) > 2 and argv[1] == "ask" else ""
    if not question:
        initial = last_data("prompt.end").get("messages", [])
        if not initial and rounds:
            initial = request_args(rounds[0]["request"]).get("messages", [])
        question = next((m.get("content", "") for m in initial if m.get("role") == "user"), "（日志未记录问题）")
    ended = any(e["event"] == "run.end" for e in events)
    failed = bool(last_data("run.end").get("exit_code")) or any(e["event"] == "run.error" for e in events)
    result = last_data("result")
    errors = [e for e in events if e["event"] in {"run.error", "llm.error", "tool.error"}]
    tools = [tool for row in rounds for tool in row["tools"]]
    for tool in tools:
        final = tool["final"] or tool["raw"]
        tool["status"] = "error" if tool["error"] else (final or {}).get("data", {}).get("result", {}).get("status", "pending")
    summary = {
        "name": name, "question": question,
        "model": last_data("agent.end").get("model") or last_data("llm.request").get("model", "未知模型"),
        "status": "failed" if failed else ("finished" if ended else "incomplete"),
        "exit_code": last_data("run.end").get("exit_code"),
        "started": events[0].get("time") if events else None,
        "duration_s": events[-1].get("elapsed_s", 0) if events else 0,
        "round_count": sum(row["loop"] is not None for row in rounds),
        "auxiliary_count": sum(row["loop"] is None for row in rounds),
        "tool_count": len(tools), "event_count": len(events),
        "tool_errors": sum(t["status"] in {"error", "approval_required"} for t in tools),
        "total_tokens": result.get("total_tokens"),
    }
    return {"summary": summary, "rounds": rounds, "warnings": warnings or [], "errors": errors,
            "answer": result.get("result") or last_data("loop.end").get("content"),
            "other_events": [e for i, e in enumerate(events) if i not in assigned]}


def load_trace(path: Path) -> dict:
    events, warnings = read_events(path)
    return build_trace(events, path.parent.name, warnings)


def render_html(trace: dict | None = None) -> str:
    # Escaping '<' prevents tool output containing </script> from escaping the data block.
    payload = json.dumps(trace, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    template = (ASSETS / "index.html").read_text(encoding="utf-8")
    return template.replace("/* VIEWER_CSS */", (ASSETS / "viewer.css").read_text(encoding="utf-8")).replace(
        "/* VIEWER_JS */", (ASSETS / "viewer.js").read_text(encoding="utf-8")
    ).replace("TRACE_DATA_PLACEHOLDER", payload)


def export_run(directory: Path, output: Path | None = None) -> Path:
    """Export an offline snapshot without loading Holmes or making any model calls."""
    target = output or directory / "trace.html"
    # Do not overwrite an earlier export or any source journal.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(render_html(load_trace(directory / "events.jsonl")))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target
