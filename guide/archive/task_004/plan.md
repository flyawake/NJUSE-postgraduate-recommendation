# 任务编号：task_004

## 1. 任务目标

把“一次发送 = 一个全新 run”的单任务模型升级为持久化、多轮、可切换的 Conversation 系统。每个 Conversation 拥有独立 workspace、profile、canonical model context、turn/run/event 历史与生命周期，支持创建、继续、切换、命名、归档、恢复归档和永久删除。

同时把应用壳升级为明确的三栏产品布局：左侧是会话管理主边栏，所有“新对话/切换/命名/归档/删除”动作从此进入；中间是连续对话；右侧默认完全关闭、不保留空白占位，只有用户点击本轮改动文件时才打开可关闭的文件/差异预览。每个 turn 终态后，必须在 transcript 末尾追加本轮文件变更摘要，点击任一文件可查看该 turn 的不可变 before/after/diff。

本任务建立后续 streaming、Queue/Steer 和 Memory 的数据平面；暂不实现消息排队/插入当前轮、reasoning delta 或跨会话记忆。

## 2. 背景与上下文

- 当前 `RunController`、RunStore 与 UI 都只有一个进程级 current run；终态后再发送会创建完全独立历史，无法进行追问。
- Codex app-server 将 thread 与 turn 分离，并为 name/archive/unarchive/delete 提供独立生命周期；DSH 使用 append-only SessionEvent 作为持久化单一事实源，并从日志投影视图。
- 本项目已有 append-only canonical history 和单调 AgentEvent，适合演进为 Conversation → Turn → Run 的层级，不应另建一套与 AgentLoop 冲突的“聊天历史”。
- task_003 完成后，左侧导航和 Composer 已具备面向会话扩展的视觉槽位。
- 用户新增的截图要求明确了最终信息架构：左侧不是“新任务/当前运行/模型设置”的功能菜单，而是以 workspace 分组的 Conversation 导航；右侧不是常驻运行详情卡，而是按需出现的文件审查面板；每轮末尾有类似“已编辑 N 个文件、+A/-D”的变更摘要。
- 当前 `RunResult.mutated_paths` 只记录成功 `write_file/edit_file` 的路径，没有 before/after 内容、行级 diff、创建/删除类型，也无法区分同一文件多次编辑后的净变化；不能直接据此伪造文件审查能力。
- 不能只调用当前 `git diff`：工作区可能不是 Git 仓库，可能在 turn 开始前已有未提交改动，同一文件也可能在后续 turn 再次变化。文件预览必须以 turn-scoped baseline/artifact 为事实源，Git 只能作为命令副作用探测的增强策略。

## 3. 技术约束

- 使用 Python 标准库 `sqlite3` 建立 `CODING_AGENT_HOME/state.db`；启用 foreign keys、事务、busy timeout 和适合本地应用的 WAL。不得为基础持久化引入 ORM。
- append-only conversation event/canonical item 是事实源；列表、标题、最后状态等可为事务内投影，不得出现双写不一致。
- ID 使用不可猜测稳定标识；所有 Conversation API 仍受 loopback/Host/Origin/session-token 防护。
- 每个 conversation 同时最多一个 active turn；不同 workspace 可在受控全局上限内并行，同一 workspace 默认互斥执行，避免并发编辑冲突。
- 切换页面不得取消后台 turn；关闭服务时取消并把未完成 turn 持久化为 INTERRUPTED，重启不自动重放副作用。
- canonical history 必须保持 tool-call/result 配对、上下文预算和压缩语义；多轮追问不得把公开脱敏 DTO 反向当作模型历史。
- archive 是可恢复软生命周期；delete 是确认后的硬删除，必须事务性清除 conversation、turn、queue 占位和事件，但不得删除用户 workspace 文件。
- 老用户无数据库时自动初始化；数据库 schema 使用显式 version/migration，失败不覆盖原文件。
- 桌面端右侧 Artifact Preview 默认 closed；closed 时不得渲染空白边栏或永久占用宽度。运行统计、验证和高级诊断进入 transcript 终态摘要/按需 disclosure，不得以常驻 Inspector 与文件预览争夺右栏。
- 文件变更摘要展示“本 turn 的净变化”，不是 tool call 次数：改后又恢复原内容的文件不计入最终 N；失败的 write/edit 不计入；同一路径多次成功修改合并为一条。
- 历史 turn 的预览必须读取该 turn 的内容寻址快照，不能直接读取当前 workspace 冒充历史结果；当前文件已继续变化时显示 divergence 提示。
- 文件预览 API 只能通过属于该 conversation/turn 的 `change_id` 访问已授权 artifact，不接受任意绝对路径或通用 read-file 参数。
- 首版预览为只读，使用 Python 标准库生成结构化 diff、React 自研轻量 renderer；不得为预览引入 Monaco/VS Code editor 或把浏览器变成本地文件任意读取器。

## 4. 实现步骤

1. 定义 `ConversationId`、`TurnId`、Conversation/Turn 状态机与 API DTO，明确 run 只是一个 turn 的执行实例。
2. 新增 storage seam 与 SQLite backend：schema version、conversations、turns、canonical_items、public_events/投影；实现事务、分页、归档和删除。
3. 把 RunController 演进为 ConversationManager + per-conversation runtime registry；后台 turn 与当前 UI selection 解耦，并加入 workspace execution lock/全局并发上限。
4. 让 AgentLoop 可从经过校验的 canonical history 启动下一 turn，把本轮追加结果原子持久化；ContextManager 对完整会话做预算投影与压缩。
5. 新增 conversation/turn CRUD、list/pagination、history/events、rename/archive/unarchive/delete/start/cancel API；保留旧 `/api/runs` 兼容适配一版并标记弃用。
6. 前端建立 conversation 列表、当前会话路由和 per-conversation store；切换时恢复独立 draft、workspace/profile、历史滚动位置与 inspector，后台运行显示状态徽标。
7. 标题默认由第一条用户消息做确定性截断，不额外调用模型；支持手动 rename，手动名称不被自动覆盖。
8. 添加迁移、崩溃恢复、并发锁、分页、隔离上下文、归档/删除与浏览器切换 E2E。
9. 新增 turn-scoped `ChangeSetService` 与 `ToolExecutionObserver`：在成功 write/edit 前后捕获文件状态，以内容 hash 合并同一路径的净变化；为 `run_command` 增加 Git-aware/bounded generic workspace probe，明确 confirmed/detected/incomplete 可信度。
10. 新增 content-addressed ArtifactStore、diff builder 和只读 preview API；保存有界 before/after snapshot，生成 create/modify/delete/rename、+A/-D、hunk 与截断元数据。
11. 重构 AppShell 为 `ConversationSidebar + ConversationMain + conditional ArtifactPreviewPane`；右栏关闭时中栏占满剩余空间，点击 action row 或 TurnChangeSummary 文件行时打开预览。
12. 每个 terminal turn 在 assistant/terminal 内容之后渲染 `TurnChangeSummary`；历史 turn 保持自己的 change set，支持“查看全部改动”、文件切换、diff/current snapshot divergence 与键盘操作。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `src/coding_agent/conversations/` | 新增 | domain、store seam、SQLite backend、migration |
| `src/coding_agent/agent.py`、`context.py`、`models.py` | 修改 | 从已验证 canonical history 继续下一 turn |
| `src/coding_agent/web/controller.py` | 重构 | ConversationManager/runtime registry/workspace lock |
| `src/coding_agent/web/app.py`、`schemas.py` | 修改 | conversation/turn API 与兼容 run API |
| `src/coding_agent/changes/` | 新增 | TurnChangeSet、ToolExecutionObserver、workspace probe、diff builder |
| `src/coding_agent/artifacts/` | 新增 | 内容寻址快照、引用/GC、安全读取 |
| `frontend/src/lib/*store*` | 重构 | per-conversation state 与订阅 |
| `frontend/src/components/AppShell.tsx` | 修改 | 三栏布局、条件右栏和 responsive drawer |
| `frontend/src/components/ConversationSidebar.tsx` | 新增 | 新对话、workspace 分组、搜索、会话生命周期 |
| `frontend/src/components/ArtifactPreviewPane.tsx` | 新增 | 文件/差异只读预览、tabs/close/navigation |
| `frontend/src/components/TurnChangeSummary.tsx` | 新增 | turn 末尾文件列表与 +A/-D 汇总 |
| `frontend/src/components/DiffViewer.tsx` | 新增 | 有界行级 diff/plain file renderer |
| `frontend/src/pages/*` | 修改/新增 | conversation view、archive management |
| `tests/`、`frontend/src/__tests__`、`frontend/e2e` | 新增/修改 | 数据、并发、恢复、多轮、change set 和 preview 闭环 |

## 6. 验收标准

- [ ] 同一 conversation 连续发送至少三轮，第三轮模型请求可使用前两轮事实；另一个 conversation 不含这些上下文。
- [ ] 新建、切换、rename、archive、unarchive、delete 在重启 GUI 后保持；删除需要确认且不触碰 workspace 文件。
- [ ] 切换离开不会取消后台 turn；不同 workspace 可受控并行，同一 workspace 并发编辑被稳定拒绝或排队且有产品提示。
- [ ] SQLite event/canonical 历史事务一致、可分页、可迁移；崩溃中的 active turn 重启后唯一终态为 INTERRUPTED，不重放工具。
- [ ] canonical tool pairing、completion verification、public redaction、CLI 与 task_003 UI/性能回归全部保持。
- [ ] Fake Model production E2E 覆盖两会话隔离、三轮追问、后台切换、重启恢复、归档恢复和永久删除。
- [ ] 左栏以 Conversation 为主导航，顶部“新对话”从左栏创建；workspace 分组、搜索、选中态、运行/未读状态、rename/archive/delete 和窄屏 drawer 均可用。
- [ ] 右侧默认不渲染/不占用空白栏；点击本轮或历史 turn 的改动文件后打开只读 preview，关闭后中栏恢复宽度，切会话不会显示不属于该会话的旧文件。
- [ ] 每个 terminal turn 的末尾按净变化显示 `已修改/新增/删除 N 个文件`、总 +A/-D 和文件行；无净变化不显示空大卡，只给克制的“本轮未改动文件”状态。
- [ ] 文件多次编辑合并为该 turn 一条净 diff；历史 preview 使用 immutable artifact，当前 workspace 后续变化时明确提示，不把当前内容冒充历史内容。
- [ ] 非 Git workspace 的成功 write/edit 仍有准确 before/after/diff；Git/命令探测增强失败时诚实标记 coverage，不影响已确认变更。

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

## 17. 三栏 Application Shell 详细规格

### 17.1 桌面布局

```text
┌──────────────────────┬──────────────────────────────────┬────────────────────────┐
│ ConversationSidebar  │ ConversationMain                 │ ArtifactPreviewPane    │
│                      │                                  │ （默认不挂载）          │
│ + 新对话             │ Header: title/workspace/profile  │ file tab / close       │
│ 搜索                 │ Transcript                       │ diff | file            │
│ workspace group A    │   Turn 1                         │ hunks / line numbers   │
│   conversation...    │   Change summary                │                        │
│ workspace group B    │   Turn 2                         │                        │
│ 已归档 / 设置        │ Composer                         │                        │
└──────────────────────┴──────────────────────────────────┴────────────────────────┘
```

- 左栏默认宽度建议 248–280 px，可折叠为窄图标栏；中栏 `min-width` 保证 transcript/Composer 可用；右栏打开时建议 420–640 px 或视口 35%–45%，可拖动调整并设上下限。
- 右栏 closed 时组件不挂载、CSS grid 只有两列，不能保留 1px 空壳、阴影或空的“运行详情”。
- 打开预览后中栏 reflow，不覆盖 Composer；视口不足时右栏转为 modal drawer/全屏 sheet。
- 左右栏宽度属于非敏感 UI preference，可本地持久；具体选中的 artifact 可写 URL query，刷新后经权限校验恢复。
- task_003 的 RunInspector 在本任务中退为 transcript terminal disclosure/topbar diagnostics drawer；文件预览是唯一常规右栏内容。高级诊断不得在页面启动时自动打开。

### 17.2 Responsive 行为

| 视口 | 左侧 | 右侧预览 | 中栏 |
| --- | --- | --- | --- |
| ≥1280 px | 常驻，可折叠 | 条件常驻 resizable pane | 占余下空间 |
| 768–1279 px | 窄栏或 drawer | overlay drawer，宽≤70vw | 不被压到不可读 |
| <768 px | 全屏/侧滑 drawer | 全屏 sheet | 单列 transcript |

- drawer 打开时 focus trap，关闭后焦点回到触发的 conversation/file row。
- `Escape` 先关闭最上层 preview/dialog，再关闭 sidebar；不能同时丢失 draft。
- 屏幕阅读器 landmark 使用 nav/main/complementary，三个区域有独立可见或 accessible heading。

## 18. 左侧 ConversationSidebar 产品规格

### 18.1 信息层级

1. 顶部品牌/应用菜单和全局搜索入口。
2. 明确的“新对话”主动作；快捷键可以存在，但按钮始终可见。
3. Conversation 搜索框/命令面板入口。
4. 按 canonical workspace 分组的 active conversations；组标题显示 workspace basename，完整路径在 tooltip/detail。
5. “已归档”入口、Memory/Settings/About 等低频入口固定底部。

当前的“新任务、当前运行、模型设置、关于/安全”功能菜单必须重构：

- “新任务”改为“新对话”，并从左栏创建 Conversation，不直接绕过 conversation API 启动 run。
- “当前运行”不再是单独页面；每个会话行用 spinner/dot/badge 表达 running/queued/unread，点击即进入对应 transcript。
- “模型设置”和“关于”移至底部设置区，不占据 conversation 首屏。

### 18.2 Workspace 分组与会话行

- 分组 key 使用服务端 canonical workspace key，不能按用户输入字符串分裂同一目录；显示名冲突时附加父目录尾段。
- 分组内按 `last_activity_at DESC, conversation_id` 稳定排序；running conversation 可用状态突出，但不破坏用户手动选中。
- 会话行显示 title、相对更新时间和最多两个状态：running/queued count/error/unread；不展示内部 run ID。
- 行主点击只切换；rename/archive/delete 位于 context menu，避免整行布满小图标。菜单键/Shift+F10 可打开。
- 搜索匹配 title、workspace display name 和第一条用户消息的安全 preview；服务端分页为事实源，前端不先下载所有会话再过滤。
- 新建流程：点击“新对话”立即创建空 Conversation 或打开轻量 workspace/profile picker；创建成功后选中并聚焦 Composer。不得先生成 fake local row 再在发送时补服务端 ID。
- 空会话可删除；未发送内容只作为 per-conversation draft，不进入 canonical history。

### 18.3 状态与性能

- sidebar 只订阅 conversation list projection 和每会话轻量 badge，不订阅所选会话全部 transcript/delta。
- 200 个 conversation 使用 cursor pagination/窗口化列表；一个模型 delta 不得导致所有 conversation row render。
- 后台完成时只更新对应行并标 unread；除非用户设置允许，不弹抢焦点 modal。

## 19. TurnChangeSet 与 Artifact 数据架构

### 19.1 领域对象

```text
TurnChangeSet
  id, conversation_id, turn_id, status(live/final/incomplete),
  additions, deletions, file_count, coverage, finalized_at

TurnFileChange
  id, change_set_id, relative_path, old_relative_path?,
  change_type(created/modified/deleted/renamed),
  source(tool_confirmed/command_detected/external_unknown),
  before_blob_id?, after_blob_id?, before_sha?, after_sha?,
  additions?, deletions?, binary, preview_status, warnings[]

ArtifactBlob
  sha256, byte_count, encoding, compression, storage_path,
  created_at, reference_count
```

- ChangeSet 按 turn 唯一；同一 relative path 在 final set 中最多一条。
- `coverage=complete/confirmed_only/incomplete` 描述探测完整性；不能把已确认 write/edit 与未能覆盖的 command side effects 混成“全部已检测”。
- `source` 是可信度，不是 UI 技术词；普通文案可显示“本轮修改”或在 incomplete 时提示“部分命令产生的变化可能未被识别”。
- path 全部是规范化 workspace-relative POSIX 表示；原始绝对路径不进入 public DTO。

### 19.2 ArtifactStore

- 使用 agent home 下固定 `artifacts/sha256/<prefix>/<digest>` 内容寻址目录，路径完全由服务端 digest 构造，不接受用户路径拼接。
- blob 先写同目录临时文件、fsync/close 后 `os.replace`；metadata/ref 建立失败时清理精确临时目标。
- 文本 snapshot 单文件上限默认 1 MiB，单 turn 新增 artifact 总量默认 20 MiB；相同内容按 SHA-256 去重。
- UTF-8 文本可以 zlib 压缩；binary 或解码失败只保存有界 metadata/hash，默认不内联预览/下载。
- conversation hard delete 在数据库事务中删除 refs，再由有界 GC 清理 refcount=0 的精确 blob；GC crash 可重试，不能递归删除整个 artifact root。
- ArtifactStore 中的源码属于本地敏感数据：不得写日志、错误 payload、截图 fixture 或导出，除非用户显式选择导出。

### 19.3 SQLite 表

| 表 | 约束/用途 |
| --- | --- |
| `turn_change_sets` | `turn_id UNIQUE`、聚合计数、coverage、final 状态 |
| `turn_file_changes` | `change_set_id + relative_path UNIQUE`、type/source/blob refs/line counts |
| `artifact_blobs` | sha PK、size/encoding/storage/ref count |
| `artifact_refs` | blob↔conversation/turn/change/side(before/after) UNIQUE |

- change set finalization 与 turn terminal 尽可能在同一 Unit of Work：至少保证 terminal snapshot 不会永久显示“有变更计数但无 change set”。
- artifact 文件写入发生在 DB transaction 外；采用 staged blob + transaction refs + finalize/GC 的补偿流程，避免持 SQLite write lock 进行大文件 I/O。

## 20. 变更捕获策略

### 20.1 Tool-confirmed 精确捕获

在 ToolExecutor 增加中立 `ToolExecutionObserver`/`MutationObserver`，而不是让 AgentLoop 或 Web controller解析工具返回文本：

```text
before_execute(prepared_call, ToolSpec.effect)
after_execute(prepared_call, ToolOutcome)
```

- 对 `ToolEffect.WRITE` 且具有规范化 workspace path 的调用，在 handler 执行前读取存在性/bytes/hash，在成功后读取 after；创建文件 before=None，删除 after=None。
- before 只在本 turn 该 path 首次可能变更时保存；after 每次成功更新。多次 edit 最终比较首个 before 与最后 after。
- hash 相同表示净无变化，final summary 移除该行；失败 outcome 不更新 after，不产生“已修改”。
- observer 捕获失败不得伪造 diff：记录 `preview_status=unavailable` 和内部稳定错误；是否允许工具继续按安全性判断，不能因审查 UI 故障回滚已经成功的原子写。
- `RunResult.mutated_paths` 可继续作完成验证输入，但 ChangeSet 是 UI 文件审查的事实源；二者在 terminal 时必须做 invariant check 并报告差异。

### 20.2 run_command/外部变化探测

`run_command` 可能修改任意文件，采用分层 probe：

1. **Git-aware 模式**：turn 开始记录 HEAD、porcelain status 和已有 dirty/untracked 路径的有界 before snapshot。turn 结束重新比较；初始 clean tracked 文件的 before 可从原 HEAD blob取得，初始 dirty 文件使用 captured snapshot。
2. **Generic bounded 模式**：非 Git workspace 记录有界 path/stat/hash manifest；对符合大小/目录预算的 UTF-8 文件保存 before snapshot。结束时识别 create/modify/delete；没有 before blob时只给 current preview与 `diff_unavailable`。
3. **Tool observer 合并**：无论 probe 是否完整，write/edit 精确记录优先；同 path 不重复。

- 默认排除 `.git`、agent home、`.venv`、`node_modules`、构建/缓存目录的 generic 深扫，但 tool-confirmed 修改即使位于排除目录仍记录。
- probe 限制文件数、总 stat/hash/快照字节和耗时；超限把 coverage 标 `confirmed_only/incomplete`，不阻塞 AgentLoop。
- 若 turn 内 HEAD/branch 改变，Git baseline 标 invalidated；UI 说明无法生成完整 turn diff，仍保留 tool-confirmed artifacts。
- probe 发现的变化只能说“本轮期间检测到”，不能在无法证明时声称一定由模型产生；外部编辑竞争标 `external_unknown`。

### 20.3 Diff 生成

- backend 使用 `difflib.SequenceMatcher(autojunk=False)` 或 `unified_diff` 生成结构化 hunks，统一处理 LF/CRLF 和末尾换行标记；before/after artifact 本身保持原字节。
- 行统计按 hunk 计算 additions/deletions；rename 且内容不变为 +0/-0。
- 单文件 diff 处理上限默认 20,000 行/1 MiB；超限返回 metadata + 截断/下载说明，不进行潜在 O(n²) 计算。
- preview DTO 返回 hunks/lines，不返回 HTML；前端转义后渲染，禁止把源码放入 `dangerouslySetInnerHTML`。
- historical diff 始终基于 blob；`current workspace` 只作为单独 mode，并带 current SHA/diverged flag，不能覆盖 turn artifact。

## 21. 文件预览 API

| Method | Path | 返回 |
| --- | --- | --- |
| GET | `/api/conversations/{cid}/turns/{tid}/changes` | ChangeSetDTO + file rows |
| GET | `/api/conversations/{cid}/turns/{tid}/changes/{change_id}` | 单文件 metadata/capabilities |
| GET | `.../{change_id}/preview?mode=diff&cursor=...` | 结构化 hunk page |
| GET | `.../{change_id}/preview?mode=before|after&cursor=...` | 有界文本行 page |
| GET | `.../{change_id}/current` | 可选当前文件摘要/hash/diverged，不返回任意路径 |

- 服务端逐层校验 cid→tid→change_id 归属、conversation 未删除、artifact ref存在；任一级不匹配统一 404，避免资源枚举。
- preview cursor opaque，limit/总 payload 有硬上限；不允许通过 mode/encoding/header 绕过。
- DTO 至少包含 `change_type`、relative path、line counts、binary/truncated、preview modes、coverage/warnings、before/after/current hashes 的安全短摘要。
- error code 包含 `artifact_not_found`、`preview_unavailable`、`binary_preview_unsupported`、`artifact_too_large`、`artifact_corrupt`、`workspace_diverged`。
- Artifact content 不进入 bootstrap、conversation list或 SSE；只在用户点文件后按需 fetch，React Query key 必须包含 conversation/turn/change/mode/cursor。

## 22. TurnChangeSummary 与 ArtifactPreviewPane 交互

### 22.1 Turn 末尾变更摘要

- 插入位置固定：该 turn 的 assistant 最终正文/terminal 状态之后、下一条 user message 之前；历史分页恢复后位置不漂移。
- header 根据净变化使用“修改了 N 个文件”或“新增/修改/删除 N 个文件”，显示总 `+A -D`；数字颜色之外仍有符号/文本。
- 默认展示前 3–5 个文件，按首次变化顺序或稳定 path 排序；“再显示 N 个文件”展开，不一次挂载数百行。
- 每行显示状态标记（A/M/D/R）、relative path、+/-；点击或 Enter 打开右侧对应 diff。
- “审查更改”打开第一条或 change list；不在本任务提供“撤销全部/撤销文件”，因为历史 snapshot 不能安全覆盖用户后续编辑。
- terminal 无净变化时不画大卡，只在 terminal summary 一行显示“本轮未改动文件”；incomplete coverage 显示克制 warning 和详情。

### 22.2 右侧预览面板

- 默认 mode：modified/renamed 为 Diff，created 为 After，deleted 为 Before；用户可切换 Diff/修改前/修改后/当前文件（可用时）。
- 顶部显示 relative path、A/M/D/R、+/-、turn 标识和 close；支持上一/下一变更文件。
- 多次从不同 turn 打开同一路径时 tab/标题包含 turn，不能复用错误 cache；切 conversation 自动关闭不属于新会话的 pane。
- diff 使用单栏 unified view作为最小交付；足够宽时可选 split view，但不得以 split view 增加依赖/延迟核心交付。
- 行号、插入/删除背景、长行横向滚动、复制选中文本可用；源码为只读，不出现可编辑 caret/save。
- current hash 与 after hash 不同显示“文件此后已变化”；用户仍默认看到历史 turn diff，可明确切到 current mode。
- loading/error/empty/binary/truncated 每种有独立产品态和重试/关闭动作，不显示 Python traceback。

### 22.3 前端状态所有权

| 状态 | 所有者 | 规则 |
| --- | --- | --- |
| sidebar selection | URL + Conversation query | 刷新可恢复、删除后回退 |
| sidebar collapsed/width | UI preference | 不影响 conversation 数据 |
| open artifact ref | `ArtifactPaneStore`/URL query | 必须含 conversation/turn/change id |
| preview mode/cursor | pane local + React Query | 切 tab 独立缓存、有界 |
| change summary expand | TurnChangeSummary local | 不写服务端 |
| current divergence | preview response | 不用前端直接读 filesystem |

## 23. 新增实施批次与交接

在原 A–D 批次后增加：

### 批次 E：ChangeSet/Artifact 后端

先实现 ToolExecutionObserver、ArtifactStore、change tables、diff builder 和 API contract tests；在旧界面用 API 验证 create/modify/revert/history/divergence。禁止先画静态假文件卡。

### 批次 F：ConversationSidebar/三栏 Shell

把新对话、切换、生命周期入口迁入左栏；移除常驻右 RunInspector，占位关闭规则和 responsive drawer先通过视觉 E2E。

### 批次 G：TurnChangeSummary/Preview

接入 terminal transcript item、右栏 diff/file、历史 artifact、分页/大文件/二进制/错误状态；完成多 turn 同文件和切会话 cache 隔离。

Developer feedback 必须额外提供：

- 两个 conversation/三个 turn 的全屏截图：右栏关闭、点击 change 后打开、历史 turn diff、窄屏 drawer。
- 一个文件同 turn 修改两次、改后还原、下一 turn 再修改的 change set/preview 证据。
- 非 Git write/edit 精确 diff、Git dirty baseline、run_command detected/incomplete coverage 三类证据。
- 任意 path/跨 conversation change ID/已删除 conversation 请求的安全拒绝测试。
- artifact byte/line/payload 上限、CAS 去重、delete/GC 和数据库/磁盘残留审计。
