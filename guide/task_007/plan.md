# 任务编号：task_007

## 1. 任务目标

建立本地、可控、可解释的记忆与知识共享模块，使用户明确保存的偏好、项目事实、决策和操作规程能够跨会话复用，同时保持 workspace/conversation 隔离、来源可追溯、内容可编辑删除，并避免把整段聊天记录或敏感信息静默塞入模型上下文。

## 2. 背景与上下文

- 多轮 Conversation 解决的是单个会话的连续上下文，不等同于跨会话长期记忆。
- Codex 与 DeepSeek Harness 的公开设计都把 memory 作为独立、可关闭的能力；DSH 还用 MCP memory 示例说明知识存储不应与核心 AgentLoop 硬耦合。
- “自动记住所有内容”会带来上下文污染、错误事实固化、提示注入和秘密泄漏。首版应优先保证用户控制、来源和删除语义，而不是追求不可解释的自动向量记忆。
- task_004 提供 Conversation/Turn 事实源与 SQLite 事务；task_005 提供模型事件；task_006 提供稳定的多轮交互。记忆层只通过明确接口向 ContextManager 提供有预算的投影。

## 3. 技术约束

- 新增 `MemoryService` 接口，核心运行不依赖云端向量数据库、embedding 服务或 agent 框架；首版采用标准库 SQLite 与 FTS5，FTS5 不可用时必须有确定性的关键词回退。
- 记忆作用域至少包括 `global`、canonical workspace、conversation；默认检索 workspace 记忆，不得把 A 工作区内容带入 B。
- 条目类型至少包括 preference、fact、decision、procedure；字段包括稳定 ID、作用域、正文、来源 conversation/turn、创建与更新时间、状态、置信/确认方式和 supersedes 关系。
- 用户明确说“记住……”时可直接创建 confirmed memory；模型自动提取只能创建 candidate，默认必须经用户批准，不得静默永久写入。
- 冲突内容通过 superseded/active 关系保留历史，不原地覆盖来源；删除后不得继续被检索或注入。
- 检索排序是确定性的组合：作用域匹配、文本相关度、用户确认级别、最近使用/更新时间；严格限制 top-k、单条长度和总字符/token 预算。
- 只注入公开的记忆摘要，不暴露或声称提供模型隐藏思维。每次注入可在运行详情中解释“使用了哪些记忆以及原因”。
- API key、token、密码、私钥、`.env` 值、大段命令输出和未脱敏日志不得进入记忆；服务端负责秘密模式拒绝/脱敏，不能只依赖前端提示。
- Memory mode 可按用户/工作区/会话关闭；关闭时不得检索、自动提取或写入候选。用户可执行单条硬删除与按作用域清空。

## 4. 实现步骤

1. 定义 MemoryEntry、scope、kind、status、provenance、conflict/supersede 与 delete 语义，并完成 SQLite schema/version migration。
2. 实现 MemoryService 的 create candidate/confirm/edit/supersede/delete/search/reset；为写入、检索和删除建立幂等键与审计事件。
3. 实现 FTS5 索引及无 FTS 回退，加入 canonical workspace identity、稳定排序、top-k 和上下文预算。
4. 在 ContextManager 建立独立 `memory projection`：只在每个 turn 的首个模型请求前检索一次，后续 step 复用快照，避免每次工具事件重复查库。
5. 实现显式“记住/忘记”入口和可选的候选提取器；候选提取失败不得影响主 turn 完成，默认不开启未经批准的自动持久化。
6. 新增记忆中心 UI：搜索、作用域/类型/状态筛选、来源跳转、编辑、确认/拒绝候选、删除、清空和总开关。
7. 在运行详情中展示本轮采用的记忆摘要、来源和作用域；普通 transcript 不插入大块技术调试信息。
8. 增加安全、隔离、冲突、删除、重启恢复和真实多会话召回 E2E；用恶意提示和疑似秘密样本验证拒绝策略。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `src/coding_agent/memory/models.py` | 新增 | 记忆领域模型与作用域 |
| `src/coding_agent/memory/service.py` | 新增 | 写入、检索、冲突、删除接口 |
| Conversation SQLite migration | 修改 | memory 表、FTS、审计与版本 |
| `src/coding_agent/context.py` | 修改 | 有预算的 memory projection |
| Web API / DTO / SSE | 修改 | 记忆 CRUD、候选与采用记录 |
| `frontend/src/pages/MemoryPage.tsx` | 新增 | 记忆中心与来源解释 |
| Settings / Run details | 修改 | scope 开关、采用记录 |
| Python/Vitest/Playwright | 新增/修改 | 安全、隔离、召回与删除闭环 |

## 6. 验收标准

- [ ] 会话 A 明确保存一条独特 workspace 事实后，新会话 B 能在相关问题中检索并采用，且 UI 可追溯到 A 的 turn。
- [ ] 不同 canonical workspace 不串记忆；global、workspace、conversation 三种 scope 的可见范围与开关符合定义。
- [ ] 自动提取只生成 candidate，用户确认前不会注入；关闭 memory mode 后不检索、不生成候选、不写入。
- [ ] 编辑产生可追踪的新版本/关系，冲突条目可 supersede；单条删除或 scope reset 后重启服务仍无法召回。
- [ ] 疑似密钥、私钥、`.env` 值和超长工具输出被服务端拒绝或安全脱敏，不出现在数据库、DOM、日志和模型请求投影。
- [ ] 检索结果稳定、去重并受 top-k/字符预算限制；2000 条记忆下的检索性能符合 task_008 预算。
- [ ] Python、Vitest 和 production Playwright 覆盖保存—切会话—召回—来源查看—删除—不可再召回完整链路。

## 7. 风险与注意事项

- 首版不引入 embedding/RAG 服务。后续只有固定评测证明 FTS 召回不足时，才在 MemoryService 后增加可替换索引器。
- 记忆文本本身可能包含提示注入；注入模型时必须标为“非指令性参考事实”，且不能获得比系统/开发者策略更高优先级。
- 事实过期比漏记更危险。UI 必须让用户看到来源、时间和作用域，项目事实可以 supersede，不能把历史值伪装成当前真相。
- 删除是产品承诺：索引、缓存、投影和审计中的正文都要按既定隐私策略处理，不能只删除列表行。

