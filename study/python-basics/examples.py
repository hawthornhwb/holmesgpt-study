"""Python 阅读练习：只使用本地数据，不导入或启动 HolmesGPT 应用。

在仓库根目录执行：
    poetry run python study/python-basics/examples.py

这里的 Agent、模型和工具都是教学实现，消息协议有所简化。
"""

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class DemoConfig(BaseModel):
    name: str
    max_steps: int = Field(default=5, ge=1)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class DemoDocument(BaseModel):
    url: str


class ExtraConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str


class DemoStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    NO_DATA = "no_data"


class DemoResult(BaseModel):
    status: DemoStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


@dataclass
class ProgressEvent:
    name: str
    message: str = ""


class DemoTool(ABC):
    def __init__(self, name: str):
        self.name = name

    def invoke(self, params: dict[str, Any]) -> DemoResult:
        # 教学版公共包装：把两种参数异常转换成业务错误对象。
        # 真实 Tool.invoke 还有审批、参数规范化和结果转换等其他行为。
        try:
            return self._invoke(params)
        except (ValueError, TypeError) as error:
            return DemoResult(status=DemoStatus.ERROR, error=str(error))

    @abstractmethod
    def _invoke(self, params: dict[str, Any]) -> DemoResult:
        raise NotImplementedError


class LogTool(DemoTool):
    def __init__(self, records: list[dict[str, str]]):
        super().__init__(name="logs")
        self.records = records

    def _invoke(self, params: dict[str, Any]) -> DemoResult:
        level = params.get("level", "ERROR")
        limit = int(params.get("limit", 10))
        if limit < 1:
            raise ValueError("limit 必须大于等于 1")
        matches = [record for record in self.records if record["level"] == level]
        selected = matches[:limit]
        status = DemoStatus.SUCCESS if selected else DemoStatus.NO_DATA
        return DemoResult(status=status, data={"records": selected})


class ScriptedModel:
    """按预设流程工作，并从实际工具结果中读取数量；不调用 LLM。"""

    def completion(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if messages[-1]["role"] == "tool":
            payload = json.loads(messages[-1]["content"])
            count = len(payload["data"]["records"])
            return {"content": f"ERROR 条数：{count}", "tool_calls": []}
        return {
            "content": "先查询日志",
            "tool_calls": [
                {
                    "name": "logs",
                    "arguments": json.dumps({"level": "ERROR", "limit": 10}),
                }
            ],
        }


class DemoAgent:
    def __init__(self, model: ScriptedModel, tool: LogTool, max_steps: int = 3):
        self.model = model
        self.tool = tool
        self.max_steps = max_steps

    def call_stream(
        self, messages: list[dict[str, Any]]
    ) -> Generator[dict[str, Any], None, None]:
        # 复制最外层列表；本例不会修改原有消息字典。
        history = list(messages)
        for _ in range(self.max_steps):
            response = self.model.completion(history)
            history.append({"role": "assistant", **response})
            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                yield {"event": "answer_end", "data": response["content"]}
                return
            for tool_call in tool_calls:
                if tool_call["name"] != self.tool.name:
                    raise ValueError(f"未知工具：{tool_call['name']}")
                yield {"event": "start_tool", "data": self.tool.name}
                params = json.loads(tool_call["arguments"])
                result = self.tool.invoke(params)
                history.append(
                    {"role": "tool", "content": result.model_dump_json()}
                )
                yield {"event": "tool_result", "data": result.model_dump(mode="json")}
        # 教学版到达上限直接抛出异常，与真实 Agent 的收尾策略不同。
        raise RuntimeError("达到教学 Agent 的步数上限")

    def call(self, messages: list[dict[str, Any]]) -> str:
        for event in self.call_stream(messages):
            if event["event"] == "answer_end":
                return event["data"]
        raise RuntimeError("没有收到最终回答")


def expect_error(error_type: type[Exception], action: Callable[[], Any]) -> None:
    """检查调用是否抛出预期类型的异常，不依赖 pytest。"""
    try:
        action()
    except error_type:
        return
    raise AssertionError(f"预期抛出 {error_type.__name__}，实际未抛出")


def basic_values() -> None:
    assert bool("False") is True
    assert (None or "默认值") == "默认值"
    assert {"limit": None}.get("limit", 10) is None
    assert (0 or 10) == 10
    messages = ["user"]
    result = messages.append("assistant")
    assert messages == ["user", "assistant"]
    assert result is None
    options = {"model": None, "enabled": False, "limit": 0, "tags": [], "name": ""}
    filtered = {k: v for k, v in options.items() if v is not None and v != []}
    assert filtered == {"enabled": False, "limit": 0, "name": ""}
    print("1. 真假值与配置过滤：", filtered)


def references() -> None:
    original = [{"content": "旧"}]
    copied = list(original)
    copied[0]["content"] = "新"
    copied.append({"content": "追加"})
    assert original == [{"content": "新"}]
    assert len(copied) == 2
    print("2. 浅复制：原列表里的嵌套字典也变成了", original)


def arguments_and_callbacks() -> None:
    def inspect(*args: Any, **kwargs: Any) -> tuple[tuple, dict]:
        return args, kwargs

    args, kwargs = inspect("logs", limit=3)
    assert args == ("logs",)
    assert kwargs == {"limit": 3}
    document = DemoDocument(**{"url": "https://example.invalid/runbook"})
    assert document.url.endswith("/runbook")
    received = []

    def record(event: ProgressEvent) -> None:
        received.append(event.message)

    callback = record
    assert received == []  # 传递函数本身没有调用它。
    callback(ProgressEvent(name="logs", message="加载完成"))
    assert received == ["加载完成"]
    print("3. 参数解包与回调：", received)


def models() -> None:
    config = DemoConfig.model_validate({"name": "demo", "max_steps": "3"})
    assert config.max_steps == 3
    assert isinstance(config.max_steps, int)
    another = DemoConfig(name="another")
    config.tags.append("logs")
    assert another.tags == []
    expect_error(ValidationError, lambda: DemoConfig(name="demo", max_steps=0))
    expect_error(ValidationError, lambda: DemoConfig.model_validate({}))
    extra = ExtraConfig.model_validate({"name": "demo", "old_name": "retained"})
    assert extra.model_dump()["old_name"] == "retained"
    print("4. Pydantic 校验与默认列表：", config.model_dump())


def generators() -> None:
    trace = []

    def steps() -> Generator[int, None, None]:
        trace.append("A")
        yield 1
        trace.append("B")
        yield 2

    stream = steps()
    assert trace == []  # 创建生成器时还没有执行函数体。
    assert next(stream) == 1
    assert trace == ["A"]
    assert list(stream) == [2]
    assert trace == ["A", "B"]
    assert list(stream) == []
    print("5. 生成器：首次取值后 A，消费剩余项后 B；耗尽后不重复")


def offline_agent() -> None:
    records = [
        {"level": "INFO", "message": "worker started"},
        {"level": "ERROR", "message": "upstream request timed out"},
        {"level": "WARNING", "message": "retry scheduled"},
        {"level": "ERROR", "message": "connection closed before response"},
    ]
    tool = LogTool(records)
    no_data = tool.invoke({"level": "DEBUG"})
    assert no_data.status == DemoStatus.NO_DATA
    assert no_data.data["records"] == []
    limited = tool.invoke({"limit": 1})
    assert len(limited.data["records"]) == 1
    invalid = tool.invoke({"limit": 0})
    assert invalid.status == DemoStatus.ERROR
    assert invalid.error == "limit 必须大于等于 1"
    assert tool.invoke({"limit": "abc"}).status == DemoStatus.ERROR
    model = ScriptedModel()
    agent = DemoAgent(model, tool)
    messages = [{"role": "user", "content": "最近有多少条错误日志？"}]
    events = list(agent.call_stream(messages))
    assert [event["event"] for event in events] == [
        "start_tool", "tool_result", "answer_end"
    ]
    assert events[-1]["data"] == "ERROR 条数：2"
    assert len(events[1]["data"]["data"]["records"]) == 2
    assert agent.call(messages) == "ERROR 条数：2"
    assert messages == [{"role": "user", "content": "最近有多少条错误日志？"}]
    expect_error(
        RuntimeError,
        lambda: list(DemoAgent(model, tool, max_steps=1).call_stream(messages)),
    )
    print("6. 离线 Agent 事件：")
    for event in events:
        print("  ", event["event"], event["data"])


def main() -> None:
    basic_values()
    references()
    arguments_and_callbacks()
    models()
    generators()
    offline_agent()
    print("全部离线练习验证通过。")


if __name__ == "__main__":
    main()
