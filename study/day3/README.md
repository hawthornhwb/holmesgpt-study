# Day 3 讲义：工具插件化（沿 09_crashpod 读源码）

2026-10-06 改写为讲义。对应 10-04 版简历中“智能故障诊断 Agent”的第一条：工具插件化。

**这份文档是自足的：今天需要看的源码已经按调用顺序摘录在正文里，不必再打开仓库对照。** 每个片段都标了 `文件:行号`，想深挖时再跳过去。全部片段来自当前工作区，行号是 2026-10-06 核对过的。

> 片段说明：为便于阅读，摘录时做过少量排版整理 —— 换行、缩进、省略号（`...`）处有删减，个别英文注释在旁边改写成中文；函数名、参数名、字段名和逻辑顺序与源码一致。标了“讲义所加/译注”的注释不是源码原文。要逐字对照时按行号打开源文件。

今天只回答一个问题：**同一个 Agent，怎么做到既查 Kubernetes 资源、又读 Pod 日志，而且以后再加一个日志平台时不用改调查主循环？**

主角是 case 里真实调用过的两个工具：

| 能力 | 工具 | 实现方式 | 本讲义 |
| --- | --- | --- | --- |
| 资源查询 | `kubernetes_tabular_query` / `kubernetes_jq_query` | YAML 定义 + Shell 脚本 | 第 2、3 节 |
| 日志读取 | `fetch_pod_logs` | Python 类 | 第 4 节 |

四个文件撑起整条链，读的时候记住它们的分工：

| 文件 | 一句话职责 | 小节 |
| --- | --- | --- |
| `holmes/core/tools.py` | Tool / Toolset / YAMLTool 的定义，以及 `invoke()` 主流程 | 1、3、5、6 |
| `holmes/core/openai_formatting.py` | 把参数定义翻译成模型能看的 JSON Schema | 1 |
| `holmes/plugins/toolsets/__init__.py` | 启动时把 YAML 工具集和 Python 工具集都装进来 | 2 |
| `holmes/core/tools_utils/tool_executor.py` | 登记工具、按名字找到工具 | 6 |

场景回顾见 [完整案例](../interview-plan.md) 前四步：用户只给服务名 `payment-processing-worker`，Agent 要先发现准确的 namespace 和 Pod 名，再读启动日志，最后核对 Deployment 配置。该 case 已于 2026-10-06 实跑通过（连续两次 1/1），实测调用链见本文第 7 节。

## 建议用法

时间紧就只走前 45 分钟：顺序读第 1–6 节，每读完一个代码片段先自己说一句“这段在解决什么”，再读下一段。第 7 节是实测记录，用来验证前面讲的机制确实在跑；第 8–11 节用于口述、追问和自测。

| 时间 | 做什么 | 留下什么 |
| --- | --- | --- |
| 45 分钟 | 读第 1–6 节，每节用一个片段回答“谁负责什么” | 一张四层职责表 |
| 20 分钟 | 读第 7 节实测调用链，对照第 6 节的调用序列 | 一条端到端路径 |
| 25 分钟 | 第 8 节三个失败分支 + 第 9–11 节的口述稿、追问与诚实边界 | 自己的回答和卡点 |

## 1 模型眼里的“工具”：只有名称、说明和参数

模型不知道背后是 Python 还是 YAML，它只收到一份 JSON Schema。这一节看这份 Schema 是怎么来的。

**Tool 的字段**（`holmes/core/tools.py:313`，省略了 transformer 相关的私有字段）：

```python
class Tool(ABC, BaseModel):
    name: str
    description: str
    parameters: Dict[str, ToolParameter] = {}
    user_description: Optional[str] = (
        None  # templated string to show to the user describing this tool invocation (not seen by llm)
    )
    icon_url: Optional[str] = Field(default=None, description="The URL of the icon for the tool, ...")
    transformers: Optional[List[Transformer]] = None
```

读法有三点：

1. `Tool` 是抽象基类（`ABC`），真正实现留给子类；`name`、`description`、`parameters` 是**所有工具都必须有的三件套**。
2. `user_description` 那行注释是关键：它只用于给人看的调用摘要，**不进模型请求**。
3. `transformers` 是可选的结果后处理（例如大输出摘要），属于工具自带能力，不属于调查主循环。

**参数怎么描述**（`holmes/core/tools.py:227`）：

```python
class ToolParameter(BaseModel):
    description: Optional[str] = None
    type: Union[str, List[str]] = "string"
    required: bool = True
    properties: Optional[Dict[str, "ToolParameter"]] = None   # 对象类型的嵌套字段
    items: Optional["ToolParameter"] = None                   # 数组元素
    enum: Optional[List[Any]] = None                          # 限定取值
```

注意 `required: bool = True`：**默认必填**，可选参数必须显式写 `required=False`。这是后面看两个实现时容易忽略的差异来源。

**翻译成模型请求**（`holmes/core/tools.py:375`）：

```python
    def get_openai_format(self):
        return format_tool_to_open_ai_standard(
            tool_name=self.name,
            tool_description=self.description,
            tool_parameters=self.parameters,
        )
```

产物在 `holmes/core/openai_formatting.py:184`（省略了 strict 模式的分支）：

```python
    result: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_description,
            "parameters": {
                "properties": tool_properties,
                "required": [...],
                "type": "object",
            },
        },
    }
```

**这一节要记住的一句话**：Schema 是工具的“对外合同”，只包含名称、说明、参数。`script`、`command`、Python 代码都不出现在这里 —— 所以模型是“按合同选工具”，而不是“读实现选工具”。合同一样，实现可以完全不同，这正是插件化的前提。

## 2 工具从哪来：两个加载入口

**入口一：扫 YAML**（`holmes/plugins/toolsets/__init__.py:149`，省略日志行）：

```python
def load_builtin_toolsets(
    dal: Optional[SupabaseDal] = None,
    additional_search_paths: Optional[List[str]] = None,
) -> List[Toolset]:
    all_toolsets: List[Toolset] = []
    for filename in os.listdir(THIS_DIR):
        if not filename.endswith(".yaml"):
            continue
        if filename == "kubernetes_logs.yaml" and not USE_LEGACY_KUBERNETES_LOGS:
            continue
        path = os.path.join(THIS_DIR, filename)
        toolsets_from_file = load_toolsets_from_file(path, strict_check=True)
        all_toolsets.extend(toolsets_from_file)

    all_toolsets.extend(
        load_python_toolsets(dal=dal, additional_search_paths=additional_search_paths)
    )
    for toolset in all_toolsets:
        toolset.type = ToolsetType.BUILTIN
        toolset.path = None      # 不向外部暴露内建工具集的路径
    return all_toolsets
```

**入口二：Python 工具集清单**（同文件 `:99`，中间条目省略）：

```python
def load_python_toolsets(
    dal: Optional[SupabaseDal],
    additional_search_paths: Optional[List[str]] = None,
) -> List[Toolset]:
    toolsets: list[Toolset] = [
        CoreInvestigationToolset(),  # Load first for higher priority
        InternetToolset(),
        ...
        BashExecutorToolset(),
        KubectlRunToolset(),
        ...
    ]
    if not USE_LEGACY_KUBERNETES_LOGS:
        toolsets.append(KubernetesLogsToolset())
    return toolsets
```

三件事值得记住：

1. **一个工具集 = 一个 YAML 文件里的一段，或一个 Python 类。** 加数据源通常是“加一个文件/一个类”，不是改主循环。
2. `kubernetes_logs.yaml` 和 `KubernetesLogsToolset` 是同一能力的两种实现，用 `USE_LEGACY_KUBERNETES_LOGS` 二选一，当前默认走 Python 版。这正好说明：**接口稳定时，实现可以整体替换。**
3. 内建工具集加载后统一标记 `BUILTIN`，默认不启用，要用户在配置里打开。

## 3 YAML 工具：kubernetes_jq_query

先看定义（`holmes/plugins/toolsets/kubernetes.yaml:1`，中间省略）：

```yaml
toolsets:
  kubernetes/core:
    description: "Read access to cluster resources (excluding secrets and other sensitive data)"
    prerequisites:
      - command: "kubectl version --client"
    tools:
      - name: "kubernetes_jq_query"
        user_description: "jq query: kubectl get {{kind}} | jq '{{jq_expr}}'"
        description: >
          Use kubectl to get json for all resources of a specific kind and filter with jq.
          Prefer over bash for large queries - paginates via the Kubernetes API so large
          clusters don't overflow context.
          ...
          The 'kind' parameter must be the plural form of the resource type
          (e.g., "pods" not "pod", "jobs" not "job").
        script: |
          #!/bin/bash
          # SECURITY: assign sanitized params to shell variables in an unquoted
          # assignment slot, then reference them only as "$VAR". Never interpolate
          # {{ param }} inside a quoted context ...
          KIND={{ kind }}
          JQ_FILTER={{ jq_expr }}
          JQ_FILTER=$(printf '%s' "$JQ_FILTER" | tr $'\n\r' '  ')
          ...
```

**这里有个反直觉的地方**：整个 `kubernetes.yaml` 里**没有任何 `parameters:` 字段**（`grep -n "parameters:" holmes/plugins/toolsets/kubernetes.yaml` 结果为空）。那 `kind` 和 `jq_expr` 的 Schema 从哪来？答案是运行时从脚本里推断（`holmes/core/tools.py:570`）：

```python
    def __infer_parameters(self):
        # 以下中文注释为讲义译注；源码此处为英文注释
        # 在 command/script 里找 {{ ... }} 占位符，未声明的补成参数
        template = self.command or self.script
        # 注释里可能只是说明文字，先把整行注释剥掉
        executable_template = re.sub(r"^\s*#.*$", "", template, flags=re.MULTILINE)
        inferred_params = re.findall(r"\{\{\s*([\w]+)[\.\|]?.*?\s*\}\}", executable_template)
        for param in inferred_params:
            if param not in self.parameters:
                self.parameters[param] = ToolParameter()
```

读法三条：

1. `{{ kind }}`、`{{ jq_expr }}` 被反推成参数名，自动进入 Schema；作者不必重复声明。
2. 补出来的参数用 `ToolParameter()` 的默认值：`type="string"`、**`required=True`**、`description=None`。所以 YAML 工具的参数**没有参数级说明文字**，语义只能靠 `description` 那段话讲清楚 —— 这也是这个工具的说明写得特别长的原因。
3. 注释整行被剥掉，所以安全注释里写 `{{ param }}` 不会变成参数。

**面试要点**：这就是“接入成本低”的具体形态与代价。YAML 写几行脚本就能成为工具，但参数语义、校验、错误处理都得靠作者自觉；能力一复杂，就该换成 Python 实现。同一文件里另外两个工具 `kubernetes_tabular_query`、`kubernetes_count` 走的也是这条 YAML 路径。

## 4 Python 工具：fetch_pod_logs

日志工具的对外定义是手写的（`holmes/plugins/toolsets/logging_utils/logging_api.py:124`，省略部分参数）：

```python
class PodLoggingTool(Tool):
    """Tool for fetching Kubernetes pod logs"""

    def __init__(self, toolset: "KubernetesLogsToolset"):
        description = (
            f"Fetch logs for a Kubernetes pod from {toolset_name}"
            " with support for regex filtering and exclusion patterns"
            f". Defaults: Fetches last {DEFAULT_TIME_SPAN_SECONDS // SECONDS_PER_DAY} days of logs, "
            f"limited to {DEFAULT_LOG_LIMIT} most recent entries"
        )

        parameters = {
            "pod_name": ToolParameter(
                description="The exact kubernetes pod name", type="string", required=True,
            ),
            "namespace": ToolParameter(
                description="Kubernetes namespace", type="string", required=True
            ),
            "limit": ToolParameter(
                description=f"Maximum number of logs to return. Default: {DEFAULT_LOG_LIMIT}",
                type="integer", required=False,
            ),
            "filter": ToolParameter(
                description="Optional keyword/phrase or case-insensitive regex to match, "
                            "e.g. filter='err|error|fatal|fail|exception|panic' for errors. "
                            "If no results, broaden the pattern or drop the filter.",
                type="string", required=False,
            ),
            ...
        }

        super().__init__(name=POD_LOGGING_TOOL_NAME, description=description, parameters=parameters)
        self._toolset = toolset
```

对比第 3 节：这里每个参数都有 `description`、明确的 `type`（`limit` 是 `integer`）、明确的 `required`。**Schema 质量由实现者决定，YAML 和 Python 只是两种写法的起点不同。**

调用时先做业务参数校验（`logging_api.py:178`，省略部分字段）：

```python
    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        structured_params = FetchPodLogsParams(
            namespace=get_param_or_raise(params, "namespace"),
            pod_name=get_param_or_raise(params, "pod_name"),
            start_time=params.get("start_time"),
            ...
        )

        result = self._toolset.fetch_pod_logs(params=structured_params)

        truncate_logs(
            logging_structured_tool_result=result, llm=context.llm,
            token_limit=context.max_token_count, structured_params=structured_params,
            tool_call_id=context.tool_call_id, tool_name=context.tool_name,
        )
        return result
```

它依赖的参数模型在 `logging_api.py:36`：

```python
class FetchPodLogsParams(BaseModel):
    namespace: str
    pod_name: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    filter: Optional[str] = None
    exclude_filter: Optional[str] = None
    limit: Optional[int] = None
```

注意 `namespace`、`pod_name` 在这里是**真正必填的 Python 类型**，缺了会在这里失败，而不是等到查询返回空结果。

真正的读取在工具集里（`holmes/plugins/toolsets/kubernetes_logs.py:221`，省略异常分支）：

```python
    def _fetch_kubectl_logs(self, params: FetchPodLogsParams, previous: bool = False) -> LogResult:
        cmd = [
            "kubectl", "logs", params.pod_name, "-n", params.namespace,
            "--all-containers=true", "--timestamps=true", "--prefix=true",
        ]
        if previous:
            cmd.append("--previous")

        try:
            result = subprocess.run(
                cmd, text=True, timeout=KUBERNETES_LOGS_TIMEOUT_SECONDS,
                check=False,  # do not throw error, we just return the error code
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            if result.returncode == 0:
                log_result = self._parse_kubectl_logs(logs=result.stdout)
                ...
            else:
                error_msg = result.stdout.strip() or (
                    f"kubectl logs command failed with return code {result.returncode}"
                )
```

四个可以直接讲的设计点：

1. **参数以数组传给 `subprocess`**，没有经过 Shell 解释 —— 参数里带 `;`、`$()` 也不会改变执行语义。这是和 YAML 脚本路径（要处理 `shlex.quote` 与引号）最重要的区别。
2. **当前与上一次容器日志是并行抓取的**（`kubernetes_logs.py:89`）：

    ```python
    def fetch_pod_logs(self, params: FetchPodLogsParams) -> StructuredToolResult:
        try:
            all_logs: list[StructuredLog] = []

            # Fetch previous and current logs in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_previous = executor.submit(self._fetch_kubectl_logs, params, previous=True)
                future_current = executor.submit(self._fetch_kubectl_logs, params, previous=False)
    ```

    本例的当前容器可能还没输出，上一次容器才是启动失败现场；两条都读，容器刚重启完时也能拿到证据。

3. **`check=False` 后按 `returncode` 自己判断**：错误不抛异常，而是拼成文本放进工具结果 —— 这样模型能看到“查询失败及原因”，而不是整轮调查崩掉。
4. `truncate_logs` 在**工具内部**按 token 预算截断，属于工具自己的责任，不是主循环的事。

**YAML 与 Python 的分工小结**：

| 维度 | kubernetes_jq_query（YAML） | fetch_pod_logs（Python） |
| --- | --- | --- |
| 工具定义 | YAML 字段 + 脚本 | 类 + `ToolParameter` |
| 参数来源 | 从 `{{ }}` 推断，无参数级说明 | 手写说明、类型、必填 |
| 执行 | Jinja 渲染后交给 Shell | 参数数组交给 `subprocess` |
| 业务校验 | 脚本内部自行处理 | Pydantic 模型 + `get_param_or_raise` |
| 错误返回 | 脚本输出 JSON，含 `stderr` 字段 | `returncode != 0` 时把输出作为错误 |

## 5 谁决定“哪些工具现在可用”

工具集不是加载了就能用，状态由前置条件决定。字段定义在 `holmes/core/tools.py:777`（省略部分）：

```python
class Toolset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    name: str
    description: str
    prerequisites: List[Union[StaticPrerequisite, ToolsetCommandPrerequisite,
                              ToolsetEnvironmentPrerequisite, CallablePrerequisite]] = []
    tools: List[Tool]
    tags: List[ToolsetTag] = Field(default_factory=lambda: [ToolsetTag.CORE])
    llm_instructions: Optional[str] = None
    approval_required_tools: List[str] = Field(default_factory=list)
```

日志工具集把“kubectl 是否可用”做成前置条件（`holmes/plugins/toolsets/kubernetes_logs.py:48`，省略 docs_url/icon_url）：

```python
    def __init__(self):
        prerequisite = StaticPrerequisite(enabled=False, disabled_reason="Initializing")
        super().__init__(
            name="kubernetes/logs",
            description="Read Kubernetes pod logs using a unified API",
            prerequisites=[prerequisite],
            enabled=True,
            tools=[],  # 先留空，再创建工具
            tags=[ToolsetTag.CORE],
        )
        self.tools = [PodLoggingTool(self)]
        enabled, disabled_reason = self.health_check()
        prerequisite.enabled = enabled
        prerequisite.disabled_reason = disabled_reason

    def health_check(self) -> Tuple[bool, str]:
        try:
            # Check if kubectl is available
            result = subprocess.run(
                ["kubectl", "version", "--client"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, ""
            else:
                return False, f"kubectl command failed: {result.stderr}"
        except subprocess.TimeoutExpired:
            return False, "kubectl command timed out"
        except FileNotFoundError:
            return False, "kubectl command not found (ensure kubectl is installed and in PATH)"
        except Exception as e:
            return False, f"kubectl health check error: {str(e)}"
```

管理层的职责在 `holmes/core/toolset_manager.py:238` 的说明里写得很直白：**前置检查并行执行并带超时，超时的工具集标记为 FAILED，避免一个挂住的数据源拖住启动**；`prepare_toolsets()`（同文件 `:666`）决定用缓存状态还是实时刷新。

有一个容易讲错的边界，值得单独记一句：**`health_check` 通过只证明 `kubectl` 客户端存在，不证明当前身份有 `pods/log` 权限。** 权限问题会在真正读取时以 `Forbidden` 返回。

## 6 一次调用怎么落地到具体实现

工具登记（`holmes/core/tools_utils/tool_executor.py:60`，省略冲突处理细节）：

```python
class ToolExecutor:
    def __init__(self, toolsets: List[Toolset], on_event: EventCallback = None):
        self.toolsets = toolsets
        self.enabled_toolsets = [ts for ts in toolsets if ts.status == ToolsetStatusEnum.ENABLED]

        self.tools_by_name: dict[str, Tool] = {}
        self._tool_to_toolset: dict[str, Toolset] = {}
        for ts, tool, resolved_name in resolve_tool_name_collisions(list(toolsets_by_name.values())):
            ...
            self.tools_by_name[resolved_name] = tool
            self._tool_to_toolset[resolved_name] = ts
```

按名字找实现（同文件 `:100`）：

```python
    def get_tool_by_name(self, name: str, user_id: Optional[str] = None) -> Optional[Tool]:
        if name in self.tools_by_name:
            return self.tools_by_name[name]
        user_tool = self.oauth_connector.find_tool(name, user_id)
        if user_tool:
            return user_tool
        logging.warning(f"could not find tool {name}. skipping")
        return None
```

发给模型的工具清单，就是把这些对象逐个转成 Schema（同文件 `:193`）：

```python
        return [tool.get_openai_format() for tool in self.tools_by_name.values()]
```

调用主流程只有一份（`holmes/core/tools.py:382`，注释为讲义所加）：

```python
    def invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        # 1. 打出人类可读的调用摘要（用 user_description / 脚本模板渲染）
        display_logger.info(f"Running tool {tool_number_str}[bold]{self.name}[/bold]: "
                            f"{self.get_parameterized_one_liner(params)}")

        # 2. 通用审批检查：需要审批就直接返回 APPROVAL_REQUIRED，不进入实现
        if not context.user_approved:
            approval_check = self._get_approval_requirement(params, context)
            if approval_check and approval_check.needs_approval:
                return StructuredToolResult(status=StructuredToolResultStatus.APPROVAL_REQUIRED, ...)

        # 3. 参数类型对齐（把模型给的字符串按 Schema 转成声明类型）
        params = self._coerce_params(params)

        # 4. 具体实现：YAML 工具和 Python 工具各自实现 _invoke
        start_time = time.time()
        result = self._invoke(params=params, context=context)

        # 5. 结果后处理（transformers）并记录耗时、输出大小
        transformed_result = self._apply_transformers(result)
        ...
        return transformed_result
```

**这就是插件化的落点**：`invoke()` 的骨架只有一份，`_invoke()` 有 N 份。一次调用从头到尾是：

```text
模型给出 工具名 + 参数 + 调用 ID
→ 执行器 get_tool_by_name() 找到 Tool 对象（找不到只 warning，不崩）
→ Tool.invoke()：审批检查 → 参数对齐 → _invoke() → transformers → 计时与日志
→ YAML 实现：渲染脚本交给 Shell   ／  Python 实现：调 _toolset.fetch_pod_logs()
→ StructuredToolResult 带着调用 ID 回到调查循环，进入下一轮模型请求
```

## 7 本 case 的实测调用链（2026-10-06 实跑，不是推演）

本次运行启用的工具集，和上面讲的加载、过滤机制对得上：

```text
ENABLED TOOLSETS (8): kubernetes/core, helm/core, internet, connectivity_check,
                      robusta, bash, skills, kubernetes/logs
```

两次通过运行的调用顺序（工具名 + 关键参数）：

| 22:45（19 次调用） | 22:48（9 次调用） |
| --- | --- |
| `kubernetes_tabular_query` ×4：找 deployments / pods / statefulsets / daemonsets | `kubernetes_tabular_query` ×2：找 deployments / pods |
| `bash` ×9 尝试 `kubectl -n app-09 describe pod ...`、`get events`、`get deployment -o yaml`（6 条被拒） | `bash` ×3：`kubectl get pods -A \| grep -i payment`、`describe pod ... -n app-09`、`get deployment ... -o yaml`（0 条被拒） |
| `fetch_pod_logs`：读到 `Environment variable DEPLOY_ENV is undefined` | `fetch_pod_logs`：同一条证据 |
| `kubernetes_jq_query`：列出 Deployment 的 `env` 字段确认没有变量 | `kubernetes_jq_query`：同一步核对 |

三个可以直接引用的观察：

1. **两次都靠 `fetch_pod_logs` 拿到根因证据，靠 `kubernetes_jq_query` 核对配置**：正是第 3、4 节讲的 YAML 与 Python 两条实现路径。
2. **22:45 的 6 条被拒命令是策略分支，不是环境故障**：命令片段没命中允许列表 → 返回 `requires approval` → pytest 入口没有审批交互 → 按拒绝处理。模型从返回信息里读出“前缀要原样出现在命令里”，把 `-n app-09` 移到命令末尾后成功；22:48 那次一开始就这么写，于是 0 条被拒。完整记录见 [本地环境与运行方法](../local-environment.md)。
3. **数字波动不小**：19 次调用 / 18.3 秒 vs 9 次调用 / 10.7 秒，结论相同。所以“工具用得多”不等于“诊断得好”，比较方案时要固定条件并重复运行。

## 8 三个失败分支：用代码定位，不用猜

| 失败情况 | 代码位置 | 会看到什么 |
| --- | --- | --- |
| 把 Deployment 名当准确 Pod 名 | `logging_api.py:178` 的 `get_param_or_raise`；`kubernetes_logs.py:221` 的 `returncode != 0` | 参数能通过，但 `kubectl logs` 返回 NotFound 文本；正确做法是先重新发现对象 |
| 当前身份没有 `pods/log` 权限 | 同上的 `returncode != 0` 分支 | Forbidden 作为工具结果返回；`health_check` 通过不代表有权限 |
| 读不到日志或读到的不是目标日志 | `logging_api.py:54` 的 `truncate_logs` + `filter` / `exclude_filter` / `limit` / `--previous` | 空结果必须区分“确实没有”“时间/过滤太窄”“读取失败”，并保留查询条件 |

一条通用边界，面试常被追问：**Schema 校验、参数类型转换、业务校验是三件事。** 本节第一行只覆盖了类型与必填；业务规则（例如 `kind` 必须是复数形式）写在 `description` 里，靠模型遵守，没有硬校验。

## 9 面试讲述：60 秒版与 2 分钟版

**60 秒精简版**（时间紧就说这五句，每句一个要点）：

1. 调查循环要同时用很多数据源，逐个在主循环里写平台逻辑，会让主循环去了解所有 API 的认证、分页和错误格式，所以要把“数据访问”和“调用框架”分开。
2. 所有工具继承同一个基类，提供名称、说明、参数三件套，统一翻译成 JSON Schema 给模型 —— 模型只看到合同，看不到实现。
3. 能力按 Toolset 组织，带共同配置、启用开关和前置条件，比如日志工具集启动时检查 kubectl 是否可用。
4. 执行器按名字登记工具，调用走同一个 `invoke()` 骨架，具体逻辑落在各自的 `_invoke()`：骨架一份，实现 N 份。
5. 接新数据源可以写 YAML 脚本，也可以写 Python 类，调查主循环不用改。

**2 分钟完整版**（可直接口述；括号里是它对应的小节，方便回查）：

> 我按三层来讲：合同层、组织层、执行层，最后说接一个新数据源要做什么。
>
> **先说不插件化会怎样。** 调查循环要同时用 Kubernetes、日志平台、监控这些数据源。如果每接一个都在主循环里加一段平台专用逻辑，主循环就得了解每个 API 的认证方式、参数格式、分页规则和错误结构，改一处影响整条流程。（第 1 节）
>
> **第一层是合同层。** 所有工具继承同一个 Tool 基类，必须提供 name、description、parameters 三件套；参数用 ToolParameter 描述类型、是否必填、取值范围，再由统一的 `get_openai_format()` 翻译成模型能读的 JSON Schema。模型看到的就是这份合同，看不到背后的 Shell 脚本或 Python 代码 —— 所以“选哪个工具、填什么参数”只依赖合同，不依赖实现。（第 1 节）
>
> **第二层是组织层。** 能力按 Toolset 分组：kubernetes/core 管资源查询，kubernetes/logs 管日志。Toolset 带共同配置、启用开关和前置条件：日志工具集初始化时会跑一次 `kubectl version --client`，不通过就把整个工具集标成不可用；前置检查并行执行且带超时，避免一个挂住的数据源拖慢启动。内建工具集加载后默认不启用，由用户配置决定开哪些，这同时控制了工具数量和上下文占用。（第 2、5 节）
>
> **第三层是执行层。** 执行器把所有启用的工具按名字登记成一张表，模型给出工具名和参数后按名字找到对象，找不到只记一条 warning，不让整轮调查崩掉。调用走同一个 `invoke()` 骨架：打调用摘要、通用审批检查、参数按 Schema 做类型对齐、调子类的 `_invoke()`、结果后处理、记录耗时和输出大小。骨架只有一份，`_invoke` 有 N 份，这就是插件化的落点。（第 6 节）
>
> **接新数据源有两条路。** 声明式的是 YAML：写一段脚本，参数从脚本里的 `{{ }}` 占位符自动推断成 Schema，适合“一段命令加参数替换”。复杂一点用 Python 类：手写参数说明，用 Pydantic 做业务校验。我用一个支付服务反复重启的 case 验证过这条链路：查资源走 YAML 工具，读日志走 Python 的 `fetch_pod_logs`，两者都通过同一个 `invoke()` 骨架执行，模型按各自的 Schema 填参数；日志工具内部还会并行抓当前和上一次容器日志，因为容器刚重启时当前容器可能还没输出。（第 3、4、7 节）
>
> **最后是一个取舍，也是我认为最关键的部分：统一接口不等于抹平差异。** 认证、业务校验、过滤和错误语义仍然由各实现负责。比如日志工具用参数数组调 kubectl、不经过 Shell；日志在工具内部按 token 预算截断；读取失败不抛异常，而是把“查了什么、为什么失败”作为结构化结果返回，模型才能自我纠正。统一的是契约和调用方式，不是把数据源的差异藏起来。（第 4、8 节）

讲的时候按需要增删：只讲三层、不加例子，约 60 秒；加上两条实现路径和一个实测结论，约 2 分钟。

## 10 高频追问与答法

### 10.1 五个核心追问

每个追问先答 30–60 秒，卡住只补当前问题：

| 核心追问 | 回答需要包含什么 | 对应小节 |
| --- | --- | --- |
| 为什么需要工具插件化？ | 多数据源接入需求；框架与数据访问职责分开；扩展时复用调查循环 | 1、2 |
| Tool、Toolset、Schema 分别负责什么？ | 一个操作、一组工具及共同管理、面向模型的能力描述 | 1、5 |
| 这个 case 怎样用到统一接口？ | 资源查询是 YAML、日志查询是 Python；同一个 `invoke()` 骨架；先发现准确 Pod | 3、4、6 |
| 模型怎么知道用哪个工具，程序怎么找到实现？ | 模型依据 description 与 Schema 选择；程序用 `tools_by_name` 按名字查找；调用 ID 关联结果 | 1、6 |
| 参数错误、无数据、权限不足分别怎么办？ | 参数对齐与业务校验；空结果要说明查询范围；底层错误作为工具结果返回 | 8 |

补两个深入问题：既然接口统一，为什么还需要每个工具自己做业务校验？工具变多以后，选择和上下文成本分别在哪里？

### 10.2 六个容易被打到的高频追问

| 追问 | 可以这样答 |
| --- | --- |
| 为什么不直接给模型 bash 跑 kubectl？ | 三条：**契约**（bash 是字符串，没有参数 Schema，模型得自己拼命令）；**上下文**（专用工具能做服务端分页、结果上限和 jq 过滤，bash 管道输出几万行会冲爆上下文）；**策略**（bash 有独立的允许/拒绝与审批，专用工具能按参数做更细的检查）。可以补实测：那次运行里有 6 条 kubectl 命令因未命中允许列表被拒，模型改用专用工具完成了同样的读取（第 7 节） |
| 工具多了会不会有上下文成本？ | 会。所以内建工具集默认关闭、按 tag 过滤、按需启用；工具数量本身不是质量指标（第 2、5 节） |
| YAML 和 Python 怎么选？ | YAML 的参数是从脚本占位符推断的，没有参数级说明、默认必填字符串，能力一复杂就讲不清；要业务校验、多步处理、平台 SDK 或精细错误语义，就用 Python（第 3、4 节） |
| 参数校验做到什么程度？ | 三层要分清：模型参数的类型对齐、Schema 层面的约束、具体工具的业务校验。第一层不等于第三层，业务规则（比如资源类型必须用复数）主要写在说明文字里、靠工具内部检查（第 8 节） |
| 工具报错模型怎么纠正？ | 错误必须结构化返回，带具体查询和原因，不能吞掉；工具描述里也会写“没结果就放宽过滤条件”这类纠偏提示（第 4、8 节） |
| 这套支持 MCP 吗？ | 支持。加载路径里除了内建 YAML/Python，还有 MCP 工具集分支（`holmes/plugins/toolsets/__init__.py:142` 的 `make_robusta_platform_mcp_toolset()`）和 OAuth 连接器（`holmes/core/tools_utils/oauth_tool_connector.py:26`），`Toolset` 本身也有 `url` 字段标注 MCP（`holmes/core/tools.py:1242`）。接口一致，所以调用路径不变。没把握时只说“加载路径里有 MCP 分支”，不要展开 |

简历里“约 50 个工具集、250 个工具能力”涉及版本与统计口径，先讲清设计，数字以实际核对为准；而且真正可用的工具还取决于启用配置与前置条件。

## 11 诚实边界与练习方法

### 11.1 面试官一定会追问：“这里面哪部分是你做的？”

这段回答描述的是 HolmesGPT 上游框架的设计。**把上游代码说成自己写的，会在下一层追问里露底**（“你改的是哪个文件、为什么这么改”）。安全的说法是：

> 这套框架是上游 HolmesGPT 的设计，我的工作是把这条链路在自己的集群里跑通、逐段核对源码，并用一个具体故障验证了它的行为和失败分支。所以我能解释每一步的输入输出、为什么这样设计、以及哪里会出问题。

如果确实自己接过工具、改过框架或写过测试，就换成具体动作和结果：改了什么、为什么改、怎么验证、结果如何。先在下表写下真实版本，再决定口述时展开到哪一层：

| 要提前想清楚的 | 我的版本 |
| --- | --- |
| 我亲手做过的（跑通 / 核对 / 写测试 / 改动） | |
| 我不能声称的（上游框架本身、没参与的设计决策） | |
| 我能当场证明的（复现步骤、日志、指标数字、失败分支） | |

### 11.2 三遍练习法

1. 第一遍：照着第 9 节的 2 分钟版读一遍，把不顺口的句子改成自己的说法。
2. 第二遍：合上文档，用自己的话讲一遍并录音；回听只检查两件事 —— 三层是否都讲到、有没有举出具体例子。
3. 第三遍：只看五张关键词卡片讲：**合同层 / 组织层 / 执行层 / 两条实现路径 / 一个取舍**。这一遍能顺下来，第 10 节的追问基本都能接住。

## 12 完成标准与当天记录

- [ ] 能解释插件化的需求，以及框架和工具各自的职责。
- [ ] 能用一个日志工具讲清接入、选择、执行与返回。
- [ ] 能说出 YAML 与 Python 两种实现各自要自己负责什么。
- [ ] 能完成 2 分钟讲述，并回答 5 个核心追问。
- [ ] 至少核对一条本页片段之外的定义或测试，说明它能证明什么。

今天的核心成果是一页自己的回答；函数名记忆和看过的文件数量不作为验收标准。想做实验可看 [本地工具接入实验](tool-lab.md)，Schema 相关断言在 [tests/test_openai_formatting.py](../../tests/test_openai_formatting.py)。

- 实际学习日期与用时：
- 我对“为什么需要插件化”的 3 句话：
- 我对 Tool、Toolset、Schema 和执行实现的分工解释：
- 我讲的那条端到端调用路径：
- 我的 2 分钟设计回答：
- 一个失败处理与一个方案取舍：
- 我核对的一条依据及它能证明的内容：
- 仍卡住的具体追问：

需要有人带着过一遍时，直接说：“带我学 Day 3，按讲义第 1–6 节，每节先让我说这段在解决什么，再检查我的解释。”
