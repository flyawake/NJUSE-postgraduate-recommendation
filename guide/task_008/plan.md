# 任务编号：task_008

## 1. 任务目标

对 task_003-task_007 的产品化改造执行最终集成、性能、安全、恢复、可访问性、真实模型评测与交付演练，形成可复现的发布候选和面试证据。此任务不继续堆叠新概念，而是把现有能力证明为稳定、可解释、可演示的完整产品。

## 2. 背景与上下文

- 单个任务的局部测试不能证明长会话、后台运行、队列、reasoning 与 memory 组合后的因果顺序和资源边界。
- 当前真实外部模型 smoke 因没有合法凭据记为 N/A；最终产品发布前必须至少用一个用户配置的真实服务完成端到端验证，不能只依赖 Fake Model。
- 题目要求公开仓库、1000 汉字以内 README.txt 和 2 分钟以内视频，且 2026-09-02 24:00 后不得推送。发布门禁必须早于截止时间完成，给录制和回退留出缓冲。

## 3. 技术约束

- 固定评测同时覆盖内核成功率和产品行为，不用“测试数量”代替任务成功：至少包含只读诊断、单文件修复、多文件修复、命令失败恢复、取消、Queue/Steer、多轮追问、会话恢复和记忆召回。
- Fake Model 是确定性回归基线；真实 provider smoke 只使用用户本地凭据，不打印、不录屏、不提交 key。
- 发布性能预算必须在约定测试机、production build 与固定数据规模上测量并保留原始证据；不得用开发服务器热更新结果代替。
- 数据 schema migration 可前向升级并在失败时保留原数据库备份；服务异常退出后 Conversation、Queue、Memory 不重复消费或静默丢失。
- 默认 UI 不展示请求体、上下文字符数、状态机 phase 等开发信息；高级诊断区可按需查看脱敏详情。
- 安全审查覆盖本地 Host 边界、CSP、凭据、路径规范化、命令执行、SSE/API 脱敏、会话导出和 memory poisoning。
- 所有最终宣称都必须有测试、截图、日志摘要或可重复命令；未完成项明确降级，不以文案掩盖。

## 4. 实现步骤

1. 建立固定离线评测清单和评分器，记录成功/失败、最终验证、step/provider attempt、工具错误、重复调用、上下文用量、首 token/总耗时。
2. 完成长会话和并发压力场景：2000 条 transcript event、2000 条 memory、多个后台 conversation、连续 Queue/Steer、SSE 断连恢复。
3. 定义并验证性能预算：静态首屏、输入响应、workspace validation 次数、事件批处理、DOM 节点数、数据库检索、进程内存和取消时延。
4. 执行 crash/restart/schema migration/损坏配置演练；验证 inbox claim、canonical history、partial reasoning 和 memory projection 的恢复语义。
5. 完成安全与隐私 threat model、秘密扫描、依赖审计、CSP/Host/API 检查、恶意工具输出和记忆注入用例。
6. 完成键盘、焦点、屏幕阅读器名称、对比度、缩放和窄屏测试；中文与英文界面不出现截断或开发占位文本。
7. 用至少一个真实 OpenAI-compatible 或已实现 native wire provider 完成“读取—修改—运行验证—多轮追问”的 GUI smoke，并保存脱敏证据。
8. 从全新目录/环境执行安装、production 静态资源、wheel/sdist、CLI 兼容、GUI 启动和离线演示；核验开源依赖许可证。
9. 冻结 release candidate，编写 1000 汉字以内 README.txt、架构/限制说明和 2 分钟演示脚本，预演后再录制最终视频。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `evals/` / 固定 fixtures | 新增 | 可复现任务集与评分结果 |
| Python/前端/E2E 测试 | 修改 | 集成、压力、恢复、安全、a11y |
| migration / recovery | 修改 | 备份、幂等恢复与错误提示 |
| production 配置 / CSP | 修改 | 发布安全和资源边界 |
| `README.md`、`README.txt` | 修改/新增 | 开发说明与最终精简说明 |
| `docs/` 或 guide 证据 | 新增 | threat model、评测与演示脚本 |
| build/package workflow | 修改 | clean install、静态资源和产物核验 |

## 6. 验收标准

- [ ] task_003-task_007 的所有功能验收与全量回归在 release commit 上一次性通过，不使用分散历史结果拼接结论。
- [ ] 固定离线任务集输出可复现指标和失败分类；关键场景不存在消息乱序、重复工具副作用、跨会话/工作区泄漏。
- [ ] production build 下输入、validation、SSE、长 transcript、数据库与取消时延满足书面预算，且有原始可复查证据。
- [ ] crash/restart/migration 演练不丢 canonical Conversation、Queue 和 confirmed Memory，不重复 claim；失败时提供可恢复错误与数据库备份。
- [ ] 安全、秘密扫描、依赖审计、Host/CSP、prompt/memory injection 和导出隐私门禁全部通过。
- [ ] 至少一个真实模型从 GUI 完成真实编程任务和多轮追问；证据脱敏且模型/provider/wire API 声明准确。
- [ ] clean install、wheel/sdist、生产静态资源、CLI fallback 和 GUI 启动在新环境复现；评审运行不要求安装 Node。
- [ ] README.txt 不超过 1000 汉字，视频不超过 2 分钟/200 MB；内容覆盖真实任务、AgentLoop、工具链、多轮/队列和一项关键工程决策。

## 7. 风险与注意事项

- task_008 是发布门禁，不是容纳延期功能的兜底任务。未通过的功能应回到对应任务整改或明确从版本范围移除。
- 真实模型具有非确定性，验收关注协议正确、工具因果顺序与可恢复性；固定成功率比较仍以离线脚本和多次重复为准。
- 截止时间不可逆。建议至少提前 24 小时冻结代码，之后只做演示和材料核验；任何远端仓库时间合规问题必须在冻结前解决。
- 演示视频不得显示 API key、本机私人路径、未脱敏会话或其他工作区记忆。

