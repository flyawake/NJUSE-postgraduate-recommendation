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

