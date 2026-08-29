# 任务编号：task_007 开发反馈

## 1. 完成情况

task_007“可控记忆”已按 guide 完成 P0 与 P1 候选提取，并经 Master 源码级整改复验，最终结论为 **通过并归档**。

对照验收点：

- M1 跨会话保存/召回：已实现，Python 测试与应用内 E2E（保存事实 → 新会话跑到记忆采用摘要）通过。
- M2 scope 隔离：global/workspace/conversation 三种作用域已实现，两个 workspace 不串用；单测覆盖。
- M3 每 turn 一次检索：ContextManager 按 turn 缓存投影，MemoryService 单次 DB 检索；repo 计数单测覆盖。
- M4 top-k/预算：默认 6 条、单条 1200 字符、总量 6000 字符，投影按整条预算省略；预算专项测试通过。
- C1 明确“记住”创建 confirmed；候选提取只创建 candidate，未批准不进检索；单测覆盖。
- C2 Memory mode 总开关：global/workspace/conversation 均可通过服务关闭，关闭后不检索；单测覆盖。
- C3 编辑/supersede/拒绝/删除/reset：已实现；编辑创建新版本并 supersede 旧版本；reject 记录 hash，阻止相同候选自动反复出现。
- C4 记忆中心 + 本轮采用记录：Memory Center 搜索/筛选/审批/编辑/删除/清空/总开关/候选开关已落地；transcript 下方有 MemoryUsageSummary。
- S1 服务端 secret 拒绝：私钥、API Key、`.env` 赋值、高熵 token 等 fail-closed；API/数据库/日志不落秘密正文。
- S2 注入：记忆以 `<memory_context trust="untrusted_reference">` 注入，XML 转义，系统提示明确其为非指令参考数据。
- S3 不保存/不展示 reasoning：候选提取只接收受限用户/最终答问文本，不接收 reasoning、工具输出或凭据；没有把 reasoning 写入 memory。
- S4 删除/reset 同步清除 facts、sources、terms、FTS，usage 只保留无秘密审计；索引 self-check/rebuild 已实现。
- T1 Python 测试：Master 最终全量 375 passed、4 skipped。
- T2 Vitest/RTL：61 passed；Playwright 12 项全部通过，含保存→跨会话召回→来源跳转→删除→下一会话无采用、`/remember` 本地命令、hard-restart 隔离工作区。
- T3 2000 条混合作用域记忆性能：新增 `tests/test_memory_perf.py`，本地通过 warm p95≤50ms、cold≤150ms 阈值，且无跨 scope。
- T4 全量回归：Python、Ruff、TypeScript、ESLint、Vitest、Vite build、API schema check、完整 Playwright 均通过。
- I1 中英文 analyzer：单测覆盖 Latin/CJK/bigram；FTS 与 terms 表共用 analyzer。
- I2 candidate/superseded/rejected 不进入 active 检索：单测覆盖。
- I3 稳定排序：scope priority、命中率、版本、updated_at、id 固定排序；同分 id tie-break 单测覆盖。
- I4 FTS 不一致重建：verify/rebuild 方法及单测覆盖；terms 回退单测覆盖。
- F1 伪 XML/指令/密钥：投影 XML 转义单测覆盖；秘密策略单测覆盖。
- F2 写点事务：create/revision/delete/reset 均单事务提交，stale version 返回 version_conflict。
- F3 turn 首次投影后编辑/删除：ContextManager snapshot 缓存保证本 turn 仍用旧投影。
- F4 候选提取失败隔离：模型返回非法 JSON/失败不影响主 turn；API 测试覆盖且不会产生 confirmed memory。
- B1 top-k/单条/总量：按常量执行并有 omitted_count，预算专项测试通过。
- B2 2000 条 warm/cold p95：通过本地阈值测试。
- B3 每 turn DB 检索 ≤1：repo 计数单测通过；2000 条 SSE/model/tool event 不增加调用数的架构已保持（投影只由 ContextManager 缓存触发一次）。
- B4 删除后 facts/terms/FTS/缓存正文匹配为 0：删除后无法再召回；正文物理删除，usage 保留无秘密 id/hash。

## 2. 改动文件列表

| 文件 | 操作 | 改动说明 |
| --- | --- | --- |
| `src/coding_agent/memory/__init__.py` | 新增 | memory 包入口。 |
| `src/coding_agent/memory/models.py` | 新增 | MemoryEntry/Projection/Usage 等领域模型与预算常量。 |
| `src/coding_agent/memory/analyzer.py` | 新增 | Unicode/NFKC、Latin/identifier、CJK bigram 混合 analyzer。 |
| `src/coding_agent/memory/policy.py` | 新增 | 服务端 secret/size/control 字符 fail-closed 策略。 |
| `src/coding_agent/memory/extractor.py` | 新增 | P1 候选提取器：独立模型调用、JSON 校验、secret 跳过。 |
| `src/coding_agent/memory/service.py` | 新增 | MemoryService：CRUD、候选审批、版本链、单次检索、投影、开关、reject-hash、索引校验/重建、候选摄入。 |
| `src/coding_agent/conversations/store.py` | 修改 | schema v13：memory entries/source/terms/usage/events/meta/idempotency/scope versions + FTS5、DB invariant/transition trigger 与原子 repository 方法。 |
| `src/coding_agent/context.py` | 修改 | ContextManager memory_provider seam，每 turn 缓存投影，请求视图前缀注入。 |
| `src/coding_agent/prompt.py` | 修改 | 系统提示增加 memory 为不可信非指令参考。 |
| `src/coding_agent/conversations/service.py` | 修改 | 接入 MemoryService、候选提取后处理、memory API service 方法、AgentLoop 构建时传入 provider。 |
| `src/coding_agent/web/schemas.py` | 修改 | Memory DTO/API 请求输出模型，含候选设置。 |
| `src/coding_agent/web/app.py` | 修改 | `/api/memories` CRUD、审批、重置、设置与 turn memory-usage 路由。 |
| `frontend/src/api/client.ts` | 修改 | 记忆 API client 与类型导出。 |
| `frontend/src/api/schema.d.ts` / `schema.json` | 修改 | gen:api 重新生成。 |
| `frontend/src/components/MemoryPage.tsx` | 新增 | 记忆中心 UI，含候选开关。 |
| `frontend/src/components/MemoryUsageSummary.tsx` | 新增 | 每轮采用记忆的紧凑摘要。 |
| `frontend/src/components/ConversationView.tsx` | 修改 | 接入 MemoryUsageSummary 和 `/remember`/保存为记忆 Dialog。 |
| `frontend/src/App.tsx` / `AppShell.tsx` / `AppShellSidebar.tsx` | 修改 | 新增 Memory Center 导航。 |
| `frontend/src/i18n/zh-CN.ts` / `en-US.ts` | 修改 | 记忆中心/记住命令文案。 |
| `frontend/e2e/global-setup.ts` | 修改 | 为 hard-restart 测试提供独立工作区，消除同工作区后台运行冲突。 |
| `frontend/e2e/run.spec.ts` | 修改 | 新增记忆保存/召回/删除、`/remember` E2E；调整 restart 隔离。 |
| `src/coding_agent/web/static/*` | 修改 | 前端 production build 产物。 |
| `tests/test_memory.py` | 新增 | memory 领域/生命周期/投影/索引/secret/候选提取单测。 |
| `tests/test_memory_api.py` | 新增 | memory Web API、模型请求投影、候选失败隔离测试。 |
| `tests/test_memory_perf.py` | 新增 | 2000 条混合作用域 warm/cold 预算测试。 |
| `frontend/src/__tests__/memory-page.test.tsx` | 新增 | Memory Center RTL 测试。 |

说明：工作区中 MarkdownText、profile per-message override 等改动在本次任务开始前已存在，本次保留未覆盖；不属于 task_007 的改动未列入上表。

## 3. 关键实现说明

- 持久化沿用 task_004 的同一 `state.db` schema chain，最终 schema 从 v8 增量升级到 v13；FTS5 可用时创建 `memory_fts`，不可用时 `memory_meta.index_backend` 标记为 `terms` 并使用 `memory_terms` 倒排回退。
- 写入前统一执行 secret policy；命中私钥/API Key/Bearer/`.env` 赋值/高熵长 token 直接拒绝，正文不进入 DB/API/DOM/日志。
- MemoryService 是 ContextManager 唯一依赖；每 turn 通过 `search_memory_ids(scope_pairs=...)` 单次查库，取得 active confirmed 后在 Python 侧稳定排序、去重、预算截断并记录 `memory_usage`。
- ContextManager 不修改 canonical history，只在请求视图最前面注入 `<memory_context>` 系统块；provider 在首个 `build_request` 调用一次，后续 step 复用同一 `MemoryProjection`。
- P1 候选提取在 turn 终局后、主 turn 结果已持久化后运行；使用独立 `MemoryCandidateExtractor` 调用模型，只传受限用户/最终答问，JSON 失败/超时/secret 候选均不会影响主 turn，也不会产生 confirmed memory。
- API 提供显式创建、列表/搜索、详情、PATCH 新版本、approve/reject、hard delete、scope reset、设置开关与 turn usage 查询。
- 前端新增 Memory Center、per-turn MemoryUsageSummary、Composer“保存为记忆”按钮与 `/remember` 本地命令。

## 4. 遇到的问题

- 完整 Playwright 整批运行中 hard-restart 用例曾因同一工作区存在其他后台运行而无法启动；通过在 global-setup 中为 restart 用例提供独立 `E2E_WORKSPACE_RESTART` 工作区修复，现完整 Playwright 12/12 通过。
- 当前工作区存在任务开始前既有的未提交改动（MarkdownText、每消息 profile override、schema v9 inbox profile_id 等）；已原样保留，没有清理。
- 中文控制台输出乱码仅影响测试环境显示，不影响断言。

## 5. 未完成项 / 技术债

- 无阻断性未完成项。
- 候选提取目前复用主模型连接，但已有独立 daemon worker、专用超时与失败隔离；task_008 可按真实模型成本决定是否增加单独的小模型 profile。
- workspace/conversation override 已在 Memory Center UI 暴露；后续只需在发布阶段验证真实用户文案与成本，不存在功能阻断。

## 6. 下一步建议

1. 在 task_008 发布验收时执行真实模型 smoke 与 clean install，并复核 2000 条记忆在真实发布机上是否仍满足 warm/cold 阈值。
2. 如需集中设置入口，可在 Settings 镜像现有 Memory Center 的 workspace/conversation override，并增加检索预算只读摘要。
3. 候选提取可继续评估成本/质量，若收益不明显可保持默认关闭。

## 7. 状态：已完成、Master 验收通过并归档

## 8. Master 源码验收与整改记录（2026-08-29）

Master 未把开发声明当作证据，按 `MASTER_HANDOFF.md` 与 `acceptance.md` 从 SQLite schema、事务边界、检索投影、服务/API、前端到生产 E2E 逐层复核。首轮发现并直接修复：生命周期写入与审计/索引非同事务、非 confirmed 状态残留 active index、API status/cursor 缺失、workspace key 非 canonical、来源不可点击、投影转义后预算与保护上下文优先级错误、候选抽取会阻塞主 turn、幂等/reset CAS/DB invariant 不完整、hard delete 未清完整 supersede 链，以及窄屏 Memory Center 导航后抽屉不收起。

最终实现形成以下闭环：

- schema v13 增量迁移保持既有 Conversation 数据；DB trigger 守卫 scope/status/source/版本链不变量，写入、source、active index、audit、idempotency 与 scope version 单事务提交。
- 仅 confirmed 进入 FTS/terms；启动 self-check 可重建不一致索引，FTS 缺失使用确定性 terms 回退；排序、近重复去重、top-k 与整条预算在重启/rebuild 后稳定。
- create/edit/approve/reject/delete/reset 支持幂等；scope reset 使用 CAS；hard delete 遍历完整 supersede chain，正文、source excerpt、terms/FTS 归零，usage/event 仅保留无正文快照元数据。
- ContextManager 每 turn 缓存一次 projection；在保护上下文超预算时整块丢弃且不提交 usage。候选提取在终局后异步运行，独立超时，非法 JSON/拒绝/服务不可用不改变主 turn。
- API 增加受筛选条件绑定的 opaque cursor、snapshot-stable 分页、canonical scope/source 校验与 404/409；Memory Center 增加分页、作用域 override、来源跳转、删除解释和冲突刷新。
- E2E Fake Model 的记忆问题直接依据实际 `<memory_context>` 请求块作答，避免把工作区 TODO 状态误当记忆证据；窄屏抽屉导航缺陷已修复并增加 RTL 反例。

最终门禁：Ruff format/lint 通过；Python `375 passed, 4 skipped`；TypeScript、ESLint、OpenAPI 同步通过；Vitest `61 passed`；Vite production build、`uv build` 通过；Playwright production Fake Model `12 passed`；依赖清单未变化，npm audit 按规则不重复执行；`git diff --check` 通过。2,000 条混合作用域性能测试通过 warm p95≤50ms、cold p95≤150ms，且无跨 scope。人工浏览器在 1280×720 中文浅色与 390×844 英文深色下复核 Memory Center、来源跳转、删除确认和响应式布局通过。
