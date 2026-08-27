# 任务编号：task_006

## 1. 任务目标

在持久化多轮 Conversation 上实现运行中用户输入：默认进入“下一轮 Queue”，当前 turn 完整结束后严格 FIFO、一次一条地发送；用户可把某条 queued message 手动“插入当前轮（Steer）”，由 AgentLoop 在下一个安全 step 边界接收。同步完成 busy Composer 的产品级状态机和队列管理 UI。

## 2. 背景与上下文

- 用户要求运行中仍可继续输入，默认等待当前任务完成，也可手动引导插入当前对话。
- Codex 源码对 queued user messages、pending steers 与 rejected steers 使用不同队列；相关公开问题证明把 Queue 当 Steer 会提前发送、多条同时消费并破坏因果顺序。
- DSH 明确把 Enter/Queue 与 Ctrl/Cmd+Enter/Steer 作为不同 gesture，Host 是 queue snapshot 的权威；Steer 若错过当前安全窗口会无损降级为下一轮 Queue。
- task_004 提供 Conversation/Turn/SQLite 事务与后台状态；task_005 提供可取消/可观察的 provider stream。没有这两个前置条件不得用纯前端数组伪造队列。

## 3. 技术约束

- Queue 与 Steer 是不同 domain state，不允许通过一个布尔值或前端样式区分。消息状态至少包括 queued、steer_pending、claimed、delivered、cancelled/removed。
- Queue 是 server-authoritative、持久化、FIFO；当前 turn terminal 后仅 claim 第一条并创建一个新 turn，后续逐条等待，不并发批量发送。
- Steer 只在 AgentLoop 安全边界注入：模型响应完成且尚未 final commit、工具调用组完成且无副作用执行中、下一次 model request 前。不得在 write/edit/run_command 执行一半时修改 canonical history。
- 如果 turn 在 steer claim 前结束，或当前 provider/turn 不可 steer，消息必须原子降级回 Queue，不能丢失、重复或向用户报假成功。
- cancel 当前 turn 不删除 Queue；delete conversation 才级联删除。refresh/reconnect/server restart 后 queue snapshot 与 transcript 保持一致。
- Composer idle 时发送普通 turn；busy 时默认 Queue。快捷键默认 Enter=Queue、Ctrl/Cmd+Enter=Steer，允许设置互换；Shift+Enter 永远换行。
- Start/Stop 仍共享右侧控制槽位。busy 且 draft 为空时显示 Stop；draft 非空时显示 Queue/Steer 主发送动作，并在相邻同一 control group 保留紧凑 Stop，不恢复两个分散的大按钮。

## 4. 实现步骤

1. 定义 InboxMessage、delivery mode、状态机、顺序键与幂等 mutation；设计 queue/steer/claim/demote 事件。
2. 在 ConversationStore 中实现事务性 enqueue/edit/remove/reorder/steer/claim；提供权威 queue snapshot 和 optimistic concurrency/version。
3. 在 AgentLoop 增加安全 inbox seam，只在既定边界读取 steer；注入 user message 时带 `source=steer`，保持 canonical tool pairing。
4. ConversationManager 在 turn terminal 后以单消费者 claim 下一条 Queue，创建下一 turn；处理 cancel、crash、服务重启和多会话竞争。
5. 新增 Inbox API/SSE 事件：list snapshot、enqueue、patch text/order、delete、steer；稳定错误包括 item_not_found、turn_not_steerable、version_conflict。
6. 前端建立 Composer 状态机、queue dock 和每行操作：编辑、删除、上/下移动、插入当前轮；提交成功前不清空 draft，Host snapshot 是可见提交事实。
7. transcript 区分普通 user turn 与 mid-turn steer caption；queued message 在未 claim 前只出现在 dock，不冒充已发送聊天气泡。
8. 添加并发/因果顺序测试与 production E2E：多条 queue、逐条消费、steer safe boundary、missed-window demotion、cancel/reconnect/restart。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `src/coding_agent/conversations/inbox*.py` | 新增 | Inbox domain 与事务存储 |
| `src/coding_agent/agent.py` | 修改 | 安全 steer seam 与 source 标记 |
| `src/coding_agent/web/controller.py`、API DTO | 修改 | queue consumer、mutation、SSE snapshot |
| `frontend/src/components/TaskComposer.tsx` | 重构 | idle/busy/queue/steer/stop 状态机 |
| `frontend/src/components/QueueDock.tsx` | 新增 | 队列展示与行操作 |
| `frontend/src/lib/*store*` | 修改 | server-authoritative queue projection |
| 测试/Fake Model/E2E | 新增/修改 | race、FIFO、降级、刷新和键盘 |

## 6. 验收标准

- [ ] 运行中连续提交三条默认 Queue，当前 turn 完整结束后严格按 FIFO 一条一条创建三轮，不在工具结束时提前发送、不同时 claim 多条。
- [ ] 用户可编辑、删除、调整 queued row，并把指定行 Steer 到当前 turn；已 claim 的竞争返回稳定状态且 UI 与 Host 最终一致。
- [ ] Steer 只在安全 step 边界进入 canonical history；副作用工具不会被半途插入，错过窗口原子回到 Queue。
- [ ] cancel、刷新、SSE reset、切换 conversation、server restart 不丢/重发 Queue；cancel 当前 turn 后 queued rows 仍在。
- [ ] Composer 默认 Enter Queue、Ctrl/Cmd+Enter Steer、Shift+Enter 换行；偏好可切换且按钮/键盘/无障碍名称一致。
- [ ] busy Composer 的发送与 Stop 视觉集中在右侧 control group，queued 状态清晰，不出现“输入消失但用户不知道去哪了”。
- [ ] Python 并发测试、Vitest 状态机测试和 production Playwright 因果顺序 E2E 全部通过。

## 7. 风险与注意事项

- Queue/Steer 最容易出现“至少一次”导致的重复执行；claim 与 turn 创建必须在同一事务或有唯一幂等键。
- Steer 不是中断正在执行的命令。产品文案应说“在下一安全节点插入”，不能承诺即时打断。
- 自动消费 Queue 前要重新解析 conversation workspace/profile/credential；配置失效时保留消息并给出可恢复错误。
- 不复制 Codex/DSH 的内部实现，只采用经公开问题验证过的语义分离。

## 8. 最小交付范围与明确非目标

### 8.1 本任务必须交付

- SQLite 持久 InboxMessage 与 server-authoritative queue snapshot。
- Queue/Steer 两套明确状态与 mutation，严格 FIFO 单消费者。
- AgentLoop 两个安全 inbox poll point，以及 terminal/steer 竞争的事务性 demotion。
- busy Composer、QueueDock、编辑/删除/排序/Steer、切换/刷新/重启恢复。
- race-oriented Python tests 与 production Playwright 因果顺序 E2E。

### 8.2 本任务不包含

- Steer 不终止正在进行的 provider HTTP 请求，不杀死正在执行的命令，不回滚已写文件。
- 不支持多条 queue 并行自动执行、不支持定时消息、跨 conversation 移动和批量 prompt workflow。
- 不把本地 optimistic array 当权威，不在 localStorage 独立保存待发送消息。
- 不支持 assistant 主动询问期间的特殊 human-in-the-loop protocol；本任务只有用户主动 Queue/Steer。
- 不实现协作多用户、远程通知或移动端推送。

## 9. Inbox 架构与实现模式

```text
Composer / QueueDock
        │ mutation + idempotency/version
        ▼
Conversation Inbox API
        │
        ▼
InboxService ─────────► SQLiteInboxRepository
        │                       │
        │                       └─ authoritative snapshot/events
        ├────────────► TurnQueueConsumer（terminal 后单消费者）
        └────────────► SteerBroker（AgentLoop safe-point poll）
                                │
                                ▼
                            AgentLoop
```

采用：

- **Transactional inbox/outbox**：消息入队、claim、新 turn 建立与状态事件在同一数据库事务或同一可恢复工作单元。
- **At-most-one active claim**：unique constraint 保证一个 inbox item 只能绑定一个 claimed turn。
- **Compare-and-swap**：所有编辑、排序、删除、steer 都带 item/version 或 queue version。
- **Single consumer per conversation**：terminal handler 是唯一 Queue 自动消费入口；HTTP handler 只入队，不直接启动第二轮。
- **Safe-point broker**：AgentLoop 主动 poll，不由 Web 线程异步修改 `_history`。
- **Server reconciliation**：前端可 optimistic 标记 pending，但每次 mutation 以 Host 返回 snapshot/version 收敛。

## 10. Inbox 数据模型

建议新增 `inbox_items`：

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定不可猜测 ID |
| `conversation_id` | FK，删除 conversation 时 cascade |
| `content` | 原始 user text，长度与普通 turn 相同上限 |
| `requested_mode` | queue / steer |
| `state` | queued / steer_pending / claimed / delivered / blocked / removed |
| `position` | conversation 内顺序整数；与 id 组成稳定排序 |
| `bound_turn_id` | steer 针对的当前 turn；queue 初始为空 |
| `claimed_turn_id` | queue 消费后创建的新 turn，unique |
| `idempotency_key` | conversation 内 unique |
| `version` | mutation CAS |
| `created/updated/claimed/delivered_at` | 生命周期证据 |
| `last_error_code` | blocked/降级原因，不存 SDK 原文 |

建议 `inbox_events` append-only 记录 enqueue/edit/reorder/steer_requested/steer_claimed/demoted/turn_claimed/delivered/blocked/removed，供 SSE 和诊断；列表 current state 是事务内 projection。

### 10.1 状态机

```text
enqueue queue ───────────────► queued
                                  ├─ edit/reorder ─► queued
                                  ├─ request steer ─► steer_pending
                                  ├─ terminal claim ─► claimed ─► delivered
                                  ├─ startup/config failure ─► blocked ─► queued（retry）
                                  └─ user remove ─► removed

steer_pending
  ├─ AgentLoop safe claim ─► delivered(source=steer, bound_turn=current)
  ├─ safe window closed/turn terminal ─► queued（demoted）
  ├─ user cancel steer ─► queued
  └─ user remove ─► removed
```

- `removed` 是不可消费终态，可在审计保留元数据但正文按删除策略清除。
- `claimed` 是极短事务状态；若进程重启且 claimed_turn 已存在，恢复为 delivered/对应 turn 状态；若没有 turn，回到 blocked/queued，不能丢失。
- delivered steer 的内容作为当前 turn 的 canonical `UserMessage(source="steer")`；delivered queue 的内容成为新 turn opener，二者在 transcript 视觉上不同。

## 11. Queue 顺序与事务算法

### 11.1 Enqueue

1. 验证 conversation active、content、profile/workspace 引用和 idempotency key。
2. `BEGIN IMMEDIATE`，读取当前 queue version/最大 position。
3. 插入 queued item 与 event，递增 queue version，提交。
4. 返回完整或增量权威 snapshot；前端收到前保留 draft。

### 11.2 编辑/删除/排序

- 仅 queued/steer_pending 可编辑；claimed/delivered 返回 `item_not_editable`。
- 排序请求传有序 item IDs + expected queue version，服务端验证集合与归属后在一个事务重新编号连续 position；不使用浮点 rank。
- remove 正文应按隐私策略清除或删除 row，同时 append 不含正文的 removed audit event。
- 与 claim 竞争时只有一个 CAS 成功；失败方返回 409 + 最新 queue snapshot，前端不自行猜测。

### 11.3 Terminal 后自动消费

```text
BEGIN IMMEDIATE
  1. compare-and-set 当前 turn → terminal
  2. 将所有绑定该 turn 的 steer_pending → queued，并分配确定位置
  3. 若 conversation 非 archived/blocked：SELECT 首条 queued ORDER BY position,id
  4. 解析配置/租约预检结果写入；可启动则创建下一 turn + user canonical item
  5. item → claimed(claimed_turn_id)，写 inbox/turn events
COMMIT
  6. RuntimeRegistry 启动该 turn
```

- 第 4 步需要可能阻塞的 credential/network 外操作时，事务外先做只读预检，事务内再次校验版本；不得持数据库写锁等待模型/文件系统。
- worker 构建失败：补偿事务把新 turn 标 rejected、item 标 blocked 并保留内容；用户修复配置后点击 retry。
- 一轮 terminal 只 claim 一条。下一条只能由新 turn 再次 terminal 触发，严格避免“工具一结束就把三条一起发出”。
- conversation 被 archive 或 workspace busy 时不自动 claim，队列保持并展示原因。

## 12. Steer 安全边界与 AgentLoop 接口

### 12.1 安全 poll point

只允许两个明确入口：

1. **READY → build request 之前**：上一工具组和 canonical commit 已完成，尚未发下一模型请求。
2. **完整 assistant response 接受后、final completion 判定之前**：无工具调用且本来要 terminal 时，先检查绑定本 turn 的 steer；若命中，先提交 assistant text，再 append steer user message，回到 READY。

不在以下位置 poll：SDK stream 正在读取、tool arguments 尚未聚合、ToolExecutor 正在执行任何调用、canonical group pending、CompletionPolicy 验证命令运行中。

### 12.2 端口设计

AgentLoop 只依赖同步 `InboxPort.poll_steer(turn_id, boundary_id) -> Optional[SteerMessage]`：

- `boundary_id` 在 turn 内单调，claim event 记录它，便于证明插入位置。
- poll 内部事务要求 item 仍为 steer_pending、bound_turn 匹配且 turn running；成功后先标 claimed_for_steer。
- AgentLoop append UserMessage 成功后调用 `ack_delivered`；若 append 失败，补偿为 queued/blocked，不丢内容。
- 每个 boundary 最多消费一条 steer；若多条 pending，其余保留到下一个边界或 terminal 时 demote Queue。
- Steer 后 step 计数继续递增、max_steps 不重置，避免无限延长。UI 应提示剩余预算可能不足。

### 12.3 与 streaming/cancel 的关系

- 用户在模型 stream 中点击 Steer，只把 item 设 steer_pending；当前 HTTP stream 不被打断，响应完成后才可能进入。
- Stop 优先级高于 Steer：cancel flag 已设置时 AgentLoop 不再 claim steer，terminal 事务将其 demote Queue。
- CompletionPolicy 正在验证时不 steer；验证结束回到 safe boundary 后再处理。不能让 steer 文本插入验证命令执行中间。

## 13. Inbox API 与事件

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/api/conversations/{id}/inbox` | queue version + 全部可见 items |
| POST | `.../{id}/inbox` | content、mode、idempotency key；返回 snapshot |
| PATCH | `.../{id}/inbox/{item}` | content 或 mode、item version |
| PUT | `.../{id}/inbox/order` | ordered IDs、queue version |
| DELETE | `.../{id}/inbox/{item}` | item version；返回 snapshot |
| POST | `.../{id}/inbox/{item}/steer` | target active turn、versions |
| POST | `.../{id}/inbox/{item}/retry` | blocked→queued |

- 每次响应返回 `queue_version`，错误 409 附最新安全 snapshot 或提示 refetch。
- SSE 发送 `inbox_snapshot`（reset/reconnect）及 versioned `inbox_changed`；前端不依赖多个细粒度 event 自己重放复杂 reorder。
- error code 至少包含 `item_not_found`、`item_not_editable`、`turn_not_steerable`、`steer_window_closed`、`version_conflict`、`conversation_archived`、`inbox_blocked`。

## 14. Composer 与 QueueDock 详细状态机

### 14.1 提交决策

| active turn | draft | 用户动作 | 结果 |
| --- | --- | --- | --- |
| 无 | 非空 | Enter/发送 | 创建普通 turn |
| running | 非空 | Enter/Queue | 持久 queued，Host ack 后清 draft |
| running | 非空 | Ctrl/Cmd+Enter/Steer | steer_pending，Host ack 后清 draft |
| running | 空 | 主槽 | Stop |
| cancelling | 任意 | Queue | 仍可 queue；不允许 steer |
| terminal 自动消费中 | 非空 | Queue | 排到现有 items 尾部 |

- 若平台/输入法与 Ctrl/Cmd+Enter 冲突，按钮 menu 始终提供两种动作；shortcut 可在 Settings 互换。
- composition event（中文输入法）期间 Enter 不提交；Shift+Enter 始终换行。
- network pending 时 draft 锁定为提交快照但仍可复制；失败恢复原 draft 并原位显示错误。

### 14.2 QueueDock

- 位于 Composer 上方，默认只显示数量和首 1–3 条预览，可展开完整列表；长队列滚动有界。
- 每行显示顺序、内容首行、状态和 menu；Steer 是明确按钮/菜单项，不能靠拖到 transcript 的隐藏手势。
- reorder 使用键盘上移/下移和 drag（若已有能力）；无论视觉拖动如何，最终提交完整顺序 + version。
- steer_pending 显示“等待下一安全节点”；demoted 显示一次非侵入说明“当前轮已结束，已转入队列”。
- claimed 后从可编辑队列移到 transcript user turn；Host 未 ack 前不得提前消失。
- 切 conversation 时 dock 显示该 conversation 的 snapshot；列表侧栏 badge 显示 queued 数量。

## 15. 并发测试设计

禁止用任意 sleep 猜 race，使用 barrier/latch 控制下列交叉点：

| Race | 控制点 | 期望 |
| --- | --- | --- |
| terminal vs steer request | terminal txn 前/后 | claim 或 demote 二选一，内容不丢 |
| queue claim vs edit | SELECT/CAS | edit 成功则新内容被 claim，或 409 后显示 claimed |
| queue claim vs remove | CAS | 只有一个终态，无幽灵 turn |
| cancel vs safe poll | cancel flag/poll txn | cancel 后不注入，item 回 queue |
| restart after claim | turn insert/item update/worker start | 恢复 delivered 或 blocked，不重复 turn |
| two browser tabs reorder | same queue version | 一次成功，一次 version conflict + snapshot |

每个测试审计 canonical history、turn rows、inbox state/events 和公开 transcript 四层事实，不只断言 HTTP 200。

## 16. 实施批次与回滚入口

### 批次 A：Inbox domain/SQLite/API

完成 schema、状态机、mutation/idempotency/race tests；UI 仍不开放运行中提交。

### 批次 B：Queue consumer

接入 terminal transaction与单条自动 turn，完成 restart/config failure 补偿；先用 API 集成测试证明 FIFO。

### 批次 C：SteerPort/AgentLoop safe points

逐一增加两个 poll point 和 canonical source marker，完成 max-step/cancel/completion pairing 回归。

### 批次 D：Composer/QueueDock

开放 busy 输入、快捷键、列表管理、SSE reconciliation 和 production E2E。若 Steer capability 未通过，UI 只开放 Queue；不得以前端即时插入替代。
