# 反馈索引

开发 agent 每次完成任务后在此登记反馈文件。

状态可选值：`待评估`、`评估中`、`已评估`、`已归档`。任务受阻时，状态登记为 `待评估`，并在备注中以 `受阻：` 开头说明原因。

| 反馈文件 | 对应任务 | 状态 | 是否已评估 | 备注 |
| --- | --- | --- | --- | --- |
| [task_001_feedback.md](archive/task_001_feedback.md) | task_001 | 已归档 | 是 | Master 整改复验通过：R1-R3 独立复现通过，标准命令 128 passed, 4 skipped；live smoke 留待后续 |
| [task_002_feedback.md](task_002_feedback.md) | task_002 | 已评估 | 是 | Master 独立源码复验结论：需整改；既有门禁 211 passed/4 skipped、Vitest 23、Playwright 3 均通过，但事件/异常脱敏、运行中事实、活动时序、刷新恢复、legacy URL、精确 Origin/Host、配置写失败回滚、完整双语与截图证据未达标；执行 R2.1-R2.8 |
