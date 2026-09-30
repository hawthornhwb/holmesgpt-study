# Day 2：追踪一次工具调用的数据变化

状态：已规划，待学习与实验。Day 1 已完成。

Day 1 你已经理解了整体链路。Day 2 的目标是：**能拿着一条工具调用，说明模型返回了什么、程序如何执行、结果如何成为下一轮的输入，并用测试核对。**

预计 90–120 分钟。今天只按这份文档往下走，完成后直接填写文末学习记录。

## 1. 从真实记录选一条调用（15–20 分钟）

沿用 Day 1 的 Trace 查看方式。选择一次成功读取 `pyproject.toml` 的 `bash` 调用；旧记录在 [Loop 2 所在的运行导读](../day1/runs/20260923-225409-379209/walkthrough.md)。

抄下以下内容，不需要抄整份日志：

| 要找的内容 | 为什么要找 |
| --- | --- |
| 本轮 loop 编号 | 确定属于哪一次模型决策 |
| `tool_calls[].id` | 后续结果靠它与请求对应 |
| `function.name` | Holmes 按这个名字找到工具对象 |
| `function.arguments` | 模型给出的参数；在响应中通常是 JSON 字符串 |
| 工具结果中的 `status` 和一行关键 `data` | 判断是否执行成功、得到了什么证据 |
| 下一轮对入口函数的检查 | 看新证据如何推动后续动作 |

先预测：下一轮请求中，除了最初的 system/user 消息，还应该出现哪些消息？我的预测：此前的消息历史、包含 `tool_calls` 的 `assistant` 消息，以及对应的 `tool` 回复。其中工具回复的 `role` 是 `tool`，`name` 是 `bash`，不是 `tool/bash` 这个角色。然后在同一运行的 `events.jsonl` 中找到下一条 `llm.request`，核对其 `messages`。已有 Trace 查看器能展开请求时，也可以直接在那里查看。



运行记录：20260923-225207-748892

| 本轮 loop 编号                          | 02                                                           |
| --------------------------------------- | ------------------------------------------------------------ |
| `tool_calls[].id`                       | call_01_RbyPHgW8G81xZzId28XS8130                             |
| `function.name`                         | bash                                                         |
| `function.arguments`                    | {\"command\": \"grep -n -A 8 -i \\\"scripts\\\\|entry\\\" /Users/weibo/Project/holmesgpt/pyproject.toml\", \"suggested_prefixes\": [\"grep\"], \"timeout\": 30} |
| 工具结果中的 `status` 和一行关键 `data` | "status": "success",<br />[tool.poetry.scripts] holmes = "holmes.main:run" |
| 下一轮对入口函数的检查                  | 入口点已找到：`holmes = "holmes.main:run"`。接下来核实 `holmes/main.py` 的 `run` 函数与 `__main__` 守卫，并查看 Dockerfile 的启动命令。 |

### 下一轮请求中的证据核对

记录文件：[本次运行的 events.jsonl](../day1/runs/20260923-225207-748892/events.jsonl)。以下数组下标均从 0 开始。

| 位置 | 核对结果 |
| --- | --- |
| 第 17 行，Loop 02 的 `llm.response`，`data.tool_calls[1].id` | 模型返回的调用 ID 为 `call_01_RbyPHgW8G81xZzId28XS8130` |
| 第 24 行，Loop 02 的 `tool.end`，`data.tool_call_id` | 工具执行结果使用同一个 ID；`data.result.status` 为 `success` |
| 第 27 行，Loop 03 的 `llm.request`，`data.messages[5].tool_calls[1].id` | 下一轮请求保留了上一轮 assistant 的工具调用请求，ID 不变 |
| 同一行，`data.messages[8]` | `role` 为 `tool`，`name` 为 `bash`，`tool_call_id` 与上述 ID 相同；`content` 包含 `10-holmes = "holmes.main:run"` |

Loop 03 请求中实际的消息顺序是：

```text
system → user
→ assistant（Loop 01 的工具调用请求）→ tool × 2
→ assistant（Loop 02 的工具调用请求）→ tool × 3
```

因此，下一轮会带上此前的历史，而不只是最近的一条工具结果。一条 assistant 消息可以请求多个工具，工具回复通过 `tool_call_id` 与对应调用的 `id` 匹配，不能只靠回复顺序判断。

表格中“下一轮对入口函数的检查”来自第 28 行的 Loop 03 `llm.response`，是模型收到证据后的回复；第 27 行的 `llm.request` 才是证明上一轮工具输出已经进入下一轮输入的位置。

本节验收：已核对同一个调用 ID 在模型调用请求、工具执行结果和下一轮请求中的位置，并确认下一轮的工具消息包含入口点证据。

完成标准：能够指出同一个调用 ID 在请求和工具回复中的位置，并在下一轮请求中找到该工具结果。



## 2. 沿着这条数据读源码（30–40 分钟）

只阅读下面五处。行号会随源码修改而变化，优先搜索函数名。

| 顺序 | 文件 / 函数 | 阅读时回答的问题 |
| --- | --- | --- |
| 1 | [`tool_calling_llm.py`](../../holmes/core/tool_calling_llm.py) / `call_stream()` | 模型响应在哪里追加到 `messages`？哪里判断是否有 `tool_calls`？ |
| 2 | 同文件 / `_invoke_llm_tool_call()` | `function.arguments` 怎样经 `json.loads()` 变为 Python 字典？调用 ID 怎样被保留？ |
| 3 | 同文件 / `_directly_invoke_tool_call()` | 怎么按名称找到工具，并调用 `tool.invoke()`？ |
| 4 | [`models.py`](../../holmes/core/models.py) / `ToolCallResult.to_llm_message()` | 为什么回复包含 `role="tool"`、`tool_call_id`、`name` 和 `content`？ |
| 5 | 回到 `call_stream()` | 工具回复在哪一行追加到历史？下一轮 `completion(messages=...)` 为什么会带上它？ |

可以把数据变化记成这一行：

```text
模型返回的工具请求 → 解析参数 → 查找并调用工具 → ToolCallResult → role=tool 的消息 → 下一轮 messages
```

特别区分三个东西：

- **工具 Schema**：给模型看的工具说明与参数规范。
- **工具调用请求**：模型返回的具体工具名、参数和调用 ID。
- **工具调用结果**：Holmes 执行后得到的数据或错误信息。

还有一处容易混淆：`call()` 里的外层 `while True` 主要配合消费事件及审批恢复；本次非交互执行的多轮模型请求，发生在 `call_stream()` 的 `while i < max_steps` 中。

完成标准：合上讲义，能用自己的话解释一次调用从 JSON 参数到下一轮上下文的转换。

## 3. 用三个可控测试核对理解（15–20 分钟）

阅读 [`tests/test_tool_calling_llm.py`](../../tests/test_tool_calling_llm.py) 中下面三个方法，然后在项目根目录运行：

```bash
poetry run pytest tests/test_tool_calling_llm.py \
  -k 'test_call_happy_path or test_message_ordering or test_call_continues_after_tool_error' \
  -q --no-cov -n 0
```

如果终端找不到 Poetry，使用 `/Users/weibo/.local/bin/poetry` 替换 `poetry`。按当前测试文件，此命令应选中 3 个测试。

### 运行结果（2026-09-30 已核对）

环境：Python 3.12.14 / pytest 8.4.2，`poetry` 已在 PATH 上，无需替换路径。

```text
======================= 3 passed, 39 deselected in 0.04s =======================
```

3 个测试全部通过，与预期一致；`39 deselected` 是被 `-k` 过滤掉的（本文件共 42 个测试）。0.04s 也说明全程在内存里跑 Mock：没有真实模型请求、没有 kubectl、没有网络等待——输出里既没有 xdist 的 worker 分组，也没有覆盖率表，正是 `-n 0 --no-cov` 生效的结果。

每个测试上方的 `live log call` 几行是被测代码自己打的日志，不是断言结果：`AI: Let me check` 是第一轮（带工具调用那轮）的文本，`The AI requested 1 tool call(s).` 是工具结果回来后的计数，最后一条空行是工具批次结束的分隔。最终答案那轮走 `ANSWER_END`，不经过这个 logger，所以每个测试只看到一条 `AI:` 行。

另外：pytest 的结果粒度是**测试函数**而不是断言，所以 `test_call_happy_path` 里的 9 条断言只对应一个 `PASSED`；断言是短路执行的，第一条挂掉后面的都不会跑——通过的断言在输出里是隐形的。

| 测试方法 | 先预测，再核对 |
| --- | --- |
| `test_call_happy_path` | 一轮请求工具、一轮输出最终答案，为什么是 2 次模型调用、1 次工具调用？ |
| `test_message_ordering` | 为什么消息顺序是 `user → assistant(tool_calls) → tool → assistant(text)`？ |
| `test_call_continues_after_tool_error` | 工具返回错误，为什么仍能进入下一轮模型调用？ |

### 核对答案

**`test_call_happy_path`：2 次模型调用、1 次工具调用**

- 2 = 循环轮数。`call_stream()` 的 `while i < max_steps` 每转一轮发一次 `completion`；第 1 轮 assistant 带回 `tool_calls`，所以不是终点，第 2 轮没有 `tool_calls`，命中 `if not tools_to_call:` 直接返回。
- 1 = 第一轮 `tool_calls` 数组的长度（`_make_mock_tool_call()` 只造了一个 `kubectl_get`）。
- 两个数字来源不同：**模型调用次数由“轮”决定，工具调用次数由“本轮请求了几个工具”决定**。一轮里请求 3 个工具，仍然是 1 次模型调用、3 次工具调用。

**`test_message_ordering`：顺序是协议强制的，不是 Holmes 自选的**

- 起始 `messages` 只有一条 `user`。第 1 轮的原始响应被整条 `model_dump()` 后追加成带 `tool_calls` 的 `assistant`——**必须先有“请求”，后面才允许有“回复”**，否则下一轮请求会被 API 拒绝。
- 随后工具结果经 `ToolCallResult.to_llm_message()` 转成 `{role: "tool", tool_call_id, name, content}` 追加。
- 第 2 轮的纯文本 `assistant` 追加后立即返回，所以永远排在最后。
- `role` 只有 `tool` 这一种，工具名放在 `name` 字段里；请求与结果的对应关系靠 `tool_call_id`，不靠先后位置。

**`test_call_continues_after_tool_error`：终止条件不看工具成败**

- 循环唯一的终止判断是 `if not tools_to_call:`，检查的是“模型这一轮还要不要用工具”，而不是“上次工具有没有成功”。
- 错误结果没有特殊分支，照样调用 `to_llm_message()` 追加成 `tool` 消息，错误以 `status="error"` + 错误文本写在消息内容里，对模型来说是“一条观察结果”而不是异常。
- 工具真的抛出异常时，也会被 `except Exception` 捕获并转成 ERROR 结果返回，所以工具层几乎不会把异常抛进循环里去中断它。

**这次运行暴露的断言强度问题**（第 4 节要补的就是这类洞）：

- `test_message_ordering` 只断言了 `"tool_call_id" in result.messages[2]`，没检查它的值——把 ID 写错成 `tc_999`，这个测试照样绿。
- `test_call_continues_after_tool_error` 只断言了“历史里恰有 1 条 `tool` 消息”，没断言内容是错误文本——把 `permission denied` 换成别的字符串，它也照样绿。
- `len(result.messages) >= 4`、`prompt_tokens > 0`、`len(tool_calls) == 1` 属于同一类弱断言：形状对就通过，挡不住“结构还在但内容错了”的 bug。

这些测试的模型和工具返回值由 Mock 提供，不请求 DeepSeek、不执行真实 kubectl，也不消耗模型 API。这里的 Mock 可以理解为“预先约定好返回内容的替身”。

先读懂这一句就能跟上测试：

```python
mock_llm.completion.side_effect = [resp_with_tool, resp_final]
```

它表示第一次调用返回工具请求，第二次返回预设的最终回答。

因此，错误测试证明的是“程序把错误作为观察结果加入历史，并继续循环”；它并不证明真实模型一定能分析错误或找到正确办法。真实模型如何调整动作，要看第一步的实际 trace。

## 4. 自己补两个断言（20–30 分钟）

在现有 `TestMessageStructure.test_message_ordering` 的断言末尾，自己补充下面两项检查，再只运行该测试。此次规划没有替你修改测试。

1. 工具回复的 `tool_call_id` 必须等于上一条 assistant 请求中对应工具调用的 `id`。
2. 工具回复的 `content` 必须包含测试工具返回的 `pod1 Running`。

提示：观察 `result.messages[1]` 和 `result.messages[2]`。`pod1 Running` 来自同文件的 `_make_tool_call_result()`。

```bash
poetry run pytest tests/test_tool_calling_llm.py \
  -k test_message_ordering -q --no-cov -n 0
```

先确认断言通过，再临时把你断言中的预期调用 ID 或预期输出改成明显不匹配的值，预测并观察测试失败；最后恢复正确值，确认再次通过。只改新增断言的预期值即可。

这个练习的目的，是亲手确认测试确实检查到了请求与结果的关联，而不只是看到一个绿色的 passed。

## 当天验收

- [ ] 从真实记录中找到了一个匹配的请求 ID / 回复 ID。
- [ ] 在下一轮模型请求里找到了上一轮工具输出。
- [ ] 能说清工具 Schema、工具调用请求、工具结果的区别。
- [ ] 能在源码中指出参数解析和结果追加到 `messages` 的位置。
- [x] 运行了上述 3 个测试，并解释各自验证什么。
- [ ] 亲手补充 2 个断言，观察一次失败后恢复通过。
- [ ] 能解释为什么“工具报错”不一定使 Agent 停止，以及这种可控测试的证明范围。

完成这些就可以结束 Day 2。上下文压缩、并发线程池、OAuth、云端 tracing 和新增业务工具留到后续；今天不要求阅读这些实现。

## 我的 Day 2 记录（完成后填写）

- 实际学习日期：
- 我选择的工具调用 / loop / ID：
- 这次工具获得的一条关键证据：
- 下一轮 messages 中对应的内容：
- 三个测试的实际结果：`3 passed, 39 deselected in 0.04s`（2026-09-30，Python 3.12.14 / pytest 8.4.2，加 `-n 0 --no-cov`）。三者都通过；各自的含义与核对答案见第 3 节。
- 我新增的两个断言：都加在 [`TestMessageStructure.test_message_ordering`](../../tests/test_tool_calling_llm.py#L966-L970) 的断言末尾。① `result.messages[1]["tool_calls"][0]["id"] == result.messages[2]["tool_call_id"]`——工具回复的调用 ID 必须等于上一条 assistant 请求中对应工具调用的 `id`；② `"pod1 Running" in result.messages[2]["content"]`——工具回复的 `content` 必须包含测试工具返回的 `pod1 Running`。
- 故意改错后，测试为什么失败：把①的预期调用 ID 改成明显不匹配的值，第 967 行就以 `AssertionError: assert 'tc_1' == 'test'` 失败——说明这条断言比较的是 ID 的值，而不只是检查 `tool_call_id` 这个键是否存在；恢复正确值后再次通过。当时的失败输出没有留档，事后用「改坏源码」的方式补验（比改预期值更严格，动的是被测代码而不是测试）：
  - 把 `ToolCallResult.to_llm_message()` 的 `tool_call_id` 改成 `WRONG_ID_0001` → 恰好失败在第 967 行：`assert 'tc_1' == 'WRONG_ID_0001'`
  - 把同处的 `content` 换成 `(content dropped)` → 恰好失败在第 971 行：`assert 'pod1 Running' in '(content dropped)'`

  两次都只挂在新加的断言上（`"tool_call_id" in ...`、`len(messages) >= 4` 这类弱断言没拦住），说明它们确实能挡住「结构还在但内容错了」的 bug。源码恢复后 `1 passed, 41 deselected in 0.03s`，整个文件 `42 passed in 0.26s`。
- 用自己的话解释一次工具调用如何进入下一轮：
- 仍不理解的一处源码或概念：

当日产出就是这份填写后的记录，以及你补充的两条测试断言。
