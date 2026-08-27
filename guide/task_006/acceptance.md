# 任务编号：task_006 验收标准

## Queue 语义

- [ ] Q1. busy 默认提交生成持久化 queued item；当前完整 turn terminal 前不会进入 canonical model context。
- [ ] Q2. 多条 queued item 严格 FIFO、一次一条 claim 和创建 turn；没有批量并发、重复 turn 或跨 conversation 消费。
- [ ] Q3. queue 支持编辑、删除、上/下移动，所有 mutation 有 version/幂等保护；竞争失败后以 Host snapshot 收敛。
- [ ] Q4. cancel/refresh/reconnect/switch/restart 不丢队列；cancel turn 不清空 queue，delete conversation 才级联清除。

## Steer 语义

- [ ] S1. 用户可将指定 queued item 或当前 draft 标记为 Steer；transcript 明确标注“插入当前轮”，不伪装为普通 turn opener。
- [ ] S2. AgentLoop 只在模型响应完成、工具组完成、下一请求前等安全边界注入；执行中的 write/edit/run_command 不被半途改变。
- [ ] S3. turn 不可 steer 或窗口关闭时，消息原子降级为 Queue，用户内容不丢失且状态可见。
- [ ] S4. steer 注入不破坏 assistant tool_call/tool result 配对、context compaction 或 completion verification。

## Composer/UI

- [ ] U1. idle Enter 发送普通 turn；busy 默认 Enter=Queue、Ctrl/Cmd+Enter=Steer，Shift+Enter=换行，设置可互换 Queue/Steer gesture。
- [ ] U2. draft 提交在 Host 接受前不清空；queued dock 立即显示权威状态、顺序和可操作按钮。
- [ ] U3. busy draft 为空时右侧主槽显示 Stop；draft 非空时 Queue/Steer 发送与紧凑 Stop 位于同一 control group，不出现两个分散大按钮。
- [ ] U4. 键盘、屏幕阅读器、窄屏和 zh-CN/en-US 均能区分 queued、steer_pending、claimed、failed。

## 质量门禁

- [ ] T1. Python race tests 覆盖 terminal/steer、claim/cancel、edit/claim、restart recovery，使用 barriers 而非 sleep 猜测。
- [ ] T2. Vitest/RTL 覆盖 Composer 状态机、draft transaction 和 queue projection；Playwright 覆盖三条 FIFO + 一条 Steer + cancel/reload。
- [ ] T3. task_001-task_005 全套与所有标准构建、安全、审计、打包门禁通过。

