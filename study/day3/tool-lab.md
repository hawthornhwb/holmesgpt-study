# 选做实验：定义一个工具，让 Agent 使用它

这是原 Day 3 的工具接入练习，保留供五天面试复习的第 4 小时使用。新的必做任务见 [Day 3](README.md)。

先完成新计划中的概念与参数处理阅读，再从本文第 3、4 节开始动手；第 5 节真实模型请求可另行选做。参数 Schema、类型转换和业务有效性校验的区别，以新计划中的源码核对为准。

状态：选做，尚未执行。完整练习预计 90–120 分钟；已完成新计划源码阅读后，第 3、4 节可安排约 60 分钟。

Day 2 关注“工具请求和结果怎样进入下一轮”。Day 3 继续往前追：**工具从哪里来，模型如何知道它能做什么，Holmes 如何执行它。**

今天的练习是添加一个本地只读工具：根据服务名称查询负责团队。按这份文档学习，记录也填写在文末。

## 1. 先分清四个对象（10–15 分钟）

打开 [Docker 工具定义](../../holmes/plugins/toolsets/docker.yaml)，只看 `docker/core` 和 `docker_inspect`，不需要启动 Docker。

| 对象 | 对照这个例子理解 |
| --- | --- |
| Toolset | `docker/core`：把一组相关工具放在一起，管理启用状态和前置条件 |
| Tool | `docker_inspect`：一个具体操作，有名称、说明、参数和执行实现 |
| Schema | 发给模型的名称、说明和参数规范；模型据此生成工具调用请求 |
| 执行实现 | YAML 中的 `command`，或 Python Tool 子类中的 `_invoke()` |

先预测两个问题，再在第二步核对：

1. 模型收到的工具 Schema 中，是否包含 `command` 的具体命令？
2. YAML 中的 `{{ container_or_image_id }}` 如何变成工具参数？

完成标准：能够说清“工具集名称”和“具体工具名称”的区别。

## 2. 顺着加载、注册、执行阅读源码（25–30 分钟）

只读表中的函数。先追主路径，审批、转换器和 OAuth 的细节留到后续。

| 顺序 | 源码位置 | 要回答的问题 |
| --- | --- | --- |
| 1 | [toolset_manager.py](../../holmes/core/toolset_manager.py) / `_load_toolsets_from_paths()`、`_load_toolsets_from_config()` | 自定义 YAML 文件如何被读取？哪些字段决定工具集名称、启用状态和类型？ |
| 2 | [toolsets/__init__.py](../../holmes/plugins/toolsets/__init__.py) / `load_toolsets_from_config()` | 配置字典怎样变成 `YAMLToolset` 和里面的 `YAMLTool` 对象？ |
| 3 | [tool_executor.py](../../holmes/core/tools_utils/tool_executor.py) / `ToolExecutor.__init__()`、`get_tool_by_name()` | 哪些状态的工具集会被注册？`tools_by_name` 为什么可以按工具名找到实现？ |
| 4 | [tools.py](../../holmes/core/tools.py) / `Tool.get_openai_format()`；[openai_formatting.py](../../holmes/core/openai_formatting.py) / `format_tool_to_open_ai_standard()` | 模型到底收到哪些字段？`command` 和 `user_description` 是否在其中？ |
| 5 | `tools.py` / `YAMLTool.__infer_parameters()`、`Tool.invoke()`、`YAMLTool._invoke()`、`__invoke_command()` | 参数怎样被推断、处理并填入命令？执行结果怎样包装为 `StructuredToolResult`？ |

把链路写成一行，再接上 Day 2 已经理解的后半段：

```text
YAML → Toolset / Tool 对象 → 按名称注册 + 生成 Schema → 模型返回工具请求
→ 找到 Tool → invoke() → _invoke() → StructuredToolResult → 下一轮 tool 消息
```

重点理解：`description` 给模型看；`user_description` 用于展示执行说明。`invoke()` 负责通用处理，`_invoke()` 负责具体操作。YAML 模板可以推断未声明的参数；本次练习仍显式填写参数说明、类型和是否必填。

## 3. 亲手添加一个本地查询工具（25–30 分钟）

在 `study/day3/` 中自己创建两个文件：

- `services.txt`：练习数据，只写 3 行，每行格式为 `服务名称 owner=团队名称`。
- `toolsets.yaml`：定义一个名为 `study/service-owners` 的工具集，其中只有一个工具 `study_lookup_service_owner`。

服务名使用 `checkout-api`、`orders-api`、`reports-api`。团队名由你自己随意取，作为练习数据；后面的模型问题中不要直接给出团队名。

工具要求如下：

| 字段 | 要实现的行为 |
| --- | --- |
| 工具集 `enabled` | 设置为 `true` |
| 工具集 `tags` | 使用 `cli` |
| 工具 `description` | 说明它根据服务名称查询本地服务负责团队 |
| `parameters.service` | `type: string`、`required: true`，补充清楚的参数说明 |
| `command` | 用 `grep -F -w -- {{ service }}` 查询固定的 `services.txt` 文件 |
| `user_description` | 简短展示正在查询哪个服务 |

文件路径使用 `/Users/weibo/Project/holmesgpt/study/day3/services.txt`，避免工作目录不同导致找不到文件。查询范围就是这份 3 行的小文件。

先在终端直接执行同样的查询，确认能返回你填写的 `checkout-api` 那一行，再写 YAML。模板参数沿用源码中的处理方式，不需要自己给 `{{ service }}` 再套一层引号。

完成标准：你能解释 YAML 每个字段的用途，并知道实际执行的是哪条命令。

## 4. 先离线验证工具，再接模型（20–25 分钟）

自己创建 `tests/core/test_day3_toolset.py`，写 3 个测试：

| 测试 | 检查什么 |
| --- | --- |
| 加载、注册和 Schema | 从 YAML 加载工具集，执行前置条件检查，再创建 `ToolExecutor`；能按名称找到工具，Schema 包含 `service` 参数并将它标为必填 |
| 成功查询 | 调用 `tool.invoke({"service": "checkout-api"}, context)`；检查状态为 `SUCCESS`、返回码为 0、输出包含你填写的团队名，且不包含其他两条服务记录 |
| 文件不存在 | 复制工具对象，把副本的 `command` 指向临时目录下一个不存在的文件；检查状态为 `ERROR`、返回码非 0，`invocation` 保留命令和路径，`data` 包含文件不存在的错误 |

可复用的入口：

- `holmes.plugins.toolsets.load_toolsets_from_file()`：加载你的 YAML。
- `toolset.check_prerequisites(silent=True)`：更新工具集运行状态。`enabled: true` 是配置意图，执行器筛选的是运行状态 `status`。
- `ToolExecutor(toolsets=[toolset])`：注册已经可用的工具。
- `tests.conftest.create_mock_tool_invoke_context(tool_name=...)`：提供执行上下文；这里的模型是替身，不请求 API。
- pytest 的 `tmp_path`：提供临时目录。错误测试在副本上改变路径，保留原始练习数据。

在项目根目录运行：

```bash
poetry run pytest tests/core/test_day3_toolset.py -q --no-cov -n 0
```

这组测试会真实执行本地查询命令，但不调用 DeepSeek。先记录预测，再运行并检查结果。

一个容易误解的细节：当前 `YAMLTool._get_status()` 将非零返回码判为 `ERROR`。`grep` 找不到匹配项时返回 1，因此在这条实现中也会得到 `ERROR`，不能预期它自动变成 `NO_DATA`。今天只观察这个规则。

## 5. 用一次真实请求观察模型选择（10–20 分钟）

离线测试通过后，沿用当前已配置的 DeepSeek 模型，在项目根目录运行一次：

```bash
DEEPSEEK_API_KEY="$(launchctl getenv DEEPSEEK_API_KEY)" \
  poetry run holmes ask \
  "请使用 study_lookup_service_owner 查询 checkout-api 的负责团队，并依据工具返回结果回答。" \
  --model deepseek/deepseek-flash \
  --custom-toolsets study/day3/toolsets.yaml \
  --show-tool-output --no-interactive --max-steps 6 \
  2>&1 | tee study/day3/ask-output.log
```

这一步会产生模型 API 用量。若终端找不到 Poetry，使用 `/Users/weibo/.local/bin/poetry` 替换 `poetry`。

先预测：至少需要几次模型请求，才能“请求工具 → 根据结果回答”？实际可能多于预测，按本次输出记录。

验收时看三件事：是否请求了你定义的工具、是否使用正确的 `service` 参数、最终团队名是否与文件中的查询结果一致。回答内容正确但没有调用该工具，仍未达到本次练习的工具使用目标。

`ask-output.log` 是终端运行记录，适合核对工具输入、输出和最终回答；它不包含完整的每轮模型请求快照。Day 1 的 `trace_run.py` 当前未提供自定义工具集参数，且其提示限制读取 `study/`，本次按上述 CLI 命令运行即可。

## 当天验收与学习记录

- [ ] 能区分 Toolset、Tool、Schema 和执行实现。
- [ ] 能指出 YAML 加载、按名称注册和 Schema 生成的位置。
- [ ] 自己完成了查询工具，能解释参数如何进入命令。
- [ ] 3 个离线测试通过，理解成功与错误状态如何产生。
- [ ] 真实请求使用了新工具，答案与实际查询结果一致。

当日产出是你编写的 YAML、练习数据、3 个测试、一次运行记录，以及下面的学习记录。核心练习完成后即可结束当天学习。

- 实际学习日期：
- 用自己的话解释 Toolset 和 Tool 的区别：
- 模型收到的 Schema 中有哪些字段，是否包含命令：
- 参数从模型请求到命令执行经历了哪些处理：
- 三个测试的结果，以及各自能证明什么：
- 真实请求用了什么工具和参数，返回了什么证据：
- `description` 与 `user_description` 的区别：
- 仍不理解的一处源码或现象：
