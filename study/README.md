# HolmesGPT 学习记录

这是 [hawthornhwb](https://github.com/hawthornhwb) 参考
[HolmesGPT/holmesgpt](https://github.com/HolmesGPT/holmesgpt) 进行的个人学习记录，
用于源码阅读、Agent 工具调用流程分析和实验练习。项目主体代码及原始设计归上游项目与其贡献者所有。

每日学习内容统一放在 `study/dayN/` 目录中：

- `README.md`：当天的学习记录、关键结论和完成情况。
- 其他文件：当天的实验结果、基线报告或专项产出。

## 目录

- [Python 零基础源码阅读讲义](python-basics/README.md)：从基本语法学到配置模型、工具继承与 Agent 事件流，配有离线练习及答案，建议在 Day 1 源码链路前阅读。
- [Day 1](day1/README.md)：理解 Agent 工具循环，接入 DeepSeek V4.1 Flash，并沿源码追踪完整调用链。
- [Day 1 本地 Trace 实验](day1/trace-guide.md)：按轮次观察真实模型响应、工具结果和源码执行位置。
- [Day 2](day2/README.md)：追踪工具调用的数据变化，用测试验证调用 ID 和结果进入下一轮消息的过程。
- [未来五天面试复习计划](interview-plan.md)：Day 3–Day 7，按简历中的工具插件、上下文控制、安全与完整案例准备，每天 3–4 小时。
- [Day 3](day3/README.md)：讲清工具加载、注册、Schema 与参数处理边界，完成离线核对和面试口述。
- [选做：本地工具接入实验](day3/tool-lab.md)：保留原 Day 3 的本地查询工具、离线测试与真实调用练习。
