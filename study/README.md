# HolmesGPT 学习记录

这是 [hawthornhwb](https://github.com/hawthornhwb) 参考
[HolmesGPT/holmesgpt](https://github.com/HolmesGPT/holmesgpt) 进行的个人学习记录，
用于设计理解、Agent 工具调用流程分析和实验练习。项目主体代码及原始设计归上游项目与其贡献者所有。

当前目标是讲清 10-04 版简历中的四个模块设计：工具插件化、分层上下文控制、工具调用安全、效果评估。你已理解 Agent 主流程，现在从 [新版 Day 3](day3/README.md) 开始，再学 [新版 Day 4](day4/README.md)。每天只打开当天主文档。

两天各有约 90 分钟核心练习，按“场景 → 设计理由 → 方案与取舍 → 面试讲述 → 针对卡点查证”推进。源码与实验按疑问选读，每天产出自己的 2 分钟回答，并练习 5 个核心追问。

## 学习入口

- [Day 3 工具插件化设计](day3/README.md)：当前学习入口，解释统一工具接口、职责、接入调用、异常与取舍。
- [Day 4 分层上下文设计](day4/README.md)：下一阶段，解释查询减量、单次结果处理、历史压缩及证据保留。
- [四个模块总计划](interview-plan.md)：核对整体目标和顺序；Day 5–Day 7 的旧材料留作后续参考。

## 按需要查阅的已有材料

- [Day 2 项目讲述](day2/README.md)：项目概述与工具排障的参考练习。
- [Day 1](day1/README.md)：已理解的主流程和源码链路参考。
- [Day 1 本地 Trace](day1/trace-guide.md)：已有真实模型与工具运行记录，可用于核对具体行为。
- [Python 源码阅读讲义](python-basics/README.md)：某个 Python 写法影响理解时再查。
- [选做工具接入实验](day3/tool-lab.md)：需要验证某个工具接入问题时使用，记录按实际执行填写。

学习记录直接填写当天 README 文末。已有 Trace、测试记录和实验文件继续保留，按需要引用。
