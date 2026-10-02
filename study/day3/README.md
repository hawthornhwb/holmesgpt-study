# Day 3：讲清工具插件化与参数处理

状态：按 2026-10-03 的面试目标重新规划，待执行。Day 1、Day 2 按你的反馈视为已完成。

今天对应简历中“智能故障诊断 Agent”的第二条：Tool / Toolset、JSON Schema、参数类型转换与有效性校验。总计划见 [未来五天面试复习](../interview-plan.md)。

**核心目标：不看源码，能讲清一个工具如何被加载、描述、选择和执行，并指出参数处理的实际边界。**

每天有 3–4 小时：下面六项必做合计 180 分钟，第 4 小时留给工具接入实验或补薄弱点。完成情况由你实际学习后填写。

## 1. 把简历四条变成自己的问题清单（20 分钟）

先用自己的话写一段 90 秒项目介绍，包含：要解决什么问题、整体调查过程、你实际做过什么、最值得展开的两个技术点。今天先写初稿，Day 7 再定稿。

然后填写这张表。Day 1、Day 2 的已有 Trace 与测试可以直接作为证据。

| 简历条目 | 已经理解的部分 | 还要补的部分 | 自己实际完成的阅读、改动或验证 |
| --- | --- | --- | --- |
| Agent Loop | 工具请求、执行、回填、下一轮 | 停止条件和失败边界 | |
| 工具插件化 | 工具调用的数据流 | 加载、注册、Schema、参数处理 | |
| 分层上下文控制 | 历史随轮次累积 | 过滤、单结果处理、Compaction | |
| 工具安全 | 工具由程序执行 | RBAC、参数处理、命令策略、审批 Token | |

讲述时分别说明“项目已有机制”和“我亲手完成的工作”。已有学习仓库基于 HolmesGPT，个人贡献可以引用源码笔记、Trace 查看与实验、补充断言等实际记录；没有完成的部分先记为待验证。

## 2. 用一个已有工具追接入链路（40 分钟）

从 [docker.yaml](../../holmes/plugins/toolsets/docker.yaml) 的 `docker/core`、`docker_inspect` 开始，只读定义，不需要启动 Docker。

先区分四个东西：Toolset 管理一组工具与前置条件；Tool 表示一个操作；Schema 给模型看名称、说明和参数；执行实现是 YAML 命令或 Python `_invoke()`。

先追下面这些入口，记录每一步输入什么、输出什么：

| 环节 | 源码入口 | 必须回答的问题 |
| --- | --- | --- |
| 加载定义 | [toolset_manager.py](../../holmes/core/toolset_manager.py) / `_load_toolsets_from_paths()`、`_load_toolsets_from_config()` | 文件与配置怎样进入工具管理器？ |
| 创建对象 | [toolsets/__init__.py](../../holmes/plugins/toolsets/__init__.py) / `load_toolsets_from_config()` | 哪一步校验工具定义并创建 Toolset / Tool？ |
| 注册与查找 | [tool_executor.py](../../holmes/core/tools_utils/tool_executor.py) / `__init__()`、`get_tool_by_name()` | `enabled` 配置与运行状态 `status` 有什么区别？名字怎样找到实现？ |
| 生成 Schema | [tools.py](../../holmes/core/tools.py) / `get_openai_format()`；[openai_formatting.py](../../holmes/core/openai_formatting.py) | 模型看到哪些字段？ |
| 执行操作 | `tools.py` / `invoke()`、`YAMLTool._invoke()` | 通用处理与具体实现为什么分开？ |

合上文件，画出：

```text
工具定义 → Toolset / Tool 对象 → 注册与 Schema
→ 模型返回工具名和参数 → 按名称查找 → invoke() → _invoke()
→ StructuredToolResult → Day 2 已掌握的消息回填
```

必须得出两个结论：Schema 不包含 YAML 的具体 `command` 与 `user_description`；程序实际执行什么由工具实现决定。模型在已提供的工具中选择操作，不负责执行本地函数。

## 3. 拆开“参数转换”和“有效性校验”（40 分钟）

阅读 [json_schema_coerce.py](../../holmes/core/json_schema_coerce.py) 的 `coerce_params()`、`_coerce_single_value()`，接上 `Tool._coerce_params()` 与 `Tool.invoke()`。

先预测再核对这四种输入。这里讨论默认 `strict=False` 的转换行为：

| 声明类型 | 模型实参 | 当前行为 | 要解释的原因 |
| --- | --- | --- | --- |
| integer | `"42"` | 转为 `42` | 可以保守修正类型 |
| integer | `"3.7"` | 保留字符串 | 不静默截断小数 |
| array | `'["cpu", "memory"]'` | 解析成列表 | 识别字符串中的 JSON 数组 |
| 未声明的字段 | 额外参数 | 保留原值 | 转换不是完整 Schema 校验 |

再读 [Prometheus 工具](../../holmes/plugins/toolsets/prometheus/prometheus.py) 的 `GetLabelValues._invoke()`，找到它对 `label` 非空的检查，作为具体业务校验的例子。

把简历中的表述拆成四层：

1. Pydantic 校验 Tool / Toolset 等**定义对象**。
2. JSON Schema 向模型描述生成参数时的类型和约束；strict 能否启用取决于配置与工具兼容性。
3. 执行前做**顶层、保守的类型转换**。
4. 具体工具继续检查业务参数，并返回可用于下一轮纠正的错误。

当前通用执行路径没有统一完成全部 JSON Schema 运行时校验：缺失字段会跳过，未知字段保留，`enum`、数值范围和嵌套结构没有在这一步统一检查。`json.loads()` 成功也只证明 JSON 能解析。

面试表达可以是：“Schema 约束模型生成，执行前做保守类型转换，业务合法性由具体工具继续检查；统一的完整运行时校验是可以补强的地方。”

## 4. 用已有离线测试核对理解（30 分钟）

先阅读这些测试的输入、预期输出和断言，再在项目根目录运行。这里验证现有逻辑，不需要新增测试文件。

```bash
poetry run pytest tests/test_json_schema_coerce.py \
  -k 'test_stringified_array or test_whole_number or test_float_string_rejected or test_param_not_in_schema_left_alone' \
  -q --no-cov -n 0

poetry run pytest tests/test_openai_formatting.py \
  -k 'test_format_tool_strict_for_compatible_tool or test_format_tool_no_strict_for_dynamic_keys' \
  -q --no-cov -n 0
```

前一组对应四种参数边界，后一组对比能使用 strict 的工具和具有动态字段的工具。记录实际结果与一个让你修正理解的断言，不必重复 Day 2 已经掌握的消息顺序实验。

这些测试通过，能证明转换与 Schema 生成的程序行为；不能证明所有模型都会生成合规参数，也不能证明所有工具都完成业务有效性校验。

## 5. 脱稿回答六个问题（30 分钟）

每题控制在 1–2 分钟。先口述或录音，卡住后再查源码。

1. 模型怎样知道有哪些工具？Schema 与实际实现是什么关系？
2. 为什么要同时有 Toolset、Tool、`invoke()` 和 `_invoke()`？
3. 新增一个查询工具，要定义哪些内容，怎样接入已有执行链？
4. JSON Schema、类型转换和业务校验分别解决什么问题？
5. 参数无法转换、参数缺失或工具查询失败，程序和模型分别会怎样处理？
6. 你亲手验证了什么？现有测试能证明什么，不能证明什么？

最后把“工具插件化”讲成一段 2 分钟回答：需求 → 抽象 → 接入链路 → 一个具体参数例子 → 失败处理 → 验证与限制。

## 6. 填写记录并验收（20 分钟）

- [ ] 脱稿画出工具接入到执行的链路，能找到各环节函数。
- [ ] 解释 Toolset / Tool / Schema / 执行实现四者的区别。
- [ ] 预测并解释四种参数输入的实际行为。
- [ ] 指出一个具体业务校验，并解释当前统一处理的边界。
- [ ] 实际运行上述两组测试，记录结果和证明范围。
- [ ] 完成 90 秒项目介绍初稿和 2 分钟工具插件化口述。
- [ ] 六个追问中至少五个能脱稿回答；剩余问题写入明天的复习清单。

当天记录直接填在这里：

- 实际学习日期与用时：
- 我的 90 秒项目介绍：
- 我画出的工具接入链路：
- 最容易混淆的参数边界与例子：
- 两组测试的实际结果及证明范围：
- 我实际完成的个人工作与证据：
- 仍答不清的问题：

## 第 4 小时：工具接入实验或补弱项（最多 60 分钟）

如果核心任务已通过验收，从 [原工具实验](tool-lab.md) 第 3、4 节开始：自己定义查询服务负责团队的本地工具，验证加载 / Schema、成功查询、文件不存在三个场景。练习代码留给你亲手完成；本文只规划任务。

到 60 分钟就记录进度，未完成部分先保留，下一天核心主题仍按计划推进。若口述还不顺，第 4 小时优先补六个追问。真实模型请求是进一步选做，调用前确认本地工具验证已完成；记录只能按实际执行情况填写。
