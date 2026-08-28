# 任务编号：task_005

## 1. 任务目标

建立 provider-neutral 的流式模型协议，让 assistant 正文、工具调用完成状态和模型明确提供的可见 reasoning/summary 以增量事件进入 AgentLoop、持久化 Conversation、SSE 和 UI。界面提供默认折叠、可展开的 Think 块，同时不伪造、不推断、不泄漏模型未公开的隐藏思考。

本任务同时新增 `openai_responses` wire adapter，使 OpenAI reasoning summary 有正式协议入口；保留并升级 `openai_chat_completions`，支持 DeepSeek/OpenAI-compatible 的 `reasoning_content` 与普通 content streaming。

## 2. 背景与上下文

- 当前 ModelClient 使用非流式 Chat Completions，AgentLoop 在整次响应返回后才收到 `AssistantTurn`，因此 UI 只能显示 step/tool，不能显示生成中的正文或 reasoning。
- DeepSeek 官方 Chat Completions streaming 在 `delta.reasoning_content` 返回思考内容；带 tool calls 的 thinking 模式还要求在后续 sub-request 中保留对应 reasoning content。
- OpenAI Responses API 提供 reasoning summary 与 `response.reasoning_summary_text.delta` 等流事件；不能把不存在的 summary 伪装为 raw chain-of-thought。
- DSH 的典型链路为 `agent/request → llm/stream → assistant/chunk* → assistant/message`，先广播 delta，再以单个完成消息作为 canonical 事实。该分层适合本项目，但不引入其插件框架。
- task_004 提供 Conversation/Turn 持久化事实源；本任务在其上追加 stream item，而不是建立第二套 UI-only buffer。

## 3. 技术约束

- 定义自有 `ModelStreamEvent`/`ModelResponse`，SDK chunk 类型不得越过 adapter 边界；AgentLoop 只消费中立事件。
- canonical history 只在完整响应校验成功后追加聚合后的 assistant/tool call 消息；partial delta 是可恢复展示事件，不得造成半个 tool call 配对。
- Chat Completions adapter 必须正确按 choice/index 聚合 text、reasoning、tool call id/name/arguments；Responses adapter 必须映射 output item、function call、reasoning summary、usage 和 refusal。
- provider/profile 明确声明 wire API 与 capability；`reasoning_mode=auto/off/visible`、effort 和 stream 配置必须经过 adapter 校验。旧 profile 有确定迁移默认值。
- 仅展示上游协议明确返回给客户端的 reasoning content/summary。若 provider 不支持，Think 区降级为“正在处理/调用工具”等结构化进度，不显示虚构内容。
- reasoning 默认折叠并可全局/每会话关闭；展开时支持流式追随但不得抢焦点或强制滚到底部。
- 高频 delta 必须在 server/client 有 coalescing/backpressure；SSE 断线重放恢复完整文本，不因逐 token 造成无界事件或 O(n²) 字符拼接。
- retry/cancel 语义明确：首 delta 前可正常重试；已产生 partial 后失败要标记该 attempt abandoned，后续 attempt 不与旧文本拼接；cancel 关闭上游 stream。

## 4. 实现步骤

1. 扩展 ModelClient contract，定义 delta/final/error/cancel 时序和 capability descriptor；给 Fake Model 建确定性碎片流。
2. 实现 Chat Completions streaming 聚合器，覆盖 content、reasoning_content、并行 tool call fragments、空 delta、finish reason 与 usage。
3. 实现 OpenAI Responses adapter，映射 output text、reasoning summary、function calls、refusal、usage 和错误；扩展 profile `wire_api` 与 reasoning 配置迁移/UI。
4. AgentLoop 在每个 provider attempt 发出 `assistant_text_delta`、`reasoning_delta/summary_delta`、`stream_attempt_abandoned` 等事件，同时只在 final aggregate 后修改 canonical history。
5. Conversation storage 对 delta 做有界批量 checkpoint；terminal final item 可重建并校验，断线/刷新从 snapshot/event cursor 恢复。
6. 前端增加增量 assistant block 和 Think disclosure：一行浅色“思考中/思考了 Ns”摘要，默认折叠；展开内容使用安全 Markdown/plaintext renderer。
7. 为无法提供 reasoning 的 provider 展示诚实降级，不把工具事件或本项目自身日志冒充模型思考。
8. 添加 adapter contract、AgentLoop pairing、重试/取消、SSE backpressure、DOM/性能及真实 provider 可选 smoke 测试。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `src/coding_agent/model_client.py`、`models.py` | 重构 | 中立 streaming contract/capabilities |
| `src/coding_agent/model_factory.py`、provider adapters | 修改/新增 | Chat Completions 与 Responses 聚合器 |
| `src/coding_agent/agent.py` | 修改 | delta 事件、final canonical commit、retry/cancel |
| `src/coding_agent/provider_config.py` | 修改 | wire API/reasoning/stream 配置迁移 |
| `src/coding_agent/conversations/` | 修改 | stream checkpoint 与重建 |
| `src/coding_agent/web/*` | 修改 | delta DTO、SSE coalescing、snapshot |
| `frontend/src/components/*` | 修改/新增 | streaming assistant、Think disclosure、设置 |
| `tests/`、前端测试、Fake Model/E2E | 新增/修改 | 碎片聚合、恢复、降级与性能 |

## 6. 验收标准

- [x] assistant 正文在模型完成前逐步显示，完成后的 canonical text 与所有 delta 拼接严格一致。
- [x] DeepSeek-compatible reasoning_content 和 OpenAI Responses reasoning summary 通过统一事件显示在可折叠 Think 块；不支持时无虚假思考文本。
- [x] streaming tool call fragments 聚合为合法 ToolCall，canonical tool pairing 与 completion verification 不回归。
- [x] refresh/SSE reset/reconnect 后正文与 Think 内容无缺失、重复或乱序；2,000 个小 delta 不造成 2,000 次持久化 fsync 或无界 DOM。
- [x] cancel 中止上游 stream；partial 后失败/重试不会把两次 attempt 文本拼接，UI 明确标记已放弃 attempt。
- [x] profile 可选择 Chat Completions/Responses 与 reasoning 策略，旧配置无损迁移，密钥仍只写不读。
- [x] Fake Model 覆盖 content/reasoning/tool fragments；有合法凭据时按 capability 做真实 smoke，无凭据可 N/A 但两种协议 fake 闭环必须通过。

## 7. 风险与注意事项

- “Think”是 provider-visible output/summary，不等于可以读取模型内部隐藏状态；UI 和 README 必须保持这一区分。
- DeepSeek thinking + tool use 的 reasoning history 回传规则与普通多轮不同，adapter contract test 必须覆盖，否则会在第二个工具 sub-turn 返回 400。
- OpenAI Responses 是新增 wire API，不允许在 AgentLoop 写 provider 分支；所有差异收敛在 adapter。
- token delta 很容易造成数据库、React 和 Markdown renderer 放大，应先确定批量边界再做 UI 动画。

## 8. 最小交付范围与明确非目标

### 8.1 本任务必须交付

- 将同步 `ModelClient.request()` 演进为 provider-neutral `stream()` contract，同时提供 CLI/旧测试兼容聚合入口。
- `openai_chat_completions` 的流式 content、可见 reasoning、tool-call fragments、usage 与错误映射。
- `openai_responses` 的 output text、reasoning summary、function call、refusal、usage 与错误映射。
- AgentLoop attempt/cancel/retry/canonical commit 的严格时序，以及 Conversation stream checkpoint/SSE 恢复。
- 默认折叠的 Think disclosure、增量正文和无 reasoning provider 的诚实降级。

### 8.2 本任务不包含

- 不实现 Anthropic Messages、Gemini native 等第三种 wire API；provider 可通过 Chat-compatible URL 使用的仍按真实协议声明。
- 不展示未由服务商返回的 hidden chain-of-thought，不用另一个模型“补写思考”，不把工具日志改名为模型思考。
- 不支持用户在流式请求中间即时打断并注入消息；Steer 由 task_006 在响应结束后的安全边界实现。
- 不把每个 token 永久保存成 canonical message，不把 SDK response/chunk 对象序列化进数据库或公开 DTO。
- 不加入语音、图片、多模态输入和 Markdown raw HTML。

## 9. 目标模型适配架构

```text
ProviderProfile + ResolvedModelConnection
                 │
                 ▼
ModelClientFactory（按 wire_api 分派）
  ├─ ChatCompletionsAdapter ─┐
  └─ OpenAIResponsesAdapter ─┤ SDK chunk → ModelStreamEvent
                              ▼
                    TurnStreamAccumulator
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
  AgentLoop FSM        Public Stream Sink     Attempt Diagnostics
        │                     │
        ▼                     ▼
 CanonicalJournal      checkpoint/coalesce → SSE → React projection
```

采用以下模式：

- **Adapter + anti-corruption layer**：SDK 类型、事件名和 provider 例外只存在于 adapter。
- **Iterator/stream port**：AgentLoop 同步 worker 内消费 blocking iterator；FastAPI event loop 不直接等待 SDK。
- **Accumulator**：delta 是暂态事实，`TurnStreamAccumulator` 验证并聚合为唯一 `AssistantTurn`。
- **Two-phase publication**：先发布 partial public event，协议完成后才提交 canonical final。
- **Capability negotiation**：profile 声明所需能力，adapter 报告实际 capability；设置 UI 只显示交集。
- **Backpressure/coalescing**：provider chunk、数据库 checkpoint、SSE batch、React commit 是四个不同频率，不做一对一转发。

## 10. Provider-neutral contract

### 10.1 能力描述

建议新增不可变 `ModelCapabilities`：

| 字段 | 含义 |
| --- | --- |
| `wire_api` | `openai_chat_completions` / `openai_responses` |
| `streaming` | adapter 是否实现真实 streaming |
| `tool_calling` | 是否支持 function tools |
| `parallel_tool_calls` | 本项目仍可强制 false；描述服务能力 |
| `visible_reasoning` | none / raw_visible / summary |
| `reasoning_efforts` | 支持的 effort 枚举集合 |
| `usage_in_stream` | 是否能在终态给 usage |
| `supports_cancel` | 关闭 iterator/HTTP stream 的能力 |

profile 的 preset capability 只是配置提示；运行时对不匹配响应仍必须 fail-closed，不能盲信配置。

### 10.2 事件联合

建议内部判别联合至少包含：

```text
StreamStarted(response_id?)
TextDelta(output_index, delta)
ReasoningDelta(output_index, delta, visibility=raw_visible)
ReasoningSummaryDelta(output_index, summary_index, delta)
ToolCallStarted(output_index, tool_index, call_id?, name?)
ToolCallArgumentsDelta(output_index, tool_index, delta)
RefusalDelta(output_index, delta)
UsageReceived(input_tokens?, output_tokens?, reasoning_tokens?)
StreamCompleted(finish_reason/raw_status)
```

- 每个事件携带当前 provider attempt number，但不携带 API key、request header、完整 raw chunk。
- tool fragment 的聚合 key 首选 provider item/call id；缺失时使用 `(output_index, tool_index)`，出现冲突/索引回退即 `PROTOCOL_ERROR`。
- `TextDelta` 与 reasoning/summary 使用不同 buffer，顺序分别稳定；不得依赖两类事件全局交替顺序重建 provider 私有对象。
- `StreamCompleted` 不代表 canonical 合法；Accumulator 还要验证 finish 状态、tool id/name/arguments、文本/refusal 组合。

### 10.3 ModelClient 接口

目标接口语义：

```text
ModelClient.stream(
  messages: Sequence[ProviderNeutralMessage],
  tools: Sequence[ToolSchema],
  request_options: ModelRequestOptions,
  cancel: CancellationToken,
) -> Iterator[ModelStreamEvent]
```

- `request()` 可以作为消费 `stream()` 的兼容 helper，不能继续发第二次非流式网络请求。
- cancellation token 在每个 chunk 前后检查；adapter 的 `close()`/context manager 必须释放 HTTP 连接。
- retry 仍由 AgentLoop 管理，SDK 内置重试关闭或设为 0，避免 attempt 计数失真。
- 异常统一映射现有 `ModelRequestError(retryable=...)`，新增 stable diagnostic category 但不公开 SDK 原文。

## 11. Wire adapter 设计

### 11.1 Chat Completions adapter

- 请求固定 `stream=true`；若 SDK/服务支持，要求终态 usage chunk，但缺失 usage 不令任务失败。
- 按 choice index 只接受配置允许的一条 choice；意外多 choice 必须明确选择 0 并记录 diagnostic 或直接拒绝，不能混合。
- 聚合 `delta.content`；对于 OpenAI-compatible 扩展，从 typed/extra fields 安全读取 `reasoning_content`，值必须是字符串。
- tool call 支持 arguments 任意碎片边界、空 delta、name/id 延迟到达；完成时逐项验证非空稳定 call id/name，但 raw arguments 留给 ToolExecutor 解析。
- `finish_reason=tool_calls` 要求至少一个完整 tool call；`stop` 不得留下未完成 tool fragment。
- provider-visible reasoning 的 round-trip 策略由 adapter metadata 指定：只在同一 logical turn 的工具后续请求且官方协议要求时保留；进入下一 user turn 时按协议剥离。不得把这一例外写进 ContextManager 通用分支。

### 11.2 OpenAI Responses adapter

- 将 response/output item 生命周期映射为 neutral indexes，处理 output text、function call arguments、reasoning summary 和 refusal。
- 只展示 summary 或 API 明确标为客户端可见的 reasoning text；encrypted/opaque reasoning item 只保存 provider continuation 所需的 opaque reference，绝不渲染。
- function call 的 `call_id`、name、arguments 与现有 ToolCall 对齐；tool output 回传由 adapter 将 neutral ToolMessage 转成 Responses input item。
- response failed/incomplete/cancelled 映射为不同 diagnostic；只有协议允许的 incomplete reason 才可重试。
- provider response id 若用于同 turn 延续，只存内部 continuation metadata；public API 最多给脱敏短 ID，默认 UI 不显示。

### 11.3 协议夹具

两种 adapter 都必须以捕获后手工脱敏的 JSON fixtures/构造 SDK fake 覆盖：单字符碎片、Unicode 跨 chunk、空 chunk、交错 tool calls、arguments 中转义、reasoning→tool→reasoning、usage 晚到、错误终态。fixtures 不包含真实 key、host query 或用户仓库内容。

## 12. AgentLoop 状态、历史与重试

### 12.1 状态机接入

- 继续使用 `REQUESTING_MODEL` 表示等待和消费 stream，不为每个 token新增 LoopPhase。
- 首个 chunk 前发 `model_stream_started`；批量公开 delta 由 stream sink 产生，不要求 AgentLoop 每 token创建完整 AgentEvent。
- 完整 stream 经 Accumulator 验证后得到 `AssistantTurn`，此时才进入 `HANDLING_RESPONSE` 并执行既有 tool/final 分支。
- canonical append 仍只有一次完整 AssistantMessage；可见 reasoning 作为 `ReasoningArtifact` 附着于该 assistant canonical item或同组 metadata，不得成为角色不明的 UserMessage。

### 12.2 Attempt 生命周期

```text
created → streaming → completed → canonical_committed
               ├─ failed_before_output → retryable
               ├─ abandoned_after_output → optional clean retry
               └─ cancelled → terminal interrupted
```

- 是否“已有输出”由已接受的 text/reasoning/tool fragment 判定，不由 SSE 是否成功送达决定。
- partial 后若策略允许 retry，先发 `stream_attempt_abandoned`，冻结旧 partial block，再开始新 attempt；新块有独立 attempt id。
- 旧 attempt 的 partial 不进入 canonical context，刷新后仍可作为诊断/视觉记录恢复，但最终 assistant 只来自成功 attempt。
- tool call 一旦进入 ToolExecutor 就不得因 provider retry 重复执行；retry 边界严格位于完整 response 被接受之前。

### 12.3 Reasoning canonical policy

- `ReasoningArtifact` 至少记录 kind（raw_visible/summary/opaque）、display text（可选）、provider continuation metadata（内部可选）、round-trip scope 与字符数。
- display text 经过与 public payload 相同的 secret redaction/大小限制；opaque 内容不进 DTO、日志和导出。
- ContextManager 不默认把 reasoning 当普通文本永久累加。adapter 在构造 provider request 时只取得协议需要的 artifact；跨 turn 是否保留按 wire contract 测试。

## 13. 流量控制、持久化和 SSE

### 13.1 四级节流

| 层级 | 输入 | 输出策略 |
| --- | --- | --- |
| SDK adapter | 原始 chunk | 立即校验成 neutral event，不做 UI 格式化 |
| stream coalescer | text/reasoning 小 delta | 最多每 50 ms 或累计 4 KiB flush，terminal 立即 flush |
| Conversation checkpoint | coalesced partial | 最多每 250 ms/16 KiB 持久化一次，final 强制提交 |
| React projection | SSE batch | animation frame 或最多 20 commits/s，后台 tab 可更低 |

具体阈值可根据基准微调，但必须写成常量、有单元测试并在 feedback 报告最终值；不能散落 magic number。

### 13.2 Checkpoint 模型

- partial checkpoint key 为 `(turn_id, attempt, channel, chunk_seq)`；写入幂等并保留累计文本或分段 blob，不能每次读取全部字符串再 O(n²) 连接。
- final commit 在同一事务标记 attempt completed、写聚合 assistant/canonical group，并把 partial projection 标 terminal。
- SSE cursor 仍单调；coalesced event payload 包含 append delta 与累计 char count，不发送整个已生成文本。
- snapshot 返回当前 attempt 各 channel 的累计文本和最后 event seq；reconnect 先 snapshot/reset，再从 cursor 续传。
- 数据库只保留有限 abandoned partial；按 conversation 删除策略级联清除。

## 14. Profile、设置与能力校验

ProviderProfile 增加版本化字段：

| 字段 | 示例 | 规则 |
| --- | --- | --- |
| `wire_api` | `openai_responses` | 必填枚举；旧配置迁移为 chat completions |
| `streaming` | true | 本任务两种 adapter 固定 true；UI 不制造假开关 |
| `reasoning_mode` | auto/off/visible | off 不请求/不展示；visible 需 capability；auto 按 provider |
| `reasoning_effort` | low/medium/high | 可空；只允许 capability 声明值 |
| `show_reasoning` | false | UI 偏好建议单独保存，不属于凭据 |

- preset 选择可预填 wire API/base URL，但用户仍可编辑；切 wire API 时保留 model/base URL，清除不兼容 reasoning 参数前需提示。
- 密钥槽仍与 profile ID 关联，切 wire API 不读取或回显 key。
- bootstrap capabilities 返回服务端编译支持的 wire APIs；profile capability 与运行时 adapter capability 错误分别展示。
- 旧 profile migration 必须幂等；未知 wire API fail-closed，不自动回退 Chat 造成请求发往错误端点。

## 15. Think 与 streaming UI 规格

### 15.1 Transcript 结构

每个 assistant attempt 的视觉顺序：

```text
ThinkDisclosure（存在可见 reasoning/summary 才出现）
AssistantTextBlock（可流式为空）
ActionRows（完整 tool call 被 AgentLoop 接受后）
AttemptStatus（仅失败/取消时）
```

- Think header 文案为“思考中…”、完成后“思考了 8 秒”或“思考摘要”；不能称“完整思维链”。
- 默认折叠偏好按用户设置；用户手动展开后本 attempt 内保持，不因 delta 自动收起/展开。
- 内容先按 plain text/pre-wrap 安全渲染；代码/链接需要 renderer 时复用现有安全能力，禁止 raw HTML。
- reasoning 和正文分别 aria-live；高频 delta 不逐 token朗读，完成/错误时给一次简短 announcement。
- 用户向上阅读时不自动滚动；Think 展开不改变 Composer 焦点。

### 15.2 无 reasoning 降级

- 请求进行中显示产品级状态“正在处理”；工具阶段由结构化 ActionRow 呈现。
- provider 返回空 reasoning 时不渲染空 Think 外壳。
- reasoning mode 被用户关闭时服务端不得发送启用 reasoning 的可选参数；若 provider 仍主动返回相关字段，adapter 在 public sink 前丢弃 display text，不发 DTO；高级诊断只显示“已关闭”，不显示正文。

## 16. 实施批次与回滚入口

### 批次 A：Neutral contract + Fake stream

先让现有 Chat 非流式 fake 经 `stream()` 聚合得到完全相同 AssistantTurn，AgentLoop 回归全绿；此时 UI 可仍不展示 delta。

### 批次 B：Chat streaming + AgentLoop attempt

完成碎片聚合、cancel/retry/canonical final、公开事件和 checkpoint；用 fake E2E 闭环后替换 production Chat 路径。

### 批次 C：Streaming/Think UI

完成 coalescing、snapshot 恢复、Think disclosure 和无 reasoning fallback；通过 2,000 chunk 性能门槛。

### 批次 D：Responses adapter + profile migration

最后加入第二 wire API、生成 OpenAPI/TS types、协议 fixtures 和可选 live smoke。若 Responses 未通过，其 profile 能力必须保持隐藏/不可选，不能影响已稳定 Chat adapter。

每批使用独立 adapter/feature capability 回滚；不得退回全局 `stream=false` 后仍在 UI 宣称流式或 Think 可用。
