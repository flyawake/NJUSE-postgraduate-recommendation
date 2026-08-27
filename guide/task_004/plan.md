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

## 8. 最小交付范围与明确非目标

### 8.1 本任务必须交付

- SQLite ConversationRepository、schema migration 和崩溃恢复。
- Conversation/Turn/Run 的稳定领域模型与 canonical/public 两条数据投影。
- 每会话独立 workspace、profile、模型上下文、历史、后台运行状态和 UI draft/scroll。
- create/list/read/rename/archive/unarchive/delete/start turn/cancel 的 API 与生产 UI。
- 至少两会话、三轮追问、后台切换、进程重启、归档恢复和删除的 Fake Model E2E。

### 8.2 本任务不包含

- Queue/Steer inbox 表可预留 migration 版本，但不得暴露假 API 或 UI；实现归 task_006。
- reasoning/文本 delta 不在此任务持久化；这里只保存完整 canonical assistant item 与现有 public events。
- memory 不从 Conversation 自动复制；跨会话共享归 task_007。
- 不实现会话 fork、云同步、多人协作、分支工作树、标签/文件夹和全文语义搜索。
- 不允许通过“把所有历史拼到一条 user message”实现多轮，也不使用公开脱敏 ToolEvent 反建 canonical history。

## 9. 目标后端架构

```text
FastAPI routes / DTO
        │
        ▼
ConversationService（生命周期、权限、事务编排）
        ├──────────────► ConversationRepository protocol
        │                         └─ SQLiteConversationRepository
        │
        └──────────────► RuntimeRegistry
                                  ├─ per-conversation TurnRuntime
                                  ├─ WorkspaceLeaseManager
                                  └─ bounded ThreadPoolExecutor
                                                │
                                                ▼
                                      AgentLoopFactory
                                                │
                     CanonicalJournal ◄─────────┼────────► PublicEventJournal
                                                │
                                          AgentLoop/Tools
```

### 9.1 责任边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| `ConversationRepository` | schema、事务、不可变事实、查询分页、乐观版本 | 启动线程、调用模型、生成用户文案 |
| `ConversationService` | 生命周期规则、DTO 编排、start/cancel、标题、删除确认 | 直接执行工具、持有 SDK client |
| `RuntimeRegistry` | active runtime、取消信号、并发上限、shutdown | 持久事实判定、UI selection |
| `WorkspaceLeaseManager` | canonical workspace key 的互斥租约 | 猜测某轮是否只读 |
| `AgentLoopFactory` | 按 conversation/profile/workspace 构造一轮 AgentLoop | 存储 UI projection |
| `CanonicalJournal` | 将已验证 canonical append 持久化并提供不可变 history | compact/rewrite 事实历史 |
| `ContextManager` | 从 canonical facts 构造有预算 request view | 把压缩结果反写数据库 |
| Web projection | 脱敏、分页、SSE cursor | 成为模型上下文来源 |

### 9.2 采用的实现模式

- **Repository pattern**：domain/service 依赖协议，SQLite 是首个 backend；测试可用 in-memory SQLite，不再用若干全局 list。
- **Unit of Work**：start/terminal/archive/delete 等跨表操作在单一 `BEGIN IMMEDIATE` 事务内完成。
- **Append-only journal + projection**：canonical item/public event 只追加；conversation 列表字段是可重建投影。
- **Optimistic concurrency**：可编辑资源带整数 `version`，PATCH 必须提供 expected version；冲突返回 `version_conflict`。
- **Runtime registry**：持久状态与进程内 worker 分离；数据库显示 active 但 registry 无 worker 时，启动恢复流程判为 INTERRUPTED。
- **Compatibility adapter**：旧 `/api/runs` 只代理到一个 legacy conversation，不复制执行逻辑。

## 10. 领域模型与状态机

### 10.1 标识与层级

- `conversation_id`：随机 UUID/ULID 字符串，跨重启稳定，对外不可使用自增主键。
- `turn_id`：随机稳定 ID，属于一个 conversation；`ordinal` 在该会话内单调递增并有唯一约束。
- `run_id`：一次实际执行尝试；本任务一个 turn 只有一个 run，为未来显式 retry/replay 保留层级。
- `canonical_seq`：conversation 内单调序列，决定模型历史顺序。
- `public_event_seq`：turn/run 内单调序列，用于 SSE；不能代替 canonical_seq。

### 10.2 Conversation 状态

```text
active ──archive──► archived ──unarchive──► active
   │                   │
   └────delete─────────┴──────────────► deleted（事务硬删除后仅返回 404）
```

- archived conversation 禁止新开 turn，但已有 active turn 必须先停止或等 terminal 后才允许 archive。
- rename 不改变 updated_at 的运行排序语义时需另设 `last_activity_at`，避免仅改名把旧会话伪装成最近运行。
- delete 需要 `expected_version` 和明确确认字段；running 时先返回 `conversation_busy`，不在删除请求中隐式 cancel。

### 10.3 Turn 状态

```text
pending → starting → running → success | error | interrupted
   └───────────────► rejected（启动前配置/租约失败）
```

- `pending/starting/running` 合称 active；每 conversation 只能存在一条 active turn（partial unique index 或事务断言）。
- 只有 terminal state 才写 `finished_at` 和 terminal result；同一 turn 终态 compare-and-set，只能成功一次。
- 进程启动时所有 starting/running turn 统一恢复为 interrupted，并写稳定 stop reason `PROCESS_RESTARTED`；不得重新执行工具。

## 11. SQLite 数据设计

建议 schema（实际列名可依代码规范调整，但不变量不得改变）：

| 表 | 关键字段/约束 | 用途 |
| --- | --- | --- |
| `schema_meta` | `version`, `applied_at` | 单行/迁移历史 |
| `conversations` | id PK、title、title_source、workspace_path/key、profile_id、state、version、created/last_activity/archived | 会话聚合根与列表投影 |
| `turns` | id PK、conversation FK、ordinal UNIQUE、state、run_id UNIQUE、user_text、timestamps、result_json、error_code | 每轮生命周期 |
| `canonical_groups` | id、conversation FK、turn FK、group_seq UNIQUE、kind、state(pending/committed/abandoned) | canonical 原子提交边界 |
| `canonical_items` | id、group FK、conversation FK、canonical_seq UNIQUE、role、payload_json、created_at | provider-neutral 不可变消息 |
| `public_events` | id、conversation/turn/run FK、event_seq UNIQUE per run、kind、payload_json、created_at | 脱敏 timeline/SSE 恢复 |
| `conversation_projection`（可合并） | last_turn/status/counts/preview | 高效列表；必须可从事实重建 |

### 11.1 数据库初始化

- 数据库路径由 `default_home()` 下统一解析，不接受 HTTP 参数指定。
- connection factory 每次设置 `PRAGMA foreign_keys=ON`、`busy_timeout`；WAL 可用时启用并记录实际 journal mode。
- migration 在 Web controller/runtime 启动前串行执行；版本只前进，不支持应用进程自动降级 schema。
- 升级前在同目录创建带版本/时间的原子备份；备份目标必须验证仍位于 agent home，保留数量有上限，不递归删除未知文件。

### 11.2 Canonical 原子组

- system/user/final assistant 可以作为单 item committed group。
- assistant tool-call 与其全部 tool results 属于同一个 canonical group。执行期间 group 为 pending，结果齐全后一次事务转 committed。
- ContextManager 只能读取 committed group，避免服务崩溃后把未配对 tool call 送给 provider。
- 若崩溃发生在工具组中：恢复流程为每个未得到结果的 call 生成确定性 synthetic tool result（说明执行结果未知、必须重新观察 workspace），将 group 标为 committed/recovered，再把 turn 置 interrupted。不得猜测工具未执行。
- canonical payload 保持内部 typed JSON；读取时必须做 schema/version 校验，损坏条目令该 conversation 进入可诊断 `data_error`，不能静默跳过破坏顺序。

### 11.3 事务不变量

1. 创建 turn、分配 ordinal、写 user canonical item、更新 conversation activity 在同一事务完成。
2. 获取 workspace lease 与启动 worker 之间使用 starting 状态；worker 构建失败需事务转 rejected 并释放 lease。
3. terminal result、最后 public event、conversation projection 更新使用幂等 `WHERE state IN (...)` compare-and-set。
4. archive/unarchive/rename 检查 expected version 并递增 version。
5. delete 通过 FK cascade 清理 agent home 内的 conversation 数据；workspace path 仅作字符串引用，禁止任何 filesystem delete。

## 12. AgentLoop 与上下文改造方式

### 12.1 新入口

现有 `AgentLoop.run(task)` 不能在每轮重复创建 SystemMessage。目标接口应体现“新会话首轮”和“既有会话续轮”的差异，例如：

```text
AgentLoop.run_turn(
  history: CanonicalHistoryPort,
  user_message: UserMessage,
  turn_context: TurnContext,
) -> RunResult
```

- CLI compatibility adapter 仍可调用 `run(task)`，内部创建 in-memory history 后转 `run_turn`。
- Web 端从 repository 加载 committed canonical groups，校验 system prompt/version 与 pairing，然后构造 journal-backed history。
- AgentLoop 不知道 conversation list、SQLite、archive 或 UI；它只向 injected journal append internal canonical message、向 EventSink emit。
- system prompt 每 conversation 固定版本；升级 prompt 时新会话使用新版本，旧会话继续使用已存文本/版本，避免中途语义漂移。

### 12.2 上下文投影

- 每个新 turn 把本轮 UserMessage 追加到事实历史后再构建第一份 RequestView。
- ContextManager 继续保留最近 step、错误和文件最新读取，但 recent 范围按整个 conversation 计算。
- public transcript 可分页，模型 context 不能按 UI 当前加载页截断。
- context overflow 返回当前 turn 的稳定 error，历史保持可继续；未来 summary/compaction 是独立任务，不在此处静默删除 user/assistant 文本。

## 13. 并发、后台运行与恢复

- 使用有界 `ThreadPoolExecutor`（默认最大 2，可配置并在 capabilities 暴露），替代每轮无限创建 daemon thread。
- RuntimeRegistry key 为 conversation_id；记录 turn_id/run_id/cancel_event/future/lease。selection 改变不影响 registry。
- WorkspaceLease key 取服务端 `Path.resolve(strict=True)` 后的平台规范键；首版整轮持锁，宁可保守拒绝并发，也不猜测未来是否写文件。
- 同 workspace 冲突返回 `workspace_busy`，DTO 包含占用 conversation 的可展示标题/ID 摘要与“切换查看”动作，不泄漏其他路径。
- shutdown 顺序：停止接收新 turn → 设置所有 cancel → 有界等待 → 未终态项标 interrupted → 关闭 DB；超时不无限阻塞应用退出。
- SSE 按 conversation/turn 订阅。断线后使用 `(run_id,event_seq)` cursor；切换回来先取 snapshot 再续订，重复 event 由 ID 去重。

## 14. HTTP API 规格

API 命名可按现有 FastAPI 风格实现，语义至少包含：

| Method | Path | 请求/并发 | 返回 |
| --- | --- | --- | --- |
| POST | `/api/conversations` | workspace、profile、可选 title | 201 ConversationDTO |
| GET | `/api/conversations` | `archived`、`query`、opaque cursor、limit≤100 | page + next_cursor |
| GET | `/api/conversations/{id}` | — | metadata + active/latest turn summary |
| PATCH | `/api/conversations/{id}` | title、expected_version | 新 version；冲突 409 |
| POST | `.../{id}/archive` | expected_version | archived DTO |
| POST | `.../{id}/unarchive` | expected_version | active DTO |
| DELETE | `/api/conversations/{id}` | expected_version + explicit confirmation | 204；不删 workspace |
| GET | `.../{id}/turns` | opaque cursor/limit | reverse chronological page |
| POST | `.../{id}/turns` | user content、idempotency key | 202 TurnDTO |
| POST | `.../{id}/turns/{turn_id}/cancel` | idempotent | current TurnDTO |
| GET | `.../{id}/events` | run/after cursor | SSE hello/reset/event/end |

- 所有 mutation 接受 client idempotency key；重复 POST 返回同一资源，不创建第二个 turn。
- list cursor 是 opaque、稳定排序基于 `(last_activity_at,id)`；同时间戳不得丢/重复。
- error code 至少覆盖 `conversation_not_found`、`conversation_archived`、`conversation_busy`、`workspace_busy`、`version_conflict`、`invalid_cursor`、`data_error`。
- OpenAPI schema 是前端类型唯一来源；修改后必须重新生成并做 drift check。

## 15. 前端会话架构与交互

### 15.1 Store 划分

- React Query 管理 conversation list/page、metadata、turn pages 与 mutation cache。
- 每 conversation 一个 event projection store，由 registry 按 ID 懒创建；非选中会话只保留轻量 run badge/unread，不挂载 transcript DOM。
- selection 写入 URL query/path，使刷新可恢复；不存在/已删除 ID 回退到最近会话或空态。
- draft、scroll anchor、inspector selection 按 conversation ID 保存于前端 session state；它们不是 canonical model context。若使用 sessionStorage，必须设大小上限、删除会话时清理并在 About 说明。

### 15.2 列表和生命周期 UX

- 列表默认按 last activity 排序，显示标题、workspace basename、相对时间与 running/queued/error/unread badge。
- 新建会话先选择 workspace/profile；创建后聚焦 Composer，不强制立即产生空 turn。
- 默认标题取第一条 user text：去首尾空白、折叠换行、按 Unicode code point 截断 40 字；空白回退“新会话”。手动 rename 后 `title_source=manual` 永不被覆盖。
- archive 从主列表消失，进入“已归档”筛选；running 时 archive 按钮禁用并解释原因。
- delete 使用 Dialog 显示标题并明确“不会删除工作区文件”；确认后清理 query/event/draft store 并选择下一个会话。
- 切换到后台 running 会话时恢复实时 SSE；离开不 cancel。完成时列表 badge 更新，可选非侵入通知但不强制抢焦点。

## 16. 实施批次与回滚入口

### 批次 A：Repository 与 migration（不接 UI）

完成 schema、domain contract、in-memory/SQLite tests、首次启动和恢复扫描。旧 RunController 仍工作。此批次失败可删除新建的测试数据库，不迁移生产用户数据。

### 批次 B：AgentLoop journal 与 ConversationService

增加 `run_turn` compatibility seam、canonical group、RuntimeRegistry/workspace lease；用 API contract tests 完成三轮上下文，仍可保留旧单 run 页面。

### 批次 C：HTTP 与前端会话壳

生成新 OpenAPI types，完成 list/selection/lifecycle/background stores 与 E2E；旧 `/api/runs` 作为薄 compatibility adapter。

### 批次 D：恢复、迁移和清理

加入真实进程重启 fixture、pending tool group 恢复、旧 API deprecation 文档；确认新路径稳定后删除 RunController 的进程级单例状态，不能留下两个事实源。

回滚时 schema 只前进：代码可暂时停用新 UI，但不得用旧二进制打开更高版本 DB。备份恢复必须是用户明确操作，不能自动覆盖当前库。
