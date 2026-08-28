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
- [ ] U4. 桌面默认显示左侧 ConversationSidebar + 中间 transcript；右侧 preview closed 时不挂载、不占宽、不显示空 RunInspector 卡。
- [ ] U5. “新对话”、搜索、workspace 分组、切换、rename、archive/delete 和状态 badge 都从左栏进入；原“新任务/当前运行”功能菜单不再作为主导航。
- [ ] U6. 点击 action row 或 turn 末尾文件行打开右侧 ArtifactPreviewPane；关闭后焦点回到触发文件、中栏恢复宽度。
- [ ] U7. 窄屏左栏和 preview 分别使用可访问 drawer/sheet；Escape/focus trap/返回焦点正确，Composer draft 不丢。

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

## TurnChangeSet 正确性

- [ ] C1. 非 Git workspace 中成功 create/modify 的 write/edit 都保存准确 before/after hash、immutable artifact 和行级 diff；失败工具不产生变更行。
- [ ] C2. 同一文件在一个 turn 内修改两次只显示一行，diff 为首个 before→最终 after；修改后完全还原则 file_count/+/- 均不计该文件。
- [ ] C3. 同一路径在后续 turn 再次修改时，两个 turn 各自打开自己的历史 diff；当前 workspace 继续变化不会改写旧 preview，并显示 divergence。
- [ ] C4. create/modify/delete/rename 和 binary/too-large/incomplete 各有稳定 change type、preview capability 和用户文案；不能生成 diff 时不伪造 +/-。
- [ ] C5. `RunResult.mutated_paths` 与 tool-confirmed ChangeSet terminal invariant 有自动检查；差异进入稳定 diagnostic/feedback，不静默丢文件。
- [ ] C6. Git workspace 从已有 dirty/staged/untracked 状态开始时，本 turn diff 以 turn baseline 为准，不把运行前改动算入本轮；HEAD 改变时 coverage fail-closed。
- [ ] C7. 非 Git/run_command probe 超出文件/字节/耗时预算时主 turn 继续完成，ChangeSet 标 `confirmed_only/incomplete`，成功 write/edit 仍完整可审查。

## 文件摘要与预览 UI

- [ ] V1. 每个 terminal turn 的 change summary 固定在该 turn 最后、下一 user message 前；显示净文件数、总 +A/-D 和 A/M/D/R 文件行。
- [ ] V2. 0 个净变化不显示空大卡，只显示克制状态；超过 5 个文件默认折叠并可“再显示 N 个”，100 文件时 DOM 有界。
- [ ] V3. modified 默认 Diff、created 默认 After、deleted 默认 Before；before/after/current mode、上一/下一文件、close 和键盘均可用。
- [ ] V4. 右栏只在点击文件/审查更改后出现；切到另一 conversation 时旧 pane 自动关闭或恢复该会话自身 artifact，不发生跨会话闪现。
- [ ] V5. preview loading/error/binary/truncated/corrupt/diverged 有独立可恢复状态；源码只读、HTML 转义、长行和行号可用。
- [ ] V6. desktop open/closed、1280×720、320px、light/dark、zh-CN/en-US 的全屏 production screenshot 与布局一致。

## Artifact/API 安全与资源门禁

- [ ] S1. preview API 只接受层级资源 ID，逐层校验 conversation→turn→change→blob 归属；任意 path、`..`、绝对路径、跨会话 change id 均不能读取文件。
- [ ] S2. 历史源码 artifact 不进入 bootstrap/list/SSE/log/error/默认导出；仅用户点击后按需 fetch，DTO/payload 有硬上限。
- [ ] S3. 单文件 text snapshot≤1 MiB、单 turn 新增 artifact 默认≤20 MiB、diff≤20,000 行；超限结果稳定且不阻塞主 turn。
- [ ] S4. CAS 相同内容去重；conversation delete 清除 refs，精确 GC 后无 orphan 正文；GC crash 可重试且不会删除仍被其他 turn 引用的 blob。
- [ ] S5. artifact 文件损坏/hash 不符时 fail-closed 为 `artifact_corrupt`，不把损坏内容渲染或回退读取任意 current path。

## 新增 E2E 场景

1. 左栏新建 Conversation A，运行修改 `a.py`，末尾 change summary 点击后右栏显示 diff；关闭右栏后中栏扩展。
2. 新建 Conversation B 并后台运行；A/B 列表 badge 独立，切换不取消，B 不显示 A 的 preview。
3. A 第二 turn 再改 `a.py`；分别点击两个 turn 的同名文件，before/after/diff 和 turn identity 不串 cache。
4. 从 dirty Git baseline 运行 write/edit + command；只统计 turn 净变化，coverage/warning 符合探测结果。
5. reload/server restart 后会话列表、turn change summary 和 artifact preview 可恢复；删除 A 后其 API 404、artifact refs/正文完成既定清理。
