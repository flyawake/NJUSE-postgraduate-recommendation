# 反馈索引

开发 agent 每次完成任务后在此登记反馈文件。

状态可选值：`待评估`、`评估中`、`已评估`、`已归档`。任务受阻时，状态登记为 `待评估`，并在备注中以 `受阻：` 开头说明原因。

| 反馈文件 | 对应任务 | 状态 | 是否已评估 | 备注 |
| --- | --- | --- | --- | --- |
| [task_001_feedback.md](archive/task_001_feedback.md) | task_001 | 已归档 | 是 | Master 整改复验通过：R1-R3 独立复现通过，标准命令 128 passed, 4 skipped；live smoke 留待后续 |
| [task_002_feedback.md](task_002_feedback.md) | task_002 | 待评估 | 否 | 审查整改完成：loopback 精确校验、CLI 共用 resolver/factory、snapshot 事件恢复、controller/SSE/关闭竞态与前端 i18n/token 修复；pytest 211 passed/4 skipped，Vitest 23，Playwright 3；真实模型 live smoke 为 N/A - 无外部凭据 |
