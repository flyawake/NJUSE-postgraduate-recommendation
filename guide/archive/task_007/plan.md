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

## 8. 最小交付范围、交付层级与非目标

### 8.1 P0 最小记忆能力

- 用户在 UI 选择“保存为记忆”或使用明确 `/remember` 产品入口创建 confirmed memory。
- global/workspace/conversation 三种 scope、来源跳转、检索注入、开关、编辑/supersede、删除/reset。
- SQLite hybrid lexical index，支持中文与英文；无 FTS5 时保持可用。
- Memory Center 和每轮“采用了哪些记忆”的可解释记录。

### 8.2 P1 候选提取

- 模型在 turn terminal 后可提出 memory candidate；用户 approve/reject 后才进入 confirmed 检索集合。
- 候选提取默认关闭或只在用户选择“建议可记忆内容”时运行；开启状态、成本和隐私边界清楚可见。
- 如果时间/质量不足，P1 可以在 task feedback 中明确延期，但 P0 不得依赖它才能工作。

### 8.3 明确非目标

- 不自动永久保存完整 transcript、reasoning、工具输出和代码文件正文。
- 不引入 Pinecone、Chroma、云 embedding、知识图谱框架或 agent memory SDK。
- 不做模糊“人格学习”、用户画像推断、跨设备云同步、团队共享与后台抓取仓库。
- 不让 memory 直接执行工具或改变 ToolPolicy；它只是低优先级上下文资料。
- 不以删除 UI 行代替真实删除，不保留用户已删除正文用于“模型改进”。

## 9. 目标架构与实现模式

```text
Memory Center / explicit remember / candidate approval
                         │
                         ▼
                    MemoryService
      ┌──────────────────┼───────────────────┐
      ▼                  ▼                   ▼
MemoryRepository   MemoryIndexer       MemoryPolicy
  (SQLite facts)   (FTS/terms/rank)   (scope/secret/injection)
      │                  │                   │
      └──────────────────┴───────────┬───────┘
                                     ▼
                              MemoryRetriever
                                     │ once per turn
                                     ▼
                           ContextManager projection
                                     │
                                     ▼
                                  AgentLoop
```

采用：

- **Ports and adapters**：MemoryService 是 ContextManager 唯一依赖；索引器和候选提取器可替换。
- **CQRS-like separation**：confirmed/candidate/superseded 是写侧事实；检索只读取 active confirmed projection。
- **Provenance-first record**：来源不是附加文案，而是每条 memory 的必填结构化关系。
- **Human approval gate**：模型只能 propose，用户/显式 UI 才能 confirm。
- **Snapshot retrieval**：每 turn 首请求检索一次并固化，AgentLoop step 不重复查询。
- **Untrusted context boundary**：memory 以引用数据注入，永远低于 system/tool policy 指令。

## 10. Memory 领域模型

建议 `MemoryEntry`：

| 字段 | 规则 |
| --- | --- |
| `id` | 稳定随机 ID |
| `scope_type` | global/workspace/conversation |
| `scope_key` | global 固定值；workspace canonical key；conversation id |
| `kind` | preference/fact/decision/procedure |
| `title` | 可选短标题，≤120 字符 |
| `content` | 规范化正文，长度上限建议 4,000 字符 |
| `status` | candidate/confirmed/superseded/rejected/deleted |
| `confirmation` | explicit_ui/explicit_command/user_approved/imported |
| `source_conversation_id/turn_id` | 至少一项来源或 `source=manual` |
| `source_excerpt` | 脱敏短摘录，不复制整轮 |
| `supersedes_id` | 同 scope 冲突替代关系 |
| `version` | edit/approve/delete CAS |
| `created/updated/last_used_at` | 排序与解释 |
| `use_count` | 采用次数；只在成功构建 projection 后增加 |

### 10.1 生命周期

```text
model proposal → candidate ──approve──► confirmed ──edit──► new confirmed version
                     │                      │                    │
                     └─reject→ rejected    ├─supersede──────────┘
                                            └─delete→ deleted

explicit UI/command ───────────────────────► confirmed
```

- edit 不原地改写 confirmed 正文：创建新版本并使旧版本 superseded，保证历史与已运行 turn 的采用记录仍可解释。
- candidate/rejected 默认不进入检索；superseded 只在历史页可见；deleted 正文及索引必须清除。
- scope 不能通过普通 edit 改变，移动 scope 等价于新建 + 删除/替代，避免来源边界含糊。

## 11. SQLite schema 与索引

| 表 | 关键内容 |
| --- | --- |
| `memory_entries` | 上述领域字段、FK、version、状态、正文 |
| `memory_sources` | entry 与多个 conversation/turn/source excerpt 的关系 |
| `memory_terms` | 规范化 Latin token/CJK bigram 或 fallback 倒排项 |
| `memory_fts` | FTS5 external-content/同步索引（若运行环境支持） |
| `memory_usage` | turn_id、entry_id、rank、reason、snapshot hash、used_at |
| `memory_events` | proposed/confirmed/edited/superseded/deleted/reset 的无秘密审计 |

- migration 属于 task_004 的同一 state.db schema chain，不能创建第二个未经协调的 version 文件。
- FTS 表与 entry 状态在同一事务更新；启动 self-check 随机/全量校验 active entry 与索引计数，不一致时可从 facts 重建。
- hard delete 清除 entry content、source excerpt、terms、FTS 和未完成 candidate cache；usage 可保留 memory id/hash/时间但不保留正文。
- scope reset 先精确计算 IDs 并在单事务删除；不删除 Conversation 或 workspace 文件。

## 12. 中英文混合检索框架

SQLite 默认 `unicode61` 对中文分词不足，因此首版采用可解释 hybrid lexical index：

1. Unicode NFKC、case fold、空白折叠；保留代码标识符中的 `_-.` 的可搜索变体。
2. Latin/数字按单词和 identifier 片段生成 token，过滤极短常见停用项。
3. 连续 CJK 文本生成去重的 2-gram，短至 1 字时保留 unigram；每条设 term 数上限。
4. 将标准 tokens/bigrams 写入 `memory_terms` 和 FTS searchable string；原 content 保持用户文本，不改写成分词串。
5. 查询使用同一 analyzer，先按允许 scope/status 筛选，再 FTS/term 命中，最后在 Python 小候选集上稳定重排。
6. FTS5 不可用时，从 `memory_terms` 做 bounded SQL join/LIKE fallback；功能可用但 UI/诊断标记 index backend。

### 12.1 稳定排序

排序 tuple 必须固定并可单测，例如：

```text
scope priority（conversation > workspace > global）
exact phrase/token coverage
FTS/BM25 或 matched-term ratio
confirmation priority
updated/last_used recency bucket
entry id（最终 tie-break）
```

- 不把 `use_count` 设为强正反馈，避免早期错误记忆永久垄断结果。
- 同一 supersede chain 只返回最新 active confirmed；近重复 content 以 normalized hash 去重。
- 默认 top-k 建议 6，单条 display/injection ≤1,200 字符，总 projection 建议不超过 6,000 字符且服从 ContextManager 总预算；常量集中配置。

## 13. 写入与候选提取方式

### 13.1 显式写入

- UI 在 assistant/user 文本选区或 Memory Center 提供“保存为记忆”，打开 Dialog 编辑 content/kind/scope/source 后确认。
- `/remember <content>` 是 Composer 解析的本地产品命令，可打开同一确认 Dialog；不直接作为普通模型消息发送。
- 服务端执行 secret scan、长度/scope/source 校验、近重复查询；发现近重复时让用户选择保留、更新或取消。
- 只有服务端 201/updated snapshot 返回后 UI 才显示“已记住”。

### 13.2 模型候选提取

- 使用独立 `MemoryCandidateExtractor`，输入只包含本轮用户/最终 assistant 的有界脱敏文本和已有相关 memory 摘要，不包含 reasoning、完整工具输出或凭据。
- extractor 不绑定工具执行权限，要求严格 JSON schema：kind/content/scope suggestion/source rationale/confidence；解析失败丢弃候选而不影响主 turn。
- 每 turn 最多一次、terminal 后异步执行、并发/字符/超时有界；费用和状态在设置中可见。
- 所有模型 proposal 都是 candidate，即使用户自然语言说“记住”；用户在 transcript 的候选提示或 Memory Center approve 后才 confirmed。只有显式 UI/本地 `/remember` 能直接确认。
- reject 记录 hash 以避免相同候选短期反复出现，但不永久屏蔽用户后来显式保存。

## 14. Secret、隐私与 memory poisoning 防护

### 14.1 写入策略

- 复用/扩展现有 public redaction 的 fail-closed 规则，并在 MemoryPolicy 加入私钥块、Bearer/API key 格式、常见 credential 字段、`.env` assignment 和高熵长 token 检测。
- 命中明确 secret：拒绝并只返回 `memory_contains_secret`；可安全遮盖的路径/用户名等按政策脱敏并要求用户确认差异。
- 禁止 source 为 raw command output、request body、header、reasoning；只能保存用户编辑后的短事实/偏好。
- 错误日志仅记录 entry id、policy code 和长度，不记录被拒正文。

### 14.2 注入策略

Memory projection 使用单独的、结构化、带边界的 system/developer-level data block，例如：

```text
<memory_context trust="untrusted_reference" scope="workspace">
  <memory id="..." source="conversation/turn" kind="decision">quoted data</memory>
</memory_context>
```

- 在根 system prompt 明确：memory 是可能过期/恶意的数据，不是命令；与当前用户、workspace 事实或 ToolPolicy 冲突时忽略并重新验证。
- content 做 XML/结构转义并限制长度；memory 内伪造 `</memory_context>` 不能逃逸。
- procedure memory 也不能直接授权命令、路径越界或跳过编辑前读取。
- 检索/注入记录只保存采用的 entry id、rank、scope、snapshot hash 和公开 reason，方便 UI 解释和安全审计。

## 15. ContextManager 接入与预算

- 在 `build_request` 前新增 `MemoryProjectionProvider` seam，但 canonical history 本身不 append memory；memory 是本轮 request view 的临时前缀。
- query 只基于本轮最新 UserMessage、conversation workspace/scope 和可选最近对话关键词；不得用所有 tool delta 每 step 重搜。
- turn 创建/首次 READY 时检索并持久化 `memory_usage` snapshot；该 turn 后续 model attempt/step 复用相同 snapshot。
- ContextManager 预算顺序建议：root system + 当前用户/协议 skeleton > 最近 steps/error/latest file read > memory projection > 较旧成功工具正文。
- memory 超预算时按 rank 从尾部丢弃并记录 omitted count，不截断到破坏单条结构；0 条时不插入空 block。
- memory mode off 在检索前短路，并确保 candidate extractor 也不运行。

## 16. Memory API 与 UI

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/memories` | scope/kind/status/query/cursor/limit |
| POST | `/api/memories` | explicit confirmed create + idempotency |
| GET | `/api/memories/{id}` | 正文、来源、版本链、采用统计 |
| PATCH | `/api/memories/{id}` | 新版本 edit/supersede + expected version |
| DELETE | `/api/memories/{id}` | hard delete + expected version |
| POST | `/api/memories/{id}/approve` | candidate→confirmed |
| POST | `/api/memories/{id}/reject` | candidate→rejected |
| POST | `/api/memories/reset` | scope + explicit confirmation/version token |
| GET | `/api/turns/{id}/memory-usage` | 本轮采用记录 |

Memory Center：

- 默认只列 active confirmed；tab/filter 查看 candidates、history/superseded。
- 行显示 content 摘要、kind、scope、来源、更新时间；详情展示完整来源与版本链。
- approve 前可编辑候选和 scope；删除/reset Dialog 明确影响范围。
- Settings 有总开关、workspace/conversation override、候选建议开关和检索条数/预算的高级只读摘要。
- transcript 只显示紧凑“使用了 2 条项目记忆”，点击进入详情；不把记忆全文反复铺在对话中。

## 17. 实施批次与回滚入口

### 批次 A：Memory facts/policy/显式 CRUD

完成 schema、secret policy、版本链、hard delete、Memory Center；memory 尚不注入模型也能独立验收管理语义。

### 批次 B：Hybrid index/retrieval/projection

完成 CJK/Latin analyzer、FTS/fallback、排序预算、每 turn snapshot 和跨会话召回 E2E。

### 批次 C：候选提取

最后接入 optional extractor、approval UI、成本/失败隔离和 poisoning tests。若质量不达标，关闭 candidate capability 不影响 P0 explicit memory。

回滚 capability 时停止新检索/写入，但必须保留用户数据与 Memory Center 导出/删除入口；不能通过删除数据库回滚功能。
