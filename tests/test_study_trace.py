"""Exercise the study recorder with the real agent loop and local test tools."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from litellm import ModelResponse

from holmes.core.llm import DefaultLLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import ApprovalRequirement, StructuredToolResult, StructuredToolResultStatus, Tool, ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from study.day1 import trace_run
from study.day1.trace_run import Recorder, instrument
from tests.mocks.toolset_mocks import SampleToolset
from tests.test_tool_calling_llm import DEFAULT_TOKEN_COUNT, _make_context_limiter_passthrough


class EvidenceTool(Tool):
    name: str = "evidence"
    description: str = "Read test evidence"
    reject: bool = False

    def _invoke(self, params, context):
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data="line one\nline two", params=params)

    def get_parameterized_one_liner(self, params):
        return "Read test evidence"

    def requires_approval(self, params, context):
        if self.reject:
            return ApprovalRequirement(needs_approval=True, reason="Test needs approval")
        return None


def read_events(directory: Path):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("reject", [False, True])
def test_real_loop_records_parallel_calls_requests_reasoning_and_approval(tmp_path, monkeypatch, reject):
    calls = [{"id": name, "type": "function", "function": {"name": "evidence", "arguments": json.dumps({"file": name})}} for name in ["tc_a", "tc_b"]]
    responses = iter([
        ModelResponse(choices=[{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "Checking evidence", "reasoning_content": "provider supplied text", "tool_calls": calls,
        }}], usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}),
        ModelResponse(choices=[{"finish_reason": "stop", "message": {"role": "assistant", "content": "Finished"}}],
                      usage={"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}),
    ])

    def completion(self, **kwargs):
        return next(responses)

    monkeypatch.setattr(DefaultLLM, "completion", completion)
    monkeypatch.setattr(DefaultLLM, "count_tokens", lambda *a, **kw: DEFAULT_TOKEN_COUNT)
    monkeypatch.setattr(DefaultLLM, "get_max_token_count_for_single_tool", lambda self: 10000)
    monkeypatch.setattr("holmes.core.tool_calling_llm.compact_if_necessary", _make_context_limiter_passthrough)
    monkeypatch.setattr("holmes.core.tool_calling_llm.check_compaction_needed", lambda *a: None)
    monkeypatch.setattr("holmes.core.tool_calling_llm.spill_oversized_tool_result", lambda **kw: None)
    llm = object.__new__(DefaultLLM)
    llm.model = "test-model"
    toolset = SampleToolset()
    toolset.tools = [EvidenceTool(reject=reject)]
    toolset.status = ToolsetStatusEnum.ENABLED
    ai = ToolCallingLLM(ToolExecutor([toolset]), 4, llm, tool_results_dir=None)
    recorder = Recorder(tmp_path / "trace")
    original_invoke = Tool.invoke
    try:
        with instrument(recorder):
            result = ai.call([{"role": "user", "content": "Find evidence"}])
    finally:
        recorder.close()
    assert Tool.invoke is original_invoke
    assert result.result == "Finished"
    assert result.num_llm_calls == 2
    assert len(result.tool_calls) == 2
    events = read_events(recorder.directory)
    requests = [e for e in events if e["event"] == "llm.request"]
    answers = [e for e in events if e["event"] == "llm.response"]
    assert [e["loop"] for e in answers] == [1, 2]
    assert answers[0]["data"]["reasoning_content"] == "provider supplied text"
    assert answers[1]["data"]["reasoning_content"] is None
    # Request snapshots must not mutate after the loop appends new messages.
    assert len(requests[0]["data"]["kwargs"]["messages"]) == 1
    assert len(requests[1]["data"]["kwargs"]["messages"]) == 4
    tools = [e for e in events if e["event"] == "tool.end"]
    assert {e["data"]["tool_call_id"] for e in tools} == {"tc_a", "tc_b"}
    assert all(e["loop"] == 1 for e in tools)
    final_tools = [e for e in events if e["event"] == "loop.tool_result"]
    if reject:
        assert all(e["data"]["result"]["status"] == "approval_required" for e in tools)
        assert all(e["data"]["result"]["status"] == "error" for e in final_tools)
        assert "NOT executed" in final_tools[0]["data"]["result"]["error"]
    else:
        assert all(e["data"]["result"]["data"] == "line one\nline two" for e in final_tools)
    stored = json.loads((recorder.directory / "result.json").read_text())
    assert stored["num_llm_calls"] == 2
    assert "line one\nline two" in (recorder.directory / "trace.md").read_text() or reject
    walkthrough = (recorder.directory / "walkthrough.md").read_text()
    assert "Loop 1" in walkthrough and "Loop 2" in walkthrough
    assert "provider supplied text" in walkthrough
    assert "Checking evidence" in walkthrough


def test_redaction_thread_safety_and_no_overwrite(tmp_path, monkeypatch):
    secret = "not-a-standard-key-format-123456"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    recorder = Recorder(tmp_path / "trace")
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: recorder.emit("test", "source.py:1", {
                "content": f"echo {secret}", "api_key": "short", "value": i,
            }), range(32)))
        # Events are readable before close (flush-on-event).
        assert len(read_events(recorder.directory)) == 32
        with pytest.raises(FileExistsError):
            Recorder(recorder.directory)
    finally:
        recorder.close()
    events = read_events(recorder.directory)
    assert [e["seq"] for e in events] == list(range(1, 33))
    for file in recorder.directory.iterdir():
        assert secret not in file.read_text()
        assert file.stat().st_mode & 0o777 == 0o600
    assert all(e["data"]["api_key"] == "[REDACTED]" for e in events)


def test_failure_keeps_events_and_restores_methods(tmp_path, monkeypatch):
    def failed_cli():
        raise RuntimeError("failed intentionally")

    monkeypatch.setattr(trace_run.cli, "run", failed_cli)
    monkeypatch.setattr("sys.argv", ["trace_run", "--output-dir", str(tmp_path / "failed")])
    original = DefaultLLM.completion
    assert trace_run.main() == 1
    assert DefaultLLM.completion is original
    events = read_events(tmp_path / "failed")
    assert events[-2]["event"] == "run.error"
    assert events[-1]["data"]["exit_code"] == 1
