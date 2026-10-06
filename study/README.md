# HolmesGPT 学习记录

这是 [hawthornhwb](https://github.com/hawthornhwb) 参考
[HolmesGPT/holmesgpt](https://github.com/HolmesGPT/holmesgpt) 进行的个人学习记录，
用于设计理解、Agent 工具调用流程分析和实验练习。项目主体代码及原始设计归上游项目与其贡献者所有。

当前目标是通过一个具体调查讲清 10-04 版简历中的四个模块设计：工具插件化、分层上下文控制、工具调用安全、效果评估。**先打开 [一个支付服务故障的完整案例](interview-plan.md)**，沿现有 `09_crashpod` 理解“发现资源 → 查日志 → 核对配置 → 诊断 → 评估”。

Day 3–6 都沿用同一个 `payment-processing-worker`，每天放大流程中的一个设计模块，约 90 分钟。原 case 已于 2026-10-06 实跑并连续两次 1/1 通过，数字记录在 [本地环境与运行方法](local-environment.md)；大日志、长历史和审批作为同场景变化推演，实测与原 case、推演分支分别标注。源码按问题查证，最后能用自己的话串起四条简历。

## 学习入口

- [完整案例与总计划](interview-plan.md)：当前主入口，走完整次调查，在对应步骤理解设计难点。
- [本地环境与运行方法](local-environment.md)：启动学习集群、验证故障、填写 DeepSeek 配置与运行原 case。
- [Day 3 工具插件化设计](day3/README.md)：放大资源发现与日志调用，理解统一接口和 YAML/Python 实现。
- [Day 4 分层上下文设计](day4/README.md)：放大查询与结果处理，理解怎样保留具体的 DEPLOY_ENV 错误证据。
- [Day 5 工具调用安全设计](day5/README.md)：放大同一次日志访问，理解权限、参数、策略、审批和内容绑定。
- [Day 6 效果评估设计](day6/README.md)：放大同一个 case 的准备与评估，区分根因正确、证据充分和效率。

## 按需要查阅的已有材料

- [Day 2 项目讲述](day2/README.md)：项目概述与工具排障的参考练习。
- [Day 1](day1/README.md)：已理解的主流程和源码链路参考。
- [Day 1 本地 Trace](day1/trace-guide.md)：已有真实模型与工具运行记录，可用于核对具体行为。
- [Python 源码阅读讲义](python-basics/README.md)：某个 Python 写法影响理解时再查。
- [选做工具接入实验](day3/tool-lab.md)：需要验证某个工具接入问题时使用，记录按实际执行填写。

学习记录直接填写当天 README 文末。已有 Trace、测试记录和实验文件继续保留，按需要引用。
