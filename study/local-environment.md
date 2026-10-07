# 本地运行 09_crashpod

本机使用 Colima 的 `holmes-study` profile 提供 Docker，kind 集群也命名为 `holmes-study`。资源配置为 4 核、6 GiB 内存、30 GiB 数据盘上限；磁盘文件按使用增长。Kubernetes 节点固定为 `kindest/node:v1.37.0`，脚本同时锁定镜像摘要。HolmesGPT 沿用仓库现有 Poetry 环境。

入口是 [local-case.sh](local-case.sh)，请在项目根目录执行下面的命令。它设置独立的 Docker context 与 kubeconfig，操作资源前核对 context 必须是 `kind-holmes-study`。

## 启动与查看故障

```bash
bash study/local-case.sh start
bash study/local-case.sh setup
bash study/local-case.sh inspect
```

`start` 创建或复用学习集群；`setup` 直接执行仓库 [09_crashpod](../tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml) 的 `before_test`；`inspect` 查看 Deployment、Pod 和最新一次容器运行的日志。这个 case 的容器会立即退出，旧容器日志可能已不可用，因此查看入口不依赖 `--previous`。

应看到 `app-09` 中的 `payment-processing-worker` 反复重启，状态可能在 `Error` 和 `CrashLoopBackOff` 之间变化，日志包含 `Environment variable DEPLOY_ENV is undefined`。这是对测试环境的验证，尚不代表 Agent 已找出根因。

当前 eval harness 即使加 `--only-setup`，也会先检查模型 API，未配置模型时可能在创建资源前跳过。因此这里直接复用 fixture 的准备脚本；模型接好后，`eval` 使用原始 pytest 入口完成准备、调查、Judge 和清理。

## DeepSeek 配置

本机文件：`/Users/weibo/.holmes/study/deepseek.env`，权限为 600，位于 Git 仓库外，目前三个参数均已填写。下面仅示意字段格式，更换接口时在本机文件中修改实际值：

```bash
DEEPSEEK_BASE_URL="你的 OpenAI 兼容接口 Base URL"
DEEPSEEK_MODEL_ID="接口接受的原始 model ID"
DEEPSEEK_API_KEY="你的 API Key"
```

model ID 使用供应商接口接受的值，不加 LiteLLM 路由前缀。Base URL 填 API 根路径，不包含 `/chat/completions`。脚本将调查模型配置为 `openai/<model ID>`，Judge 使用原始 model ID，两者共用你提供的接口和密钥。本机已验证这个 model ID 支持工具调用，但 Judge 需要下面的适配插件才能运行。

本次调查把上下文预算设为 128,000 tokens、单轮最大输出设为 16,384 tokens（第一次实测用 4,096，思考内容也占用该预算，最终回答为空，因此提高）。这是学习环境的预算，不是对供应商模型上限的声明。自定义模型的费用需要与供应商账单核对，未识别价格时报告里的零费用不能作为免费依据。

填写后执行：

```bash
bash study/local-case.sh collect
bash study/local-case.sh eval
```

`collect` 只检查能否选中该测试，不调用模型。`eval` 用单进程运行一次 `09_crashpod`，使用原 fixture 创建并清理 `app-09`，在 `~/.holmes/study/eval-09-*/eval.log` 保存按顺序输出的执行日志，并把 `evals_report.md` 复制到同目录；无论通过还是失败都会保存，退出码沿用 pytest 的结果。失败时先看日志，区分 API、环境准备、调查与 Judge 问题。

`eval` 会自动加载 [study/judge_thinking_adapter.py](judge_thinking_adapter.py)（`-p judge_thinking_adapter`）。原因见下面“Judge 适配”一节：这家供应商拒绝 autoevals 强制的 `tool_choice`，插件只对 Judge 请求关闭思考模式。

## 清理与关闭

```bash
# 删除 app-09 的测试资源
bash study/local-case.sh cleanup

# 停止虚拟机，释放运行占用；集群数据保留
bash study/local-case.sh stop
```

下次使用 `start` 启动，再按需要 `setup` 重建故障。停止不会移除已安装工具或学习环境的数据。

## 配置与验证记录

2026-10-06：已安装 Colima 0.10.3、Docker CLI 29.8.1、kind 0.33.0、kubectl 1.37.1、Helm 4.3.0；虚拟机内 Docker Engine 为 29.5.2。本机原有 `jq` 可用。运行入口的三个保护用例通过，原 `09_crashpod` 可被 pytest 收集。

Docker 镜像下载使用本机已有代理，配置只写入 `~/.colima/holmes-study/colima.yaml`；修改前的配置保存在同目录的 `colima.before-study-proxy.yaml`。创建 kind 节点时沿用 Docker 后台可访问的代理地址，避免把宿主机的 `127.0.0.1` 带进节点。该配置方式见 [Colima 配置](https://colima.run/docs/configuration/) 和 [Docker 后台代理配置](https://docs.docker.com/engine/daemon/proxy/)。

**集群与故障（22:38 验证）**：`start` 复用已就绪的 `holmes-study` 节点，`setup` 执行 fixture 的 `before_test`。`app-09` 的 Deployment `payment-processing-worker` 为 0/1，Pod 处于 `CrashLoopBackOff`，上一次容器日志是 `Environment variable DEPLOY_ENV is undefined`，与 fixture 设计的故障一致。查看当前现场可运行 `inspect`。

**评测结果（2026-10-06，三次运行）**：

- 22:39 第一次未通过，两个原因都不在调查能力。其一是最终回答为空：模型在 7 轮、13 次工具调用后没有输出结论文本，Judge 收到空输出；其二是 Judge 在记录分数前中断，报告显示 `Test not executed` 与 0%。
- 修正后 22:45 与 22:48 连续两次 1/1 通过，correctness 都得 1 分，`memories_generated: false` 的硬断言同时通过（都没有提出 Skills 建议），Compaction 都为空。回答以 `## Issue: payment-processing-worker crash-loops because DEPLOY_ENV is undefined` 开头，并说明 Deployment 没有提供该变量。
- 两次通过的差别说明单次数字波动不小：22:45 为 18.3 秒、7 轮、19 次工具调用、105,072 tokens（输入 101,375、输出 3,697、推理 1,090），另有 6 条 Bash 命令被拒；22:48 为 10.7 秒、5 轮、9 次工具调用、67,750 tokens（输入 65,978、输出 1,772、推理 302），没有被拒命令。比较改动前后要固定 case、模型和判分标准，并重复运行。
- 调查实际调用了 `kubernetes_tabular_query` 发现资源，用 `fetch_pod_logs` 读取当前与上一次容器日志，再用 `kubernetes_jq_query` 或 `bash` 核对 Deployment 配置。22:45 那次真实触发了一次安全分支：6 条 `kubectl ... describe pod`、`kubectl ... get events`、`kubectl ... get deployment -o yaml` 的片段未命中允许列表，工具返回 `requires approval`，而 pytest 入口没有审批交互，于是按拒绝处理；模型从返回信息读出“前缀要原样出现在命令里”，把 `-n app-09` 移到命令末尾后才通过。22:48 那次直接用了专用工具，没有被拒命令。这条记录可以与第 5 天的审批推演对照。

**Judge 适配**：这家供应商的思考模式拒绝任何指定函数的 `tool_choice`，返回 `HTTP 400 Thinking mode does not support this tool_choice`；autoevals 的 `LLMClassifier` 一律指定 `select_choice`，因此 Judge 必然失败。仓库因此在 [judge_thinking_adapter.py](judge_thinking_adapter.py) 提供一个 pytest 插件，只给 Judge 请求加上 `thinking={"type": "disabled"}`，模型、提示词、期望答案和解析都不变，调查模型仍使用正常思考模式；`local-case.sh` 用 `-p judge_thinking_adapter` 加载它，仓库的评测代码没有改动。插件用正例与反例各验证一次：指出 `DEPLOY_ENV` 缺失得 1 分，只描述 `CrashLoopBackOff` 得 0 分。这也意味着这次分数来自“关闭思考的 Judge + 正常思考的调查模型”这一组合，换用原生支持强制工具调用的供应商时应当去掉插件再复测。

**范围说明**：以上完整评测只覆盖原 case 的三次运行。允许列表拒绝与要求审批的分支已在 22:45 那次运行中触发；人工批准后的续跑仍未验证。大日志、过滤与截断、摘要、落盘和 Compaction 都仍是同场景推演，没有运行。费用与单价未核对，报告中的费用为空。

**现场恢复（2026-10-07）**：重新执行原 fixture 的 `before_test`，恢复 `app-09` 的故障 Deployment。直接调用 Holmes 的 `kubernetes_jq_query` 找到真实 Pod，再调用 `fetch_pod_logs`，成功读出 `Environment variable DEPLOY_ENV is undefined`。这次检查只验证环境与实际工具，没有调用调查模型或 Judge。故障资源保留供学习使用；`inspect` 已改为读取最新容器日志。

DeepSeek 的接口、model ID 和密钥保存在本机文件 `~/.holmes/study/deepseek.env`，未写入仓库。上面的评测结果来自本机实跑，完整日志和报告在 `~/.holmes/study/eval-09-*/`；未运行的部分已单独标注。
