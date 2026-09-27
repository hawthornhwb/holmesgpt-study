"""Observe the real non-interactive CLI without editing its implementation.

Run from the repository root with: poetry run python -m study.day1.trace_run
Instrumentation is process-local and restored on exit. Each event is flushed
to JSONL and Markdown so a failed/interrupted run still leaves a useful trace.
"""

import argparse
import inspect
import json
import os
import re
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import patch

from pydantic import BaseModel, SecretStr

import holmes.main as cli
from holmes.config import Config
from holmes.core.llm import DefaultLLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import Tool
from holmes.utils.stream import StreamEvents


ROOT = Path(__file__).resolve().parents[2]
QUESTION = "Inspect this repository and identify its main Python entry point"
SECRET_FIELD = re.compile(r"api[_-]?key|password|secret|authorization|access[_-]?token|refresh[_-]?token|approval_token", re.I)
EVENT_LABELS = {
    "run.start": "开始执行真实 CLI",
    "run.end": "CLI 结束",
    "run.error": "CLI 异常或中断",
    "config.start": "加载并合并配置",
    "config.end": "配置加载完成",
    "agent.start": "装配模型与工具执行器",
    "agent.end": "Agent 装配完成",
    "prompt.start": "构造初始提示词",
    "prompt.end": "初始 messages 已生成",
    "llm.request": "发送模型请求",
    "llm.response": "收到模型响应 / 本轮公开说明",
    "llm.error": "模型请求失败",
    "tool.start": "开始执行工具（含审批检查）",
    "tool.end": "工具返回（尚未做大结果落盘裁剪）",
    "tool.error": "工具抛出异常",
    "loop.tool_result": "循环收到工具结果",
    "loop.end": "Agent 循环结束",
    "loop.event": "循环事件",
    "result": "汇总最终结果",
}


def source_of(fn: Callable) -> str:
    """Resolve current source locations, including the user's local edits."""
    fn = inspect.unwrap(fn)
    path = inspect.getsourcefile(fn)
    return f"{path}:{inspect.getsourcelines(fn)[1]}" if path else str(fn)


def json_value(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported trace value: {type(value).__name__}")


class Recorder:
    def __init__(self, directory: Path):
        # Never silently overwrite an earlier experiment.
        directory.mkdir(parents=True, exist_ok=False)
        directory.chmod(0o700)
        self.directory = directory
        self.lock = threading.RLock()
        self.sequence = 0
        self.requests = 0
        self.tool_loops: dict[str, int | None] = {}
        self.timeline: list[dict] = []
        self.started = time.monotonic()
        self.secrets = sorted(
            {v for k, v in os.environ.items() if SECRET_FIELD.search(k) and len(v) >= 8},
            key=len, reverse=True,
        )
        self.events = self._open("events.jsonl")
        self.report = self._open("trace.md")
        self.report.write(
            "# HolmesGPT 本地源码执行记录\n\n"
            "这是一次真实执行产生的记录。按编号从上到下阅读；轮次以 loop 标注，"
            "并发工具用 tool_call_id 对应。请求快照折叠显示，JSONL 保留全部事件。\n\n"
            "公开说明来自 assistant.content；reasoning_content 只记录供应商实际返回的内容，"
            "缺失时标注未提供，不推测隐藏思考。源码位置按本次运行的本地文件计算。\n\n"
        )
        self.report.flush()

    def _open(self, name: str):
        fd = os.open(self.directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        return os.fdopen(fd, "w", encoding="utf-8")

    def clean(self, value: Any) -> Any:
        value = json.loads(json.dumps(value, default=json_value, ensure_ascii=False))

        def redact(item: Any) -> Any:
            if isinstance(item, dict):
                return {k: "[REDACTED]" if SECRET_FIELD.search(k) else redact(v) for k, v in item.items()}
            if isinstance(item, list):
                return [redact(v) for v in item]
            if isinstance(item, str):
                for secret in self.secrets:
                    item = item.replace(secret, "[REDACTED]")
                return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", item)
            return item

        return redact(value)

    def emit(self, event: str, source: str, data: Any, loop: int | None = None):
        with self.lock:
            self.sequence += 1
            entry = self.clean({
                "seq": self.sequence,
                "time": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(time.monotonic() - self.started, 3),
                "event": event, "loop": loop, "source": source, "data": data,
            })
            self.events.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.events.flush()
            suffix = f" · loop {loop}" if loop is not None else ""
            self.report.write(f"## {self.sequence:03d}{suffix} · {EVENT_LABELS.get(event, event)}\n\n")
            self.report.write(f"`{event}` · +{entry['elapsed_s']}s · [源码]({source})\n\n")
            if event == "llm.response":
                self.timeline.append({"loop": loop, **entry["data"]})
                for label, field in [("模型公开说明 / 答案", "content"), ("供应商返回的 reasoning_content", "reasoning_content")]:
                    self.report.write(f"**{label}**\n\n")
                    self._block(entry["data"].get(field) or "（本轮未提供）", "text")
            if event in {"tool.end", "loop.tool_result"}:
                result = entry["data"]["result"]
                self.report.write("**工具输出（保留换行）**\n\n")
                output = result.get("data")
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False, indent=2)
                self._block(output, "text")
                if result.get("error"):
                    self._block(result["error"], "text")
            if event in {"llm.request", "prompt.end"}:
                self.report.write("<details>\n<summary>展开完整请求 / 提示词快照</summary>\n\n")
            self._block(json.dumps(entry["data"], ensure_ascii=False, indent=2), "json")
            if event in {"llm.request", "prompt.end"}:
                self.report.write("</details>\n\n")
            self.report.flush()

    def _block(self, text: str, language: str):
        # Tool output may itself contain Markdown fences.
        fence = "`" * max(4, 1 + max((len(m[0]) for m in re.finditer(r"`+", text)), default=0))
        self.report.write(f"{fence}{language}\n{text}\n{fence}\n\n")

    def save_result(self, result: Any):
        with self._open("result.json") as output:
            json.dump(self.clean(result), output, ensure_ascii=False, indent=2)

    def close(self):
        self.report.write("## 轮次速览\n\n| loop | 模型请求编号 | 工具请求数 | API finish_reason | 下一步 |\n| --- | --- | --- | --- | --- |\n")
        for row in self.timeline:
            self.report.write(f"| {row['loop']} | {row['request']} | {len(row['tool_calls'])} | {row['finish_reason']} | {row['next']} |\n")
        self.report.close()
        self.events.close()
        self._write_walkthrough()

    def _write_walkthrough(self):
        """Render a short reading route from the already-redacted event journal."""
        entries = [json.loads(line) for line in (self.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        with self._open("walkthrough.md") as output:
            def block(value: Any):
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
                fence = "`" * max(4, 1 + max((len(m[0]) for m in re.finditer(r"`+", text)), default=0))
                output.write(f"{fence}text\n{text}\n{fence}\n\n")

            output.write(
                "# 按轮次阅读这次真实执行\n\n"
                "先读各轮公开说明，再看工具输入与输出，最后看下一轮如何使用结果。"
                "reasoning_content 是 API 实际返回的原文，折叠展示；未提供时明确标注。"
                "这是新增了工作目录和简短行动说明提示的学习实验，不是原始 Day 1 基线的逐字重放。\n\n"
                "完整请求、工具 Schema、原始事件见 [events.jsonl](events.jsonl)；"
                "详细时间线见 [trace.md](trace.md)。下文编号对应 JSONL 中的 seq。\n\n"
                "## 启动链路\n\n"
            )
            for entry in entries:
                if entry["event"] in {"run.start", "config.end", "agent.end", "prompt.end"}:
                    output.write(f"- #{entry['seq']} {EVENT_LABELS[entry['event']]}：[源码]({entry['source']})\n")
                    if entry["event"] == "run.start":
                        block(entry["data"])
            for response in [e for e in entries if e["event"] == "llm.response"]:
                loop, data = response["loop"], response["data"]
                output.write(f"\n## Loop {loop} · 模型请求 {data['request']}\n\n")
                output.write(f"#{response['seq']} · API 耗时 {data['duration_s']} 秒 · [模型调用源码]({response['source']})\n\n")
                requests = [e for e in entries if e["event"] == "llm.request" and e["data"]["request"] == data["request"]]
                if requests:
                    request = requests[0]
                    args = request["data"]
                    messages = args.get("messages", args.get("kwargs", {}).get("messages", []))
                    output.write(f"请求快照：#{request['seq']}，本轮携带 {len(messages)} 条历史消息。完整内容见 JSONL。\n\n")
                output.write("**公开说明 / 回答**\n\n")
                block(data.get("content") or "（本轮未提供）")
                output.write("<details>\n<summary>供应商返回的 reasoning_content 原文</summary>\n\n")
                block(data.get("reasoning_content") or "（本轮未提供，不补写或猜测）")
                output.write("</details>\n\n")
                for call in data["tool_calls"]:
                    output.write(f"### 工具 {call['function']['name']} · {call['id']}\n\n")
                    block(call["function"]["arguments"])
                    raw = [e for e in entries if e["event"] == "tool.end" and e["data"]["tool_call_id"] == call["id"]]
                    final = [e for e in entries if e["event"] == "loop.tool_result" and e["data"]["tool_call_id"] == call["id"]]
                    if not final:
                        output.write("没有收到循环层工具结果；检查异常事件。\n\n")
                        continue
                    entry = final[-1]
                    result = entry["data"]["result"]
                    output.write(f"#{entry['seq']} · 状态 `{result['status']}` · 返回码 `{result.get('return_code')}` · [循环源码]({entry['source']})\n\n")
                    if result.get("error"):
                        block(result["error"])
                    if result.get("data"):
                        block(result["data"])
                    if raw and raw[-1]["data"]["result"].get("data") != result.get("data"):
                        output.write(f"工具原始数据见事件 #{raw[-1]['seq']}（循环前可能经过序列化或大结果裁剪）。\n\n")
                output.write(f"**循环分支：** {data['next']}；API finish_reason=`{data['finish_reason']}`。\n\n")
            output.write("## 本次结束状态\n\n")
            for entry in entries:
                if entry["event"] in {"result", "run.error", "llm.error", "run.end"}:
                    output.write(f"#{entry['seq']} `{entry['event']}`\n\n")
                    block(entry["data"])


@contextmanager
def instrument(recorder: Recorder) -> Iterator[None]:
    """Install scoped wrappers; all original return values/exceptions pass through."""
    with ExitStack() as stack:
        def hook(owner: Any, name: str, factory: Callable):
            original = getattr(owner, name)
            wrapped = wraps(original)(factory(original, source_of(original)))
            stack.enter_context(patch.object(owner, name, wrapped))

        def stage(event: str, summarize: Callable):
            def factory(original: Callable, source: str):
                def wrapper(*args, **kwargs):
                    recorder.emit(event + ".start", source, {})
                    result = original(*args, **kwargs)
                    recorder.emit(event + ".end", source, summarize(result))
                    return result
                return wrapper
            return factory

        hook(Config, "load_from_file", stage("config", lambda c: {"model": c.model, "max_steps": c.max_steps}))
        hook(Config, "create_toolcalling_llm", stage("agent", lambda a: {
            "model": a.llm.model, "max_steps": a.max_steps,
            "enabled_toolsets": [t.name for t in a.tool_executor.enabled_toolsets],
            "tools": list(a.tool_executor.tools_by_name),
        }))
        hook(cli, "build_initial_ask_messages", stage("prompt", lambda messages: {"messages": messages}))

        def completion(original: Callable, source: str):
            def wrapper(self, *args, **kwargs):
                # i is the actual core loop counter. Auxiliary summarization calls
                # have loop=None and are not misreported as agent iterations.
                caller = inspect.currentframe().f_back
                loop = caller.f_locals.get("i") if caller.f_code.co_name == "call_stream" else None
                del caller
                bound = inspect.signature(original).bind(self, *args, **kwargs)
                with recorder.lock:
                    recorder.requests += 1
                    request = recorder.requests
                recorder.emit("llm.request", source, {
                    "request": request, "model": self.model,
                    **{k: v for k, v in bound.arguments.items() if k != "self"},
                }, loop)
                started = time.monotonic()
                try:
                    response = original(self, *args, **kwargs)
                except BaseException as exc:
                    recorder.emit("llm.error", source, {"request": request, "error": str(exc), "type": type(exc).__name__}, loop)
                    raise
                choice = response.choices[0]
                message = choice.message
                tool_calls = [t.model_dump(mode="json") for t in (message.tool_calls or [])]
                with recorder.lock:
                    for tool_call in tool_calls:
                        recorder.tool_loops[tool_call["id"]] = loop
                recorder.emit("llm.response", source, {
                    "request": request, "duration_s": round(time.monotonic() - started, 3),
                    "content": message.content,
                    "reasoning_content": getattr(message, "reasoning_content", None),
                    "tool_calls": tool_calls,
                    "finish_reason": choice.finish_reason,
                    "usage": response.usage,
                    "next": "执行工具，再携带结果进入下一轮" if tool_calls else "无工具请求，将由循环处理结束",
                }, loop)
                return response
            return wrapper

        def invoke(original: Callable, source: str):
            def wrapper(self, params, context):
                loop = recorder.tool_loops.get(context.tool_call_id)
                identity = {"tool": self.name, "tool_call_id": context.tool_call_id}
                recorder.emit("tool.start", source, {**identity, "params": params, "implementation": source_of(self._invoke)}, loop)
                try:
                    result = original(self, params, context)
                except BaseException as exc:
                    recorder.emit("tool.error", source, {**identity, "error": str(exc)}, loop)
                    raise
                recorder.emit("tool.end", source, {**identity, "result": result}, loop)
                return result
            return wrapper

        def stream(original: Callable, source: str):
            def wrapper(self, *args, **kwargs):
                for event in original(self, *args, **kwargs):
                    data = event.data
                    if event.event == StreamEvents.ANSWER_END:
                        recorder.emit("loop.end", source, {
                            "num_llm_calls": data.get("num_llm_calls"),
                            "content": data.get("content"),
                            "metadata": data.get("metadata"), "costs": data.get("costs"),
                        }, data.get("num_llm_calls"))
                    elif event.event == StreamEvents.TOOL_RESULT:
                        recorder.emit("loop.tool_result", source, data, recorder.tool_loops.get(data.get("tool_call_id")))
                    elif event.event not in {StreamEvents.AI_MESSAGE, StreamEvents.START_TOOL, StreamEvents.TOKEN_COUNT}:
                        recorder.emit("loop.event", source, {"event": event.event, "data": data})
                    yield event
            return wrapper

        def call(original: Callable, source: str):
            def wrapper(self, *args, **kwargs):
                result = original(self, *args, **kwargs)
                recorder.save_result(result)
                recorder.emit("result", source, {
                    "num_llm_calls": result.num_llm_calls,
                    "tool_calls": len(result.tool_calls or []),
                    "total_tokens": result.total_tokens,
                    "finish_reason": result.finish_reason,
                    "result": result.result,
                })
                return result
            return wrapper

        hook(DefaultLLM, "completion", completion)
        hook(Tool, "invoke", invoke)
        hook(ToolCallingLLM, "call_stream", stream)
        hook(ToolCallingLLM, "call", call)
        yield


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default=QUESTION)
    parser.add_argument("--model", default="deepseek/deepseek-flash")
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    directory = args.output_dir or ROOT / "study/day1/runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    recorder = Recorder(directory.resolve())
    cli_args = [
        "holmes", "ask", args.question, "--model", args.model,
        "--no-interactive", "--max-steps", str(args.max_steps),
        "--system-prompt-additions",
        f"This is a repository-reading exercise. The repository is {ROOT}. "
        "Read only source files inside this repository. Do not read study/, .git/, "
        "credentials, environment variables, the home directory or sibling projects. "
        "Use the available read-only tools; keep outputs bounded. "
        "Before each tool batch, provide a brief user-facing description in Chinese "
        "of what evidence you are checking. This is an action summary, not private reasoning. "
        "Give the final answer in Chinese with source evidence.",
    ]
    source = source_of(cli.run)
    exit_code = 0
    try:
        recorder.emit("run.start", source, {"argv": cli_args, "cwd": str(Path.cwd()), "trace_dir": recorder.directory})
        with instrument(recorder), patch.object(sys, "argv", cli_args):
            cli.run()
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
        if exit_code:
            recorder.emit("run.error", source, {"type": "SystemExit", "exit_code": exit_code})
    except (Exception, KeyboardInterrupt) as exc:
        exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
        recorder.emit("run.error", source, {"type": type(exc).__name__, "error": str(exc)})
    finally:
        recorder.emit("run.end", source, {"exit_code": exit_code})
        recorder.close()
    print(f"\n先读：{recorder.directory / 'walkthrough.md'}")
    print(f"完整时间线：{recorder.directory / 'trace.md'}")
    print(f"结构化事件：{recorder.directory / 'events.jsonl'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
