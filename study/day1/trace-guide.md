# 亲眼观察一次 Agent 的执行

用 `trace_run.py` 启动真实的非交互 `holmes ask`，它会在源码的关键函数边界记录事件。无需安装或连接额外的 trace 服务。

## 先看什么

现在可以用本地网页查看，不必逐行翻 Markdown。在仓库根目录运行：

```bash
python3 -m study.trace_viewer serve
```

然后打开 <http://127.0.0.1:8765>。左上角选择运行记录，左侧选择 Loop；页面支持：

- 查看本轮模型回复，展开 API 实际返回的 reasoning。
- 按工具调用 ID 查看参数、执行状态、循环收到的结果，以及处理前的原始返回。
- 在“输入上下文”里展开每条历史消息，在“原始事件”里查看日志与源码位置。
- 搜索回复、命令和结果，查看最终回答与运行异常；勾选自动刷新可每 5 秒读取新增日志。
- 导出可离线打开的 HTML。新 trace 结束后也会自动生成 `trace.html`，可以直接双击打开。

查看器只读本地日志，不会调用模型或执行日志中的命令，无需额外依赖或前端构建。
服务仅监听本机 `127.0.0.1`，按 Ctrl+C 停止。端口被占用时加 `--port 8766`；
其他记录目录可用 `--runs-dir /path/to/runs`（也接受单次运行目录）。

为旧记录单独生成离线页面：

```bash
python3 -m study.trace_viewer export study/day1/runs/20260923-225409-379209
```

默认生成该目录下的 `trace.html`，不会覆盖已有文件；需要新快照时用 `--output /path/to/new-snapshot.html`。
离线页包含该次记录的消息和工具输出，保留记录器已做的脱敏；它是生成时的快照，不会自动更新。
网页仅显示已落盘事件：缺少结束事件会标为“未记录结束”，不会据此认定进程仍在运行；
不完整或损坏的 JSONL 行会给出提示，其余可读事件仍会展示。

本机已完成一次 DeepSeek 实跑：[打开本次按轮次导读](runs/20260923-225409-379209/walkthrough.md)。共有 10 轮模型调用、26 次工具请求（23 次 `bash`、3 次 `TodoWrite`），108 个事件，进程退出码为 0。前 9 轮返回了 `reasoning_content`，第 10 轮未返回该字段。

建议先读这份记录的 Loop 1–3：第一轮 `cat` 请求被审批机制拒绝；第二轮改用 `head/grep`；第三轮依据打包声明检查入口函数。不要急着把全部日志读完，先跟清一条“请求 → 结果 → 下轮决策”的线。

原始响应按实际输出保存，并非经过人工纠正的答案。例如最终文本说“两个 Python 入口点”，这里只能视为它本轮聚焦的 CLI 和服务入口，不能据此认定仓库没有 Operator 等其它入口。观察日志仍需要区分“模型说了什么”和“工具证据证明了什么”。

每次运行保存在 `study/day1/runs/日期-时间/`：

| 文件 | 用途 |
| --- | --- |
| `trace.html` | **浏览器中查看**。轮次导航、搜索、可展开的工具输入输出和完整上下文；离线可用 |
| `walkthrough.md` | **先读这个**。按 Loop 分组，显示模型公开说明、工具输入和输出、继续/结束分支；可展开供应商返回的 reasoning |
| `trace.md` | 按真实时间排序的全部观测事件，带源码位置、耗时和完整请求快照 |
| `events.jsonl` | 一行一个事件，便于搜索、过滤、重放分析；每写一条都会 flush |
| `result.json` | 成功走完 Agent 后的最终 `LLMResult`，包括完整消息历史 |

这几个文件来自同一次运行，不是分别请求模型生成的解释。`walkthrough.md` 由事件日志生成；如果运行失败，已写入的事件仍可查看。正常异常和 Ctrl+C 会生成失败记录；强制杀死进程可能来不及生成导读，但 JSONL 和时间线仍保留已刷新部分。

## 怎么运行

在项目根目录的终端执行（会实际请求远程模型，产生 API 用量）：

```bash
cd /Users/weibo/Project/holmesgpt
DEEPSEEK_API_KEY="$(launchctl getenv DEEPSEEK_API_KEY)" \
  poetry run python -m study.day1.trace_run
```

如果当前终端找不到 `poetry`，将它换为 `/Users/weibo/.local/bin/poetry`。

默认问题仍是 Day 1 的“找出仓库主 Python 入口”，模型仍是 `deepseek/deepseek-flash`。为便于观察，额外提示了仓库位置、只检查项目源码，以及在工具调用前简短说明行动目的。它是新的学习实验，不能当成原始 12 轮基线的原样重放。路径提示也不是文件系统沙箱。

可以改问题和输出目录：

```bash
DEEPSEEK_API_KEY="$(launchctl getenv DEEPSEEK_API_KEY)" \
  poetry run python -m study.day1.trace_run \
  --question "Explain how the CLI ask command creates its initial messages. Cite source files." \
  --max-steps 16 \
  --output-dir study/day1/runs/prompt-investigation
```

输出目录必须不存在，脚本会拒绝覆盖旧实验。不指定目录时自动使用时间戳。

## 沿一轮循环阅读

打开 `walkthrough.md`，按以下顺序观察：

1. **本轮携带几条历史消息？** 找到请求快照的事件编号，再到 JSONL 查看 `messages`。第二轮应该包含第一轮的工具请求和结果。
2. **模型说要检查什么？** 看“公开说明 / 回答”，其原文来自 `assistant.content`。如果供应商返回 `reasoning_content`，会单独保存；未返回就标注未提供，不生成替代的“思考”。
3. **模型要求调用什么？** 看 `tool_calls` 的工具名、参数与 ID。参数是在模型响应里出现的，不是脚本预先规定的命令。
4. **HolmesGPT 实际执行了吗？** 看工具状态、返回码和错误。`approval_required` 经非交互循环变成 `error` 表示没有执行，不能把请求数当成成功执行数。
5. **结果怎样影响下一步？** 看下一轮的公开说明和工具请求。遇到权限错误后是否改用了其他命令？获得入口声明后是否继续核对 `run()`？

例如先请求 `cat` 被拒、再请求 `grep` 成功的过程，就展示了“观察错误 → 下一轮调整动作”。这两步都能在日志里对应到真实源码。

## 日志与源码对应关系

| 事件 | 被观测的源码函数 | 你能看到什么 |
| --- | --- | --- |
| `run.start` | `holmes.main.run` | 本次参数和工作目录 |
| `config.start/end` | `Config.load_from_file` | 配置阶段和最终模型/轮次上限；不记录配置对象内的密钥 |
| `agent.start/end` | `Config.create_toolcalling_llm` | 模型、可用工具集、工具名 |
| `prompt.start/end` | `build_initial_ask_messages` | 首次 system/user 消息 |
| `llm.request` | `DefaultLLM.completion` | 本轮完整消息、工具 Schema、tool_choice、实际 loop 编号 |
| `llm.response` | 同上 | content、reasoning_content、工具调用、finish_reason、usage、耗时 |
| `tool.start/end` | `Tool.invoke` | 执行参数、调用 ID、具体工具实现位置、裁剪前的工具返回值 |
| `loop.tool_result` | `ToolCallingLLM.call_stream` | 循环层处理后的结果，包括审批拒绝后的状态变化 |
| `loop.end` | 同上 | 循环结束、模型轮数与最终内容 |
| `result` | `ToolCallingLLM.call` | 最终汇总 |
| `run.error/end` | 学习启动脚本 | 异常、中断或 CLI 退出状态 |

事件中的源码行号运行时计算，因此能对应你当前添加了中文注释的文件。工具并发执行时，日志顺序可能与模型发出请求的顺序不同，应使用 `tool_call_id` 关联。

`llm.request` 记录的是 Holmes 传入 `DefaultLLM.completion` 的参数，不是底层 HTTP 报文；LiteLLM 的供应商转换、内部 HTTP 重试和请求头不在这份 trace 范围内。`tool.end` 在通用工具转换器之后、Agent 大输出落盘裁剪之前记录，下一轮 `messages` 则显示模型实际收到的结果。辅助摘要模型请求若出现会标成 `loop=null`，不会冒充主循环轮次。

## 一个实际发现：进程退出成功，不等于任务完成

第一次验证把上限设为 8。Agent 到第 8 轮时，`call_stream()` 中的这行生效：

```python
tools = None if i == max_steps else tools
```

最后一次请求不再携带工具。该次模型输出了文本形式的 DSML 工具标记；响应的结构化 `tool_calls` 为空，因此循环仍正常结束，进程退出码为 0，但没有给出完整入口结论。

这份记录保留在 `runs/20260923-225207-748892/`，可用来对照源码理解退出机制。默认上限已调整为 16，后续验证使用新的目录。不能只看 `exit_code=0` 或 `finish_reason=stop`，还应读最终答案内容。

## 实现方式与验证

学习启动脚本临时包装上述 Python 方法，进入原来的 `holmes.main.run()`。所有原函数都会被调用，返回值和异常保持原样；退出后恢复方法。范围限于这个启动进程，因此应作为独立 CLI 学习工具使用，不应嵌入多请求服务进程。

测试使用真实的 `ToolCallingLLM` 循环、`ToolExecutor` 和 `Tool.invoke`，模型响应由测试数据提供，验证了两轮循环、并发工具 ID、审批拒绝、请求快照、reasoning 缺失、异常恢复、并发写入、脱敏和禁止覆盖：

```bash
poetry run pytest tests/test_study_trace.py \
  tests/core/test_prompt.py tests/core/test_tool_executor.py \
  tests/test_tool_calling_llm.py -q --no-cov -n 0
```

结果：111 个测试通过。

真实运行文件仅保存本地（`runs/` 已忽略提交），文件权限为仅当前用户读写。记录器对密钥字段和已知环境密钥做脱敏；工具读取到的普通业务内容仍会记录，分享前应检查文件。
