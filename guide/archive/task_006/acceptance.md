# 任务编号：task_006 验收标准

## Queue 语义

- [x] Q1. busy 默认提交生成持久化 queued item；当前完整 turn terminal 前不会进入 canonical model context。
- [x] Q2. 多条 queued item 严格 FIFO、一次一条 claim 和创建 turn；没有批量并发、重复 turn 或跨 conversation 消费。
- [x] Q3. queue 支持编辑、删除、上/下移动，所有 mutation 有 version/幂等保护；竞争失败后以 Host snapshot 收敛。
- [x] Q4. cancel/refresh/reconnect/switch/restart 不丢队列；cancel turn 不清空 queue，delete conversation 才级联清除。

## Steer 语义

- [x] S1. 用户可将指定 queued item 或当前 draft 标记为 Steer；transcript 明确标注“插入当前轮”，不伪装为普通 turn opener。
- [x] S2. AgentLoop 只在模型响应完成、工具组完成、下一请求前等安全边界注入；执行中的 write/edit/run_command 不被半途改变。
- [x] S3. turn 不可 steer 或窗口关闭时，消息原子降级为 Queue，用户内容不丢失且状态可见。
- [x] S4. steer 注入不破坏 assistant tool_call/tool result 配对、context compaction 或 completion verification。

## Composer/UI

- [x] U1. idle Enter 发送普通 turn；busy 默认 Enter=Queue、Ctrl/Cmd+Enter=Steer，Shift+Enter=换行，设置可互换 Queue/Steer gesture。
- [x] U2. draft 提交在 Host 接受前不清空；queued dock 立即显示权威状态、顺序和可操作按钮。
- [x] U3. busy draft 为空时右侧主槽显示 Stop；draft 非空时 Queue/Steer 发送与紧凑 Stop 位于同一 control group，不出现两个分散大按钮。
- [x] U4. 键盘、屏幕阅读器、窄屏和 zh-CN/en-US 均能区分 queued、steer_pending、claimed、failed。

## 质量门禁

- [x] T1. Python race tests 覆盖 terminal/steer、claim/cancel、edit/claim、restart recovery，使用 barriers 而非 sleep 猜测。
- [x] T2. Vitest/RTL 覆盖 Composer 状态机、draft transaction 和 queue projection；Playwright 覆盖三条 FIFO + 一条 Steer + cancel/reload。
- [x] T3. task_001-task_005 全套与所有标准构建、安全、审计、打包门禁通过。

## 事务与故障注入

- [x] F1. terminal、steer request、queue claim 在所有排列下只有一个合法结果；inbox 内容、canonical item、turn 不丢失不重复。
- [x] F2. worker 在 item claim 前、turn insert 后、worker start 前后崩溃，重启后 item 为 delivered/blocked/queued 之一且与唯一 turn 对应。
- [x] F3. 两客户端使用同 version 编辑/reorder/remove，最多一个成功；失败响应携带/触发最新 Host snapshot 收敛。
- [x] F4. 自动消费前 profile credential 缺失、workspace 不可访问或 lease busy 时，item 保持 blocked/queued 并有可恢复动作，不创建运行中的幽灵 turn。
- [x] F5. delete conversation 级联清除 inbox 正文；archive/running/cancel 的策略与 task_004 生命周期一致。

## AgentLoop 审计

- [x] A1. 源码只有 READY-before-request 和 final-before-terminal 两个 steer poll point，ToolExecutor/stream/canonical pending 中无异步 history mutation。
- [x] A2. 每个 safe boundary 最多消费一条 Steer；Steer append 后 max_steps/attempt/tool counters 不重置。
- [x] A3. Stop 已请求时不 claim Steer；pending steer 由 terminal 事务转 Queue，下一轮仍可发送。
- [x] A4. transcript/canonical 明确区分普通 turn opener 与 `source=steer`，provider tool-call/result pairing 在插入前后均合法。

## 交付证据矩阵

| 证据 | 必须包含 |
| --- | --- |
| SQL/state audit | item/queue version、claim、turn、canonical、inbox event 的一致快照 |
| race tests | barrier 控制的 terminal/steer、claim/edit/remove、cancel/poll、restart |
| E2E | busy 连续 Queue 3 条、reorder、Steer 1 条、Stop、reload、切会话、自动逐轮消费 |
| UX 证据 | Composer 所有状态、QueueDock 展开/错误/降级、中文输入法与键盘 |
| 性能 | queue 100 条时输入、展开、reorder 与 snapshot 大小有界 |
