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

- [ ] assistant 正文在模型完成前逐步显示，完成后的 canonical text 与所有 delta 拼接严格一致。
- [ ] DeepSeek-compatible reasoning_content 和 OpenAI Responses reasoning summary 通过统一事件显示在可折叠 Think 块；不支持时无虚假思考文本。
- [ ] streaming tool call fragments 聚合为合法 ToolCall，canonical tool pairing 与 completion verification 不回归。
- [ ] refresh/SSE reset/reconnect 后正文与 Think 内容无缺失、重复或乱序；2,000 个小 delta 不造成 2,000 次持久化 fsync 或无界 DOM。
- [ ] cancel 中止上游 stream；partial 后失败/重试不会把两次 attempt 文本拼接，UI 明确标记已放弃 attempt。
- [ ] profile 可选择 Chat Completions/Responses 与 reasoning 策略，旧配置无损迁移，密钥仍只写不读。
- [ ] Fake Model 覆盖 content/reasoning/tool fragments；有合法凭据时按 capability 做真实 smoke，无凭据可 N/A 但两种协议 fake 闭环必须通过。

## 7. 风险与注意事项

- “Think”是 provider-visible output/summary，不等于可以读取模型内部隐藏状态；UI 和 README 必须保持这一区分。
- DeepSeek thinking + tool use 的 reasoning history 回传规则与普通多轮不同，adapter contract test 必须覆盖，否则会在第二个工具 sub-turn 返回 400。
- OpenAI Responses 是新增 wire API，不允许在 AgentLoop 写 provider 分支；所有差异收敛在 adapter。
- token delta 很容易造成数据库、React 和 Markdown renderer 放大，应先确定批量边界再做 UI 动画。

