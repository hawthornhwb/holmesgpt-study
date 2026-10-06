# Day 6 讲义：效果评估（沿 09_crashpod 读源码）

2026-10-06 改写为讲义。对应 10-04 版简历中"智能故障诊断 Agent"的第四条：效果评估。

**这份文档是自足的：今天要看的评估代码已经按"一次评测怎么跑完"的顺序摘录在正文里，不必再打开仓库对照。** 每个片段都标了 `文件:行号`，想深挖时再跳过去。全部片段来自当前工作区，行号是 2026-10-06 核对过的。

> 片段说明：为便于阅读，摘录时做过少量排版整理 —— 换行、缩进、省略号（`...`）处有删减，个别英文注释在旁边改写成中文；函数名、参数名、字段名和逻辑顺序与源码一致。标了"讲义所加/译注"的注释不是源码原文。要逐字对照时按行号打开源文件。

今天只回答一个问题：**Agent 说出了 `DEPLOY_ENV` 之后，凭什么说它"答对了"、"是查出来的"、"代价可以接受"？**

评估要分成三件事讲，混在一起就会讲糊：**正确性**（LLM Judge 按 `expected_output` 打 1 / 0，第 3–4 节）、**过程可信度**（程序化硬断言：Skills 建议数、禁止工具、Token 上限，第 6 节）、**效率**（耗时、轮数、工具数、Token、费用、被拒命令，第 7–8 节）。

一条链上的四份源码：`tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml`（场景本体）→ `tests/llm/utils/classifiers.py`（Judge 的提示词、分数、模型）→ `tests/llm/utils/property_manager.py`（交给 Judge 哪些文本）→ `tests/llm/test_ask_holmes.py`、`tests/llm/utils/denied_commands.py`、`tests/llm/utils/reporting/`（硬断言、计时、指标口径）。

## 建议用法

整体顺序沿用 Day 3–Day 5 的做法：具体场景 → 评估设计 → 规则与 Judge 的分工 → 指标与取舍 → 面试讲述；时间紧就只走前 60 分钟，顺序读第 1–7 节，每读完一个片段先自己说一句"这段在评什么"，再读下一段。第 8–9 节是 2026-10-06 的实测对照与 Judge 适配，用来验证前面讲的机制确实在跑；第 10–11 节用于取舍讨论与口述。

| 顺序 | 时间 | 要做什么 | 留下什么 |
| --- | --- | --- | --- |
| 1 | 10 分钟 | 用第 1 节区分现象、根因与有证据的诊断 | 一条正确答案要求 |
| 2 | 20 分钟 | 读第 2–5 节：可复现场景、预期答案、规则断言与 Judge 的分工 | 一张评估流程图 |
| 3 | 20 分钟 | 读第 6–8 节：硬断言与效率指标口径，对照两次实测数字 | 一张"指标来自哪个字段"的表 |
| 4 | 25 分钟 | 第 10–11 节：比较质量和效率，练习 2 分钟回答，再回答 5 个追问 | 自己的回答和具体卡点 |
| 5 | 15 分钟 | 针对卡点按第 11 节的查证顺序核对至多两段资料，复述并记录 | 修订后的回答和一条依据 |

可分两次完成：前 3 项共 50 分钟，后 2 项共 40 分钟。你原先提供的 3–4 小时是可用时间，达到验收标准即可结束，额外时间优先补卡点。

## 1 场景：09_crashpod 定义了什么

整个评估的输入只有四项，`test_case.yaml` 开头就写完了用户问题与期望答案（`tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml:1`；后文简写为 `test_case.yaml:行号`，仓库里同名文件很多，指的都是这一个）：

```yaml
user_prompt: "What is the issue with payment-processing-worker?"
expected_output:
  - The `DEPLOY_ENV` environment variable is undefined or missing
```

三点解读：`user_prompt` 只给服务名，**没给 namespace、Pod 名，更没提环境变量**，评的是发现能力而不是复述能力；`expected_output` 是给 Judge 看的标准答案，**模型看不到它**，所以可以放心写具体验证值；这个 case 的期望只有一条，且具体到变量名 `DEPLOY_ENV`。

先判断三种教学回答，评估设计就是从这里长出来的：

| 回答 | 能说明什么 | 还缺少什么 |
| --- | --- | --- |
| “服务处于 CrashLoopBackOff” | 描述了现象 | 没有指出具体根因 |
| “可能是环境配置问题” | 提出了一个宽泛假设 | 缺少具体配置项与证据 |
| “DEPLOY_ENV 缺失，启动报错后退出” | 匹配这个场景的具体根因 | 还需结合实际工具记录核对证据来源 |

评估先定义"要检查什么"，再决定"怎么检查"。回答流畅、篇幅长、调用很多工具，都不能单独证明根因正确。

## 2 故障准备与清理：before_test / after_test

评估要有意义，先得有一个**真的坏着**的环境：`before_test` 先用 `kubectl create namespace app-09 || true` 建命名空间，再把 Deployment 清单用 `cat <<'EOF' | kubectl apply -f -` 交给 kubectl（`test_case.yaml:4`）。

容器启动脚本自己判断变量是否存在（`test_case.yaml:22`，只摘容器段）：

```yaml
        containers:
        - name: payment-processing-container
          image: busybox:1.36
          command: ["/bin/sh"]
          args: ["-c", "if [ -z \"${DEPLOY_ENV}\" ]; then echo 'Environment variable DEPLOY_ENV is undefined'; exit 1; else while true; do echo hello; sleep 10;done; fi"]
  EOF
```

这两段决定了"标准答案从哪来"：根因不是猜的，是**构造出来的**——Deployment 里根本没有 `DEPLOY_ENV`，脚本发现变量为空就打印一行明确的错误并 `exit 1`。于是"变量缺失"既是故障原因，也是容器日志里可查询的证据。

关键是 before_test 建完不走，还要**等到故障状态真的出现**（`test_case.yaml:28`）：

```yaml
  # Wait for pod to exist first with retry loop to handle race condition
  POD_EXISTS=false
  for i in {1..60}; do
    if kubectl get pod -l app=payment-processing-worker -n app-09 2>/dev/null | grep -q payment-processing-worker; then
      ...
  # Then wait for CrashLoopBackOff
  kubectl wait --for=jsonpath='{.status.containerStatuses[0].state.waiting.reason}'=CrashLoopBackOff pod -l app=payment-processing-worker -n app-09 --timeout=300s || exit 1
```

这里的取舍值得单独说：**先等 Pod 出现、再等 CrashLoopBackOff，两步都不省**。`kubectl wait` 在 Pod 还没被调度时就会失败，所以先用重试循环等对象出现；两步都以 `exit 1` 收尾，意味着"环境没准备好"会以**准备失败**的形式暴露，而不是让 Agent 面对一个不存在的故障、再被记成"诊断错误"。

清理也由 fixture 负责（`test_case.yaml:49`）：

```yaml
after_test: |
  kubectl delete namespace app-09
```

`after_test` 删除的是**测试命名空间**，属于环境管理，不是 Agent 提出的修复动作；整个 namespace 一起删，也是最省事的可重入做法。

## 3 期望答案与"不要提 Skills"

除了用户问题和期望答案，这个 case 还有一条**确定性行为要求**，注释写得很清楚（`test_case.yaml:58`）：

```yaml
# The SuggestSkills frontend tool is available to the agent in every eval by
# default (it mirrors the tool the Robusta UI attaches to each chat request,
# letting the agent propose reusable environment know-how as "skills"). This
# investigation is routine and teaches nothing reusable about the environment,
# so a well-behaved agent must NOT propose any skill here.
# `memories_generated: false` fails the test if it proposes one anyway -
# guarding against skill spam, where users get flooded with low-value
# suggestion chips after ordinary investigations.
memories_generated: false
```

（讲义译注：`SuggestSkills` 是前端工具，每次评测默认注入，对应生产环境 UI 每次对话都会挂上它，让 Agent 把"可复用的环境知识"提成 skills。这次调查是常规操作，学不到可复用的环境知识，所以合格的 Agent 不该提任何 skill；`memories_generated: false` 会在它仍然提了时判定失败，防止"skill 刷屏"——普通调查之后用户被一堆低价值建议淹没。）

为什么这条要求放在评估里而不是提示词里？因为**它检查的是行为结果，不是指令遵守**：指令可以写得客气，但只有程序数一数实际产出了几条建议，才能确定没提。"建议数量"因此属于硬断言，不属于 Judge 的语义评分。

五个字段或步骤对起来看：

| 字段或步骤 | 本例具体内容 | 为什么需要 |
| --- | --- | --- |
| before_test | 创建缺少 DEPLOY_ENV 的 Deployment，等 Pod 出现及 CrashLoopBackOff | 让预期根因有可重复查询的环境证据 |
| user_prompt | 只问 payment-processing-worker 有什么问题 | 让 Agent 自己发现 namespace、Pod、错误和配置 |
| expected_output | 指出 DEPLOY_ENV 未定义或缺失 | 给 Judge 一个明确的语义目标 |
| memories_generated: false | 不应产生 Skills 建议 | 用确定性行为要求约束这次常规调查 |
| after_test | 删除测试命名空间 | 管理测试环境；不属于 Agent 执行的修复 |

## 4 Judge 怎么打分：提示词、分数映射与模型

正确性由 `evaluate_correctness()` 负责，骨架就是"把期望和输出塞进提示词，让模型选 A 或 B"。严格模式的提示词（`tests/llm/utils/classifiers.py:151`，只留定义与选项）：

```python
    prompt_prefix = """
You are evaluating the correctness of an OUTPUT given by a LLM. You must return a score that
represents the correctness of that OUTPUT.
  ...
Make a judgement call whether each ELEMENT sufficiently matches the OUTPUT. ELEMENTS do
not need to appear verbatim or be a perfect match but their essence should be
present in the whole OUTPUT, even if it spans multiple sentences.
  ...
Return a choice based on the number of EXPECTED ELEMENTS present in the OUTPUT.
Possible choices:
- A: All elements are presents
- B: Either no element is present or only some but not all elements are present
"""
```

三点解读：**判的是"要点是否覆盖"，不是措辞**——原文写明 `do not need to appear verbatim`，所以"缺少 DEPLOY_ENV 环境变量"和"The deployment does not define DEPLOY_ENV"都算过，只讲 `CrashLoopBackOff` 不算；**strict 是全有或全无**——A 要求所有要点都在，B 是"一个都没有或只有一部分"，没有部分分，本 case 只有 1 条要点，多要点 case 里漏一条就是 0 分，这是最容易起争议的地方；**loose 换的是判定口径**（`classifiers.py:175` 起）——不再把 EXPECTED 拆成列表项，选项改成"The OUTPUT reasonably matches the EXPECTED content" / "does not match"，用于措辞差异本来就很大的 case。本 case 没写 `evaluation` 字段，默认 strict（`tests/llm/utils/property_manager.py:150`），所以严格口径就是这次实际生效的口径。

分数映射与模型参数在同一处（`classifiers.py:215`）：

```python
    classifier = LLMClassifier(
        choice_scores={"A": 1, "B": 0},
        use_cot=True,
        # With use_cot the judge writes its rationale inside the same JSON
        # tool call as the choice. autoevals' default max_tokens=512 truncates
        # that JSON on long rationales (large evaluation outputs like
        # 95_skill_memory_leak_detection), crashing scoring with
        # JSONDecodeError before any metric is recorded. Set the limit far
        # above any realistic rationale length so truncation cannot happen.
        max_tokens=16384,
        model=params.model,
        api_key=params.api_key if not params.is_azure else None,
        base_url=params.api_base if not params.is_azure else None,
    )
```

（讲义译注：开了 `use_cot` 之后，Judge 把理由和选项写进同一个 JSON 工具调用；autoevals 默认的 `max_tokens=512` 会在理由很长时截断这个 JSON，导致 `JSONDecodeError`、分数还没记录就崩掉，所以这里把上限抬到远高于任何现实理由长度。）

三个可引用的设计点：`choice_scores={"A": 1, "B": 0}` 把 Judge 的自由文本压成**二值分数**，后面的 `assert ... == 1` 才有意义；`max_tokens=16384` 是**给 Judge 的输出预算**，买的是"评分不会因为理由太长而崩"，代价是单次评审的潜在输出成本变高；`use_cot=True` 让 Judge 先写理由，理由作为 `rationale` 记进 trace（`classifiers.py:242`），人工复核争议结果时看的就是这段。

Judge 用哪个模型、连到哪，由 `get_classifier_model_params()` 决定（`classifiers.py:35`）：

```python
def get_classifier_model_params() -> ClassifierModelParams:
    """Get classifier model parameters from model list or environment variables."""
    if _model_list_exists():
        llm = create_eval_llm(CLASSIFIER_MODEL)
        model_for_api = llm.model
        ...
    else:
        model_for_api = CLASSIFIER_MODEL
        if AZURE_API_BASE:
            client_base_url = AZURE_API_BASE
        elif OPENAI_API_KEY:
            client_api_key = OPENAI_API_KEY
            client_base_url = OPENAI_API_BASE
        ...
```

两条分支的区别：**有 model list 时**，Judge 和被测模型从同一份 `model_list.yaml` 取配置（`create_eval_llm` 见 `tests/llm/utils/test_case_utils.py:695`，没有 model list 时退回 `DefaultLLM(model, tracer=tracer)`）；**没有 model list 时**，Judge 走环境变量：模型名取 `CLASSIFIER_MODEL`，密钥取 `OPENAI_API_KEY`，base_url 取 `OPENAI_API_BASE` —— 本机这一跑就是这条分支，`study/local-case.sh:57` 正是把这三个变量指向供应商接口，按 [本地环境与运行方法](../local-environment.md) 的说明**给 Judge 传原始 model ID，给调查模型加 `openai/` 前缀**。

还有一个容易讲错的边界：**Judge 不经过 litellm**，autoevals 直接用 openai SDK 发请求（`classifiers.py:44`），所以 litellm 风格的前缀要剥掉——这也解释了第 9 节的适配插件为什么能、且只能在这一层打补丁。

## 5 交给 Judge 的不是只有最终回答

`update_test_results()` 决定"评审输入"怎么拼。它先做一件很多人不知道的事——**把中间的 assistant 输出也拼进去**（`tests/llm/utils/property_manager.py:157`）：

```python
        # Build evaluation output, optionally including intermediate responses and tool calls
        evaluation_output = output or ""

        # Check if we should include intermediate outputs (based on CLI flag, defaults to True)
        include_intermediate = request.config.getoption("include_intermediate", True)

        # If flag is set and we have the full result with messages, include intermediate LLM outputs
        if (
            include_intermediate
            and result
            and hasattr(result, "messages")
            and result.messages
        ):
            intermediate_outputs = []
            for msg in result.messages:
                # Include assistant messages (LLM outputs) but skip tool messages
                if msg.get("role") == "assistant" and msg.get("content"):
                    ...
```

这是个**口径性质的取舍**：默认 `include_intermediate=True`（`conftest.py:58`），意味着"模型中途说对了、最后一句说错了"也可能拿到分；关掉它（`--no-include-intermediate`）就只看最终回答。两种都合理，但不能不声明——同一个 case 换开关可能换分数，比较两次运行前要确认开关一致。

第二件事：因为本 case 写了 `memories_generated`，Skills 建议也会被拼进评审输入（`property_manager.py:203`）：

```python
            else:
                memory_block += (
                    "The LLM emitted NO skill suggestions via the "
                    "SuggestSkills tool this turn.\n"
                )
```

所以这次 Judge 看到的是"最终回答 + 一句：本轮没有提任何 skill 建议"。但它不是唯一裁判：建议的**内容**归 Judge，建议的**条数**归第 6 节的硬断言，两者分工不同。

第三件事是 `include_tool_calls` 分支（`property_manager.py:211`）：

```python
        # Also include tool calls if requested
        if (
            test_case.include_tool_calls
            ...
        ):
            # Format tool calls as a string to include in evaluation
            tool_calls_text = "\n\n# Tool Calls\n\n"
            for i, tc in enumerate(result.tool_calls, 1):
                tool_calls_text += f"* Tool #{i}: {tc.description}\n"
                    ...
            evaluation_output = evaluation_output + tool_calls_text
```

**本 case 没有配置 `include_tool_calls`，默认是 `False`**（`tests/llm/utils/test_case_utils.py:129`），所以这次工具调用记录**没有**交给 Judge。这一点必须讲准：这次的 1 分只能证明"回答内容匹配"，**不能**证明"结论来自查询"。想证明后者，要么打开这个开关让 Judge 看记录，要么像 Day 3 讲义那样直接核对实际调用序列。

最后是调用与取分（`property_manager.py:233`）：

```python
        # Evaluate correctness with combined output
        correctness_eval = evaluate_correctness(
            output=evaluation_output,
            expected_elements=expected,
            parent_span=eval_span,
            evaluation_type=evaluation_type,
            caplog=caplog,
        )
        scores["correctness"] = correctness_eval.score
```

只有 `.score` 被留下：**Judge 自己的 Token、耗时与费用没有并回这次运行**，所以报告里的用量列只覆盖调查阶段。

## 6 程序化硬断言：哪些是代码判定，不是 Judge

Judge 打完分之后，`test_ask_holmes.py` 才做确定性检查。Skills 建议数最先做（`tests/llm/test_ask_holmes.py:195`）：

```python
    memory_check_failed = False
    if test_case.memories_generated is not None:
        actual_memories = len(suggested_memories)
        memory_check_failed = (
            test_case.memories_generated and actual_memories < 1
        ) or (not test_case.memories_generated and actual_memories != 0)
        if memory_check_failed:
            update_property(request, "actual_correctness_score", 0)
            scores["correctness"] = 0
```

三个细节面试里讲出来很加分：**断言是双向的**——`True` 要求至少 1 条，`False` 要求恰好 0 条，`None` 才是不检查，本 case 是"恰好 0 条"；**判失败时会把分数改写为 0**，先于报告与断言执行，所以不会出现"Judge 给了 1 分但 pytest 失败"的矛盾状态；`suggested_memories` 来自 `extract_suggested_skills(result.tool_calls)`（`test_ask_holmes.py:166`），数的是 **SuggestSkills 工具参数里的建议条数**，不是文本里提到"skill"的次数。

真失败时的报错也写明了意图（`test_ask_holmes.py:253`）：

```python
        raise AssertionError(
            f"Test {test_case.id} expected NO skill suggestions but the "
            f"LLM emitted {len(suggested_memories)}. This usually means the "
            f"SuggestSkills tool/prompt is being too eager. "
            f"Suggestions:\n{suggested_memories}"
        )
```

正确性断言、禁止工具断言与 Token 上限断言（`test_ask_holmes.py:275`、`:282`、`:294`）：

```python
    assert (
        int(scores.get("correctness", 0)) == 1
    ), f"Test {test_case.id} failed (score: {scores.get('correctness', 0)})\nActual: {output}\nExpected: {expected_output}"

    # Deterministic negative check: assert Holmes did NOT call tools it should
    # not have (e.g. an unnecessary lookup when the answer was already in the
    # request). This checks actual tool calls, not prompt content or LLM grading.
    forbidden_tools = getattr(test_case, "forbidden_tools", None) or []
    if forbidden_tools:
        called = [
            getattr(tc, "tool_name", None) for tc in (result.tool_calls or [])
        ]
        offending = sorted({t for t in called if t in forbidden_tools})
        ...

    # Check token limit if configured
    if test_case.max_tokens is not None:
        actual_tokens = result.total_tokens
        assert actual_tokens <= test_case.max_tokens, (
            f"Test {test_case.id} exceeded token limit: "
            ...
        )
```

这几个字段都是可选的，默认值决定了"不配置就不检查"：`include_tool_calls: Optional[bool] = False`、`max_tokens: Optional[int] = None`、`memories_generated: Optional[bool] = None`、`forbidden_tools: Optional[List[str]] = None`（`tests/llm/utils/test_case_utils.py:129`、`:151`、`:164`、`:238`）。

| 断言 | 判定依据 | 09_crashpod |
| --- | --- | --- |
| `memories_generated: false` | SuggestSkills 建议条数 | **本 case 已配置，实测通过**（两次运行均 0 条） |
| `forbidden_tools` | 实际调用过的工具名集合 | **本 case 未配置**（`None`），未做禁止工具检查 |
| `max_tokens` | `result.total_tokens` 与上限比较 | **本 case 未配置**（`None`），未做 Token 上限检查 |
| `include_tool_calls` | 是否把工具记录拼进 Judge 输入 | **本 case 未配置**（默认 `False`），Judge 看不到工具记录 |

一句话总结分工：**Judge 判"说了什么"，代码判"做了什么"**。把两者混为一谈，就会出现"1 分 = 证据充分"的过度解读——本 case 恰好就是不能过度解读的例子。

## 7 指标口径：耗时、轮数、工具、Token、通过率

计时的边界写在调查调用处（`test_ask_holmes.py:683`）：

```python
        with tracer.start_trace("Holmes Run", span_type=SpanType.TASK) as llm_span:
            start_time = time.time()
            result = ai.call(messages=messages, trace_span=llm_span)
            holmes_duration = time.time() - start_time
            ...
            if request:
                request.node.user_properties.append(
                    ("holmes_duration", holmes_duration)
                )
                if result.num_llm_calls is not None:
                    request.node.user_properties.append(
                        ("num_llm_calls", result.num_llm_calls)
                    )
                if result.tool_calls is not None:
                    request.node.user_properties.append(
                        ("tool_call_count", len(result.tool_calls))
                    )
```

**计时只包住 `ai.call()`**，这是最重要的一条口径：前面构造消息、后面 Judge 评审、以及 fixture 的 `before_test` / `after_test` 都不在 `holmes_duration` 里。所以"18.3 秒"是**用户等调查的时间**，不是这次评测的墙钟时间。同一处紧接着还定义了三个计数的来源（被拒命令那段放在下面单独展示）：`num_llm_calls`（轮数）、`len(result.tool_calls)`（工具调用数）、`extract_denied_commands(...)`（被拒命令）。注意后两者的包含关系：**被拒的命令也是工具调用**，所以"工具调用 19 次"里包含那 6 次被拒的尝试。

Token 与费用从 `LLMResult` 上抄进报告：`property_manager.py:262` 起把 `total_cost`、`total_tokens`、`prompt_tokens`、`completion_tokens`、`cached_tokens`、`reasoning_tokens`、`num_compactions` 逐个挂到 pytest 的 user_properties 上，报告再按下面的表逐列取用。

其中 `reasoning_tokens` 不是独立计的，它是供应商 usage 里**完成 token 的明细项**：`holmes/core/llm_usage.py:66` 的 `completion_details = usage.get("completion_tokens_details", None)`。

费用则取自 litellm 算好的 `response_cost`（`holmes/core/llm_usage.py:47`）：

```python
    try:
        cost_value = (
            response._hidden_params.get("response_cost", 0)
            if hasattr(response, "_hidden_params")
            else 0
        )
        cost = float(cost_value) if cost_value is not None else 0.0
    except (AttributeError, TypeError, KeyError):
        logging.debug("Could not extract cost from LLM response")
```

**这解释了"费用为空"**：`response_cost` 依赖 litellm 认识这个模型的单价，自定义供应商模型不在价格表里就是 0，而报告里 0 会渲染成 "—"（`github_reporter.py:630`：`cost_str = f"${cost:.4f}" if cost and cost > 0 else "—"`）。[本地环境与运行方法](../local-environment.md) 因此明确写了：未识别价格时报告里的零费用不能当作免费依据。

报告每一列怎么来的，看渲染代码最直接（`tests/llm/utils/reporting/github_reporter.py:604`、`:611`、`:684`）：

```python
        # Format time (plain, no inline comparison)
        exec_time = result.get("holmes_duration")
        time_str = f"{exec_time:.1f}s" if exec_time and exec_time > 0 else "—"
        ...
        # Format turns (LLM calls)
        num_llm_calls = result.get("num_llm_calls")
        if num_llm_calls and num_llm_calls > 0:
            turns_str = str(num_llm_calls)
        ...
        tool_call_count = result.get("tool_call_count")
        if tool_call_count and tool_call_count > 0:
            tools_str = str(tool_call_count)
        ...
        memories_count = result.get("memories_count", 0) or 0
        skill_generated_str = str(memories_count) if memories_count else "—"
        ...
        denied_commands = result.get("denied_commands") or []
        total_denied_commands += len(denied_commands)
        denied_commands_str = _fmt_denied_commands(denied_commands)
```

费用与 Token 的取数与回退（`github_reporter.py:630`、`:636`）：

```python
        cost = result.get("cost", 0)
        cost_str = f"${cost:.4f}" if cost and cost > 0 else "—"
        ...
        total_tokens = result.get("total_tokens", 0) or 0
        prompt_tokens = result.get("prompt_tokens", 0) or 0
        completion_tokens = result.get("completion_tokens", 0) or 0
        cached_tokens = result.get("cached_tokens")
        num_compactions = result.get("num_compactions", 0) or 0
        ...
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
```

"被拒命令"的定义单独成文件：`tests/llm/utils/denied_commands.py:25` 给出四个标记 `"Command blocked"`、`"Invalid prefix"`、`"requires approval"`、`"Tool call rejected"`；判定函数 `_is_denied_result()`（同文件 `:33`）在状态为 `APPROVAL_REQUIRED` 时直接算拒绝，注释解释了原因：评测环境没有交互式审批人，"需要审批"实际等于"被拒"；状态为 `ERROR` 时再看错误信息是否命中四个标记。

提取循环只统计 `bash`（`tests/llm/utils/denied_commands.py:58`）：

```python
    for tc in tool_calls:
        if getattr(tc, "tool_name", None) != "bash":
            continue
        result = getattr(tc, "result", None)
        if result is None or not _is_denied_result(result):
            continue
        params = getattr(result, "params", None) or {}
        command = getattr(result, "invocation", None) or params.get("command") or getattr(tc, "description", None)
        if command:
            denied.append(str(command))
```

"通过率"的分母有两个口径，别混（`github_reporter.py:518`、`:531`）：

```python
        if result["test_type"] == "ask":
            ask_holmes_total += 1
            if status.is_skipped:
                ask_holmes_skipped += 1
            elif status.is_setup_failure:
                ask_holmes_setup_failures += 1
            elif status.passed:
                ask_holmes_passed += 1
    ...
    if ask_holmes_total > 0:
        markdown += f"- ask_holmes: {ask_holmes_passed}/{ask_holmes_total} test cases were successful, {ask_holmes_regressions} regressions"
```

"通过"两个字由两件事共同决定，后半段在 `tests/llm/utils/test_results.py:63`：

```python
    @property
    def passed(self) -> bool:
        # A test only counts as passed when BOTH the judge accepted the answer
        # (actual_correctness_score == 1) AND pytest itself reported success.
        ...
        if self.status and self.status not in ("passed", ""):
            return False
        return self.actual_score == 1
```

两条合起来：**GitHub 摘要行的分母是该轮所有 ask 结果行**——包含 skipped 与 setup failure，所以它不等于"有效运行通过率"；而"通过"要求 Judge 给了 1 分**且** pytest 状态是 passed，缺一不可。要讲"有效运行"，用终端报告的失败计数口径（`tests/llm/utils/reporting/terminal_reporter.py:60`）：`ResultType.FAILED` 会排除 mock failure、setup failure、throttled 与 skipped。**讲指标时必须先说清用哪个分母**，否则"1/1 通过"和"有效运行 1/1 通过"会被听成同一件事。

合成一张"列 → 口径"表（列名取自 `github_reporter.py:552` 的表头）：

| 报告列 | 实际取的值 | 需要声明的边界 |
| --- | --- | --- |
| Time | `holmes_duration` | 只含 `ai.call()`；不含 Judge、环境准备与清理 |
| Turns | `num_llm_calls` | 调查期间的模型请求次数；不含 Judge 的请求 |
| Tools | `len(result.tool_calls)` | **包含被拒的尝试**；与"有效工具调用"不是一回事 |
| Cost | `result.total_cost` | `> 0` 才渲染，否则 "—"；未识别单价时为空 |
| Total / Input / Output / Reasoning | `total_tokens` / `prompt_tokens` / `completion_tokens` / `reasoning_tokens` | 只含调查阶段；Total 为 0 时回退成输入 + 输出；Reasoning 是 Output 的明细项，缓存未知时算不出非缓存量 |
| Skill Generated | `memories_count` | 建议条数；0 渲染成 "—"，与"未注入工具"看起来一样 |
| Compactions | `num_compactions` | 0 也渲染成 "—" |
| Denied commands | `extract_denied_commands()` | 只统计 `bash` 工具 |

## 8 两次通过运行的数字怎么对上口径

以下数字全部来自 [本地环境与运行方法](../local-environment.md)（2026-10-06 实跑），本页不新增任何实测数字；那次实测只覆盖 09_crashpod 的三次运行，大日志、过滤与截断、摘要、落盘、Compaction 与审批仍是同场景推演、没有运行（同页"范围说明"）。报告里另有 Cached / Max input / Max output 等列，本页只讲口径、不引用具体数字。

| 报告列 | 22:45 那次 | 22:48 那次 | 口径来源 |
| --- | --- | --- | --- |
| Time | 18.3 秒 | 10.7 秒 | `holmes_duration`（只含调查） |
| Turns | 7 | 5 | `num_llm_calls` |
| Tools | 19 | 9 | `len(result.tool_calls)`（含被拒） |
| Total tokens | 105,072 | 67,750 | `result.total_tokens` |
| Input / Output | 101,375 / 3,697 | 65,978 / 1,772 | `prompt_tokens` / `completion_tokens` |
| Reasoning | 1,090 | 302 | `completion_tokens_details.reasoning_tokens` |
| Compactions | 空（0） | 空（0） | `num_compactions`，0 显示 "—" |
| Denied commands | 6 | 0 | `extract_denied_commands()` |
| Cost | 空 | 空 | 未识别单价，0 显示 "—" |
| correctness | 1 | 1 | Judge |

三处可以直接讲的对账：

1. **Total = Input + Output**：101,375 + 3,697 = 105,072；65,978 + 1,772 = 67,750。报告在 `total_tokens` 有值时就直用它，只有为 0 才回退成两者之和（`github_reporter.py:646`），所以这个等式是**核对**，不是算法。
2. **Reasoning 是 Output 的明细**：1,090 ⊂ 3,697、302 ⊂ 1,772，来自 `completion_tokens_details`；把 Reasoning 再加进 Total 就是重复计算。
3. **被拒命令 6 条就在 19 次工具调用里**：`extract_denied_commands(result.tool_calls)` 遍历的正是同一个 `result.tool_calls`。第二次只有 9 次调用、0 条被拒，更快也更省，**结论相同**——这就是"工具用得多不等于诊断得好"的实测版本。

两点边界。其一，**单次数字波动很大**（19 次调用 / 18.3 秒 vs 9 次调用 / 10.7 秒），比较改动前后必须固定 case、模型与判分标准并重复运行；报告摘要那行 `1/1` 只说明这一轮这一个 case 的结果。其二，**不要与 Day 3 讲义的计数混用**：那里列的"工具名 + 关键参数"是关键调用序列（用来讲清 YAML 工具与日志工具各自的路径），本页的 19 / 9 是代码口径下的总计数（含被拒尝试）。

## 9 22:39 那次失败，不是"诊断错了"

第一次运行失败，两个原因都与调查能力无关（记录见 [本地环境与运行方法](../local-environment.md)）：最终回答为空、Judge 在记录分数前中断，报告显示 `Test not executed` 与 0%。

`Test not executed` 不是错误信息，而是**运行前写入的默认值**（`property_manager.py:49`）：

```python
    request.node.user_properties.append(
        ("actual", "Test not executed")
    )  # Will be overwritten if test runs
    request.node.user_properties.append(
        ("actual_correctness_score", 0)
    )  # Will be overwritten if test runs
```

（讲义译注：注释的意思是"测试真的跑起来就会被覆盖"。）所以看到 `Test not executed` + 0 分，正确解读是**流程没走完**，不是"Judge 认为答案是错的"。两件事要分开归因：回答为空——拼接评审输入时用的是 `evaluation_output = output or ""`（`property_manager.py:158`），最终回答为空就变成空字符串，Judge 没有内容可评；Judge 中断——`evaluate_correctness()` 崩在记录分数之前，这个 case 就没有分数，而 `TestStatus.passed` 要求 pytest 状态是 passed（`test_results.py:72`），所以这次也**不可能**被统计成通过。

**Judge 适配（本机特有，必须讲清边界）**：这家供应商的思考模式拒绝任何指定函数的 `tool_choice`，返回 `HTTP 400 Thinking mode does not support this tool_choice`；而 autoevals 的 `LLMClassifier` 一律强制一个具名函数（本机安装的 autoevals 0.0.129，`autoevals/llm.py:206`：`tool_choice={"type": "function", "function": {"name": "select_choice"}}`），所以 Judge 必然失败。仓库因此提供 [study/judge_thinking_adapter.py](../judge_thinking_adapter.py) 作为 pytest 插件，它自述的边界是（`study/judge_thinking_adapter.py:19`）：只给 Judge 请求加 `thinking={"type": "disabled"}`，Judge 的提示词、模型、期望答案、工具 schema 和解析都不变；调查模型走 litellm，仍是正常思考模式。

实现只对"钉死具名函数"的请求生效（`study/judge_thinking_adapter.py:50`）：

```python
    # Only touch requests that pin a specific function, which is the autoevals
    # Judge shape. Anything else (plain chat, "auto") is passed through as-is.
    tool_choice = request.get("tool_choice")
    if not (isinstance(tool_choice, dict) and tool_choice.get("function")):
        return request

    extra_body = dict(request.get("extra_body") or {})
    extra_body.setdefault("thinking", {"type": "disabled"})
    request["extra_body"] = extra_body
```

| | 适配插件改变了什么 | 没改变什么 |
| --- | --- | --- |
| Judge 请求 | 增加 `extra_body["thinking"] = {"type": "disabled"}`，用 `setdefault` 不覆盖已有值 | 模型名、提示词、`expected_output`、工具 schema、解析逻辑 |
| 调查请求 | 不经过这条路径（走 litellm），仍是供应商默认的思考模式 | 工具选择、预算、计时口径 |
| 仓库代码 | 只在 pytest 里多加载一个插件（`study/local-case.sh:124` 的 `-p judge_thinking_adapter`） | 评测 harness 本身未改；去掉 `-p` 即回到原状 |

结论要一起说：**这次分数来自"关闭思考的 Judge + 正常思考的调查模型"这一组合**。插件自述里的验证方式是正例（指出 `DEPLOY_ENV` 缺失）得 1 分、反例（只描述 `CrashLoopBackOff`）得 0 分；换用原生支持强制工具调用的供应商时，应去掉插件再复测。

## 10 猜中、误判与方案比较

**防止猜中。** 如果 `expected_output` 只要求"存在配置问题"，模型可能凭经验满足要求。更强的设计是检查**只有查询才能发现**的具体值：变量名、错误码、资源标识、验证码，再核对证据来源。09_crashpod 的具体变量名比泛泛的"配置异常"更有区分力，但这不等于仅凭这个案例就完全排除了猜测——尤其本 case **没有**把工具记录交给 Judge（第 5 节），所以"答对"和"查过"在这份报告里是两件事。可提出的改进：核对实际返回是否包含该变量的日志或配置证据、检查结论是否引用相符资源、并在不同故障实例中改变待发现的配置项。这里是评估设计练习，**尚未修改测试**；也不要固定某个工具或调用顺序，读 Deployment 配置同样足以发现根因。

**Judge 可能误判，规则也可能过窄。** 固定清楚的要求、人工复核有争议的结果（理由就在 trace 的 `rationale` 里）、再调整标准，分数才有解释力。失败分析按四类讲：数据没查到、证据已到但推理错误、预算或调用过程失败、评审标准或 Judge 本身有问题。先看一条具体 Trace，再讨论总体通过率。

**比较两个方案。** 以下数字全部是教学假设，未表示实测结果或当前模型价格：

| 方案 | 通过次数 / 有效运行次数 | 平均调查耗时 | 平均模型调用次数 | 平均费用 |
| --- | --- | --- | --- | --- |
| A | 8 / 10 | 40 秒 | 6 次 | 0.02 美元 |
| B | 9 / 10 | 65 秒 | 8 次 | 0.04 美元 |

先说清 B 的通过率更高、耗时与费用也更高，再结合使用目标判断是否值得；10 次运行样本很少，应查看失败类型并重复验证。比较时固定场景、工具权限、评价标准、`include_intermediate` 开关与调用预算，并记录模型和配置变化——Judge 模型或提示词一变，可比性就断了。

## 11 2 分钟讲述与核心追问

先借助骨架讲一遍，再合上文档用自己的话讲第二遍。个人实际跑过的场景与结果填在文末；本机已有记录（[本地环境与运行方法](../local-environment.md)），可以直接引用，也可以自己再跑一次。

> 在 09_crashpod 里，fixture 先创建缺少 `DEPLOY_ENV` 的支付服务并等到 `CrashLoopBackOff`，再让 Agent 只根据服务名调查。评估分三层：正确性由 LLM Judge 按 `expected_output` 判 1 / 0，strict 口径要求所有要点都在，评审输入默认还会带上中间 assistant 输出；本 case 没开 `include_tool_calls`，所以 1 分只证明回答内容匹配，不证明结论来自查询。过程可信度由程序判定：`memories_generated: false` 数 SuggestSkills 的建议条数，必须恰好 0；`forbidden_tools` 和 `max_tokens` 这两条断言本 case 没有配置。效率指标各有口径：耗时只包住 `ai.call()`，轮数是 `num_llm_calls`，工具数是 `len(tool_calls)` 且含被拒尝试，Token 与费用只含调查阶段、未识别单价时费用为空，通过率的分母要先说清是全部结果行还是有效运行。本机两次通过是 18.3 秒 / 7 轮 / 19 次调用 与 10.7 秒 / 5 轮 / 9 次调用，结论相同但数字波动很大，所以比较方案要固定条件并重复运行。

每个追问先回答 30–60 秒，卡住后只补当前问题：

| 核心追问 | 回答需要包含什么 | 对应小节 |
| --- | --- | --- |
| 怎样构造故障场景，正确答案从哪里来？ | 已知根因、真实可查询证据、环境准备验证；答案用于评价而不提前交给 Agent | 1、2 |
| 为什么同时使用规则断言和 LLM Judge？ | 语义匹配与确定性条件的分工；`memories_generated` 是双向计数断言，Judge 判内容 | 4、6 |
| 怎样判断答案来自调查，避免猜中？ | 具体可发现信息与实际工具证据；`include_tool_calls` 与硬断言的区别，本 case 两者都没用于"证明证据" | 5、10 |
| 通过率和耗时、调用次数、Token、费用怎样一起看？ | 质量与效率一起比较；明确分母、计时边界与成本范围 | 7、8 |
| 怎样比较两个方案，失败或 Judge 误判后怎么办？ | 固定场景与评估条件、重复运行；查看 trace 的 rationale、分类问题、人工复核 | 9、10 |

答顺以后，再补两个深入问题：单元测试通过为何不能证明模型诊断正确？一次评测通过为何不足以说明结果稳定？

**遇到具体疑问时怎样查证。** 每次选一个问题，最多查看两段相关资料；10 分钟后仍卡住，就记录问题，通过中文解释或输入输出推演继续学习。查证顺序建议：**场景与预期根因**看第 1–3 节（区分用户看到的问题与评估用的答案，以及"不许提 Skills"的理由）；**Judge 怎么评分**看第 4 节（要点列表、strict 的 A / 1 与 B / 0、模型与 base_url 来源）；**交给 Judge 什么**看第 5 节；**哪些是程序判定的硬条件**看第 6 节；**耗时、轮数、工具数、Token、费用的口径**看第 7 节；**这次实测数字怎么解释**看第 8–9 节。

核心学习可以用场景定义、教学回答与假设指标完成。本机已经有一条真实结果，可以用它核对根因、证据、耗时和用量；方案、已有实测与纸面推演分别记录。需要创建或运行新 eval 时，再按仓库的 create-eval 流程处理环境与验证。

## 12 完成标准与当天记录

- [ ] 能用一个故障场景解释已知根因、证据与答案要求。
- [ ] 能说清规则断言、LLM Judge 和工具调用记录的分工。
- [ ] 能完成 2 分钟讲述，并回答 5 个核心追问。
- [ ] 能解释质量与效率指标的口径，以及一项防猜测或误判措施。
- [ ] 核对一条现有依据，区分纸面方案与实际评测结果。

今天的核心成果是一页自己的回答。评测运行次数和源码阅读数量不作为验收标准。

- 实际学习日期与用时：
- 我选择的故障场景、已知根因和可查询证据：
- 我的评估流程与答案要求：
- 规则断言和 Judge 的分工：
- 我的 2 分钟设计回答：
- 指标的分母、计时和成本范围：
- 一个防猜测措施与一个失败分析例子：
- 核对的一条依据及其证明范围：
- 我实际跑过的场景与结果，未运行则注明：
- 仍卡住的具体追问：

可以直接在聊天里开始：“带我学 Day 6，沿 09_crashpod 看答对 DEPLOY_ENV 后如何验证证据与效率，每次先讲一个设计难点，再检查我的解释。”
