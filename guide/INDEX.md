# 任务索引

新任务由主规划 agent 在 `guide/task_XXX/` 下创建，并在此登记。

任务编号采用 `task_XXX` 格式，`XXX` 为从 `001` 开始递增且不复用的三位数字。状态可选值：`未开始`、`进行中`、`已完成`、`已归档`、`受阻`。

| 任务编号 | 状态 | 优先级 | 关联 feedback | 备注 |
| --- | --- | --- | --- | --- |
| [task_001](archive/task_001/plan.md) | 已归档 | P0 | [task_001_feedback.md](../feedback/archive/task_001_feedback.md)（已归档，通过） | 2026-08-27 整改复验通过：128 passed, 4 skipped；R1-R3 独立复现通过；live smoke 与仓库时间合规性转后续跟踪 |
| [task_002](archive/task_002/plan.md) | 已归档 | P1 | [task_002_feedback.md](../feedback/archive/task_002_feedback.md)（已归档，通过） | 2026-08-27 第三次复验通过：A1-A22、R2、R3 全部闭环；238 passed/4 skipped、Vitest 33、Playwright 5、audit 0；真实模型 smoke 依规则 N/A，转后续任务 |
| [task_003](archive/task_003/plan.md) | 已归档 | P0 | [task_003_feedback.md](../feedback/archive/task_003_feedback.md)（已归档，通过） | 2026-08-28 用户授权 Master 特例完成 R4；248 passed/4 skipped、Vitest 44、Playwright 7、audit 0，源码与视觉复验通过。 |
| [task_004](archive/task_004/plan.md) | 已归档 | P0 | [task_004_feedback.md](../feedback/archive/task_004_feedback.md)（已归档，通过） | 2026-08-28 Master 源码验收并直接整改通过：持久多轮/恢复、三栏会话 UI、逐轮 ChangeSet/CAS 预览闭环；Python 272+3 定向、Vitest 46、Playwright 5、audit 0。 |
| [task_005](archive/task_005/plan.md) | 已归档 | P0 | [task_005_feedback.md](../feedback/archive/task_005_feedback.md)（已归档，通过） | 2026-08-28 Master 源码验收并直接整改通过：provider-neutral streaming、Chat/Responses 双 adapter、可展示 Think、断线恢复与 opaque continuation 闭环；304 passed/4 skipped、Vitest 53、Playwright 9。真实模型 smoke 按规则 N/A。 |
| [task_006](archive/task_006/plan.md) | 已归档 | P1 | [task_006_feedback.md](../feedback/archive/task_006_feedback.md)（已归档，通过） | 2026-08-29 Master 源码验收整改通过：SQLite Inbox 状态约束与原子 claim、restart/Retry 恢复、严格 FIFO、两个 Steer 安全点、Host snapshot QueueDock 和生产 E2E 闭环；Python 325/4 skipped、Vitest 54、Playwright 10。 |
| [task_007](archive/task_007/plan.md) | 已归档 | P1 | [task_007_feedback.md](../feedback/archive/task_007_feedback.md)（已归档，通过） | 2026-08-29 Master 源码验收并直接整改通过：Memory schema v13、作用域/版本链/事务/幂等、单次投影、FTS/terms 自愈、候选隔离、Memory Center 与跨会话闭环；Python 375/4 skipped、Vitest 61、Playwright 12。 |
| [task_008](task_008/plan.md) | 未开始 | P0 | —（待提交） | 依赖 task_003-task_007 与 task_009；集成、性能、安全、恢复、真实模型与最终交付门禁 |
| [task_009](archive/task_009/plan.md) | 已归档 | P0 | [task_009_feedback.md](../feedback/archive/task_009_feedback.md)（已归档，通过） | 2026-08-30 Master 源码验收并直接整改通过：受控公网 search/fetch、SSRF/重定向防护、附件 CAS/事务归属、Chat/Responses 多模态映射和响应式 GUI 闭环；Python 396/4 skipped、Vitest 68、Playwright 13。Task 8 未开始。 |
