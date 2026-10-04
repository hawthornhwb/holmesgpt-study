# Day 4：分层上下文控制与证据保留

状态：已规划，待你学习与实验。对应简历中“智能故障诊断 Agent”的第三条：定向查询与过滤、Transformer 摘要、长历史 Compaction。

Day 1、Day 2 你已经掌握了工具结果怎样进入下一轮，Day 3 研究工具如何接入。今天继续回答：**工具返回很多数据、历史越来越长时，怎样控制上下文，同时保留诊断需要的证据？**

按每天 3–4 小时安排：必做任务合计 180 分钟，第 4 小时用于补弱项或选做实验。总计划见 [未来五天面试复习](../interview-plan.md)。今天从本页依次往下做，学习记录直接填在文末。

## 时间与产出

| 顺序 | 任务 | 时间 | 必须留下的产出 |
| --- | --- | --- | --- |
| 1 | 从已有消息流找到上下文增长的位置 | 15 分钟 | 一张处理链路草图 |
| 2 | 按顺序读过滤、单结果处理与历史压缩 | 45 分钟 | 四个机制的源码入口与关键条件 |
| 3 | 填对比表、手算预算、列关键证据 | 30 分钟 | 对比表、三个预算判断、证据清单 |
| 4 | 预测并运行三组离线测试 | 50 分钟 | 测试结果与每组的证明范围 |
| 5 | 脱稿回答面试追问 | 25 分钟 | 2 分钟上下文控制讲述 |
| 6 | 验收并填写学习记录 | 15 分钟 | 完成情况、个人验证与待补问题 |

## 1. 找到上下文增长的位置（15 分钟）

打开 [Day 2 消息流记录](../day2/README.md)，或 [Day 1 已有 Trace 导读](../day1/runs/20260923-225409-379209/walkthrough.md)，选一条你已经熟悉的工具调用。

具体做三件事：

1. 找到工具结果进入下一轮 `messages` 的位置，写出它的 `role`、`tool_call_id` 和一条关键内容。
2. 用自己的话回答：一条结果很大，与每条结果不大但轮数很多，分别会造成什么问题？
3. 先凭理解画草图，第二步读完源码后再修正：

```text
收窄查询范围 / 返回字段
→ 工具执行
→ 单次结果处理
→ 转为 tool 消息并加入历史
→ 每轮请求前检查整体预算、必要时 Compaction
→ 下一轮模型请求
```

本节验收：能区分“单次结果过大”和“历史累计过大”，指出两者处理位置不同。原 Trace 没有触发压缩时，直接记录“本次未观察到”，后面用可控测试核对机制。

## 2. 按四个机制阅读源码（45 分钟）

先读 [项目上下文管理说明](../../docs/reference/context-management.md) 的单次结果落盘、历史 Compaction 和交互流程，然后只沿下面指定入口阅读。用函数名定位，暂时跳过图像处理、供应商兼容与指标上报细节。

### 2.1 查询减量：服务端与本地过滤（10 分钟）

对照两个例子：

- [loki_api.py](../../holmes/plugins/toolsets/grafana/loki_api.py) / `execute_loki_query()`：找到 `params` 和 `requests.get()`，抄下直接传到 API 的 `query`、`start`、`end`、`limit`。
- [kubernetes.yaml](../../holmes/plugins/toolsets/kubernetes.yaml) / `kubernetes_jq_query`：只找 `?limit=...`、`continue` 与 `MATCHES=...jq...` 三处，指出 API 分页与本地 jq 分别在哪里执行。

写出结论：Loki 可以把上述查询条件交给服务端；这个 Kubernetes 工具从 API 分页取数据后在本地做 jq 过滤。每页 `limit` 控制一页的大小，遍历所有页并不等于限制最终总返回量。

本节产出：各举一个服务端减量、本地过滤的例子，并说明为什么查询减量仍然有价值。

### 2.2 Transformer：单次结果的有损摘要（10 分钟）

按顺序读：

1. [llm_summarize.py](../../holmes/core/transformers/llm_summarize.py) 文件开头说明，以及 `should_apply()`、`transform()`。
2. [tools.py](../../holmes/core/tools.py) / `Tool._apply_transformers()`，找到跳过、摘要不变短时回退、异常处理的分支。
3. [config.py](../../holmes/config.py) 的 `fast_model` 默认值，核对模型配置条件。

回答四个问题：

- 什么条件下才会摘要？fast model 可用，而且输入字符数严格超过配置阈值。
- 阈值单位是什么？这里比较 `len(input_text)`，是字符数。
- 摘要为空或不比原文短怎么办？转换器可抛错；工具处理层捕获转换错误，`llm_summarize` 结果不变短时回退到转换前内容。
- 当前默认是否使用它？当前源码把 `llm_summarize` 标为历史机制、默认关闭且不推荐；需要结合实际配置说明是否启用。

本节产出：写出“何时执行、处理什么、失败怎样处理、有什么代价”四句话。额外模型调用、延迟和有损信息都是取舍的一部分。

### 2.3 落盘：保留结果、上下文放路径和预览（10 分钟）

按顺序读：

1. [tool_calling_llm.py](../../holmes/core/tool_calling_llm.py) / `_invoke_llm_tool_call()` 中调用 `spill_oversized_tool_result()` 的位置，注意可读文件的 Bash 工具是否可用。
2. [tool_context_window_limiter.py](../../holmes/core/tools_utils/tool_context_window_limiter.py) / `spill_oversized_tool_result()`：只看计数、未超限、保存成功、不能保存四个分支。
3. [filesystem_result_storage.py](../../holmes/core/tools_utils/filesystem_result_storage.py) / `save_large_result()`：找到实际写入文件的内容。
4. [llm.py](../../holmes/core/llm.py) / `get_max_token_count_for_single_tool()`：核对单次结果预算来自哪里。

默认正常百分比配置下，单次结果上限是 `min(上下文窗口 × 15%, 25,000 tokens)`；两项都可配置。与上限相等时保留，严格超过时才进入大结果处理。

保存成功时，后续 `tool` 消息包含路径、预览和读取提示，而非全部结果；没有额外摘要模型调用。存储被禁用、目录或文件访问能力不可用、写入失败时，会返回要求缩小查询的错误。

**注意保存范围：落盘保留它收到的工具结果。若前面启用了 Transformer 并已用摘要替换原文，后续落盘不能恢复被摘要丢掉的信息。** 文件按需读取也取决于访问工具与文件生命周期，不能把临时落盘当永久保存。

本节产出：画“未超限 / 超限可保存 / 超限不能保存”三个分支，分别写出下一轮看到什么。

### 2.4 Compaction：每轮请求前处理整体历史（15 分钟）

按顺序读：

1. `tool_calling_llm.py` / `call_stream()` 循环顶部：先检查与执行 Compaction，再调用模型；注意新的 `messages` 如何替换旧历史。
2. [input_context_window_limiter.py](../../holmes/core/truncation/input_context_window_limiter.py) / `compact_if_necessary()`：找到触发条件、只有变短才采用的判断，以及最后的硬上限检查。
3. [compaction.py](../../holmes/core/truncation/compaction.py) / `compact_conversation_history()`：先读函数说明，再只看摘要失败的回退、无可用摘要时返回原历史、成功后的消息组装。
4. [压缩提示模板](../../holmes/plugins/prompts/conversation_history_compaction.jinja2)：把它当项目传给模型的提示内容，找出要求保留的资源名、日志证据、调查结论与待办。

当前默认触发条件是：

```text
输入 token（包括 messages 与工具 Schema）+ 最大输出预算
> 上下文窗口 × 95%
```

阈值可配置，历史压缩默认开启。摘要成功后，有原 system 和最后 user 消息的常见情形变为：

```text
原 system → user 角色的历史摘要 → 原最后一条 user 问题
```

成功生成摘要还不等于预算一定够用：调用方会核对它是否变短，并继续检查输入加输出预算是否超过整个窗口。摘要与回退都失败时，压缩函数会返回原历史；如果随后仍超硬上限，调用方会抛出 `CompactionInsufficientError`。

本节产出：分别写清“什么时候调用”“替换哪些消息”“失败后是否还能继续”的答案。

## 3. 填比较表、手算预算、列证据清单（30 分钟）

### 3.1 不看源码填写对比表（10 分钟）

| 机制 | 处理对象与发生时机 | 是否额外调用模型 | 原文怎样找回 | 失败处理 / 主要限制 |
| --- | --- | --- | --- | --- |
| 定向查询与过滤 | | | | |
| 单次结果 Transformer 摘要 | | | | |
| 单次结果落盘 | | | | |
| 全历史 Compaction | | | | |

再回答：为什么只做 Compaction 不能替代查询减量？为什么落盘不能完全替代历史压缩？

### 3.2 手算三个边界（10 分钟）

以下是教学假设，不是你当前模型的实际配置：窗口为 10,000 tokens，压缩阈值 95%，输出预算为 1,000 tokens，输入已经包含工具 Schema。

1. 输入 8,500 tokens：是否触发 Compaction？
2. 输入 8,501 tokens：是否触发 Compaction？
3. 压缩结束后输入仍为 9,500 tokens：是否能发送下一轮请求？

先写预测，再对照源码。应分别得到：不触发（刚好等于阈值）、触发（严格超过阈值）、超过硬上限（9,500 + 1,000 > 10,000）。

补一句解释：字符长度、单次工具 token 上限、整个请求 token 预算，不能混用。摘要变短也不自动等于诊断信息充分。

### 3.3 列出必须保留的诊断证据（10 分钟）

以“服务反复重启”的调查为例，写下至少六项：资源名、命名空间、时间范围、错误原文、查询条件、已验证结论、已排除假设、未完成调查与下一步。

检查一份摘要时，逐项确认有没有具体证据支撑。如果只剩“服务存在异常”这样的概括，说明不足以继续调查；需要重新读取仍可访问的原数据，或重新查询缺失证据。

## 4. 用三组离线测试核对理解（50 分钟）

每组按“先读输入和断言 → 写预测 → 运行 → 用一句话解释结果”执行。在项目根目录运行，学习记录填在文末。

### A. 摘要条件与回退（15 分钟）

```bash
poetry run pytest tests/core/transformers/test_llm_summarize.py \
  -k 'test_should_apply_no_fast_model or test_should_apply_with_custom_threshold or test_transform_empty_response or test_integration_with_tools_non_expanding_logic' \
  -q --no-cov -n 0
```

四个测试分别检查：没有 fast model 不应用；阈值 500 时 499 / 500 / 501 字符的区别；空摘要作为错误；摘要膨胀时工具层保留原文。

特别区分：转换器抛出错误，与整个 Agent 调查终止是不同层次的行为。

### B. 单次结果边界与落盘（20 分钟）

```bash
poetry run pytest tests/core/tools_utils/test_tool_context_window_limiter.py \
  -k 'test_within_token_limit or test_edge_case_exactly_at_limit or test_exceeds_token_limit or test_spill_to_disk_without_images' \
  -q --no-cov -n 0
```

四个测试分别检查：上限内不改结果；恰好上限不改结果；超限且没有存储目录时返回错误；超限且可落盘时工具消息包含路径。

注意 Mock 直接指定了 token 数与上限，这些数字用于制造边界，不是实际 tokenizer 的测量。最后一个测试主要断言路径与图片提示字段；完整文件内容的保留还需要结合 `save_large_result()` 阅读或第 4 小时的补充断言核对。

### C. 历史重组与失败回退（15 分钟）

```bash
poetry run pytest tests/core/truncation/test_compaction.py \
  -k 'test_compaction_output_shape_user_summary_no_trailing_system or test_compaction_returns_original_history_when_fallback_also_fails' \
  -q --no-cov -n 0
```

两个测试分别检查：压缩后为 `system → user 摘要 → 最后 user`；摘要请求和回退都失败时返回原历史。

第二个测试只证明压缩函数的回退行为，不能单独证明后续整体预算检查一定允许继续请求。

这三组共选中 10 个离线测试，使用 Mock / Fake 模型；落盘测试会写 pytest 的临时文件。`test_compaction.py` 另有真模型测试，本次按上述筛选运行。

编写计划时已核对命令，结果为 **4 passed、4 passed、2 passed**。这是计划验证记录；你的学习完成情况仍以自己预测、执行并解释后的记录为准。预期错误场景中的 ERROR / WARNING 日志不等于测试失败，以断言结果为准。

## 5. 脱稿讲述并回答追问（25 分钟）

先不看文档，用 2 分钟讲清：

```text
为什么数据会超上下文
→ 怎样先缩小查询
→ 摘要与当前落盘机制分别怎么处理一条结果
→ 历史累积如何触发 Compaction
→ 怎么检查关键证据与处理失败
```

再回答六个追问，每题约 1–2 分钟：

1. 服务端过滤和 jq 本地过滤有什么区别？分页为什么不等于总结果限制？
2. Transformer 和 Compaction 都能摘要，为什么处理位置不同？
3. 当前版本为什么优先落盘？有哪些代价与使用条件？
4. 触发 Compaction 为什么要计算工具 Schema，并预留输出预算？
5. 摘要丢掉错误码或资源名后，怎样补回证据？
6. 哪些行为经过了你的实测？离线测试能否证明真实模型摘要不会丢信息？

简历讲述按实际实现与验证填写：可以说明研究了分层方案、验证了边界与失败处理；当前默认方案、显式开启的摘要实验和真实模型效果分别说明。压缩比例、成本下降等数字只引用自己测量过的结果。

## 6. 当天验收与记录（15 分钟）

- [ ] 找到了上下文增长的位置，区分单条过大与累计过大。
- [ ] 能指出过滤、Transformer、落盘、Compaction 的源码入口。
- [ ] 完成机制对比表，并解释当前摘要默认关闭的情况。
- [ ] 正确完成三个预算判断，能区分字符数与 token。
- [ ] 自己运行了三组测试，并解释实际结果和证明范围。
- [ ] 写出关键证据清单，知道信息不足时怎样重新取证。
- [ ] 完成 2 分钟口述，六个追问中至少五个能脱稿回答。

学习记录直接填在这里：

- 实际学习日期与用时：
- 我选择的 Trace / 工具调用，以及进入下一轮的一条证据：
- 我的处理链路图与四种机制对比：
- 三个预算判断与理由：
- 摘要或预览中必须保留的证据：
- 三组测试的实际结果：
- 一条测试断言能证明什么、不能证明什么：
- 我的 2 分钟项目讲述：
- 我亲手阅读、验证或修改的部分：
- 仍答不清的问题，明天开头复习：

## 第 4 小时：选做一次证据保留实验（最多 60 分钟）

核心任务通过后，从下面两项选一项完成；如果口述还不顺，先补追问。

**选项 A：给落盘测试补一条强断言。** 参考 Day 2 的断言练习，在 `test_spill_to_disk_without_images` 中自己增加“保存文件全文等于原始输入”的检查。先记录预期，再运行这个测试；把新增断言的预期值临时改错，确认失败后恢复并重新通过。原测试、生产实现及测试数据的改动分别记录。此计划没有替你修改测试。

**选项 B：比较全文、预览与手工摘要。** 从第 1 步同一个 Trace 取工具输出，把它在笔记中分别写成短预览和手工摘要，逐项检查第 3 步的证据清单。记录哪项被遗漏、下一轮会受什么影响、应重新读取还是重新查询。手工摘要只验证你对证据的理解，不代表真实摘要模型的效果。

到 60 分钟时记录结果与未完成部分，后续继续按五天计划推进。
