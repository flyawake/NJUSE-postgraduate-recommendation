# 任务索引

新任务由主规划 agent 在 `guide/task_XXX/` 下创建，并在此登记。

任务编号采用 `task_XXX` 格式，`XXX` 为从 `001` 开始递增且不复用的三位数字。状态可选值：`未开始`、`进行中`、`已完成`、`已归档`、`受阻`。

| 任务编号 | 状态 | 优先级 | 关联 feedback | 备注 |
| --- | --- | --- | --- | --- |
| [task_001](archive/task_001/plan.md) | 已归档 | P0 | [task_001_feedback.md](../feedback/archive/task_001_feedback.md)（已归档，通过） | 2026-08-27 整改复验通过：128 passed, 4 skipped；R1-R3 独立复现通过；live smoke 与仓库时间合规性转后续跟踪 |
| [task_002](task_002/plan.md) | 未开始 | P1 | 待任务开始后创建 | 本地图形化 Coding Agent：[UI 规格](task_002/ui_spec.md)、实时工具轨迹、多服务商 profile 与凭据设置；task_001 前置条件已满足，等待正式启动 |
