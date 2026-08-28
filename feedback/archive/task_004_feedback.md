# 任务编号：task_004 开发反馈

## 1. 完成情况

按 task_004 `plan.md` / `acceptance.md` 实现了核心后端与前端会话壳，但未达到全部验收点的“已完成”标准，因此状态记为 **进行中**。

已完成：

- D1：Conversation / Turn / Run / canonical item / public event 领域类型与文档（`conversations/domain.py`）。
- D2：`CODING_AGENT_HOME/state.db` 标准库 SQLite、外键、WAL、busy timeout、显式 schema version 与备份入口。
- D4 / F2：启动时将 running/starting turn 恢复为唯一 INTERRUPTED，并为 pending tool group 生成确定性 synthetic tool result，改为 recovered，不重放命令。
- M1 / M2：同一 conversation 多轮 canonical 上下文，两会话隔离已验证。
- M4 / 并发：单会话单 active turn、canonical workspace lease、有界 ThreadPoolExecutor。
- L1 / L2 / L3：create / list / paginate / read / rename / archive / unarchive / delete / start / cancel turn API 与自动标题、版本冲突。
- L4（部分）：旧 `/api/runs` 与 CLI 继续可运行；当前仍是旧 RunController 单 run 适配，未完全收敛为 ConversationService 单一实现。
- F3 / F4：idempotency key（进程内）与 stale version 409。
- ChangeSet/Artifact：tool-confirmed write/edit 捕获 before/after、净变化、CAS artifact、diff、change/preview API；非 Git workspace 的 write/edit 有证据。
- UI 壳：左侧 ConversationSidebar（新建/搜索/列表）、ConversationView（turn 列表、发起 turn、TurnChangeSummary）、DiffViewer/ArtifactPreviewPane；右栏在会话模式默认不挂载。
- 测试：新增 `tests/test_conversations.py`（8 项）与 `tests/test_conversation_api.py`（4 项）。

未完成/未通过：

- U1 / U2 / U5：左栏 rename/archive/delete 操作、归档管理页、键盘/焦点管理、危险删除 Dialog 未实现。
- U4 / U6 / U7：桌面右栏在会话模式已关闭，但 ArtifactPreview 目前是 `ConversationView` 内的 overlay，不是 AppShell 条件右栏；窄屏 drawer/focus trap/返回焦点未完整。
- U3 / About：存储与隐私边界说明未同步到 About 页。
- Q2：未新增两会话/三轮/后台切换/重启恢复的 production Playwright E2E。
- C6 / C7：Git dirty baseline、run_command generic probe、coverage 分级、超预算不阻塞主 turn 未实现。
- S3 / S4 / S5：单文件/单 turn artifact 大小上限、删除 GC、损坏 blob fail-closed 的完整边界只部分实现（store 有基础 CAS/引用，GC 未实现）。
- A3：旧 `/api/runs` 没有改为 ConversationService compatibility path，仍存在第二套 in-memory worker/event 实现。
- F1 / F5：事务写点故障注入、migration 失败回滚/备份恢复未完整覆盖。

## 2. 改动文件列表

| 文件 | 操作 | 改动说明 |
| --- | --- | --- |
| `src/coding_agent/conversations/` | 新增 | domain、SQLite store、CanonicalJournal、RuntimeRegistry、ConversationService |
| `src/coding_agent/artifacts/` | 新增 | 内容寻址 artifact store |
| `src/coding_agent/changes/` | 新增 | ToolChangeCollector、diff builder |
| `src/coding_agent/agent.py` | 修改 | 新增 `run_turn(history=...)` 与 canonical journal 注入 |
| `src/coding_agent/tools/executor.py` | 修改 | 新增 ToolExecutionObserver before/after 钩子 |
| `src/coding_agent/web/app.py` | 修改 | conversation/turn/change/preview API 与错误码 |
| `src/coding_agent/web/schemas.py` | 修改 | Conversation/Turn/ChangeSet/Preview DTO 与请求模型 |
| `src/coding_agent/web/server.py` | 修改 | GUI server 接入 ConversationService、shutdown |
| `src/coding_agent/web/openapi_json.py` | 修改 | 临时 home 生成 OpenAPI，避免污染用户目录 |
| `frontend/src/api/client.ts` | 修改 | conversation/turn/change/preview API 客户端与类型 |
| `frontend/src/api/schema.*` | 修改 | 重新生成 OpenAPI TS 类型 |
| `frontend/src/components/ConversationSidebar.tsx` | 新增 | 左侧会话列表/搜索/新建 |
| `frontend/src/components/ConversationView.tsx` | 新增 | 会话 turn 列表、发起 turn、变更摘要与预览 |
| `frontend/src/components/TurnChangeSummary.tsx` | 新增 | 净变更摘要 |
| `frontend/src/components/ArtifactPreviewPane.tsx` | 新增 | 只读文件/差异预览 |
| `frontend/src/components/DiffViewer.tsx` | 新增 | 有界行级 diff 渲染 |
| `frontend/src/App.tsx`、`AppShell.tsx`、`AppShellSidebar.tsx` | 修改 | 接入会话侧栏与条件右栏 |
| `frontend/src/i18n/*` | 修改 | 新增会话/预览文案 |
| `tests/test_conversations.py` | 新增 | repository/service 多轮、锁、恢复、ChangeSet 测试 |
| `tests/test_conversation_api.py` | 新增 | conversation API 生命周期/幂等/冲突测试 |

## 3. 关键实现说明

- canonical history 继续以 AgentLoop 自研 `CanonicalHistory` 为运行时事实，`CanonicalJournal` 只在 service 注入时把新增消息按 `canonical_groups` 原子持久化；provider 请求每次都由 `ContextManager` 从 committed/recovered 历史投影，不会把公开 DTO 反向当模型历史。
- `AgentLoop.run_turn(task, history=...)` 保留原有 `run(task)` 兼容；已有 system prompt 不重复写入。
- 每个 conversation 同一时间最多一个 active turn（SQLite partial unique index + service 前置检查）；不同 conversation 同 canonical workspace 由 `RuntimeRegistry` 租约拒绝，返回稳定 `workspace_busy`。
- 重启恢复：`recover_active_turns()` 把所有 starting/running 置为 `INTERRUPTED`，`recover_pending_groups_for_turn()` 给缺失工具结果补 synthetic `PROCESS_RESTARTED` 结果并转 `recovered`。
- ChangeSet：`ToolChangeCollector` 作为 `ToolExecutor` observer 捕获 WRITE 工具调用前后的字节/hash，finalize 时按首个 before -> 最后 after 合并净 diff；失败 outcome 不进入 after。
- Artifact 仅通过 `change_id` 访问，返回结构化 `lines`，前端用文本节点渲染，不使用 `dangerouslySetInnerHTML`。

### 验证命令与结果

```powershell
uv run pytest -q                     # 260 passed, 4 skipped（历史 255+新增；个别旧跑偶发 shutdown 超时复跑通过）
uv run ruff format --check .         # 102 files already formatted
uv run ruff check .                  # All checks passed!
uv run python -m py_compile ...      # 通过
npm run typecheck                    # 通过
npm run lint                         # 通过
npm test -- --run                    # 44 passed
npm run build                        # Vite production build 成功
npm run check:api                    # API types up to date (no diff)
```

具体新增测试：8 项 conversations 单测 + 4 项 conversation API 测试；未运行真实模型，未运行新增 Playwright E2E（本任务未完成该场景）。

## 4. 遇到的问题

- 最初用 `time.strftime("%f")` 在 Windows 不合法，已改为 `datetime.now(timezone.utc).isoformat()`。
- `ConversationService.start_turn` 原先在 worker 启动后再置为 running，竞态可能覆盖 terminal，已改为启动前置为 starting。
- 创建文件 ChangeSet 被误标 binary，已修正 binary 判定（仅实际存在且解码失败的一侧算 binary）。
- `openapi_json` 使用临时目录生成 schema，但 SQLite 连接未关闭导致 Windows 无法清理临时目录，已显式 `repository.close()`。
- 旧 `test_shutdown_cancels_running_worker` 有一次超时失败，单测复跑通过，未发现与本任务改动有关。

## 5. 未完成项 / 技术债

1. 未实现 Git/command probe：ChangeSet 仅覆盖 tool-confirmed write/edit，coverage 固定 `confirmed_only`，C6/C7 未满足。
2. 未实现 artifact GC、大小预算和损坏 fail-closed 的完整测试。
3. 前端会话壳仍是“先保留旧单 run 页 + 新增会话页”的过渡形态：rename/archive/delete、归档页、窄屏 drawer/focus 管理未完成。
4. 旧 `/api/runs` 仍是独立 RunController 单 run 实现，未改为 ConversationService compatibility adapter；A3 不通过。
5. idempotency 目前是进程内 map，进程重启后重复请求不保证幂等；应落库到 turns 表或独立 idempotency 表。
6. `set_turn_terminal`、canonical group 写入与 turn 创建不在同一 Unit of Work，故障注入下仍可能出现“turn 存在但无初始 canonical”窗口。

## 6. 下一步建议

1. 先完成批次 D：将旧 `/api/runs` 改为 ConversationService compatibility path，删除旧 RunController 第二套 worker/event 事实源，并补充 A3 源码断言。
2. 落地 Git/command probe 与 `coverage` 分级，使 C6/C7 可验证。
3. 完成 ArtifactStore 的预算、GC、corrupt fail-closed 测试（S3/S4/S5）。
4. 前端完成会话生命周期操作、归档页、右侧 condition pane、responsive drawer/keyboard accessibility（U1/U2/U5/U6/U7）。
5. 补充 production Playwright E2E：两会话隔离、三轮追问、后台切换、重启恢复、归档/删除（Q2 与交付证据）。
6. 将 idempotency 与迁移事务统一收口，补齐 F1/F5 故障注入。

## 7. 状态：进行中

（说明：本轮实现了核心数据平面、多轮 API、ChangeSet/Artifact 后端与前端会话壳，但上述 U/C/S/F/A3/Q2 项未全部闭环，故不标记为已完成。）

## 8. Master 源码验收与直接整改（2026-08-28）

用户明确授权 Master 直接修复验收发现的问题，以“发现 → 修复 → 定向反例 → 复验”的方式减少往返。本节保留最初开发反馈作为审计历史，并以当前工作树的源码和可重复证据为最终事实。

### 8.1 首验发现

1. SQLite 初版只有部分投影事实，turn 初始 canonical 组、幂等键和生命周期投影没有完整事务边界；迁移失败、并发重复请求和崩溃恢复证据不足。
2. `run_command` 变更无法覆盖非 Git 工作区，Git dirty baseline、HEAD 切换和探测预算超限缺少 fail-closed 语义；Artifact CAS 缺少压缩格式标记、损坏校验、精确引用计数与启动回收。
3. 生产旧 `/api/runs` 仍可能落入进程内单 run 状态；会话 API、旧 API 和 GUI 没有完全收敛到同一持久执行路径。
4. 前端只是过渡会话壳：缺 rename/archive/restore/delete、危险操作确认、真正条件右栏、窄屏 drawer、独立 draft/scroll、历史 turn 变更摘要和重启中断恢复文案。
5. 原 E2E 仍以旧“新任务/运行详情”为主，未证明两会话隔离、三轮追问、后台运行、归档删除、历史文件预览和真实进程重启。

### 8.2 已完成整改

- 数据层升级为 schema v3。`conversation_events` 是 Conversation 生命周期日志；`turns`、`canonical_groups/items`、`public_events`、`turn_change_sets/file_changes`、`artifact_blobs/refs` 通过 FK、唯一约束和 partial unique index 保持一致。start turn 与 system/user canonical 初始组在一个 `BEGIN IMMEDIATE` 事务中完成，durable idempotency 由 `(conversation_id, idempotency_key)` 唯一索引保证。
- migration 先在 `backups/state-v{n}-*.db` 留备份，再在事务内升级；故障注入证明失败后 schema version 与原数据不半升级。启动扫描把 active turn 唯一恢复为 `interrupted`，pending tool call 补确定性 synthetic result，明确副作用未知且绝不重放。
- `ConversationService` 成为生产 Web 唯一执行编排；旧 `/api/runs` 由 `ConversationRunAdapter` 投影同一持久 conversation/turn/event。task_002 的 `RunController` 类型仅保留给旧嵌入测试兼容，生产 server 不再通过它启动第二个 worker/history/event 事实源。
- `ToolChangeCollector` 同时覆盖成功 write/edit 与有界 command workspace probe，合并同路径首个 before→最终 after，并识别无歧义 rename。已有 dirty 文件不计入本轮；HEAD 改变、命令失败或文件/字节/时间预算超限时 coverage 降级为 `incomplete`，工具确认的变更仍保留。
- ArtifactStore 使用 SHA-256 CAS、1 MiB 单文件和 20 MiB 单 turn 预算、20,000 行 diff 上限；压缩内容带 `CAZ1` 格式头并校验原文 hash。删除会话事务性删除 refs，仅在最后一个引用消失后回收正文；启动 reconciliation 可重试地清理数据库外孤儿。
- 应用壳已收敛为左侧 ConversationSidebar、中间连续 transcript、按需右侧 ArtifactPreviewPane。左栏完成新建、搜索/分页、workspace 分组、运行徽标、rename、归档管理、恢复和永久删除；桌面右栏关闭时不挂载，窄屏侧栏/预览均为可访问 drawer。
- 每个 terminal turn 末尾固定显示净 ChangeSet；modified/created/deleted 默认分别进入 diff/after/before，支持 before/after/current、文件前后切换、Escape/关闭、焦点恢复、divergence 和 corrupt/binary/too-large 等独立状态。会话切换会关闭不属于新会话的 preview。
- draft、scroll、follow 状态按 conversation 存入 sessionStorage；切换不会取消后台 worker。Composer 在运行中保持可编辑但不伪装成 Queue，真正 Queue/Steer 仍按计划属于 task_006。
- Vite/esbuild 开发工具链升级到 Vite 7 / Vitest 3，官方 npm registry 审计由 5 个旧开发依赖告警降为 0 vulnerabilities。

## 9. 架构与交付证据

### 9.1 schema 与迁移

```text
conversations
  ├─< conversation_events          append-only 生命周期日志
  ├─< turns                        UNIQUE(conversation_id, ordinal)
  │    ├─< canonical_groups ─< canonical_items
  │    ├─< public_events            UNIQUE(run_id, event_seq)
  │    └─1 turn_change_sets ─< turn_file_changes
  └─< artifact_refs >─ artifact_blobs

partial unique: turns(conversation_id) WHERE state IN (pending, starting, running)
durable idempotency: UNIQUE(conversation_id, idempotency_key) WHERE key IS NOT NULL
当前版本: 3；备份目录: CODING_AGENT_HOME/backups/
```

迁移清单：v1 建立持久会话/canonical/event/change/artifact 主表；v2 增加 durable turn idempotency 唯一索引；v3 增加 `conversation_events` 并回填 create 投影基线。fresh install 直接生成 v3，旧库逐版本事务升级。

### 9.2 多轮、并发与恢复

- 三轮 Fake Model/Playwright 夹具在同一 conversation 中形成三个 ordinal；刷新后仍有三轮。Python 模型请求记录证明 A 的第三轮包含 A 的前两轮 canonical user/assistant facts，B 的请求中不含 A 的文本。
- 同 conversation 由数据库 partial unique + runtime registry 双重拒绝；同 canonical workspace 由 lease 拒绝；两个不同 workspace 可独立登记运行，ThreadPoolExecutor 的 `max_workers` 控制真实同时执行数。新增 max_workers=1 定向测试证明第二 workspace 只排队、不突破上限。
- production 重启夹具直接终止 GUI 进程，用同一 home/port 启动新进程；页面恢复 `running → interrupted`，显示“未完成的模型请求、命令和文件操作不会自动重放”，且不存在 final answer。

### 9.3 视觉证据

- `feedback/task_004_evidence/conversation-success-1280x720-zh-light.png`：左侧会话、中间 transcript、turn 末尾文件摘要，右栏未打开且不占宽。
- `feedback/task_004_evidence/file-preview-1280x720-zh-light.png`：三栏状态、历史 `hello.py` unified diff 与文件审查导航。
- `feedback/task_004_evidence/delete-confirm-1280x720-zh-light.png`：有焦点管理的永久删除确认，并明确不删除工作区文件。
- `feedback/task_004_evidence/narrow-320x720-en-light.png`：320 px 英文侧栏 drawer，无横向溢出。
- `feedback/task_004_evidence/settings-1280x720-zh-dark.png`：1280×720 深色全屏设置与会话侧栏。
- `feedback/task_004_evidence/restart-interrupted-1280x720-zh-light.png`：真实硬重启后的 interrupted 产品状态与无重放说明。

## 10. 最终验证结果

- Python 全仓基线：`uv run pytest -q` → 272 passed、4 skipped；随后新增的分页插入、全局 worker 上限和探测边界 3 项定向反例全部通过。4 个 skip 均为当前 Windows 无符号链接创建权限的既定条件跳过。
- Ruff：`uv run ruff check .` 通过，`uv run ruff format --check .` → 105 files already formatted；`git diff --check` 无错误，仅 Windows CRLF 提示。
- 前端：11 files、46 tests 全部通过；typecheck、ESLint、OpenAPI generated types 与 production Vite build 全部通过。
- Production Playwright：完整 Task 4 矩阵 5 passed，覆盖文件摘要/右栏、两会话后台切换、三轮刷新、归档恢复删除、320 px/i18n/About 和硬重启恢复。工具链升级后遵照用户要求只做影响面验证：核心预览/两会话场景与 settings/narrow 定向场景通过，不重复执行无关矩阵。
- 供应链/打包：官方 registry `npm audit --audit-level=high` → 0 vulnerabilities；`uv build` 成功生成 sdist 与 wheel，最终静态资源已随 Python package 重建。
- 人工源码复核：事务边界、CAS 引用/GC、command probe、层级 preview 归属校验、生产 legacy adapter、React 状态所有权和五张主流程/一张重启截图均与自动化结论一致。

## 11. 最终状态：通过，已归档

task_004 的持久多轮、会话生命周期、后台隔离、崩溃恢复、逐轮 ChangeSet、按需文件审查和产品三栏布局均已闭环；task_005 可在此稳定事实源上继续实现 provider-neutral streaming 与可展示 reasoning summary。
