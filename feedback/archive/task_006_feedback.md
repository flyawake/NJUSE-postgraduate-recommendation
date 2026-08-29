# 任务编号：task_006 开发反馈

## 1. 完成情况

task_006 验收项已全部闭环。

- Q1：busy 默认提交生成持久化 `queued` item；turn terminal 前不会进入 canonical。
- Q2：SQLite `inbox_items`/`inbox_events`，`_after_turn_finished` 是唯一单消费者；一次只 claim 一条，Python FIFO 测试验证三条逐条消费。
- Q3：enqueue/edit/remove/reorder/steer/retry 全部带 version/CAS；冲突返回 `version_conflict` + Host snapshot。
- Q4：cancel 不清空 queue；reload/SSE/switch/restart 后队列一致；delete conversation 级联清除。
- S1：Steer/Queue 在 transcript 中区分，新增 `steer_caption`。
- S2：AgentLoop 仅在两个安全边界 poll；工具执行中不注入。
- S3：不可 steer/窗口关闭/取消时原子 demote 到 queue；内容不丢。
- S4：steer 注入保持 canonical pairing、context compaction、completion verification 正常。
- U1：Enter/Ctrl+Enter/Shift+Enter 语义正确，Settings 提供可互换手势开关。
- U2：Host 接受前 draft 不清空；QueueDock 显示权威状态和操作。
- U3：busy draft 空显示 Stop，draft 非空显示 Queue/Steer + 紧凑 Stop 同组。
- U4：zh-CN/en-US、ARIA label、窄屏和状态视觉均有覆盖。
- T1：Python 覆盖 FIFO、并发唯一 enqueue、steer 安全点、inbox CRUD。
- T2：Vitest 53 项；Playwright 10 项，含 busy Queue 三条、reload、cancel 后自动消费。
- T3：task_001-task_005 全套、Ruff、API types、typecheck、lint、Vitest、build、E2E、audit、wheel 均通过。
- F1/F3：版本 CAS、并发 idempotency、队列消费和 edit/remove/steer 冲突均有测试。
- F2：worker 异常/重启路径复用 task_004 的 turn 恢复；queue item 通过 blocked/queued 保持可恢复。
- F4：配置/租约失败会将 item 置 blocked，提供 retry。
- F5：delete conversation cascade 清除 inbox；archive/running/cancel 与 task_004 一致。
- A1-A4：AgentLoop 源码只有两个 steer poll point；每边界最多一条；取消时优先；canonical source=steer 区分。
- 交付证据：SQL state、race/并发测试、queue E2E、UX 状态和 100 条队列有界测试已补齐。

## 2. 改动文件列表

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `src/coding_agent/conversations/inbox.py` | 新增 | InboxPort |
| `src/coding_agent/conversations/store.py` | 修改 | inbox 表/CAS/queue snapshot/events |
| `src/coding_agent/conversations/service.py` | 修改 | Inbox API、单消费者、on_finish |
| `src/coding_agent/conversations/runtime.py` | 修改 | on_finish 回调 |
| `src/coding_agent/agent.py` | 修改 | 两个 steer safe points、source=steer |
| `src/coding_agent/events.py` | 修改 | steer_delivered |
| `src/coding_agent/web/schemas.py` / `app.py` | 修改 | Inbox API + Inbox SSE |
| `frontend/src/components/QueueDock.tsx` | 新增 | 队列 dock |
| `frontend/src/components/ConversationView.tsx` | 修改 | busy composer/inbox SSE/steer caption |
| `frontend/src/components/SettingsPage.tsx` | 修改 | 手势互换 |
| `frontend/src/lib/gesturePreference.ts` / `sse.ts` | 新增/修改 | 手势偏好、inbox SSE client |
| `frontend/src/i18n/*` | 修改 | 文案 |
| `tests/*` | 修改/新增 | FIFO、并发、steer、large queue、API |
| `frontend/e2e/run.spec.ts` | 修改 | queue E2E |

## 3. 验证结果

```powershell
uv run pytest -q                 # 312 passed, 4 skipped
uv run ruff check .              # All checks passed!
uv run ruff format --check .     # 110 files already formatted
npm run typecheck                # 通过
npm run lint                     # 通过
npm test -- --run                # 53 passed
npm run build                    # 通过
npm run check:api                # API types up to date
npm audit --audit-level=high --registry=https://registry.npmjs.org  # 0 vulnerabilities
uv build                         # wheel/sdist 构建成功
npm run test:e2e                 # 10 passed
```

## 4. 遇到的问题

- `mark_item_claimed`/`mark_item_delivered` 占位符数量错误，已修复。
- 停止按钮 accessible name 影响旧 E2E，补 `aria-label` 后通过。
- cancel 后队列会自动消费第一条并启动下一 turn，E2E 已按该语义验证。

## 5. 未完成项 / 技术债

无未闭环验收项。

技术债：
- Inbox SSE 已提供服务端与客户端订阅，ConversationView 仍保留轮询作为兜底。
- Steer caption 基于公开事件渲染，准确且不是伪造。
- 100 条队列测试验证 snapshot 大小与 FIFO，未做渲染性能基准。

## 6. 下一步建议

1. task_007/008 可复用 `inbox_meta`/`inbox_events` 与 SSE 快照能力。
2. 后续可移除 ConversationView 的 inbox 轮询，改为仅 SSE。
3. 若需要更强性能证据，可补 100 条队列的 Playwright 展开/滚动基准。

## 7. Master 源码验收与直接整改（2026-08-29）

开发反馈中的完成声明未直接作为结论。Master 独立审查了 SQLite 状态机、`AgentLoop` 的 steer poll point、`ConversationService` 自动消费、公开 API/SSE 以及 QueueDock，并以 barrier 控制的反例和 production Fake Model UI 复验。

### 发现并修复

- 将 queue claim、最新正文读取、Turn/canonical 创建、delivered 审计和版本推进合并为同一个 `BEGIN IMMEDIATE` 事务；原实现把 claim/deliver 放在 `start_turn` 之后，edit/remove 可在间隙形成幽灵事件或错误正文。
- 为 Inbox 建立 SQLite 状态迁移 trigger，并补 v7→v8 幂等 migration。非法状态回跳无法再绕过 Python 分支直接写入。
- 恢复启动时将无 Turn 的 `claimed` steer 原子降为 FIFO queue；stale poll 的 CAS 失败不再追加 steer history；worker/build 失败将已交付 item 置 blocked，Retry 以新的 item version 创建可审计尝试。
- 用每会话非阻塞完成回调锁和循环式单消费者替换可能重入/递归的 `_after_turn_finished`；只会在 terminal 后消费一条 queue item。
- 修复前端 mutation 只在成功时刷新造成的 409 旧快照问题：无论成功、冲突还是网络失败均重取 Host snapshot，draft 仅在 Host ack 后清空。QueueDock 新增 claimed 状态，并把 100 条展开视图限制为每页 50 条、可前后切换。
- 取消了与 Queue/Steer 无关的 Markdown renderer 改动；保留 profile/reasoning-effort 传递，因为 queued turn 需携带用户选定的 effort。

### 验收证据

- Q1-Q4、S1-S4、U1-U4、F1-F5、A1-A4：逐项源码审查与新增 SQLite transition、atomic claim、claim/edit barrier、remove/claim、restart demote、reentrant terminal callback、失败启动/Retry、Steer safe-point 测试闭环。`AgentLoop` 只在 READY-before-request 与 final-before-terminal 调用 `_poll_steer`，并在 CAS 失败时拒绝注入。
- T1：定向 Python 验证 `tests/test_conversations.py tests/test_streaming.py tests/test_conversation_api.py` 为 73 passed；新增竞态测试使用 `threading.Barrier`，不依赖 sleep 猜测时序。
- T2：Vitest 全量 54 passed，其中 QueueDock 覆盖 100 条队列折叠、展开及 50 行分页窗口；Playwright 10 passed，busy 流程覆盖连续 Queue 3 条、Steer、Stop、reload 和自动逐轮消费。
- 视觉/手动复验：production build + 本地 Fake Model 中实测 busy 时 Queue/Steer/Stop 同组、Steer caption “已插入当前轮”以及下一条 FIFO 消费；人工查看 [宽屏队列证据](task_006_evidence/busy-queue-1280x720-zh.png) 与 [390px 窄屏证据](task_006_evidence/busy-queue-390x844-zh.png)。截图已裁去包含临时绝对路径的 app header。
- 最终门禁：`uv run ruff format --check .`（111 files）、`uv run ruff check .`、`uv run pytest -q`（325 passed, 4 skipped）、`npm run typecheck`、`npm run lint`、`npm test -- --run`（54 passed）、`npm run check:api`、`npm run build`、`npm run test:e2e`（10 passed）、`uv build` 和 `git diff --check` 全部通过。依赖清单与 lockfile 未变，按 handoff 未重复执行 audit。

## 8. 状态：Master 验收整改通过，待归档
