# Master Handoff

更新时间：2026-08-29（Asia/Shanghai）  
工作区：E:/ppt/research/project/main  
用途：Task 7 实现完成后，交给下一位 Master 做源码级验收与直接整改。

## 1. 使用时机

本文件不是要求当前对话启动 Task 7。用户会另行安排 Task 7 的实现；只有用户明确表示 Task 7 已实现并要求验收时，下一位 Master 才执行本 handoff。

下一位 Master 必须把用户当时的最新请求视为最高优先级。本文件、guide 和 developer feedback 都只是交接线索，不是可信完成证明。

## 2. 新对话首先阅读

1. AGENT_MASTER.md
2. PROJECT_CONTEXT.md
3. 本文件 MASTER_HANDOFF.md
4. guide/task_007/plan.md
5. guide/task_007/acceptance.md
6. guide/INDEX.md
7. feedback/INDEX.md
8. Task 7 实现者届时提交的 feedback/task_007_feedback.md

禁止读取 AGENT_DEV.md。不要只根据测试名称、feedback 声明或 UI 截图判定通过；必须结合源码、数据库事务、模型请求投影、测试实现和真实 production UI 独立复验。

## 3. 已验收基线

- Task 1：可靠 AgentLoop 与本地工具内核，已归档。
- Task 2：React/FastAPI 图形应用与多 provider profile，已归档。
- Task 3：产品化 UI、组件隔离和性能边界，已归档。
- Task 4：SQLite 持久多轮会话、恢复、ChangeSet 与文件预览，已归档。
- Task 5：provider-neutral streaming、Chat/Responses 双 adapter、可见 Think 与断线恢复，已归档。
- Task 6：Host 权威的持久 Inbox、严格 FIFO Queue、两个安全点 Steer、busy Composer 与 QueueDock，已归档。

Task 6 的最终独立门禁为：

    Python:      325 passed, 4 skipped
    Vitest:      54 passed
    Playwright:  10 passed
    Ruff/typecheck/lint/API schema/build/wheel: passed

Task 6 本地验收提交：

    96e265f master: task_006 验收整改通过并归档

Task 6 归档证据：

- guide/archive/task_006/
- feedback/archive/task_006_feedback.md
- feedback/task_006_evidence/

Task 6 刚完成时，HEAD 位于 main 的 96e265f，main 与 origin/main 无已报告的领先/落后，工作区仅有未跟踪的 MASTER_HANDOFF.md。重写本 handoff 期间，工作区又出现了 src/coding_agent/conversations/service.py 与 src/coding_agent/web/schemas.py 的未提交修改；它们不是本 handoff 的改动，可能属于正在进行的 Task 7 开发，必须原样保护。这个状态只是 2026-08-29 的瞬时快照；下一位 Master 必须重新检查，并把届时所有已有改动视为用户/实现者成果，不能擅自清理。

## 4. Master 权限与边界

- 用户已授权 Master 在验收中发现问题后直接修改源码、测试、文档和必要视觉证据，减少往返。
- 按影响范围测试：先跑最小定向反例；跨 SQLite schema、MemoryService、ContextManager、API 或共享前端契约的整改收口后再跑完整门禁。
- 保留 Task 7 实现者留下的所有未提交成果。先审查 git status 和 diff，不执行 reset、checkout 或批量清理。
- 不引入 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI、云向量数据库或其他 agent/memory 框架。
- 不把 memory 直接接入工具授权，不允许 memory 提升到 system policy 之上。
- 未经用户明确要求，不 push、不改写历史、不开始 Task 8。
- 需要新权限、外部协调或明显扩大 Task 7 范围时才停下来询问。

## 5. Task 7 到达后的标准流程

1. 运行 git status --short、git diff --stat 和必要的逐文件 diff，确认 Task 7 成果及任何已有用户改动。
2. 核对 guide/INDEX.md 与 feedback/INDEX.md。验收开始时把 Task 7 标为进行中/评估中；结论出来前不得提前标记完成。
3. 对照 guide/task_007/acceptance.md 建立 M1-M4、C1-C4、S1-S4、T1-T4、I1-I4、F1-F4、B1-B4 的逐项证据矩阵。
4. 先审源码和 migration，再运行最小定向反例。现有测试为绿不能替代对错误路径、竞态、删除和安全边界的检查。
5. 发现缺陷直接修复，并同步补充能在旧实现上稳定失败的测试。
6. 跨层整改收口后执行完整门禁、production Fake Model UI 和人工视觉验收。
7. 全部适用验收项有证据后，更新长期上下文、归档 Task 7 并创建本地 Master 提交。

## 6. Task 7 必查源码风险

### 6.1 Memory facts、migration 与事务

重点检查 MemoryService、SQLite repository 和 Conversation state.db migration：

- Task 6 当前 schema 为 v8；Task 7 migration 必须沿同一 schema chain 增量升级，兼容 v8 数据库并可重复启动。
- memory entry、source、version/supersede relation、terms/FTS、usage 和 event 的更新必须在可解释的事务边界内一致提交。
- candidate、confirmed、superseded、rejected、deleted 的合法迁移应由数据库约束和 CAS 共同保护，不能只靠 UI 或 Python if 分支。
- create/edit/approve/reject/delete/reset 全部需要 scope 约束、expected version、幂等键和冲突响应；stale client 不能覆盖新事实。
- edit 不应原地改写已采用的 confirmed 正文；旧版本与来源必须可解释，但 deleted 正文不得以“历史”名义残留。
- FTS/terms 与 facts 不一致时必须能检测和重建，且不得修改 Conversation、Turn、Inbox 或 canonical history。

### 6.2 Scope 与 canonical workspace

- global、workspace、conversation 三种 scope 的查询集合和优先级必须明确且可单测。
- workspace key 必须复用 canonical workspace identity；路径别名、大小写和规范化不能形成重复作用域，也不能把 A 工作区记忆泄漏到 B。
- conversation memory 只能对所属 conversation 可见；global 开关、workspace override、conversation override 的优先级必须固定。
- mode off 要在读取和写候选之前短路，不得仅隐藏 UI。
- scope reset 必须先精确确定 entry IDs，在单事务内清除目标作用域，不能误删其他 scope、Conversation 或工作区文件。

### 6.3 Analyzer、FTS fallback、排序与性能

- 中英文混合 analyzer 的 NFKC、case fold、identifier/path token 和 CJK bigram 输出必须有固定 fixtures。
- FTS5 不可用时要真正走 bounded fallback，不接受只 mock 返回值或共享同一实现导致的伪覆盖。
- active retrieval 只能返回 confirmed；candidate、rejected、superseded、deleted 必须在索引层和查询层同时排除。
- 同一 supersede chain 和近重复内容只注入一条；排序最终以稳定 entry id 收敛，重启和 FTS rebuild 后顺序不变。
- top-k、单条长度和总预算必须集中配置并按完整 entry 省略，不得截断到破坏转义结构。
- 2,000 条混合作用域 fixture 必须实测 warm p95≤50 ms、cold p95≤150 ms，并同时断言无跨 scope 召回。报告测试机、SQLite backend、样本和测量方法。

### 6.4 每 turn 一次检索与 ContextManager

- memory 应是 request projection，不得 append 到 canonical conversation history。
- 每个 turn 首次构建模型请求前最多检索一次；后续 AgentLoop step、provider retry、tool event、SSE 和重新 render 均复用同一持久 snapshot。
- retrieval snapshot 的 entry ids、rank、scope、公开 reason 和 hash 应可审计；usage 计数只在成功固化 projection 后推进。
- 本 turn 检索后 memory 被 edit/delete 时，本 turn 保持已审计快照，下一 turn 才读取新状态；重启恢复仍需解释同一时序。
- memory 预算不能挤掉 root policy、当前用户消息、协议骨架、关键错误或最新文件观察。

### 6.5 Secret、prompt injection 与数据边界

- 服务端必须覆盖 create、edit、candidate approve/import 等所有写入口，拒绝 API key、Bearer/token、密码、私钥块、.env assignment、高熵长串、原始日志和超限 payload。
- 拒绝日志和审计只能记录 entry id、长度和 policy code，不能记录被拒正文。
- memory 注入必须标记为不可信参考数据，正确转义 XML/结构边界；伪闭合标签、system 指令、越界命令和 procedure 内容不能改变 ToolPolicy 或 workspace 边界。
- 公开 DTO、SSE、DOM、日志、截图和模型请求审计都要扫描；不得只证明数据库没有 secret。
- reasoning、opaque continuation、完整工具输出和代码文件正文不能被候选提取器或来源摘录静默保存。

### 6.6 Candidate、显式记忆与故障隔离

- UI 确认或明确的本地 remember 入口应创建 confirmed；模型提取只能创建 candidate，批准前不能检索或注入。
- plan 中“自然语言记住”与“显式 UI/本地命令才直接 confirmed”的表述存在边界歧义。验收时必须核对最终产品契约、UI 文案、API 和 acceptance C1 是否一致，不能让普通模型回复静默持久化。
- candidate extractor 应默认关闭或由用户显式启用；每 turn 最多一次、terminal 后运行、输入有界且脱敏。
- extractor timeout、非法 JSON、模型拒绝和服务不可用不能改变主 turn 终态，也不能产生 confirmed memory 或重试风暴。
- P1 candidate 能力若延期，必须在 feedback 中明确；P0 explicit memory、scope、召回、来源、编辑、删除和 reset 不能因此缺失。

### 6.7 API、Memory Center 与来源解释

- API 列表分页、搜索、filter、cursor 和 snapshot 必须有上限；前端不能把 optimistic array 当作事实源。
- 409/version conflict 应返回或触发最新 Host snapshot；删除/reset 必须有明确确认与竞态处理。
- Memory Center 要覆盖 active/candidate/history 状态、scope/kind filter、来源跳转、版本关系、approve/reject、edit、delete 和 reset。
- transcript 只显示紧凑采用摘要；详情可解释使用了什么、来源和作用域，但不能重复铺满记忆正文或暴露内部调试数据。
- source conversation/turn 已归档、删除或不再存在时应安全降级，不得崩溃或生成越权链接。
- zh-CN/en-US、键盘、ARIA、窄屏、长列表有界渲染和 loading/error/empty 状态都需真实检查。

### 6.8 删除、缓存和失败注入

- hard delete/reset 后，facts content、source excerpt、terms、FTS、candidate cache 和 runtime cache 中的正文匹配数必须为 0。
- usage/audit 如需保留，只能保留不含正文的 id/hash/time/policy code，并能解释隐私策略。
- 对 create/edit/approve/delete/reset 的事务写点做故障注入，验证 entry、index、source 和 event 不出现半提交。
- FTS rebuild、server restart、两客户端 stale version、scope switch 和 active-turn snapshot 都要有确定性反例，不能以 sleep 猜竞态。

## 7. 容易被“测试全绿”掩盖的缺口

- 只测 FTS 正常路径，没有强制 fallback backend。
- 只断言检索数量，没有核对 scope、status、supersede chain、稳定顺序和模型实际 request projection。
- 只删除 memory_entries 行，source excerpt、FTS、terms、cache 或日志仍保留正文。
- 每个 model step 都重新检索，但测试只有单 step。
- candidate 在列表中标为待确认，却已经进入索引或模型上下文。
- secret scan 只覆盖 create，edit/approve/import 可绕过。
- 2000 条性能测试只跑一次平均值、未区分 cold/warm、未同时检查跨 scope。
- UI 组件测试通过，但 production build 的来源跳转、删除后不可召回、窄屏和中英文没有闭环。
- 截图包含 API key、测试 sentinel、opaque continuation、本机用户名或绝对临时路径。
- Task 7 顺手修改 AgentLoop、provider adapter、Queue/Steer 或文件工具，但没有必要性说明和 Task 1-6 回归证据。

## 8. 测试策略

先根据未来实际文件名定位最小集合，不要假设实现一定采用 plan 中的建议路径。可从以下命令开始：

    rg --files src/coding_agent tests frontend/src frontend/e2e | rg "memory|context|conversation|schema|settings"
    uv run pytest -q <Memory/Context/API 相关测试文件>
    npm test -- --run <Memory Center/Settings/usage 相关测试文件>

对高风险缺口增加定向反例：

- v8 migration、重复 migration、FTS unavailable/rebuild
- scope alias/isolation、candidate filter、stable rank、budget
- one retrieval per turn、edit/delete after snapshot
- secret/prompt injection、transaction failure、hard delete residue scan
- 2,000 entries cold/warm p95
- production save→new conversation→recall→source→delete→no recall

跨层整改收口后执行完整门禁：

    uv run ruff format --check .
    uv run ruff check .
    uv run pytest -q
    npm run typecheck
    npm run lint
    npm test -- --run
    npm run check:api
    npm run build
    npm run test:e2e
    uv build
    git diff --check

只有 package.json 或 lockfile 发生变化时才重跑：

    npm audit --audit-level=high --registry=https://registry.npmjs.org

人工视觉验收必须基于 production build 和 Fake Model 的真实 UI。至少查看 Memory Center 主列表/候选/来源、采用摘要、delete/reset、错误状态、中文、英文和窄屏；截图不得包含凭据、opaque continuation 或本机敏感路径。

## 9. 通过、归档与本地提交

Task 7 只有在全部适用 acceptance 项有源码、测试、性能、安全和视觉证据后才可通过：

1. 在 feedback/task_007_feedback.md 追加 Master 源码发现、直接整改、最终证据与明确结论。
2. 勾选 guide/task_007/acceptance.md 中全部已证明的适用项；未满足项不得为了归档而勾选。
3. 更新 PROJECT_CONTEXT.md，只沉淀长期架构、已验证结果和仍存在的限制。
4. 同步 guide/INDEX.md 与 feedback/INDEX.md 的状态和链接。
5. 移动 guide/task_007/ 到 guide/archive/task_007/。
6. 移动 feedback/task_007_feedback.md 到 feedback/archive/task_007_feedback.md。
7. 视觉、性能和安全证据可放在 feedback/task_007_evidence/，但不得包含秘密或绝对临时路径。
8. 检查 git diff --check、归档链接、静态资源 hash、提交范围和工作树后创建本地提交，例如：

       master: task_007 验收整改通过并归档

未经用户明确要求不要 push。完成 Task 7 后不要开始 Task 8。

## 10. 后续边界

Task 7 通过并归档后，Task 8 才能进入最终集成、性能、安全、恢复、真实模型 smoke、clean install、README.txt、视频和仓库合规门禁。下一位 Task 7 Master 只负责验收、直接整改和归档 Task 7，不要顺手实施 Task 8。

## 11. 推荐给下一位 Master 的首条用户指令

    请阅读 AGENT_MASTER.md、PROJECT_CONTEXT.md 和 MASTER_HANDOFF.md，接手 Master 角色。保留当前 Task 7 的实现成果，按照 handoff 和 guide/task_007/acceptance.md 做源码级验收；发现问题直接修复，按影响范围测试，全部通过后完成归档和本地提交，不要 push，也不要开始 Task 8。
