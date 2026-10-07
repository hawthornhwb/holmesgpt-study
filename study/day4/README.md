# Day 4 讲义：分层上下文控制（沿 09_crashpod 读源码）

2026-10-06 改写为讲义。对应 10-04 版简历中“智能故障诊断 Agent”的第二条：分层上下文控制。

2026-10-07 修订：区分核心阅读与深入实现，补充离线落盘验证、陌生变式和隔天复习。原 case 的实测范围保持单独记录。

**这份文档是自足的：今天需要看的源码已经按数据流顺序摘录在正文里，不必再打开仓库对照。** 每个片段都标了 `文件:行号`，想深挖时再跳过去。全部片段来自当前工作区，行号是 2026-10-06 核对过的。

> 片段说明：为便于阅读，摘录时做过少量排版整理 —— 换行、缩进、省略号（`...`）处有删减；函数名、参数名、字段名和逻辑顺序与源码一致。代码块里的中文注释，凡标了“讲义注”或“讲义译注”的是本次改写或补充，其余中文注释是当前工作区源码里原本就有的。要逐字对照时按行号打开源文件。

今天只回答一个问题：**同一次调查里，一条数据从数据源走到模型，要过几道闸门？每道闸门归谁管、按什么预算判断、丢什么、留什么？**

沿用 [09_crashpod 完整案例](../interview-plan.md)：调查对象是 `app-09` 的 `payment-processing-worker`，它因为缺少 `DEPLOY_ENV` 反复重启，关键证据日志是 `Environment variable DEPLOY_ENV is undefined`。今天放大的是“查询和结果进入上下文”这一段。

四道闸门按数据流排队，读的时候记住它们各自解决哪一种“太大”：

| # | 闸门 | 判断依据 | 归属 | 本 case 实测 |
| --- | --- | --- | --- | --- |
| 1 | 查询减量（分页 / 服务端与本地过滤 / `limit`） | 查询条件 | 具体工具的实现 | 用到了，但数据本来就小 |
| 2 | 工具内按 token 预算截断 | `context.max_token_count` | 具体工具的实现 | 路径经过，未真正截断（推演） |
| 3 | 单次结果落盘 + 预览 | `get_max_token_count_for_single_tool()` | 框架通用层（`ToolCallingLLM`） | 未触发（推演） |
| 4 | 历史预算检查与 Compaction | 上下文窗口 × 95% | 框架通用层（每轮模型请求前） | 未触发，报告里 Compactions 为空 |

> **范围声明（先读这一句）**：09_crashpod 已于 2026-10-06 实跑通过（连续两次 1/1），但那次运行的日志只有一行、查询结果很小。**原 case 没有实测大日志、过滤与截断、摘要、落盘、Compaction**，本页第 2–6 节的触发过程是照源码推演，第 9 节明确区分哪一格是实测、哪一格是推演。第 11.1 节的新增任务用 Mock 验证落盘程序分支，不能替代这些真实调查实验。

## 建议用法

**核心路线**：第 1 节三种“太大” → 第 2 节过滤位置 → 第 3 节截断方向与预算 → 第 4 节落盘输入输出 → 第 6 节预算判定公式 → 第 7 节证据边界表 → 第 9 节实测范围。先读解释与表格，必读骨架是单次预算公式和 Compaction 的“输入 + 输出预留”判定。

**深入选读**：截断循环的字符估算、落盘文件生命周期实现、第 5 节 Transformer 与 fast model、第 6 节事件与回退细节。先知道摘要在落盘前、默认不生效，需要追问时再展开。

| 顺序 | 时间 | 要做什么 | 留下什么 |
| --- | --- | --- | --- |
| 1 | 5 分钟 | 脱稿复习 Day 3 的调用路径 | 工具内处理与框架处理的位置 |
| 2 | 35–50 分钟 | 按核心路线区分四层，逐段解释丢什么、留什么 | 分层路径与证据清单 |
| 3 | 15 分钟 | 第 11.1 节最小验证 | 一条消息与落盘文件的对照 |
| 4 | 20 分钟 | 第 10 节口述与核心追问 | 自己的录音、时长与一个取舍 |
| 5 | 15 分钟 | 第 11.2 节陌生变式，填写记录 | 证据缺口与重新查询的条件 |

约 90–110 分钟，可拆成两次。首次阅读需要补语法时按实际用时记录；深入实现和真实大日志实验另行安排。

## 1 先分清三种“太大”，再谈分层

三种问题发生在不同位置，只用一层解决不了全部：

| 遇到的情况 | 问题在哪里 | 优先怎么处理 |
| --- | --- | --- |
| 发现 Pod 时返回全体资源的完整 JSON，只需要目标服务的状态 | 无关对象和字段占用输入 | 收窄查询：筛选目标资源，只投影 namespace、Pod 名与容器状态 |
| 查准确 Pod 的日志，输出很多，启动错误在较早位置 | 单条工具结果占用太大 | 先核对 `filter` / `exclude_filter` / `limit`，再看截断与落盘 |
| 反复查状态、日志与配置，每次都能容纳，累计历史很长 | 对话累计占用超过预算 | 下一轮请求前检查整体预算，必要时 Compaction |

本 case 的真实数据规模很小：容器启动时只打印一行 `Environment variable DEPLOY_ENV is undefined` 就退出（fixture 见 [test_case.yaml](../../tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml) 第 26 行的启动命令），日志工具读到的就是这一行。所以这次四道闸门都挂在路径上，却都没有真正动手 —— 这正是后面必须把“机制存在”和“机制触发过”分开说的原因。

## 2 第 1 道闸门：查询减量

这一层不是框架统一做的，而是每个工具自己的实现责任。`kubernetes_jq_query` 的说明把设计意图写在工具描述里（`holmes/plugins/toolsets/kubernetes.yaml:19`，中间省略）：

```yaml
        description: >
          Use kubectl to get json for all resources of a specific kind and filter with jq.
          Prefer over bash for large queries - paginates via the Kubernetes API so large
          clusters don't overflow context.
```

它对应脚本里的分页循环（同一文件 `:113` 与 `:156`，中间省略）：

```bash
              QUERY="${API_PATH}?limit=${LIMIT}"
              QUERY="${API_PATH}?limit=${LIMIT}&continue=${CONTINUE}"
            CONTINUE=$(echo "$OUTPUT" | jq -r '.metadata.continue // empty')
```

**读法**：分页是把“一次拉全量”换成“多次拉一页”，`jq` 过滤发生在本地、过滤后的匹配项才进上下文。所以它减少的是**模型输入**，不是 API 读取次数 —— 讲方案时要区分过滤位置，别把本地过滤说成服务端筛选。

日志工具同理，`filter` / `exclude_filter` / `limit` 全是拿到日志之后在本地做的，`limit` 还有一个容易讲错的方向（`holmes/plugins/toolsets/kubernetes_logs.py:776`）：

```python
    if params.limit and params.limit < len(filtered_logs):
        filtered_logs = filtered_logs[-params.limit :]
```

`[-limit:]` 取的是**最后 N 条**（较晚的日志），默认数量为 100（`holmes/plugins/toolsets/logging_utils/logging_api.py:22` 的 `DEFAULT_LOG_LIMIT = 100`），不是不可调整的硬上限。崩溃循环的启动错误往往在较早位置，这个默认方向偏向“最近发生了什么”。

## 3 第 2 道闸门：工具内按 token 预算截断（truncate_logs）

这是本 case 真正会用到的第一道硬闸门，写在日志工具内部。它的循环逻辑（`holmes/plugins/toolsets/logging_utils/logging_api.py:54`，中间省略）：

```python
def truncate_logs(
    logging_structured_tool_result: StructuredToolResult,
    llm: LLM,
    token_limit: int,
    structured_params: FetchPodLogsParams,
    tool_call_id: str,
    tool_name: str,
):
    original_token_count = count_tool_response_tokens(
        llm=llm,
        structured_tool_result=logging_structured_tool_result,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    token_count = original_token_count
    text = None
    while token_count > token_limit:
        ...
        ratio = token_count / token_limit
        character_count = len(text)
        number_of_characters_to_truncate = character_count - ceil(
            character_count / ratio
        )
        ...
        else:
            linefeed_truncation_offset = max(
                text[number_of_characters_to_truncate:].find("\n"), 0
            )  # keep log lines atomic
            ...
            text = (
                TRUNCATION_PROMPT_PREFIX
                + text[number_of_characters_to_truncate + linefeed_truncation_offset :]
            )
            logging_structured_tool_result.data = text
            token_count = count_tool_response_tokens(
                llm=llm,
                structured_tool_result=logging_structured_tool_result,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
```

1. **输入输出**：输入是已经过滤、已经 `limit` 过的日志结果，输出是同一个 `StructuredToolResult`，只是 `data` 被换成了更短的文本。函数没有返回值 —— 它原地改结果，调用方直接 `return result`。
2. **按 token 判断、按字符裁剪**：token 数用 `count_tool_response_tokens()` 重新计算（`holmes/core/tools_utils/token_counting.py:6`），裁剪却只能按字符比例估算，所以要 `while` 循环反复逼近，而不是算一次就够。
3. **保留尾部**：`text[number_of_characters_to_truncate + ...:]` 丢的是前面，保的是后面，并在开头插入 `TRUNCATION_PROMPT_PREFIX`（`logging_api.py:29`）说明“上面的日志已被截断”。所以截断是**有损且偏向新日志**的：本例那条启动错误如果落在被丢掉的前半段，模型就看不到它了 —— 这正是第 8 节要推演的分支。

预算从哪来？调用点在 `logging_api.py:193`：

```python
        truncate_logs(
            logging_structured_tool_result=result,
            llm=context.llm,
            token_limit=context.max_token_count,
            ...
        )
```

`context.max_token_count` 由框架在每次调用工具时填好，值就是“单个工具结果允许占多少 token”：`holmes/core/tool_calling_llm.py:824` 的 `max_token_count=self.llm.get_max_token_count_for_single_tool()`。

算法在 `holmes/core/llm.py:327`：

```python
    def get_max_token_count_for_single_tool(self) -> int:
        if (
            0 < TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT
            and TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT <= 100
        ):
            context_window_size = self.get_context_window_size()
            calculated_max_tokens = int(
                context_window_size * TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT // 100
            )
            return min(calculated_max_tokens, TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS)
        else:
            return TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS
```

两个默认值在 `holmes/common/env_vars.py:137` 与 `:142`：`TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT = 15`、`TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_TOKENS = 25000`。

**本学习环境的具体数字（推演）**：窗口设成 128,000（见第 6 节），则 `128000 × 15% = 19200`，比绝对上限 25,000 小，于是单次工具结果预算是 **19,200 tokens**。这个数字同时被日志工具的内部截断和第 4 节的通用落盘使用 —— 同一笔预算，两道闸门各查一次。本次运行日志只有一行，远低于 19,200，`while` 循环一次都没进，所以“截断做了什么”没有运行记录。

## 4 第 3 道闸门：把过大结果落盘，只把路径和预览放进历史

上一道闸门在**工具内部**，只管自己那一类结果；这一道在**框架的工具调用层**，对所有工具生效。判断入口（`holmes/core/tools_utils/tool_context_window_limiter.py:47`，中间省略）：

```python
    t0 = time.monotonic()
    message = tool_call_result.to_llm_message()
    messages_token = llm.count_tokens(messages=[message]).total_tokens
    max_tokens_allowed = llm.get_max_token_count_for_single_tool()
    ...
    if messages_token <= max_tokens_allowed:
        return messages_token
```

它把整条工具消息（含工具名、调用 ID）算成 token，和上面同一个预算比。超了才进入落盘分支（同一文件 `:72`，中间省略）：

```python
    file_path = None
    filesystem_data = ""
    image_paths: list[str] = []
    if tool_results_dir and load_bool("HOLMES_TOOL_RESULT_STORAGE_ENABLED", True):
        filesystem_data, is_json = tool_call_result.result.stringify_data(compact=False)
        ...
        file_path = save_large_result(
            tool_results_dir=tool_results_dir,
            tool_name=tool_call_result.tool_name,
            tool_call_id=tool_call_result.tool_call_id,
            content=filesystem_data,
            is_json=is_json,
        )
```

保存成功后，进历史的不再是原文，而是“路径 + 读取提示 + 预览”（同一文件 `:88`，中间省略）：

```python
    if file_path:
        boilerplate = (
            f"{size_info}\n"
            f"Saved to: {file_path}\n"
            f"Use `cat {file_path}` to read it (pre-approved, no user approval needed). "
            f"You can pipe the output into any command to filter, for example: "
            f"`cat {file_path} | jq '.field'`, `cat {file_path} | grep -oP 'pattern'`, etc.\n"
        )
        boilerplate += "\nPreview:\n"
        # Allocate remaining char budget to the preview so the final string fits the context window
        chars_per_token = 4
        safety_margin_chars_per_token = chars_per_token / 2
        max_chars = max_tokens_allowed * safety_margin_chars_per_token
        preview_budget = int(max(0, max_chars - len(boilerplate)))
        preview = filesystem_data[:preview_budget]
        tool_call_result.result.data = f"{boilerplate}{preview}"
```

1. **输入输出**：输入是工具刚返回的 `ToolCallResult`，输出不是新对象，而是**原地替换** `result.data`；原文 `filesystem_data` 只落在磁盘上，落盘的是 `stringify_data(compact=False)` 的完整文本而不是摘要。要注意执行顺序：Transformer（第 5 节）在 `Tool.invoke()` 内部先跑，这道闸门后跑，所以它比的是**那时**那条消息的大小 —— 没有 Transformer 生效时就是工具自己的输出大小。
2. **只有能读回来才值得落盘**：`tool_results_dir` 并不总是传进来。调用点（`holmes/core/tool_calling_llm.py:1009`）：

    ```python
                # See docs/reference/context-management.md for how this fits with compaction
                original_token_count = spill_oversized_tool_result(
                    tool_call_result=tool_call_result,
                    llm=self.llm,
                    tool_results_dir=self.tool_results_dir
                    if self.tool_results_dir and self._has_bash_for_file_access()
                    else None,
                )
    ```

    条件里的 `_has_bash_for_file_access()`（同一文件 `:254`）要求启用了 `bash` 工具集且允许列表不是 `none`（`return config.builtin_allowlist != "none"`）—— 因为“读回来”靠的是 `cat`。没有文件访问能力时宁可不落盘，也不留一个读不到的路径。

3. **落盘是把“丢数据”换成“换位置”**：真的写不成功时，走失败分支（`tool_context_window_limiter.py:131`）：

    ```python
        else:
            tool_call_result.result.status = StructuredToolResultStatus.ERROR
            tool_call_result.result.data = None
            tool_call_result.result.images = None
            tool_call_result.result.error = (
                f"{size_info}\n"
                f"Try to repeat the query but proactively narrow down the result "
                f"so that the tool answer fits within the allowed number of tokens."
            )
    ```

    也就是：要么给路径、要么要求收窄查询，不会把超限内容硬塞进历史。

落盘的实现很短（`holmes/core/tools_utils/filesystem_result_storage.py:43`）：

```python
def save_large_result(
    tool_results_dir: Path,
    tool_name: str,
    tool_call_id: str,
    content: str,
    is_json: bool = False,
) -> Optional[str]:
    ...
    try:
        safe_name = re.sub(r"[^\w\-]", "_", tool_name)
        safe_id = re.sub(r"[^\w\-]", "_", tool_call_id)
        extension = ".json" if is_json else ".txt"
        file_path = tool_results_dir / f"{safe_name}_{safe_id}{extension}"
        file_path.write_text(content, encoding="utf-8")
        logging.info(f"Saved large tool result to filesystem: {file_path}")
        return str(file_path)
    except Exception as e:
        logging.warning(f"Failed to save tool result to filesystem: {e}")
        return None
```

返回文件路径；任何异常都退化成 `None`，由调用方走失败分支（docstring 原话是 “Returns the file path, or None if storage failed.”）。注意这带来一个常被追问的边界：**落盘文件不是长期证据仓库**。目录按会话创建（`base / uuid4() / "tool_results"`），会话结束在 `finally` 里 `shutil.rmtree` 清理，清理失败只记一条警告（`holmes/core/tools_utils/filesystem_result_storage.py:26` 的 `tool_result_storage()`）。它服务于当前会话内的继续调查；跨会话要留证据，得靠报告或外部存储。

## 5 Transformer：工具自带的结果后处理（llm_summarize 保留但默认不生效）

Transformer 是“结果从 `_invoke()` 出来之后、交给框架之前”的可选处理，挂在工具自己身上；按执行顺序它在第 4 节的落盘**之前**，本节放在后面是为了先讲完框架层的通用闸门。字段定义（`holmes/core/tools.py:324`）：

```python
    transformers: Optional[List[Transformer]] = None
```

真正执行在 `Tool.invoke()` 的末尾（同一文件 `:414` 的 `transformed_result = self._apply_transformers(result)`）。骨架（同一文件 `:458`）：

```python
    def _apply_transformers(self, result: StructuredToolResult) -> StructuredToolResult:
        ...
        if (
            not self._transformer_instances
            or result.status != StructuredToolResultStatus.SUCCESS
        ):
            return result
```

docstring 的承诺是“转换失败就返回原结果”。四个直接可讲的约束：

1. **没配就整段跳过**：`_transformer_instances` 为 `None`（没有配置）或结果不是 `SUCCESS` 时原样返回 —— Transformer 不会“顺手”处理失败结果。
2. **先问 `should_apply()`**（同一文件 `:486`）：转换器自己按条件决定要不要动手，典型条件就是“输入是否超过阈值”。
3. **摘要没变短就回退**（同一文件 `:499`）：

    ```python
                    # Check if this is llm_summarize and revert if summary is not smaller
                    post_transform_size = len(transformed_data)
                    if (
                        transformer_instance.name == "llm_summarize"
                        and post_transform_size >= pre_transform_size
                    ):
                        # Revert to original data if summary is not smaller
                        transformed_data = original_data
                        ...
                        continue  # Don't mark as applied
    ```

4. **失败不影响调查**（同一文件 `:523`）：

    ```python
                except TransformerError as e:
                    logger.warning(
                        f"Transformer '{transformer_instance.name}' failed for tool '{self.name}': {e}"
                    )
                    # Continue with other transformers, don't fail the entire chain
                    continue
    ```

    抛 `TransformerError` 或其他异常都只记日志、保留转换前内容，继续走后面的闸门。这就是“摘要失败不等于这一轮失败”的代码依据。

`llm_summarize` 的定义位置是 `holmes/core/transformers/llm_summarize.py`（在 `holmes/core/transformers/__init__.py:14` 注册进 registry，之后按名字创建实例）。它的定位写在文件开头（`holmes/core/transformers/llm_summarize.py:1`）：

```python
"""
LLM Summarize Transformer for fast model summarization of large tool outputs.

LEGACY — disabled by default and NOT recommended. This predates the
spill-to-disk mechanism (see docs/reference/context-management.md) and never
worked well in practice: summarization is lossy (the original tool output is
unrecoverable afterwards), adds latency and cost to every large tool call,
and modern models do better working from the full data spilled to disk.
Kept for backwards compatibility with existing configs that reference it.
"""
```

“默认关闭”的确切含义要讲准，它由两个条件共同决定：

- **配置里声明**：不少内建工具集确实声明了它。比如 `kubernetes_jq_query`（`holmes/plugins/toolsets/kubernetes.yaml:179`）：

    ```yaml
            transformers:
              - name: llm_summarize
                config:
                  input_threshold: 10000
    ```

- **运行时要有摘要模型**：没有可用的 fast model 时，`should_apply()` 直接返回 `False`（`holmes/core/transformers/llm_summarize.py:111`）：

    ```python
            # Skip if no fast model is configured
            if self._fast_llm is None:
                logger.debug("Skipping summarization: no fast model configured")
                return False
    ```

    fast model 来自配置的 `fast_model` 字段（`holmes/config.py:111`），启动时注入（同一文件 `:192`）：

    ```python
                if self.fast_model:
                    LLMSummarizeTransformer.set_default_fast_model(self.fast_model)
    ```

**取舍**：声明了 Transformer ≠ 生效。本学习环境没有配置 `fast_model`（[local-case.sh](../local-case.sh#L59-L65) 只导出 `MODEL`、`CLASSIFIER_MODEL`、`OVERRIDE_MAX_*`），所以即使 YAML 里声明了 `llm_summarize`，`should_apply()` 也会跳过 —— 这一点是照代码推断的，运行记录里没有摘要条目可比对。这也解释了为什么“简历里写的摘要方案”和“当前实现的实际选择”要分开讲：**当前仓库把摘要标为历史机制，大结果优先落盘**，摘要仍然保留给引用它的旧配置。

## 6 第 4 道闸门：历史预算检查与 Compaction

前三道都只看“单次结果”，这一道看整段历史。先看两个预算的来源（`holmes/core/llm.py:518`）：

```python
    def get_context_window_size(self) -> int:
        if self.max_context_size:
            return self.max_context_size

        if OVERRIDE_MAX_CONTENT_SIZE:
            ...
            return OVERRIDE_MAX_CONTENT_SIZE
```

另一个是输出预算（`holmes/core/llm.py:792`）：

```python
    def get_maximum_output_token(self) -> int:
        ...
        max_output_tokens = max(64000, self.get_context_window_size() * 12 // 100)

        if OVERRIDE_MAX_OUTPUT_TOKEN:
            ...
            return OVERRIDE_MAX_OUTPUT_TOKEN
```

优先级一目了然：模型配置里的 `max_context_size` 最高，其次是环境变量覆盖，最后才是 litellm 的模型表 / 兜底值；两个环境变量在 `holmes/core/llm.py:54` 读取：

```python
OVERRIDE_MAX_OUTPUT_TOKEN = environ_get_safe_int("OVERRIDE_MAX_OUTPUT_TOKEN")
OVERRIDE_MAX_CONTENT_SIZE = environ_get_safe_int("OVERRIDE_MAX_CONTENT_SIZE")
```

**本学习环境把 `OVERRIDE_MAX_CONTENT_SIZE` 设为 128000、`OVERRIDE_MAX_OUTPUT_TOKEN` 设为 16384**（[local-environment.md](../local-environment.md#L33)、脚本 [local-case.sh](../local-case.sh#L62-L65)）。为什么需要覆盖：这是一个自定义 model ID，litellm 的模型表里没有它的窗口信息，不覆盖就只能落到兜底值；输出预算也踩过坑 —— 第一版设成 4,096，思考内容也算在这笔预算里，最终回答为空、那次运行未通过（[local-environment.md](../local-environment.md#L68)）。**输入预算和输出预算是同一道算术题的两半。**

判定逻辑（`holmes/core/truncation/input_context_window_limiter.py:34`，中间省略）：

```python
    if not ENABLE_CONVERSATION_HISTORY_COMPACTION:
        return None

    initial_tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    max_context_size = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()

    if (initial_tokens.total_tokens + maximum_output_token) > (
        max_context_size * get_context_window_compaction_threshold_pct() / 100
    ):
```

阈值也在 `holmes/core/llm.py:157`（运行时读取，方便测试覆盖）：

```python
def get_context_window_compaction_threshold_pct() -> int:
    """Get the compaction threshold percentage at runtime to support test overrides."""
    return environ_get_safe_int("CONTEXT_WINDOW_COMPACTION_THRESHOLD_PCT", default="95")
```

- **算的是什么**：`count_tokens(messages=..., tools=...)` —— 不只是历史文本，**工具 Schema 也计入**（工具多了，光凭能力描述就吃掉一截预算）。
- **为什么要加 `maximum_output_token`**：输入加输出都得装进窗口，所以判定用“输入 + 输出预留 > 窗口 × 95%”，预留不足就提前压缩，而不是等真超限才报错。
- **什么时候算需要**：默认阈值 95%。这个开关默认打开（`holmes/common/env_vars.py:150` 的 `ENABLE_CONVERSATION_HISTORY_COMPACTION = load_bool(..., default=True)`）。

真正执行压缩的是 `compact_if_necessary()`（`holmes/core/truncation/input_context_window_limiter.py:94`，中间省略）：

```python
    if ENABLE_CONVERSATION_HISTORY_COMPACTION and (
        initial_tokens.total_tokens + maximum_output_token
    ) > (max_context_size * get_context_window_compaction_threshold_pct() / 100):
        num_messages_before = len(messages)
        compaction_result = compact_conversation_history(
            original_conversation_history=messages, llm=llm, tools=tools
        )
        ...
        if compacted_total_tokens < initial_tokens.total_tokens:
            messages = compaction_result.messages_after_compaction
            ...
```

注意 `if compacted_total_tokens < initial_tokens.total_tokens:` 这个条件：**只有真的变短才替换历史**，否则记错误日志、保留原历史（`holmes/core/truncation/input_context_window_limiter.py:158`）：

```python
        else:
            logging.error(
                f"Failed to reduce token count when compacting conversation history. Original tokens:{initial_tokens.total_tokens}. Compacted tokens:{compacted_total_tokens}"
            )
```

压缩之后还有一道硬检查（同文件 `:163`）：

```python
    tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    if (tokens.total_tokens + maximum_output_token) > max_context_size:
        if ENABLE_CONVERSATION_HISTORY_COMPACTION:
            failure_msg = (
                f"Conversation history compaction failed to reduce tokens sufficiently. "
                ...
            )
        ...
        raise CompactionInsufficientError(failure_msg, events=events, compaction_usage=compaction_usage)
```

也就是说：**压缩不是“做过就算数”，压完还要再按窗口上限核对一次；仍超限就报错终止**，而不是发一个必然失败的请求。

调用点在每轮模型请求之前（`holmes/core/tool_calling_llm.py:1168`，中间省略）：

```python
            compaction_start_event = check_compaction_needed(self.llm, messages, tools) # 检查是否需要压缩上下文
            if compaction_start_event:
                yield compaction_start_event

            try:
                limit_result = compact_if_necessary(
                    llm=self.llm, messages=messages, tools=tools
                )  # 实际检查并执行压缩
            ...
            yield from limit_result.events
            messages = limit_result.messages
```

`check_compaction_needed()` 和 `compact_if_necessary()` 用同一个公式判断两次：前者的作用只是**先把“开始压缩”事件推给前端**，避免阻塞式的压缩调用让界面卡住；真正改 `messages` 的是后者。这解释了为什么 grep 会看到两处几乎一样的阈值判断。

**本学习环境的具体数字（教学推演，不是实测）**：窗口 128,000、阈值 95% → 触发线 121,600；输出预留 16,384 → 单次请求的历史 + Schema 超过 **105,216 tokens** 时才会压缩。这里有个容易搞错的坑：报告里的 `105,072`（第一次通过运行）是**整场调查累计**的 token，而压缩判定看的是**单次请求**的 token，两者口径不同，不能据此说“差一点就触发压缩”。真正的依据是报告里 Compactions 一栏为空 —— 按单次请求口径都没到线。

## 7 四道闸门的边界：证据在哪一层可能丢

把前面的机制放在一张表里，就能讲“分层”的取舍：

| 机制 | 进入上下文的内容 | 主要价值 | 主要代价或限制 |
| --- | --- | --- | --- |
| 查询减量与 `limit` | 过滤后的匹配项、较晚的 N 条 | 从源头减少输入 | 过滤在本地做时并不减少 API 读取；`limit` 偏向新数据 |
| 工具内截断 | 保留尾部 + 截断提示 | 单条结果不会无限长 | 有损，丢掉的是较早内容；阈值与方向由工具自己定 |
| Transformer 摘要 | 转换后的文本 | 单条输出更短 | 有损、增加模型调用与延迟；被替换的原文只能靠落盘或重查 |
| 单次结果落盘 | 文件路径、读取提示、预览 | 收到过的数据还在，可按需读回 | 依赖存储与文件访问能力；文件随会话清理；读回也占调用和上下文 |
| 历史 Compaction | 压缩后的简短历史 | 控制多轮累计占用 | 额外模型调用、有损；压完还要核对预算 |

一条必须讲清的边界：**落盘保留的是“进入落盘阶段”的结果。** 如果日志工具已经过滤、`limit` 或截断掉较早内容，或者 Transformer 已用摘要替换原文，后面的落盘无法找回之前丢掉的东西。落盘也不能保证模型一定去读 —— 它给的是“可读的路径”，不是“已经读到的证据”。

一份支持继续调查的记录应保留：`app-09`、准确 Pod 名、实际时间范围、错误原文、查询与截断条件（`filter` / `limit` / 时间窗）、配置核对结果，以及尚未确认的事项。可以手写“日志报告变量为空，Deployment 未提供 `DEPLOY_ENV`，待按应用要求确认正确值”。压缩提示不能保证无损；丢失位置在历史里就重新读取仍可访问的数据或重新查询，**已经在工具内丢掉的日志必须回数据源查**，不能只回读后续的落盘文件。

## 8 三个推演失败分支（用代码定位，不靠猜）

以下分支都没有在本 case 运行过，位置是照源码找的：

| 失败情况 | 代码位置 | 会看到什么 |
| --- | --- | --- |
| 单次结果超限且不能落盘（未启用 `bash` 或写文件失败） | `holmes/core/tools_utils/tool_context_window_limiter.py:131` 的失败分支 | 结果被清空并返回错误，要求模型收窄查询后重试；数据是真的丢了 |
| Transformer 摘要报错，或摘要没有变短 | `holmes/core/tools.py:499` 的回退、`holmes/core/tools.py:523` 的异常分支 | 保留转换前内容，记为“未应用”；单次结果仍大时继续交给落盘 |
| Compaction 没把历史压到可用预算 | `holmes/core/truncation/input_context_window_limiter.py:158` 与同文件 `:163` | 先保留原历史并记错误；硬检查仍超限时抛 `CompactionInsufficientError`，提示开新会话 |

再补一条本例特有的推演：如果日志被放大很多，`truncate_logs` 保留尾部、丢掉较早内容，而 `Environment variable DEPLOY_ENV is undefined` 恰好出现在启动时的第一批日志里，模型就会看到“容器反复退出”却看不到那句变量名 —— 只凭现象推不出根因。要补回它，只能回数据源重查（缩小时间窗、改进 `filter`），而不是期待落盘文件里有。

## 9 本 case 的实测对照（2026-10-06 实跑，不是推演）

数字全部来自 [本地环境与运行方法](../local-environment.md)，两次通过的运行：

| 指标 | 22:45 | 22:48 |
| --- | --- | --- |
| 结果 | 1/1 通过，correctness 1.0 | 1/1 通过，correctness 1.0 |
| 时长 / 轮数 / 工具调用 | 18.3 秒 / 7 轮 / 19 次 | 10.7 秒 / 5 轮 / 9 次 |
| 总 tokens | 105,072（输入 101,375、输出 3,697、推理 1,090） | 67,750（输入 65,978、输出 1,772、推理 302） |
| 被拒的 Bash 命令 | 6 条 | 0 条 |
| Compactions | 空 | 空 |

1. **证据完整保留**：两次都读到 `Environment variable DEPLOY_ENV is undefined`，并核对 Deployment 未提供该变量；回答以 `## Issue: payment-processing-worker crash-loops because DEPLOY_ENV is undefined` 开头。
2. **第 4 道闸门没有触发**：Compactions 为空。这既符合“本轮历史很短”的预期，也说明**没触发不等于没接线** —— 每次请求前都会照第 6 节的公式算一遍。
3. **单次数字波动不小**：19 次工具调用 / 105,072 tokens 与 9 次 / 67,750 tokens 得到同样结论。比较上下文相关改动时要固定 case、模型和判分标准，并重复运行。

**本页明确标注为“未运行”的部分**：大日志、`filter` / `exclude_filter` 的实际过滤效果、`limit` 截到 100 条、`truncate_logs` 的真实截断、单次结果落盘、Transformer 摘要、历史 Compaction、以及第 8 节三个失败分支。第 3、4、6 节里的数字（单次工具结果预算 19,200、绝对上限 25,000、Compaction 触发线 121,600、单次请求阈值 105,216）都是照当前默认值和本学习环境的两个覆盖值算出来的教学推演，不是运行观测值。另外，本次运行的日志与查询结果都很小，所以第 2、3 节的 `limit` 与截断“未动手”也是根据实测日志内容（只有一行关键输出）与代码推断的，报告里没有单独的触发记录。

## 10 2 分钟讲述与核心追问

> 在支付服务反复重启这个 case 里，要保留 `DEPLOY_ENV` 的具体错误和配置核对结果。数据进入上下文前有四道闸门：第一道在查询侧，分页、服务端条件和本地 `filter` / `limit` 把无关数据挡在外面，我会说明过滤发生在服务端还是本地，因为本地过滤减少的是模型输入、不是 API 读取；第二道在工具内部，日志工具按单次工具结果的 token 预算截断，按 token 判断、按字符裁剪，保留尾部并插入截断提示，所以是有损且偏向新日志的；第三道在框架的工具调用层，整条工具消息超限时把完整结果写到磁盘，历史里只留路径、读取提示和预览，前提是有 `bash` 能 `cat` 回来，写不成功就要求模型收窄查询；第四道在每轮模型请求前，把历史加工具 Schema 再加输出预留去比上下文窗口的 95%，需要时才做 Compaction，压完还要按窗口上限再核对一次，仍超限就报错。摘要那条路还在，但被标为历史机制、默认不生效，因为它有损且增加成本；当前优先落盘，代价是文件随会话清理、读回也占上下文。各层都要盯住证据：落盘保不住被工具截掉的日志，缺失时按丢失位置回读或重查。

每个追问先回答 30–60 秒，卡住后只补当前问题。

| 核心追问 | 回答需要包含什么 | 对应小节 |
| --- | --- | --- |
| 为什么需要分层，不能只做 Compaction？ | 无关数据、单条过大、历史累计是三种问题；越早减量，后面的压力越小 | 1、7 |
| 单次结果落盘与历史 Compaction 有什么区别？ | 处理对象（一条结果 vs 整段历史）、触发位置（工具返回后 vs 每次请求前）、保留的信息不同 | 4、6 |
| 为什么当前仓库优先落盘而不是摘要？ | 摘要有损且每次大调用都要额外模型成本；落盘保留收到的原文，可按需读回，但依赖 bash 与文件生命周期 | 5、7 |
| `DEPLOY_ENV` 那条日志在工具内被截掉，落盘能找回来吗？ | 不能：落盘收到的是已截断结果；要回数据源重查，核对时间窗与过滤条件，避免无证据断言 | 3、7、8 |
| 为什么预算要包含工具 Schema，并预留输出？ | 请求里不只有历史文本；输入加输出必须装进窗口，输出预留不足会表现为回答被截断或为空 | 6 |
| 两次运行差这么多 tokens，怎么比较方案？ | 单次波动大；固定 case、模型和判分标准，重复运行；用 Compactions 这类字段判断闸门有没有触发 | 9 |

答顺以后，再补两个深入问题：分页和 `limit` 能保证总结果不大吗？摘要失败或压缩后仍超限，系统怎样处理？

可选的预算推演（数字是教学假设）：假设窗口 10,000 tokens，历史 7,500、工具 Schema 1,000、输出预算 2,000，总计 10,500，超过窗口；需要减小占用并重新检查。具体阈值和输出预算以实际模型配置为准。

## 11 遇到具体疑问时怎样查证

每次选一个问题，最多查看两段相关资料。10 分钟后仍卡住，就记录问题，通过中文解释或具体输入输出继续学习。

| 当前疑问 | 查哪一小段 | 查到什么即可停止 |
| --- | --- | --- |
| 本例日志在哪里过滤、limit、截断？ | [logging_api.py](../../holmes/plugins/toolsets/logging_utils/logging_api.py#L54) 的 `truncate_logs()`；[kubernetes_logs.py](../../holmes/plugins/toolsets/kubernetes_logs.py#L677) 的 `filter_logs()` | 本地过滤、`limit` 取较晚内容、按 token 预算截掉较早内容 |
| 单次结果预算怎么算？ | [llm.py](../../holmes/core/llm.py#L327) 的 `get_max_token_count_for_single_tool()` | 窗口百分比与绝对上限取小值 |
| 落盘成功和失败时给模型什么？ | [tool_context_window_limiter.py](../../holmes/core/tools_utils/tool_context_window_limiter.py#L33) 的 `spill_oversized_tool_result()` | 找到路径、预览，以及不能保存时的错误分支 |
| 落盘文件活多久？ | [filesystem_result_storage.py](../../holmes/core/tools_utils/filesystem_result_storage.py#L26) 的 `tool_result_storage()` | 目录按会话创建、会话结束清理 |
| Transformer 什么时候不生效？ | [tools.py](../../holmes/core/tools.py#L458) 的 `_apply_transformers()`；[llm_summarize.py](../../holmes/core/transformers/llm_summarize.py#L1) 文件开头 | 无配置 / 非成功结果跳过；没有 fast model 时摘要跳过 |
| Compaction 为什么还要检查预算？ | [input_context_window_limiter.py](../../holmes/core/truncation/input_context_window_limiter.py#L26) 的 `check_compaction_needed()` 与 `compact_if_necessary()` | 输入、工具 Schema 与输出预留，以及压缩后的窗口上限检查 |

证据练习直接用这个 case 的错误原文和 Deployment 配置：先手写一份支持下一步的短记录，再故意删掉变量名，说明结论会受到什么影响。手工推演不代表摘要模型效果；[已有 Trace](../day1/runs/20260923-225409-379209/walkthrough.md) 也不是这个 Kubernetes case 的运行记录。

需要实验核对其他失败分支时，再选择现有 [单次结果测试](../../tests/core/tools_utils/test_tool_context_window_limiter.py) 或 [历史压缩测试](../../tests/core/truncation/test_compaction.py) 的相关断言。旧计划编写时的摘要 4 passed、单次结果 4 passed、历史压缩 2 passed 是历史记录；不能替代下面的最小验证，也不表示你的学习任务已完成。

### 11.1 最小验证：消息与落盘文件的区别（15 分钟）

在项目根目录运行两个现有离线用例，不调用模型或集群：

```bash
study_test_tmp=$(mktemp -d /tmp/holmes-study-day4.XXXXXX)
poetry run pytest tests/core/tools_utils/test_tool_context_window_limiter.py \
  -k 'test_within_token_limit or test_error_result_spills_with_short_inline_preview' \
  --basetemp="$study_test_tmp" --no-cov -n 0 -q
```

现有错误预览测试使用固定字符数断言，macOS 的默认长临时路径会影响它，因此命令为本次运行新建短临时目录；预算、Mock 和被测代码保持原样。用例的数据保存在该目录中，可在这次实验后查看。

运行前预测两个分支；运行后打开同名测试，只读输入与断言。第一条检查未超限时结果保持原样；第二条构造超大的错误结果，检查消息中的短预览与路径，并读取临时文件确认完整错误和 payload 被保存。写下“原始测试内容 → 消息里留下什么 → 文件里留下什么”，指出哪条断言支持你的结论。

这里的 token 计数由 Mock 提供，证明的是框架分支与文件保存，不证明真实 tokenizer 精度，也没有触发模型 Compaction。测试临时文件由 pytest 管理；不需要修改原 fixture。完成标志是能解释断言与证明范围，只有 `passed` 字样不算完成。

### 11.2 陌生变式与隔天复习

合上正文先答：日志结果带 `Saved to:`，预览只有健康检查，没有启动错误。你能说应用没有报错吗？如果工具结果还带“较早日志已截断”，你的下一步会怎样变化？分别列出已知信息、证据缺口和下一步。

<details>
<summary>回答后再看检查点</summary>

预览不代表整个文件，先在仍可访问的落盘文件里有针对性地查找；如果证据在落盘前已被工具截掉，文件也无法补回，需要回数据源按合适的时间或过滤条件重查。数据源可能已经不保留旧日志，无法补回时必须保留不确定性，不能套用 DEPLOY_ENV 的已知答案。

</details>

隔天用 5 分钟画出四层位置，解释一处有损处理，并脱稿回答“工具内丢失的证据，后面的落盘为什么恢复不了”。

## 完成标准与当天记录

- [ ] 能区分查询范围太宽、单条过大、历史累计过大。
- [ ] 能用日志场景解释各层的位置与设计理由。
- [ ] 能完成 2 分钟讲述，并回答 5 个核心追问。
- [ ] 能比较摘要与落盘的一项取舍，列出关键证据。
- [ ] 能解释当前默认方案和失败处理，并核对一条具体依据。
- [ ] 完成第 11.1 节最小验证，解释消息与文件断言及 Mock 的证明范围。
- [ ] 独立回答第 11.2 节陌生变式，给出补证据的下一步。

今天的核心成果是一页自己的回答。函数名记忆、阈值背诵和测试执行数量不作为验收标准。

- 实际学习日期与用时：
- 我对三种数据规模问题的解释：
- 我的分层处理路径：
- 我的 2 分钟设计回答：
- 我的录音实际时长与删改内容：
- 摘要与落盘的一项取舍：
- 必须保留的证据，以及缺失后怎样补回：
- 一个失败分支及处理方式：
- 核对的一条依据及它能证明的内容：
- 我实际使用或验证的方案与配置：
- 最小验证的结果、关键断言与证明范围（未执行则注明）：
- 陌生变式：已知信息、缺口、下一步，以及看检查点后的修订：
- 隔天复习日期与结果（独立答出 / 提示后答出 / 待补）：
- 对应简历的个人动作、可展示依据和未验证范围：
- 仍卡住的具体追问：

可以直接在聊天里开始：“带我学 Day 4，沿 09_crashpod 看 DEPLOY_ENV 证据怎样经过过滤、截断、落盘和历史压缩，每次先讲一个设计难点，再检查我的解释。”
