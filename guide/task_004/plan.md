# 任务编号：task_004

## 1. 任务目标

把“一次发送 = 一个全新 run”的单任务模型升级为持久化、多轮、可切换的 Conversation 系统。每个 Conversation 拥有独立 workspace、profile、canonical model context、turn/run/event 历史与生命周期，支持创建、继续、切换、命名、归档、恢复归档和永久删除。

本任务建立后续 streaming、Queue/Steer 和 Memory 的数据平面；暂不实现消息排队/插入当前轮、reasoning delta 或跨会话记忆。

## 2. 背景与上下文

- 当前 `RunController`、RunStore 与 UI 都只有一个进程级 current run；终态后再发送会创建完全独立历史，无法进行追问。
- Codex app-server 将 thread 与 turn 分离，并为 name/archive/unarchive/delete 提供独立生命周期；DSH 使用 append-only SessionEvent 作为持久化单一事实源，并从日志投影视图。
- 本项目已有 append-only canonical history 和单调 AgentEvent，适合演进为 Conversation → Turn → Run 的层级，不应另建一套与 AgentLoop 冲突的“聊天历史”。
- task_003 完成后，左侧导航和 Composer 已具备面向会话扩展的视觉槽位。

## 3. 技术约束

- 使用 Python 标准库 `sqlite3` 建立 `CODING_AGENT_HOME/state.db`；启用 foreign keys、事务、busy timeout 和适合本地应用的 WAL。不得为基础持久化引入 ORM。
- append-only conversation event/canonical item 是事实源；列表、标题、最后状态等可为事务内投影，不得出现双写不一致。
- ID 使用不可猜测稳定标识；所有 Conversation API 仍受 loopback/Host/Origin/session-token 防护。
- 每个 conversation 同时最多一个 active turn；不同 workspace 可在受控全局上限内并行，同一 workspace 默认互斥执行，避免并发编辑冲突。
- 切换页面不得取消后台 turn；关闭服务时取消并把未完成 turn 持久化为 INTERRUPTED，重启不自动重放副作用。
- canonical history 必须保持 tool-call/result 配对、上下文预算和压缩语义；多轮追问不得把公开脱敏 DTO 反向当作模型历史。
- archive 是可恢复软生命周期；delete 是确认后的硬删除，必须事务性清除 conversation、turn、queue 占位和事件，但不得删除用户 workspace 文件。
- 老用户无数据库时自动初始化；数据库 schema 使用显式 version/migration，失败不覆盖原文件。

## 4. 实现步骤

1. 定义 `ConversationId`、`TurnId`、Conversation/Turn 状态机与 API DTO，明确 run 只是一个 turn 的执行实例。
2. 新增 storage seam 与 SQLite backend：schema version、conversations、turns、canonical_items、public_events/投影；实现事务、分页、归档和删除。
3. 把 RunController 演进为 ConversationManager + per-conversation runtime registry；后台 turn 与当前 UI selection 解耦，并加入 workspace execution lock/全局并发上限。
4. 让 AgentLoop 可从经过校验的 canonical history 启动下一 turn，把本轮追加结果原子持久化；ContextManager 对完整会话做预算投影与压缩。
5. 新增 conversation/turn CRUD、list/pagination、history/events、rename/archive/unarchive/delete/start/cancel API；保留旧 `/api/runs` 兼容适配一版并标记弃用。
6. 前端建立 conversation 列表、当前会话路由和 per-conversation store；切换时恢复独立 draft、workspace/profile、历史滚动位置与 inspector，后台运行显示状态徽标。
7. 标题默认由第一条用户消息做确定性截断，不额外调用模型；支持手动 rename，手动名称不被自动覆盖。
8. 添加迁移、崩溃恢复、并发锁、分页、隔离上下文、归档/删除与浏览器切换 E2E。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `src/coding_agent/conversations/` | 新增 | domain、store seam、SQLite backend、migration |
| `src/coding_agent/agent.py`、`context.py`、`models.py` | 修改 | 从已验证 canonical history 继续下一 turn |
| `src/coding_agent/web/controller.py` | 重构 | ConversationManager/runtime registry/workspace lock |
| `src/coding_agent/web/app.py`、`schemas.py` | 修改 | conversation/turn API 与兼容 run API |
| `frontend/src/lib/*store*` | 重构 | per-conversation state 与订阅 |
| `frontend/src/components/AppShell.tsx` | 修改 | 会话列表、状态徽标、归档入口 |
| `frontend/src/pages/*` | 修改/新增 | conversation view、archive management |
| `tests/`、`frontend/src/__tests__`、`frontend/e2e` | 新增/修改 | 数据、并发、恢复和多轮闭环 |

## 6. 验收标准

- [ ] 同一 conversation 连续发送至少三轮，第三轮模型请求可使用前两轮事实；另一个 conversation 不含这些上下文。
- [ ] 新建、切换、rename、archive、unarchive、delete 在重启 GUI 后保持；删除需要确认且不触碰 workspace 文件。
- [ ] 切换离开不会取消后台 turn；不同 workspace 可受控并行，同一 workspace 并发编辑被稳定拒绝或排队且有产品提示。
- [ ] SQLite event/canonical 历史事务一致、可分页、可迁移；崩溃中的 active turn 重启后唯一终态为 INTERRUPTED，不重放工具。
- [ ] canonical tool pairing、completion verification、public redaction、CLI 与 task_003 UI/性能回归全部保持。
- [ ] Fake Model production E2E 覆盖两会话隔离、三轮追问、后台切换、重启恢复、归档恢复和永久删除。

## 7. 风险与注意事项

- 这是后续功能的核心数据迁移，禁止把 React Query cache/localStorage 当成持久化事实源。
- SQLite 中会保存本地对话、模型消息和必要工具结果；README/About 必须说明数据位置、明文边界和删除语义。
- 多会话并行可能对同一 repo 产生竞争；workspace lock 必须用解析后的 canonical path，而不是用户输入字符串。
- delete 和 migration 都是高风险动作，必须先精确解析目标并有失败回滚/备份策略。

