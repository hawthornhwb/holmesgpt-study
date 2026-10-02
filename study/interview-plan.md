# HolmesGPT：未来五天面试复习计划

规划日期：2026-10-03。依据《黄伟博_AI应用开发_简历.pdf》中“智能故障诊断 Agent”的四条项目描述，以及你已完成 Day 1、Day 2 的反馈。

**目标：五天后能脱稿介绍项目，逐条解释简历里的技术点，用源码、测试或实际 Trace 支撑回答，并说清自己的工作与方案取舍。**

接下来五天记作 Day 3–Day 7，按每次学习推进，不固定为日历日期。每天 3 小时核心任务，另留最多 1 小时补薄弱点或做实验，总计约 15–20 小时。

## 五天总表

| 阶段 | 主问题 | 对应简历 | 当日产出 |
| --- | --- | --- | --- |
| [Day 3](day3/README.md) | 一个新工具如何接入，模型实参怎样处理？ | 工具插件化；项目介绍初稿 | 工具链路图、四种参数边界、2 分钟口述 |
| Day 4 | 大结果和长历史怎样进入有限上下文？ | 分层上下文控制 | 三层控制与落盘对比表、关键证据保留清单 |
| Day 5 | 模型选择操作后，程序怎样控制权限与执行？ | 工具调用安全 | 安全边界表、审批时序、2 分钟口述 |
| Day 6 | 如何用证据完成一次调查，失败时怎么办？ | Agent Loop；完整项目案例 | 一个完整案例、异常表、效果评估方案 |
| Day 7 | 能否应对连续追问并说明个人贡献？ | 四条串讲 | 90 秒 / 3 分钟 / 8 分钟讲述、模拟面试记录 |

Day 1、Day 2 的主链路与消息流直接复用：Day 3 的简短复述并入第 1 项 20 分钟，Day 4 起每天开头用 15 分钟复习。每天优先完成“解释机制 → 核对行为 → 口述取舍”，当天代码阅读范围由下面列出的入口限定。

## Day 3：工具插件化与参数处理

按 [新 Day 3 文档](day3/README.md) 执行。核心任务共 180 分钟：简历映射和介绍初稿 20 分钟、接入链路 40 分钟、参数边界 40 分钟、已有测试 30 分钟、追问口述 30 分钟、记录验收 20 分钟。

第 4 小时选做 [本地工具接入实验](day3/tool-lab.md)，保留亲手接入工具的体验。先完成四种参数边界和口述，再决定是否继续实验。

## Day 4：分层上下文控制

**当天问题：为什么查询减量、单次结果处理和历史 Compaction 要分别做？**

180 分钟安排：复习 15、源码阅读 45、对比整理 30、离线验证 50、口述 25、记录 15。

先读以下入口：

| 环节 | 入口 | 要回答什么 |
| --- | --- | --- |
| 查询范围与结果过滤 | [Loki API](../holmes/plugins/toolsets/grafana/loki_api.py)；[Kubernetes YAML](../holmes/plugins/toolsets/kubernetes.yaml) 的 `kubernetes_jq_query` | 时间、对象、数量和字段在哪一侧过滤？ |
| 单次结果 Transformer | [tools.py](../holmes/core/tools.py) 的 `_apply_transformers()`；[llm_summarize.py](../holmes/core/transformers/llm_summarize.py) | 什么时候摘要？阈值单位是什么？失败与摘要膨胀怎么办？ |
| 当前大结果落盘 | [tool_context_window_limiter.py](../holmes/core/tools_utils/tool_context_window_limiter.py) 的 `spill_oversized_tool_result()` | 上下文保留什么？原始结果在哪？不能落盘时怎么办？ |
| 长历史 Compaction | [input_context_window_limiter.py](../holmes/core/truncation/input_context_window_limiter.py) 的 `compact_if_necessary()`；[compaction.py](../holmes/core/truncation/compaction.py) | 在哪轮之前检查？预算是否包含工具 Schema 与输出？压缩后保留哪些消息？ |

先读 [项目上下文管理说明](../docs/reference/context-management.md)，再画“收窄查询 → 单次结果处理 → 加入历史 → 全历史压缩 → 下一轮请求”。

必须核对三处实际行为：

- `llm_summarize` 在当前源码中已标为历史机制，默认关闭；使用摘要需要相应配置。当前大结果方案优先保留完整结果到磁盘，给模型路径、预览与读取提示。
- `jq` 通常是客户端过滤；`kubernetes_jq_query` 的 API 分页不等于总结果数量上限。Loki 的 `query/start/end/limit` 则直接进入服务端请求。
- 单次结果摘要与全历史 Compaction 都可能丢失信息。提示保留关键证据是设计要求，不能承诺无损。

运行下面少量离线测试，先预测再检查断言：

```bash
poetry run pytest tests/core/transformers/test_llm_summarize.py \
  -k 'test_should_apply_no_fast_model or test_should_apply_with_custom_threshold or test_transform_empty_response or test_integration_with_tools_non_expanding_logic' \
  -q --no-cov -n 0

poetry run pytest tests/core/tools_utils/test_tool_context_window_limiter.py \
  -k 'test_within_token_limit or test_edge_case_exactly_at_limit or test_spill_to_disk_without_images' \
  -q --no-cov -n 0

poetry run pytest tests/core/truncation/test_compaction.py \
  -k 'test_compaction_output_shape_user_summary_no_trailing_system or test_compaction_returns_original_history_when_fallback_also_fails' \
  -q --no-cov -n 0
```

这些选择使用 Mock / Fake 模型；`test_compaction.py` 还包含真模型测试，今天按上述筛选运行。

产出一张对比表：处理对象、触发条件、是否额外调用模型、能否找回原文、失败处理。再写关键证据清单：资源名、命名空间、时间、错误原文、查询条件、已排除假设、下一步待办。

验收：用 2 分钟回答“为什么不能只靠 Compaction”“摘要丢证据怎么办”“当前落盘与摘要方案怎么选”。节省比例与性能收益只引用自己实际测量的数据。

第 4 小时：从现有 Trace 取一条工具输出，比较全文、短预览与手工摘要分别保留了哪些证据，并指出需要重新读取原文的情形。

## Day 5：安全边界与审批

**当天问题：RBAC、参数处理、命令策略与审批分别提供什么保护？**

180 分钟安排：复习 15、源码阅读 45、边界与审批图 40、离线验证 40、口述 25、记录 15。

| 层次 | 入口 | 必须解释的边界 |
| --- | --- | --- |
| Kubernetes 权限 | [RBAC 模板](../helm/holmes/templates/holmesgpt-service-account.yaml)；[Helm 配置](../helm/holmes/values.yaml) | 允许访问哪些资源与命名空间？当前默认范围是什么？ |
| YAML 参数处理 | [tools.py](../holmes/core/tools.py) 的 `sanitize()`、`YAMLTool._build_context()` | 参数化模板与完整 Bash 命令为什么是两条处理路径？ |
| Bash 命令策略 | [validation.py](../holmes/plugins/toolsets/bash/validation.py) 的 `validate_command()`；[bash_toolset.py](../holmes/plugins/toolsets/bash/bash_toolset.py) 的 `requires_approval()` | 如何得到允许、拒绝、需要审批三种结果？ |
| 审批恢复 | [tool_calling_llm.py](../holmes/core/tool_calling_llm.py) 的 `_execute_tool_decisions()` | 缺少审批交互会怎样？用户决定如何关联原调用？ |
| Approval Token | [approval_tokens.py](../holmes/utils/approval_tokens.py) 的 `mint_token()`、`verify_token()` | 调用 ID、工具名、参数摘要为何必须绑定？ |

画出“模型请求操作 → 参数与策略检查 → APPROVAL_REQUIRED → 用户决定 → Token 校验 → 恢复执行”，旁边单独标注集群 RBAC 的约束位置。

当前实现的口述边界：默认 Kubernetes RBAC 是集群范围读权限，收窄命名空间需要配置；项目存在可选写操作，实际权限需结合工具与配置判断；应用命令策略不等于操作系统沙箱；签名 Token 用于绑定获批内容，不等于身份认证或完整的一次性防重放机制。

用下面两组现有防御测试核对审批决策与内容绑定：

```bash
poetry run pytest tests/toolsets/bash/test_bash_approval_flow.py \
  -k 'test_approval_not_required_for_allowed_command or test_unapproved_command_returns_approval_required_status or test_denied_command_returns_error' \
  -q --no-cov -n 0

poetry run pytest tests/test_approval_tokens.py \
  -k 'test_mint_then_verify_round_trip or test_verify_tolerates_semantically_equal_args or test_verify_rejects_all_failure_modes_uniformly' \
  -q --no-cov -n 0
```

产出安全表：控制点、输入、允许 / 拒绝 / 待审批条件、证明范围与限制。模板或单测通过只能记录对应层的证据，真实集群权限按实际验证情况填写。

验收：脱稿解释“为什么 Prompt 不能代替权限控制”“为什么参数与命令检查要分开”“审批为什么需要内容绑定”“RBAC 与应用检查如何配合”，再压缩成 2 分钟回答。

第 4 小时：阅读 [命名空间 RBAC 测试](../tests/test_namespace_scoped_rbac_helm.py) 与 [YAML 参数防御测试](../tests/plugins/toolsets/test_toolset_command_injection.py) 的断言，补齐防御边界与各项检查的证明范围。

## Day 6：一个完整案例、失败处理与评估

**当天问题：你怎么证明 Agent 的结论来自证据，而且能处理调查失败？**

180 分钟安排：复习 15、已有 Trace 重述 35、Kubernetes 案例 50、异常与测试 35、效果评估 30、记录 15。

先用 [Day 1 已有 Trace](day1/trace-guide.md) 复述一条实际链路：用户问题 → 模型提出工具请求 → 工具返回证据 → 下一轮动作 → 最终结论。每个结论标一条实际证据，避免只讲设计。

再基于 [09_crashpod 案例定义](../tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml) 整理一个 Kubernetes 案例，今天作为**案例阅读与推演**，运行状态由实际执行记录决定：

1. 用户只问某个服务为什么异常，模型需要发现资源状态、事件和日志。
2. `CrashLoopBackOff` 是症状，不能直接当根因；日志中的 `DEPLOY_ENV` 缺失是更具体的证据。
3. 说明还需要哪些资源配置证据来交叉核对，哪些竞争假设已经排除、哪些还没排除。
4. 最终输出根因、依据、建议和不确定性；新增建议要说明如何验证生效。

做一张异常表：工具错误、无数据、非法参数、审批拒绝、模型请求失败、上下文压缩失败、达到最大步数。每项写“下一轮能否继续、给谁反馈、怎样避免编造结论”。

阅读 [call_stream()](../holmes/core/tool_calling_llm.py) 的停止判断，再运行已有测试：

```bash
poetry run pytest tests/test_tool_calling_llm.py \
  -k 'test_call_continues_after_tool_error or test_max_steps_forces_termination or test_max_steps_exceeded_raises' \
  -q --no-cov -n 0
```

需要说准确：模型不再请求工具时结束；最后允许轮次撤去工具；若仍异常返回工具调用，超步数保护会报错。最大步数提供调用预算，不保证在预算内一定找到根因。

效果评估写成一页方案：程序逻辑测试验证协议与执行行为；端到端评估检查正确根因、证据引用、必要工具调用、误报或幻觉、请求轮次、耗时与成本。选择有可发现特征的真实证据，避免只检查通用回答；方案和实际跑出的结果分别记录。

验收：用 3–5 分钟讲完整案例，再回答“证据不足怎么办”“状态异常为何不是根因”“单测通过为何不代表诊断准确”“预算用尽怎样结束”。

第 4 小时：把案例改为“权限不足”或“日志为空”，重写调查分支与最终回答；也可以用 [17_oom_kill 案例](../tests/llm/fixtures/test_ask_holmes/17_oom_kill/test_case.yaml) 练习区分症状、直接终止原因和进一步待查原因。真实集群评估不作为当天核心任务。

## Day 7：模拟面试与最终整理

**当天问题：连续追问时，你能否从概述深入到实现、验证与取舍？**

180 分钟安排：复习 15、讲述定稿 25、连续追问 60、模拟面试 40、补洞 25、整理 15。

准备同一项目的三种长度：90 秒概述，3 分钟“四条简历串讲”，8 分钟“一个完整案例与两项技术取舍”。每种版本都应说明个人贡献，使用前四天真实留下的记录。

连续追问至少覆盖下面 12 题：

1. 为什么需要多轮 Agent，而不直接一次调用模型？
2. 每轮上下文增加什么，工具请求与结果怎样对应？
3. 工具如何注册，模型怎么选择，新增工具要改哪里？
4. Schema、类型转换、业务校验有什么差别？
5. 如何处理大结果，服务端过滤与本地过滤怎样区分？
6. Transformer、落盘与 Compaction 分别处理什么？
7. 压缩遗漏关键证据时怎么办？
8. RBAC、参数处理与命令策略如何配合？
9. 审批 Token 绑定什么，解决什么问题，有什么限制？
10. 工具失败、模型失败与超步数有什么不同？
11. 怎样评价根因正确性、幻觉、耗时与成本？
12. 哪些能力来自上游，哪些工作由你亲手完成，最想改进哪里？

模拟面试 40 分钟：项目介绍 5、实现追问 15、上下文与安全追问 10、案例与个人贡献 10。每题按以下标准打分：0 分答不出；1 分能讲概念；2 分能讲实现并指出证据；3 分能补充取舍或限制。核心四条均应达到至少 2 分，答不清的题先回到已有材料补洞。

最终验收：每条简历都有一个具体例子、一个源码入口或实测证据、一项方案取舍；能脱稿讲一个完整调查；没有把尚未执行的实验记为成果，也没有无测量依据的效果数字。

第 4 小时：针对得分最低的两题补材料，再做一轮有连续追问的模拟面试。

## 每日记录模板

每天使用当天文档或一个笔记中的同一节记录，控制在一页：

- 今天对应的简历条目：
- 我的 2 分钟解释：
- 一个具体输入 / 输出或诊断例子：
- 源码入口与证明它的测试 / Trace：
- 实际测试结果、证明范围与未验证部分：
- 一个方案取舍或限制：
- 我实际完成的个人工作：
- 脱稿还答不清的问题，明天开头复习：

规划文档中的命令是待执行学习任务；本次重规划只核对源码、测试选择和文档路径，不代表已经完成这些学习与实验。
