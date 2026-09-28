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

先预测：下一轮请求中，除了最初的 system/user 消息，还应该出现哪些消息？然后在同一运行的 `events.jsonl` 中找到下一条 `llm.request`，核对其 `messages`。已有 Trace 查看器能展开请求时，也可以直接在那里查看。

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

如果终端找不到 Poetry，使用 `/Users/weibo/.local/bin/poetry` 替换 `poetry`。按当前测试文件，此命令应选中 3 个测试；实际结果由你运行后填写。

| 测试方法 | 先预测，再核对 |
| --- | --- |
| `test_call_happy_path` | 一轮请求工具、一轮输出最终答案，为什么是 2 次模型调用、1 次工具调用？ |
| `test_message_ordering` | 为什么消息顺序是 `user → assistant(tool_calls) → tool → assistant(text)`？ |
| `test_call_continues_after_tool_error` | 工具返回错误，为什么仍能进入下一轮模型调用？ |

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
- [ ] 运行了上述 3 个测试，并解释各自验证什么。
- [ ] 亲手补充 2 个断言，观察一次失败后恢复通过。
- [ ] 能解释为什么“工具报错”不一定使 Agent 停止，以及这种可控测试的证明范围。

完成这些就可以结束 Day 2。上下文压缩、并发线程池、OAuth、云端 tracing 和新增业务工具留到后续；今天不要求阅读这些实现。

## 我的 Day 2 记录（完成后填写）

- 实际学习日期：
- 我选择的工具调用 / loop / ID：
- 这次工具获得的一条关键证据：
- 下一轮 messages 中对应的内容：
- 三个测试的实际结果：
- 我新增的两个断言：
- 故意改错后，测试为什么失败：
- 用自己的话解释一次工具调用如何进入下一轮：
- 仍不理解的一处源码或概念：

当日产出就是这份填写后的记录，以及你补充的两条测试断言。
