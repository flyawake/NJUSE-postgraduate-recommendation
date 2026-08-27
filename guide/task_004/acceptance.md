# 任务编号：task_004 验收标准

## Conversation 数据模型

- [ ] D1. Conversation、Turn、Run/attempt、canonical item 与 public event 的责任边界有类型和文档，run 不再等同于 conversation。
- [ ] D2. `CODING_AGENT_HOME/state.db` 使用显式 schema version、foreign keys、事务和迁移；首次启动与旧用户无损初始化。
- [ ] D3. append-only canonical/event 数据是事实源，标题/状态/计数投影可从日志校验；注入写失败不会留下半个 turn。
- [ ] D4. active turn 崩溃/进程退出后重启被标记为唯一 INTERRUPTED 终态，不自动重放命令或写文件。

## 多轮与隔离

- [ ] M1. 同一 conversation 三轮追问保留前文、工具关联与 ContextManager 预算；模型看到的是 canonical history，不是公开 DTO 拼接文本。
- [ ] M2. 两个 conversation 的 workspace、profile、draft、history、scroll、run 状态和 model context 相互隔离。
- [ ] M3. 页面切换不取消后台运行；后台状态在会话列表可见，返回后 snapshot/SSE 无丢失重复。
- [ ] M4. 每 conversation 最多一个 active turn；同一 canonical workspace 的并发副作用受锁保护，不同 workspace 受全局并发上限保护。

## 生命周期与 API

- [ ] L1. 支持 create/list/paginate/read/rename/archive/unarchive/delete 与 start/cancel turn，全部使用稳定 DTO/error code。
- [ ] L2. 默认标题由第一条用户消息确定性生成；手动名称可重复、重启保持且不会被自动覆盖。
- [ ] L3. archive 从默认列表隐藏但可恢复；delete 必须二次确认并事务性删除本地 conversation 数据，不删除 workspace 文件。
- [ ] L4. 旧 `/api/runs` 和现有 CLI 在明确兼容期内继续工作，弃用行为有文档和测试。

## UI/UX

- [ ] U1. 左侧会话列表支持新建、搜索/分页、运行徽标、rename、archive；归档管理页支持恢复和删除。
- [ ] U2. 切换会话无整页刷新，键盘和窄屏可操作；危险删除使用有焦点管理的确认 Dialog。
- [ ] U3. 默认页面不暴露 SQLite/event/canonical 等开发术语，存储与隐私边界位于 About。

## 质量门禁

- [ ] Q1. Python 覆盖 migration、事务失败、crash recovery、workspace lock、上下文隔离、生命周期和分页。
- [ ] Q2. Vitest/RTL 与 production Playwright 覆盖两会话三轮对话、后台切换、刷新/重启、归档/恢复/删除。
- [ ] Q3. task_001-task_003 全套、Ruff、API types、typecheck、lint、build、E2E、audit、wheel、diff check 全部通过。

## 数据不变量与故障注入

- [ ] F1. 对 start turn 的每个事务写点注入异常，数据库不存在“有 active turn 但无 user canonical item”或 ordinal 重复。
- [ ] F2. 在 assistant tool-call 写入后、每个 tool result 前后和 terminal compare-and-set 前终止进程；恢复后 provider history 始终配对，未知副作用被明确标记且不重放。
- [ ] F3. 同一个 idempotency key 并发发送两次只创建一个 turn；两个不同请求争用同 conversation 只有一个进入 active。
- [ ] F4. rename/archive/delete 的 stale version 返回 409 `version_conflict`，不会静默覆盖另一客户端的新状态。
- [ ] F5. migration 任一步失败保留原 DB 与备份，schema version 不半升级；损坏 canonical payload fail-closed 为 `data_error`。

## API 与分页证据

- [ ] A1. Conversation/Turn DTO、状态枚举、cursor 与错误码写入 OpenAPI，生成的 TS 类型无手写漂移。
- [ ] A2. 201 条会话/turn fixture 以 limit 50 翻完所有页，无重复/遗漏；中途插入一条新记录仍满足文档化 cursor 语义。
- [ ] A3. 旧 `/api/runs` 仅调用 ConversationService compatibility path，源码中不存在第二个 worker/history/event 实现。
- [ ] A4. 所有 mutation 继续通过 session token、Host/Origin 与 loopback 防护；跨 conversation ID 不可读写他人状态（本地单用户边界内仍须校验资源归属）。

## 交付证据

| 证据 | 必须说明 |
| --- | --- |
| schema 图/迁移清单 | 表、FK、unique/partial constraint、version、备份位置 |
| 三轮模型请求夹具 | 每轮 canonical 输入摘要、tool pairing、conversation 隔离 |
| 并发测试 | 同 conversation、同 workspace、不同 workspace、全局 worker 上限 |
| 重启 E2E | running→interrupted、SSE 恢复、无命令重放 |
| UI 截图 | 新建、两会话切换、后台 running、归档、删除确认、窄屏 |
