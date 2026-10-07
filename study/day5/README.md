# Day 5 讲义：工具调用安全（沿 09_crashpod 走一次日志访问）

2026-10-06 改写为讲义。对应 10-04 版简历中“智能故障诊断 Agent”的第三条：工具调用安全。

2026-10-07 修订：纠正审批与 API 访问的顺序，说明 fetch_pod_logs 的工具集引用缺口；审批推演改用 Bash 日志访问，并补充离线验证和迁移自测。

**这份文档是自足的：今天需要看的源码已经按调用顺序摘录在正文里，不必再打开仓库对照。** 每个片段都标了 `文件:行号`，想深挖时再跳过去。全部片段来自当前工作区，行号是 2026-10-06 核对的。

> 片段说明：为便于阅读，摘录时做过少量排版整理 —— 换行、缩进、省略号（`...`）处有删减；函数名、参数名、字段名和逻辑顺序与源码一致，代码行均为源码原文。若有个别英文注释改写为中文，会标注“讲义译注”，标注处不是源码原文；要逐字对照时按行号打开源文件。

今天只回答一个问题：**同一次“读取 app-09 里 payment-processing-worker 日志”的请求，从模型给出参数到真正执行，中间有几道各自独立的门？每道门能挡住什么、挡不住什么？**

主角就是 case 里真实用过的那次资源与日志访问：

| 请求 | 实际使用的工具 | 本文位置 |
| --- | --- | --- |
| 先找到准确的 namespace 与 Pod 名 | `kubernetes_tabular_query` / `bash` | 3、4 节 |
| 读启动日志（证据 `Environment variable DEPLOY_ENV is undefined`） | `fetch_pod_logs` | 5、6 节 |
| 核对 Deployment 配置 / 需要人工确认时暂停并恢复一次 Bash 日志访问 | `kubernetes_jq_query` / `bash`；完整审批 + Token 恢复（推演，未运行） | 5、7 节 |

场景回顾见 [完整案例](../interview-plan.md) 前四步：用户只给服务名，Agent 要先发现准确对象，再读日志。该 case 已于 2026-10-06 实跑通过（22:45、22:48 连续两次 1/1）；其中 22:45 那次**真实触发过一条 Bash 策略分支**，见第 4 节。实测只引用 [本地环境与运行方法](../local-environment.md)，本讲义不引入该文件之外的数字。

## 建议用法

**核心路线**：第 1 节控制职责与分支 → 第 2 节 invoke 骨架和日志工具引用缺口 → 第 3 节允许/拒绝/待审批三态 → 第 4 节实测 → 第 5 节三条参数路径表 → 第 6 节 RBAC 边界 → 第 7 节 Token 载荷与恢复结论。必读代码是审批返回、Token 载荷和恢复校验；其余先读解释与表格。

**深入选读**：前缀匹配全部细节、Shell 模板引用实现、保存片段、签名密钥生命周期和真实集群权限实验。完整人工审批恢复另作实验，不计入当天核心用时。

| 顺序 | 时间 | 要做什么 | 留下什么 |
| --- | --- | --- | --- |
| 1 | 5 分钟 | 脱稿复习 Day 4 的证据边界 | 查询结果与执行权限的区别 |
| 2 | 35–50 分钟 | 按核心路线解释每个控制点 | 职责表与审批分支 |
| 3 | 15 分钟 | 第 9.1 节最小验证 | Token 正反例与日志审批缺口 |
| 4 | 20 分钟 | 第 8 节口述与核心追问 | 自己的录音、时长与一个取舍 |
| 5 | 15 分钟 | 第 9.2 节陌生变式，填写记录 | 合法审批与实际权限的边界 |

约 90–110 分钟，可拆成两次。首次阅读按实际卡点延长，深入实现按追问选读；最小验证未完成时记录待补。

## 1 全景：同一次日志访问上的五道控制

五个控制点不是对所有工具依次执行的流水线。配置检查需要工具持有工具集引用，命令策略只适用于完整 Bash；需要审批时，在执行命令和访问 API 之前暂停：

```text
模型提出 工具名 + 参数 + 调用 ID
→ 配置审批检查；未命中时再问工具自己的审批钩子（Bash 在此分析命令）
  ├─ 不需审批：进入参数处理与具体实现
  ├─ 需审批：暂停 → 签发 Token → 人的决定 → 恢复校验
  │           ├─ 批准且验证通过：进入参数处理与具体实现
  │           └─ 拒绝、无审批入口或验证失败：返回错误，不执行命令
  └─ Bash 策略拒绝：由具体实现返回错误，不执行命令
→ 实际执行查询时，由 Kubernetes 按访问身份检查 RBAC
```

| 控制点 | 负责什么 | 代码位置 | 挡不住什么 |
| --- | --- | --- | --- |
| 配置门 | 按工具名（支持通配）要求审批 | `holmes/core/tools.py` | 默认空列表表示不审批；缺少 `toolset` 引用时检查也不生效 |
| 策略门 | 按命令结构判定允许 / 拒绝 / 待审批 | `bash/validation.py`、`bash/bash_toolset.py` | 只作用于 Bash 工具，不是所有工具的公共闸门 |
| 参数门 | 决定参数能否改变执行语义 | `tools.py`、`kubernetes_logs.py`、`kubernetes.yaml` | 不做权限判断；模板写法错了引用保护会失效 |
| 权限门 | 实际身份能否读这个资源 | Kubernetes RBAC | 审批通过也不会增加权限 |
| 审批与 Token | 把人的决定绑定到调用 ID、工具名与参数 | `tool_calling_llm.py`、`approval_tokens.py` | 不证明审批人身份，也不是完整的一次性防重放 |

这三类问题正好各归一道门：**越权**（身份没有 `pods/log` 权限）由 Kubernetes API 回答；**参数注入**（Pod 名或 jq 参数混入 Shell 语法）由参数门按执行路径回答；**高风险操作**（策略要求读日志也必须审批）由配置门与人的门回答。Prompt 可以影响模型选择，但不能替代其中任何一道，也不必把所有安全机制都归结为“只读”或“加了黑名单”。

## 2 第一道门：配置说“这个工具要审批”

审批要求的返回值是一个小模型（`holmes/core/tools.py:143`）。输入是“工具集配置 + 工具名”，输出只有三种可能：没有要求（`None`）、需要审批（`needs_approval=True` 并给出 `reason`）、以及在 Bash 场景下顺带带回“批准后可以记住的片段”。

```python
class ApprovalRequirement(BaseModel):
    needs_approval: bool
    reason: str = ""
    # Prefixes to save when user approves (for bash toolset)
    prefixes_to_save: Optional[List[str]] = None
```

这段判断放在所有工具共用的 `invoke()` 骨架前部，早于参数类型对齐和具体实现（`holmes/core/tools.py:392`，省略了中间一条多行日志）。三个设计点：**输入**是模型给的参数加调用上下文，**输出**是 `StructuredToolResult(status=APPROVAL_REQUIRED)`，此时 `_invoke()` 根本没被调用，所以这次待审批调用尚未执行；`user_approved=True` 时整段跳过，这是第 7 节恢复路径的开关；`prefixes_to_save` 被写回 `params["suggested_prefixes"]`，随之进入展示给用户的待审批对象，服务端保存片段时还要再核对（第 7 节）。

```python
        if not context.user_approved:
            approval_check = self._get_approval_requirement(params, context)
            if approval_check and approval_check.needs_approval:
...
                if approval_check.prefixes_to_save is not None:
                    params["suggested_prefixes"] = approval_check.prefixes_to_save
                return StructuredToolResult(
                    status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                    error=approval_check.reason,
                    params=params,
                    invocation=self.get_parameterized_one_liner(params),
                )
```

那“是否需要审批”由谁决定（`holmes/core/tools.py:429`，省略了工具集查找与空值判断）。它按顺序问两个来源：先看工具集配置，再问工具自己的钩子。配置项是一个通配列表（`holmes/core/tools.py:805`），`fnmatch` 支持 `*`（字段描述里写明 `'*'` 表示全部工具），匹配用 `mcp_tool_name or self.name` 而不是对外的改名，避免 MCP 工具改名后绕过策略。

```python
    def _get_approval_requirement(
        self, params: Dict, context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        toolset_approval = self._check_approval_config()
        if toolset_approval and toolset_approval.needs_approval:
            return toolset_approval
        return self.requires_approval(params, context)
...
    def _check_approval_config(self) -> Optional[ApprovalRequirement]:
...
        real_name = getattr(self, "mcp_tool_name", "") or self.name
        for pattern in getattr(toolset, "approval_required_tools", []):
            if fnmatch.fnmatch(real_name, pattern):
                return ApprovalRequirement(
                    needs_approval=True,
                    reason=f"Tool '{real_name}' matches approval pattern '{pattern}'",
                )
        return None
```

```python
    approval_required_tools: List[str] = Field(
        default_factory=list,
        description="Tool names/patterns that require user approval before execution (use '*' for all tools)",
    )
```

工具自己的钩子默认什么都不要求（`holmes/core/tools.py:452`），Bash 工具重写它来做真正的命令分析（第 3 节）。记住这条顺序：**配置门是粗粒度的名字开关，钩子才是能看参数的地方。**

```python
    def requires_approval(
        self, params: Dict, context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        """Override to implement tool-specific approval logic."""
        return None
```

**当前日志工具的实现缺口（2026-10-07 离线核对）**：`_check_approval_config()` 用 `getattr(self, "toolset", None)` 取工具集，缺少该属性就返回 `None`；`PodLoggingTool` 却只在构造时保存 `self._toolset = toolset`（`logging_utils/logging_api.py:176`）。因此，只在 `kubernetes/logs` 配置 `approval_required_tools: ["fetch_pod_logs"]`，并不能保证暂停审批。第 9.1 节可重现这个检查结果。本页记录该缺口，审批流程改用持有 `toolset` 引用的 Bash 工具讲解；不能把通用接口存在说成所有工具都已接通配置。

## 3 第二道门：Bash 的允许 / 拒绝 / 待审批

完整 Bash 工具的判定结果是三态，不是布尔值（`holmes/plugins/toolsets/bash/validation.py:76`，省略了各枚举的 docstring）。注意 `DenyReason` 里只有 `PREFIX_NOT_IN_COMMAND` 带 `fabricated_prefix` 这个值，它专门对付“模型声明的片段在命令里根本不存在”；`ValidationResult` 另外带一个 `prefixes_needing_approval`，只有“待审批”时才非空——它就是上面 `prefixes_to_save` 的来源。

```python
class ValidationStatus(Enum):
...
    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
...
class DenyReason(Enum):
...
    HARDCODED_BLOCK = "hardcoded_block"
    DENY_LIST = "deny_list"
    PREFIX_NOT_IN_COMMAND = "fabricated_prefix"
    DANGEROUS_ARGUMENT = "dangerous_argument"
...
@dataclass
...
    status: ValidationStatus
...
    # Prefixes that need approval (for APPROVAL_REQUIRED status)
    prefixes_needing_approval: Optional[List[str]] = None
```

生效的列表由 `get_effective_lists()` 从内置档位加用户配置拼出来（`holmes/plugins/toolsets/bash/validation.py:104`，省略了 docstring 与“把工具结果目录加入白名单”的 14 行）。输入是工具集配置，输出是两份列表：`builtin_allowlist` 决定内置档位（`core` / `extended` / `none`），deny 列表来自 `DEFAULT_DENY_LIST` 加用户配置，两者都做过去重和排序的拷贝。

```python
def get_effective_lists(config: BashExecutorConfig) -> Tuple[List[str], List[str]]:
...
    if config.builtin_allowlist == "extended":
        builtin = EXTENDED_ALLOW_LIST
    elif config.builtin_allowlist == "core":
        builtin = CORE_ALLOW_LIST
    else:
        builtin = []
...
    allow_list = sorted(set(builtin + config.allow + tool_result_prefixes))
    deny_list = sorted(set(DEFAULT_DENY_LIST + config.deny))

    return allow_list, deny_list
```

判定分两个层次：先看硬编码块（`HARDCODED_BLOCKS` 定义在 `holmes/plugins/toolsets/bash/common/config.py:9`，注释写明“永远阻止且不可覆盖”，匹配用的是与 deny 列表相同的严格函数），再看前缀匹配（`holmes/plugins/toolsets/bash/validation.py:325`，第一段是 check_hardcoded_blocks，第二段是 match_prefix 的判定体；省略 docstring 示例），其中前缀匹配是整条链上最关键的一段逻辑：**allow 前缀必须是这一段的字面开头**，前缀之后只能接空白或 `/`。第 4 节的实测案例就是被这一条挡下的。

```python
    segment_lower = segment.lower()
    for block in HARDCODED_BLOCKS:
        if match_prefix_for_deny(segment_lower, block):
            return block
...
def match_prefix(segment: str, prefix: str) -> bool:
...
    segment = segment.strip()
    prefix = prefix.strip()

    if not segment.startswith(prefix):
        return False

...
    if len(segment) > len(prefix):
        next_char = segment[len(prefix)]
        # Allow whitespace or path separator as boundary
        if not (next_char.isspace() or next_char == "/"):
            return False

    return True
```

单段判定的顺序写得很直白（`holmes/plugins/toolsets/bash/validation.py:430`）：硬编码块 → deny 列表 → allow 列表 → 都不命中就待审批。deny 用的是更严格的匹配函数（额外匹配复数形式，例如 `kubectl get secret` 也能拦住 `kubectl get secrets`），所以“先 deny 再 allow”意味着加白名单无法放开被拒绝的命令。

```python
    1. Hardcoded blocks -> DENIED
    2. Deny list -> DENIED
    3. Allow list -> ALLOWED
    4. Neither -> APPROVAL_REQUIRED
...
    # Step 3: Check allow list
    for allow_prefix in allow_list:
        if match_prefix(segment, allow_prefix):
            return ValidationResult(status=ValidationStatus.ALLOWED)
...
    return ValidationResult(
        status=ValidationStatus.APPROVAL_REQUIRED,
        message=f"Command segment '{segment}' is not in the allow list.",
    )
```

到了整条命令这一层还有两条补充规则。第一，模型声明的片段必须真的出现在命令里，否则直接拒绝（`holmes/plugins/toolsets/bash/validation.py:483`）；这条把“先声明几个无害片段、实际执行别的命令”这种绕过方式堵死。第二，命令含复合语句时一律走审批，即使每一段都在白名单里（`holmes/plugins/toolsets/bash/validation.py:546`，省略了构造待保存片段列表的 10 行）；而 allow 是否命中是逐段判断的，任意一段被 deny 就整条拒绝。

```python
    # Verify all suggested prefixes actually appear in the command
    for prefix in suggested_prefixes:
        if prefix not in command:
            return ValidationResult(
                status=ValidationStatus.DENIED,
                deny_reason=DenyReason.PREFIX_NOT_IN_COMMAND,
                message=f"Suggested prefix '{prefix}' does not appear in the command.",
            )
```

```python
    if contains_compound_command:
        return ValidationResult(
            status=ValidationStatus.APPROVAL_REQUIRED,
            message="Contains compound statements (for/while/if/etc).",
            prefixes_needing_approval=[],
        )
...
            status=ValidationStatus.APPROVAL_REQUIRED,
            message=f"Segment(s) not in allow list: {', '.join(repr(s) for s in unapproved_segments)}",
            prefixes_needing_approval=prefixes_needing_approval,
        )
```

Bash 工具把这份分析接进第 2 节的钩子（`holmes/plugins/toolsets/bash/bash_toolset.py:174`，省略了 docstring 与 DENIED 分支）：`DENIED` 时返回 `None`（不在审批层解决，而是让 `_invoke()` 返回错误），`APPROVAL_REQUIRED` 时才构造 `ApprovalRequirement`，并把 `prefixes_needing_approval` 作为可保存片段带出去。拒绝文案由 `_build_deny_error_message()` 按原因分类（`holmes/plugins/toolsets/bash/bash_toolset.py:275`，省略了分支之间的空行），三种原因给模型三种措辞，模型因此能区分“永久禁止”“配置禁止”“片段声明无效”。

```python
        command_str = params.get("command", "")
        suggested_prefixes = params.get("suggested_prefixes", [])
...
        if not command_str or not suggested_prefixes:
            return None  # Let _invoke() handle validation errors
...
        if validation_result.status == ValidationStatus.APPROVAL_REQUIRED:
            prefixes_to_save = validation_result.prefixes_needing_approval
            return ApprovalRequirement(
                needs_approval=True,
                reason=f"Command requires approval. {validation_result.message}",
                prefixes_to_save=prefixes_to_save,
            )
```

```python
        if validation_result.deny_reason == DenyReason.HARDCODED_BLOCK:
            return f"Command blocked: {validation_result.message}"
...
        elif validation_result.deny_reason == DenyReason.DENY_LIST:
            return f"Command blocked by configuration: {validation_result.message}"
...
        elif validation_result.deny_reason == DenyReason.PREFIX_NOT_IN_COMMAND:
            return f"Invalid prefix: {validation_result.message}"
...
        else:
            return validation_result.message or "Command denied."
```

模型要使用的片段由它自己声明（`holmes/plugins/toolsets/bash/bash_toolset.py:123`，省略了 type / items / required 字段），参数描述里明确写了“一个命令段一个片段、不要带资源名和 namespace”。这是一个有意思的取舍：**策略判定的输入里有一部分来自被检查者自己**，所以校验端必须反过来确认这些片段确实出现在命令里（本节第一条补充规则），而 deny 检查用的是命令本身而不是片段。还要记住一个边界：allow 列表不是操作系统沙箱，命令仍然交给 `/bin/bash` 执行，策略只决定“现在放不放行”。

```python
                "suggested_prefixes": ToolParameter(
                    description=(
                        "Array of command prefixes, one per command segment. "
                        "Include command name and subcommand (e.g., 'kubectl get', 'grep'). "
                        "Do NOT include resource names, namespaces, or flag values."
                    ),
```

## 4 实测：22:45 的 6 条 `requires approval`，22:48 的 0 条

[本地环境与运行方法](../local-environment.md) 记录：22:45 那次有 6 条 Bash 命令被拒，命令片段形如 `kubectl ... describe pod`、`kubectl ... get events`、`kubectl ... get deployment -o yaml`，片段未命中允许列表，工具返回 `requires approval`，而 pytest 入口没有审批交互，于是按拒绝处理；模型从返回信息里读出“前缀要原样出现在命令里”，把 `-n app-09` 移到命令末尾后通过；22:48 那次 0 条被拒。

机制在代码里完全对得上。`core` 档位的白名单里本来就有这几条（`holmes/plugins/toolsets/bash/common/default_lists.py:29`，中间省略了 kubectl top / explain / api-resources 等条目），所以问题不在“缺少白名单”，而在**段落的开头**：`kubectl -n app-09 describe pod ...` 这一段不以 `kubectl describe` 开头，`match_prefix` 不命中（第 3 节），于是 `validate_segment` 走到第四步返回 `APPROVAL_REQUIRED`。理由字符串由 `bash_toolset.py:192` 拼成，内容是 `Command requires approval.` 加上 `holmes/plugins/toolsets/bash/validation.py:563` 的 `Segment(s) not in allow list: '...'`。模型读到的正是“这一段不在允许列表里，而允许列表里的前缀必须原样出现在命令里”，于是把 `-n app-09` 移到末尾，段落开头变回 `kubectl describe`，命中白名单后直接执行。

```python
    "kubectl get",
    "kubectl describe",
    "kubectl logs",
...
    "kubectl events",
```

为什么“没有审批交互”会变成拒绝？因为 `enable_tool_approval` 只在存在审批回调时才为真（`holmes/core/tool_calling_llm.py:642`），而 pytest 入口调用模型时不带审批回调（`tests/llm/test_ask_holmes.py:685`）。没有审批流程时，框架不是静默丢掉这条结果，而是把它改写成明确的错误（`holmes/core/tool_calling_llm.py:1409`，省略了收集待审批对象的 28 行）：状态从 `APPROVAL_REQUIRED` 改为 `ERROR`，文案里特意写明“命令没有执行、也没有排队等待任何人的审批”。那段注释解释得很清楚——如果只回一句 “requires approval”，模型会告诉用户“命令正在等人工批准”，而那是假的。

```python
                    if (
                        tool_call_result.result.status
                        == StructuredToolResultStatus.APPROVAL_REQUIRED
                    ):
...
                            tool_call_result.result.status = (
                                StructuredToolResultStatus.ERROR
                            )
                            tool_call_result.result.error = (
                                "Tool call rejected: it requires human approval, "
                                "and approval is not available in this conversation. "
                                f"({original_error}) "
                                "The command was NOT executed and NOT submitted or queued for approval - "
                                "nothing is waiting for anyone's approval. "
                                "Do not tell the user the command was submitted or is awaiting approval. "
                                "Instead, explain that you cannot run this command in this conversation and why."
                            )
```

三点可以直接引用的观察：这是**策略分支而非环境故障**（集群、kubectl、模型都正常，是命令写法没满足前缀匹配）；**返回信息质量决定自愈能力**（错误带了具体段落和原因，模型才能一次改对）；数字波动不小（22:45 为 19 次调用、22:48 为 9 次），比较方案要固定 case、模型与判分标准并重复运行。

## 5 第三道门：参数怎样进入执行

同一次“读日志”，两个工具的参数入口完全不同。Python 日志工具 `_fetch_kubectl_logs()` 把参数放进数组交给 `subprocess`（`holmes/plugins/toolsets/kubernetes_logs.py:227`，省略了 previous 分支、try 与 timeout 行）。输入是已经过 Pydantic 校验的 `pod_name` / `namespace`，输出是 kubectl 的原始输出与返回码；参数没有经过 Shell，所以值里带 `;` 或 `$()` 不会变成第二条命令。这是本例三条路径里唯一不需要引号处理的一条。

```python
        cmd = [
            "kubectl",
            "logs",
            params.pod_name,
            "-n",
            params.namespace,
            "--all-containers=true",
            "--timestamps=true",
            "--prefix=true",
        ]
...
            result = subprocess.run(
                cmd,
                text=True,
...
                check=False,  # do not throw error, we just return the error code
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
```

YAML 工具走的是另一条路：Jinja 渲染成脚本，再交给 `/bin/bash` 执行（`holmes/plugins/toolsets/kubernetes.yaml:32`，中间省略一行空行与两行注释）。它的输入是模型给的 `kind` 与 `jq_expr`，输出是脚本的 JSON 输出。关键是 `KIND={{ kind }}` 这种赋值写法：参数先经过 `sanitize()` 处理（`holmes/core/tools.py:150`，省略了注释与空行），即 `shlex.quote()`，然后放在**没有引号的赋值位置**，脚本里只以 `"$VAR"` 引用。源码注释解释了为什么不能写成 `KIND="{{ kind }}"`：`shlex.quote` 会给危险值套单引号，而在双引号内部单引号只是普通字符，命令替换仍然生效。参数进入模板的统一入口在 `holmes/core/tools.py:601`（`_build_context()` 的第一行），所有 YAML 工具的参数都先过 `sanitize_params`。

```yaml
          # SECURITY: assign sanitized params to shell variables in an unquoted
          # assignment slot, then reference them only as "$VAR". Never interpolate
          # {{ param }} inside a quoted context - sanitize() (shlex.quote) wraps
          # dangerous values in single quotes, which are literal (and command
          # substitution stays active) inside a double-quoted or single-quoted slot.
          KIND={{ kind }}
...
          JQ_FILTER={{ jq_expr }}
          JQ_FILTER=$(printf '%s' "$JQ_FILTER" | tr $'\n\r' '  ')
```

```python
def sanitize(param):
...
    if param == "":
        return ""

    return shlex.quote(str(param))
```

第三个入口是完整 Bash 工具本尊（`holmes/core/tools.py:714`，省略了函数签名细节、异常处理与超时、管道设置）：渲染后的字符串以 `shell=True`、`executable="/bin/bash"` 执行。也就是说，**第 3 节的策略门是这条路径唯一的程序性防线**，一旦放行，Shell 语义全部生效。还有一类参数不属于工具作者设计的入参，而是透传的请求上下文（例如 HTTP 头），仓库对它们采取直接拒绝而不是加引号：含 Shell 元字符就报错（`holmes/core/tools.py:179` 定义字符集，`holmes/core/tools.py:182` 是校验函数）。

```python
    def __execute_subprocess(self, cmd: str) -> Tuple[str, int]:
...
            result = subprocess.run(
                protected_cmd,
                shell=True,
                executable="/bin/bash",
```

| 路径 | 参数如何进入执行 | 输入校验 | 需要自己处理的风险 |
| --- | --- | --- | --- |
| Python `fetch_pod_logs` | 参数数组 → `subprocess`，默认不过 Shell | Pydantic 模型 + 必填检查 | 参数语义与资源权限 |
| YAML `kubernetes_jq_query` | `sanitize()` → 无引号赋值 → `"$VAR"` | 由模板写法决定 | 引号上下文、模板位置 |
| 完整 Bash 工具 | 渲染后的整条命令 → `/bin/bash` | 第 3 节的策略门 | 命令结构本身 |

JSON Schema 只描述参数类型，不能代替上面任何一种执行检查；这张表也说明为什么“所有工具都过了同一道安全检查”是不成立的说法。本 case 没有构造过注入攻击（参数里带 Shell 元字符）的实测，本节对三条路径的区分来自代码阅读，注入场景未运行。

## 6 第四道门：权限始终由 Kubernetes 决定

日志工具的返回码分支只有一件事：**把 kubectl 的错误原文当成结果返回，不重试、不绕路**（`holmes/plugins/toolsets/kubernetes_logs.py:252`，省略成功分支的 3 行）。因为 `stderr` 被合并进了 stdout（同文件 249 行的 `stderr=subprocess.STDOUT`），kubectl 的 `Error from server (Forbidden): ...` 会原样落在 `result.stdout` 里，成为 `LogResult.error`；两条日志（当前容器与上一次容器）都失败时，工具把它们当作错误结果交给模型（同文件 151–156 行）。所以“权限不足”在这个设计里是一个**正常的工具结果**，不是异常，也不需要框架替模型做权限判断。

```python
            if result.returncode == 0:
                # Parse the logs - kubectl with --all-containers prefixes lines with container name
...
            else:
                error_msg = (
                    result.stdout.strip()
                    or f"kubectl logs command failed with return code {result.returncode}"
                )
```

健康检查容易被误读成权限检查。日志工具集的前置条件只是跑一次客户端命令（`holmes/plugins/toolsets/kubernetes_logs.py:69` 起的 `health_check` 调 `kubectl version --client`），它证明 kubectl 在 PATH 里，**不证明当前身份有 `pods/log` 权限**。部署场景下权限由 Helm 渲染的 RBAC 决定：默认 `namespaceScopedRBAC: false`（[values.yaml](../../helm/holmes/values.yaml)），角色模板按该开关在 Role 与 ClusterRole 之间切换（[服务账号模板](../../helm/holmes/templates/holmesgpt-service-account.yaml)），是否渲染及实际规则还取决于安装选项与自定义规则。本例的 `kubernetes_jq_query` 走的是跨命名空间集合接口，即使 jq 最终只留 app-09 的结果，也仍然需要对应 API 的读取权限。

一句总结：**审批通过只能让 Agent 有权“提出这次调用”，不能让它在集群里多出任何权限。** 本节的代码路径在本次实测里没有被触发过——集群用的是本机 kubectl 凭据，没有部署 Helm chart，也没有验证过 Forbidden 分支，因此这一节是按代码的说明，不是实测结果。

## 7 第五道门：人工审批与签名 Token

需要审批的调用在参数处理和 API 访问前暂停，跨过“人”的时间，再继续执行。这里用同一调查的 Bash 日志访问推演：在 **`bash` 工具集**配置 `approval_required_tools: ["bash"]`，模型提出 `command: "kubectl logs <准确 Pod 名> -n app-09"`、`suggested_prefixes: ["kubectl logs"]`。完整交互恢复未在本 case 实跑；不能用第 2 节有引用缺口的 `fetch_pod_logs` 配置替代。

过程拆成三步：保存待审批调用 → 绑定签名 Token → 恢复时校验。

**第一步，暂停。** 工具返回 `APPROVAL_REQUIRED` 后，框架先把这条调用登记成待审批对象，再在对话历史里给它打标记并签发 Token（`holmes/core/tool_calling_llm.py:1416`，第一段登记待审批调用并保留完整参数，第二段打标记并签发 Token）。两个关键事实：**待审批的调用留在历史消息里**，客户端只需要回传“调用 ID + 决定”；**Token 与这条历史消息一一对应**，不与人或会话绑定。

```python
                            pending_approvals.append(
                                PendingToolApproval(
                                    tool_call_id=tool_call_result.tool_call_id,
                                    tool_name=tool_call_result.tool_name,
                                    description=tool_call_result.description,
                                    params=tool_call_result.result.params or {},
                                )
                            )
...
                if pending_approvals:
                    for approval in pending_approvals:
                        tool_call = self.find_assistant_tool_call_request(
                            tool_call_id=approval.tool_call_id, messages=messages
                        )
                        token = mint_token(
                            tool_call_id=tool_call["id"],
                            tool_name=tool_call.get("function", {}).get("name", ""),
                            args_json=tool_call.get("function", {}).get("arguments", ""),
                        )
                        tool_call["pending_approval"] = True
                        tool_call["approval_token"] = token
```

**第二步，Token 绑定什么。** Token 是 HS256 JWT（`holmes/utils/approval_tokens.py:64`，第一段是 args_hash 的口径，第二段是 mint_token 的载荷），载荷只有调用 ID、工具名、参数哈希和签发 / 过期时间（`TOKEN_TTL_SECONDS` 是 30 天，见 `holmes/utils/approval_tokens.py:24`）。参数本身进不了 Token，只进哈希：先把 JSON 字符串解析、再按 `sort_keys=True` 和紧凑分隔符重新序列化，因此**键顺序与空白差异不影响哈希，参数值一改哈希就变**；空字符串、纯空白和 `None` 归一成 `{}`，非法 JSON 会抛解析异常，恢复校验会将其包装为审批验证失败。

```python
def args_hash(args_json_string: Optional[str]) -> str:
...
    text = (args_json_string or "").strip()
    parsed = json.loads(text) if text else {}
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
...
    return jwt.encode(
        {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "args_hash": args_hash(args_json),
            "iat": now,
            "exp": now + TOKEN_TTL_SECONDS,
        },
        SIGNING_KEY,
        algorithm="HS256",
    )
```

**第三步，恢复时校验。** 恢复路径的入口是客户端回传决定后的 `tool_decisions`（`holmes/core/tool_calling_llm.py:1117` 先处理它们），恢复逻辑在 `_execute_tool_decisions()` 里，校验函数只显式比对三项（`holmes/utils/approval_tokens.py:100`，省略了“没有 Token”的两行与两个异常分支），签名与有效期由 PyJWT 在 `jwt.decode` 里自动完成（过期会抛 `ExpiredSignatureError`，再被包装成 `ApprovalTokenError`）。校验通过后会把 `pending_approval` 与 `approval_token` 两个字段从历史里删掉（`holmes/core/tool_calling_llm.py:300`，省略了记录拒绝原因的 15 行），然后才继续执行；校验失败则把该调用改成“未批准、未验证”。

```python
    try:
        claims = jwt.decode(token, SIGNING_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
...
        ok = (
            claims.get("tool_call_id") == tool_call_id
            and claims.get("tool_name") == tool_name
            and claims.get("args_hash") == args_hash(args_json)
        )
...
    if not ok:
        raise ApprovalTokenError(
            "claims do not match tool_call_id / tool_name / args_hash"
        )
```

```python
                    if tool_call.get("pending_approval"):
                        try:
                            verify_token(
                                tool_call.get("approval_token"),
                                tool_call_id=tool_call.get("id", ""),
                                tool_name=tool_call.get("function", {}).get("name", ""),
                                args_json=tool_call.get("function", {}).get("arguments", ""),
                            )
                        except ApprovalTokenError as exc:
...
                        del tool_call["pending_approval"]
                        tool_call.pop("approval_token", None)
                        pending_tool_calls.append(
                            ToolCallWithDecision(
                                tool_call=ChatCompletionMessageToolCall(**tool_call),
                                decision=decision,
                                message_index=i,
                            )
                        )
```

两种结局的差别只在几行代码里。批准时以 `user_approved=True` 重新发起同一次调用（`holmes/core/tool_calling_llm.py:344`，省略了 OAuth 分支、拒绝文案分支与结果字段）——这一路会跳过第 2 节的审批检查，Bash 工具也不再重复校验（`bash_toolset.py:234` 是那句 `if not context.user_approved:`）。未批准或校验失败时返回一条普通错误结果，其中 `verified=False` 正是 Token 校验失败的专用标记（`holmes/core/models.py:130`）；用户看到的是统一拒绝文案，具体原因只进服务端日志，避免探测者从差异里推断哪一项校验没过（`holmes/utils/approval_tokens.py:27`）。

```python
            if tool_decision and tool_decision.approved:
                # Process OAuth auth code exchange if this decision carries one
                oauth_code = parse_oauth_decision(tool_decision.decision)
                user_id = (request_context or {}).get("user_id")
...
                        user_approved=True,
                        session_approved_prefixes_by_agent=session_prefixes_by_agent,
                        request_context=request_context,
                        enable_tool_approval=True,  # always True when processing decisions
                    )
...
                tool_result = ToolCallResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.function.name,
...
                        error=error_text,
                    ),
                )
```

签名密钥的取值方式决定了一个运维后果（`holmes/utils/approval_tokens.py:54`，省略了 if raw 与它的日志行）：环境变量 `HOLMES_APPROVAL_SIGNING_KEY` 存在就用它（不校验强度），否则**每次启动随机生成 32 字节**。没配置持久密钥时，重启会让所有未完成的审批失效——这正是拒绝文案里提示的常见原因。CLI 入口默认会弹出审批菜单（`holmes/interactive.py:2314`），而第 4 节看到的 pytest 入口没有审批回调，两者差别只在于是否传入了这个回调。“记住这个片段”也是审批的一部分：决定里带 `save_prefixes` 时，服务端只在它确实属于该命令已声明的片段时才保存（`holmes/core/tool_calling_llm.py:415` 起），防止客户端虚报，保存的片段还会带着自己的签名 Token 存进历史。

```python
    raw = os.environ.get("HOLMES_APPROVAL_SIGNING_KEY", "").strip()
...
        return raw
    return secrets.token_bytes(32)
```

至少记住一项取舍：严格的策略能减少误操作，也会挡住合理的排查动作；人工审批提供人的判断，代价是中断自动调查，并要求用户看懂自己要批准什么。Token 的边界也要说清：**它证明“这条调用被签发过”，不证明审批人是谁**；恢复时删字段只保证同一份历史里不会重复兑现，客户端保留旧历史仍可在有效期内重放，所以“完整的一次性防重放”还需要会话或服务端的其他机制。

### 7.1 各种情形的实际验证范围

| 情形 | 会走到哪道门 | 预期结果 | 状态 |
| --- | --- | --- | --- |
| `fetch_pod_logs` 读 app-09 某个 Pod 日志 | 配置门未命中 → 直接执行 | 执行；若身份无 `pods/log` 权限则由 RBAC 返回 Forbidden | 读取路径已实测；Forbidden 分支未运行 |
| 完整 Bash 命令的片段不在允许列表 | 策略门返回 `APPROVAL_REQUIRED` | pytest 入口无审批交互 → 改写为 `ERROR` | 22:45 实测 |
| 在日志工具集配置 `approval_required_tools: ["fetch_pod_logs"]` | 通用检查读不到 `toolset` 引用 | 当前检查返回 `None`，不能声称配置已触发审批 | 2026-10-07 离线检查；未执行日志查询 |
| 在 Bash 工具集配置 `approval_required_tools: ["bash"]`，提出上述日志命令 | 配置门命中 → 暂停；支持交互时签发 Token | 人工决定后恢复；无交互时按拒绝处理 | 配置检查可离线核对；完整交互未运行 |
| 待审批 Bash 日志请求将 `command` 中的 `-n app-09` 改成 `-n prod` | 恢复校验比对 `args_hash` | 内容不匹配 → 拒绝；“已批准”标记不足以通过 | Token 原语可按第 9.1 节离线验证；完整恢复未运行 |

## 8 面试讲述与核心追问

先借助骨架讲一遍，再合上文档用自己的话讲第二遍。简历写的是“分析并验证”这些机制，实际验证范围见第 10 节。

> 在支付服务重启这个 case 里，日志访问受实际 Kubernetes 身份约束，人工批准也不会增加权限。Python 日志工具用参数数组调用 subprocess，YAML 查询要正确引用 Shell 模板参数，完整 Bash 另有允许、拒绝和待审批三态策略。需要审批时，先暂停并签发绑定调用 ID、工具名与参数哈希的 Token；人工批准、恢复校验通过后，才执行查询并接受 RBAC 检查。例如待审批 Bash 日志命令中的 namespace 被改动，Token 校验就应拒绝。本机实测过无审批入口时命令被拒、模型据错误改写命令；完整人工批准恢复仍是推演。另外，当前 Python 日志工具的工具集引用没有接通通用审批配置，不能声称只加审批列表就生效。Token 绑定内容的作用，也要与身份认证和防重放分开说明。

每个追问先回答 30–60 秒，卡住后只补当前问题。

| 核心追问 | 回答需要包含什么 | 对应小节 |
| --- | --- | --- |
| 为什么 Prompt 或“只读”说明不足以保证安全？ | 模型请求可能出错；程序检查与实际身份权限决定能否执行 | 1 |
| 同一个 case 的 Python 日志工具与 YAML 查询工具，安全路径一样吗？ | 参数数组与 Shell 模板引用的区别；完整 Bash 策略是另一条路径；API 权限仍然有效 | 5、6 |
| 为什么需要人工审批，拒绝和待审批有什么区别？ | 部分操作需要人的决定；拒绝返回错误，待审批需要有效交互与恢复路径 | 3、7 |
| Approval Token 为什么绑定调用 ID、工具名和参数？ | 防止待审批内容被替换；恢复时检查签名、有效期与内容匹配 | 7 |
| 审批通过后还有哪些限制，怎样验证这些机制？ | 数据源权限仍然有效；Token 的作用边界；正例、拒绝例与篡改例分别验证 | 6、7、7.1 |

答顺以后，再补两个深入问题：复合命令怎样避免只检查第一段？签名密钥在重启后变化，会怎样影响未完成的审批？

## 9 遇到具体疑问时怎样查证

每次选一个问题，最多查看两段资料。10 分钟后仍卡住，就记录问题，通过中文解释或输入输出推演继续学习。

| 当前疑问 | 查哪一小段 | 查到什么即可停止 |
| --- | --- | --- |
| 审批配置在哪里生效？ | [tools.py](../../holmes/core/tools.py) 的 `_check_approval_config()`；[logging_api.py](../../holmes/plugins/toolsets/logging_utils/logging_api.py) 的 `PodLoggingTool.__init__()` | 默认空列表；检查需要 `toolset` 引用，日志工具只有 `_toolset` |
| 完整命令怎样判定？ | [validation.py](../../holmes/plugins/toolsets/bash/validation.py) 的 `validate_segment()` / `validate_command()`；[bash_toolset.py](../../holmes/plugins/toolsets/bash/bash_toolset.py) 的 `requires_approval()` | 允许、拒绝、待审批三态，以及 `DenyReason` 分类 |
| 本例参数是否经过 Shell？ | [kubernetes_logs.py](../../holmes/plugins/toolsets/kubernetes_logs.py) 的 `_fetch_kubectl_logs()`；[kubernetes.yaml](../../holmes/plugins/toolsets/kubernetes.yaml) 的 SECURITY 注释与 `KIND` / `JQ_FILTER` 赋值 | 一个是参数数组，一个需要模板与变量引用正确配合 |
| Token 绑定什么，恢复在哪里校验？ | [approval_tokens.py](../../holmes/utils/approval_tokens.py) 的 `args_hash()` / `mint_token()` / `verify_token()`；[tool_calling_llm.py](../../holmes/core/tool_calling_llm.py) 的 `_execute_tool_decisions()` | 只校验调用 ID、工具名与参数哈希；失败必然拒绝 |

如果需要一条验证依据，可阅读 [Token 测试](../../tests/test_approval_tokens.py) 的篡改或过期断言，或者 [Bash 审批测试](../../tests/toolsets/bash/test_bash_approval_flow.py) 的允许、拒绝、待审批例子。模板测试证明渲染结果，Mock 测试证明对应程序分支；真实集群权限需要结合实际部署验证。测试执行按疑问选做。

### 9.1 最小验证：Token 正反例与配置引用（15 分钟）

在项目根目录执行现有 Token 用例；它们不运行命令、不访问集群或模型：

```bash
poetry run pytest tests/test_approval_tokens.py \
  -k 'test_mint_then_verify_round_trip or test_verify_rejects_all_failure_modes_uniformly' \
  --no-cov -n 0 -q
```

读同名测试的输入与断言，写清原调用验证成功，而改工具名、调用 ID 或参数值分别为什么失败。再核对日志配置引用缺口：

```bash
poetry run python - <<'PY'
from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset

toolset = KubernetesLogsToolset()
toolset.approval_required_tools = ["fetch_pod_logs"]
tool = toolset.tools[0]
print("has_toolset:", getattr(tool, "toolset", None) is not None)
print("approval_requirement:", tool._check_approval_config())
PY
```

当前预期是 `False` 和 `None`。构造工具集仅检查本地 kubectl 客户端；不要调用日志查询来验证这个缺口。完成标志是能解释两类结果：Token 绑定测试证明原语，配置检查暴露引用问题；两者都不能证明完整人工审批恢复、审批人身份或真实 RBAC。

### 9.2 陌生变式与隔天复习

合上正文先答：在已经接通审批的工具上，用户同意了访问，Token 的签名、有效期与内容都正确，但当前 Kubernetes 身份没有 `pods/log` 权限。接下来应该看到什么？能否换 namespace 或其他身份继续使用原 Token？

<details>
<summary>回答后再看检查点</summary>

执行实际查询时仍应收到 Forbidden，保留该错误并说明证据不足。改变工具参数必须重新满足内容绑定和审批要求；审批不会授予数据源权限，也不能据此使用未授权身份。核对其他已授权证据来源时，应重新判断其权限与审批要求。

</details>

隔天用 5 分钟画出“审批前暂停 → 恢复校验 → 实际查询”的分支，解释一次 Token 内容不匹配和一次 Forbidden，并复述日志工具配置缺口。

## 10 完成标准与当天记录

- [ ] 能解释越权、参数注入、高风险操作这三类问题，并说清各自由哪道门回答。
- [ ] 能说清五道门的职责，以及它们在调用过程中的位置。
- [ ] 能完成 2 分钟讲述，并回答 5 个核心追问。
- [ ] 能说明参数篡改为何被拒，并说出一项取舍和 Token 的一项边界。
- [ ] 核对一条具体实现或测试依据，说明实际验证了什么、没有验证什么。
- [ ] 完成第 9.1 节最小验证，区分 Token 原语、配置引用和完整恢复的证明范围。
- [ ] 独立回答第 9.2 节陌生变式，说明审批与 RBAC 的关系。

今天的核心成果是一页自己的回答。函数名记忆与测试执行数量不作为验收标准。

- 实际学习日期与用时：
- 我对三类安全问题的解释：
- 我的控制职责表与审批路径：
- 我的 2 分钟设计回答：
- 我的录音实际时长与删改内容：
- 一个允许、拒绝或待审批例子：
- 参数篡改的处理方式，以及 Token 的作用边界：
- 一个方案取舍：
- 核对的一条依据及其证明范围：
- 我亲手分析或验证的部分：
- 最小验证的结果、关键断言与证明范围（未执行则注明）：
- 陌生变式：已知信息、缺口、下一步，以及看检查点后的修订：
- 隔天复习日期与结果（独立答出 / 提示后答出 / 待补）：
- 对应简历的个人动作、可展示依据和未验证范围：
- 仍卡住的具体追问：

可以直接在聊天里开始：“带我学 Day 5，沿 09_crashpod 分析这次日志访问的权限、参数和审批恢复，每次先讲一个设计难点，再检查我的解释。”
