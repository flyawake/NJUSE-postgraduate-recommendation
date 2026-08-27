# 任务编号：task_001 - 实现端到端 Coding Agent MVP

## 1. 任务目标

交付一个可安装、可测试、可从命令行运行的最小编程智能体。它必须使用普通模型 API 客户端和模型原生 function/tool calling，在用户指定的本地工作区内完成“观察文件 - 决策 - 调用工具 - 回传结果 - 继续决策 - 给出最终答复”的完整闭环。

本任务的单一目标是形成可独立验证、可继续演进的端到端内核首版，不追求产品界面或功能数量。考核给出的闭环是最低线；首版额外落实工具执行管线、资源感知上下文投影、完成前验证和可关联事件，因为这些能力会直接影响真实任务可靠性。以下事项不在本任务范围内：

- GUI/TUI、IDE 插件、Web 服务或多用户能力。
- 多智能体、子任务并行、长期记忆、向量数据库、会话持久化和崩溃恢复。
- 流式输出、语音/图像输入、远程 MCP、服务端托管的 Code Interpreter、Files API 或 file search。
- 完整操作系统沙箱、容器隔离、交互式命令审批界面或通用插件系统。
- repository map、AST/LSP 索引、语义检索、通用补丁语言和多语言专用编辑器。
- 最终 `README.txt`、演示视频、姓名 zip、现场答辩材料及远端仓库发布操作。
- 同时实现多家厂商的专用协议；MVP 只保证一个 OpenAI-compatible Chat Completions 适配器，其他协议以后通过既定接口扩展。

## 2. 背景与上下文

题目要求重要 agent 逻辑自行编写，尤其是上下文管理、工具定义与本地执行、模型输出解析、循环终止和错误处理。允许使用普通模型 API 客户端及模型原生 tool calling，但禁止使用任何 agent 框架或 Agent SDK。

采用以下架构边界：

```text
CLI
  -> Config + System Prompt
  -> AgentLoop (显式状态机与 tool-call/result 配对所有者)
       -> ContextManager / RequestProjector
       -> ModelClient -> ResponseNormalizer
       -> LoopGuard (call ID / repeat / cancellation)
       -> ToolExecutor
            -> argument decoder + tool validator
            -> ToolPolicy
            -> ToolRegistry -> six Tool handlers
            -> ToolOutcome normalizer + model renderer
       -> CompletionPolicy
       -> EventSink
```

选择 Chat Completions 而非服务端会话状态，是为了兼容常见 OpenAI-compatible 网关，并让项目能够展示由本地代码维护完整消息历史和 tool-call 关联。官方 `openai` Python 包仅作为普通 HTTP/API 客户端使用，不得引入 `openai-agents` 或任何编排层。

### 2.1 参考工程与迁移结论

截至 2026-08-27，本方案只研究公开资料和本机已安装工程，不引入其运行时，也不复制框架代码：

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)：借鉴 provider/tool/policy 分层、文件观察版本、工具输出保留、重试与重复调用提醒；MVP 用 Python 标准库重建明确的简化语义。
- [OpenAI Codex protocol](https://github.com/openai/codex/blob/main/codex-rs/docs/protocol_v1.md) 与 [app-server lifecycle](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)：借鉴 core 与 UI 解耦、Task/Turn/Item 生命周期、命令/文件操作的结构化事件，以及工具路由和取消上下文。
- [OpenCode Session V2 规格](https://github.com/anomalyco/opencode/blob/dev/specs/v2/session.md)：借鉴事实历史与模型可见投影分离、工具调用先规范化后结算、上下文压缩不重放不确定副作用；MVP 只实现进程内 canonical history，不伪装成持久会话。
- [Gemini CLI policy engine](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/policy-engine.md)：借鉴“工具定义、策略判定、确认/执行调度”分层；MVP 实现无交互的 ALLOW/DENY ToolPolicy seam，审批模式留到后续。
- [OpenHands Runtime](https://github.com/OpenHands/docs/blob/main/openhands/usage/architecture/runtime.mdx)：借鉴 Action/Observation 与执行 runtime 的边界；Docker 隔离作为后续 Runtime 适配器，不在首版虚假宣称安全沙箱。
- [SWE-agent history processors](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/agent/history_processors.py)：借鉴 history processor 对模型请求副本做资源感知裁剪，并保留文件最新窗口；不改写 canonical history。
- [Aider repository map](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/repomap.md) 与 [Coder loop](https://github.com/Aider-AI/aider/blob/main/aider/coders/base_coder.py)：借鉴 repository map、模型适配的编辑格式以及修改后 lint/test/reflection；首版只实现通用的有界完成验证，repository map 和专用编辑格式进入后续路线。

这些参考形成四条不可破坏的边界：事实记录与模型呈现分离、策略与执行分离、可预期工具失败与运行级失败分离、核心状态与 CLI 展示分离。后续添加 MCP、容器、索引或 UI 时，应接到这些接口上，不得重写 AgentLoop 的协议不变量。

当前公开远端为 `git@github.com:flyawake/NJUSE-postgraduate-recommendation.git`，最早提交时间为 2026-08-26 15:09:37+08:00。项目负责人必须在最终交付前确认该时间晚于题目正式发布时间；此核验不阻塞本任务的本地实现。

本任务内部目标完成时间为 2026-08-29 24:00（北京时间），为后续真实模型联调、演示录制和提交预留时间。

## 3. 技术约束

### 3.1 技术栈与依赖

- Python `>=3.10`，使用 `uv` 管理虚拟环境、依赖和锁文件，采用 `src/` package layout。
- 运行时第三方依赖仅允许普通 `openai` Python 客户端；优先使用标准库完成配置、数据结构、路径处理、子进程和工具参数校验。
- 开发依赖使用 `pytest` 与 `ruff`。新增其他依赖前必须在 feedback 中说明必要性、许可证和可替代方案。
- 必须提交 `pyproject.toml` 与 `uv.lock`，禁止依赖未记录的全局包。
- 禁止 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI 及任何等价 agent/harness 框架。

### 3.2 模型接口与输出解析

- 定义项目自己的 `ModelClient` 协议，使 AgentLoop 不依赖 SDK 对象；生产适配器使用 `openai` 客户端的 OpenAI-compatible Chat Completions 接口。
- 配置项为 `OPENAI_API_KEY`、`OPENAI_MODEL` 和可选 `OPENAI_BASE_URL`。API key 不得支持命令行明文参数，也不得记录到日志。
- 本地维护并在每次请求中发送消息历史；不得使用服务端托管会话、服务端文件或服务端代码执行工具。
- 请求应关闭流式响应并设置 `parallel_tool_calls=False`。若兼容服务仍返回多个 tool calls，按返回顺序逐个执行并逐个回传，不得丢失调用。
- 将 SDK 响应标准化为项目自有的 assistant turn/tool call 数据结构。每个调用必须保留 `tool_call_id`、函数名和原始参数。
- 函数参数必须解析为 JSON object。无效 JSON、非 object、缺少字段或未知工具都转换为结构化错误结果回传模型，不得令进程崩溃。
- 连接错误、请求超时、HTTP 429 和 5xx 由项目代码做最多 3 次总尝试的有限退避；其他 API 错误立即形成明确失败。重试逻辑必须可注入零等待策略进行单元测试。

### 3.3 本地工具及责任边界

MVP 必须提供以下六个工具。工具 schema、参数校验、调用分发、观察策略和返回结构均由项目代码定义，不得依赖 DeepSeek Harness 或其他 agent 工程运行：

1. `glob(pattern, path=".")`：使用项目自研、基于标准库的文件发现后端返回相对路径；固定最多 100 项，跳过 `.git`、`.venv`、`node_modules` 和常见缓存/构建目录，截断时返回已省略数量和收窄建议。
2. `grep(pattern, path=".", include?)`：对工作区内 UTF-8 文本执行正则搜索，可限定一个 glob；按文件、行号返回，固定最多 200 条匹配、每行预览最多 2,000 字符。无匹配是成功空结果，无效正则是稳定参数错误。
3. `read_file(path, offset=1, limit=200)`：带 1-based 行号读取 UTF-8 窗口；`limit` 最大 500 行，单个窗口最多 50 KiB，每行最多展示 2,000 字符，并返回 `total_lines`、下一窗口提示及文件版本指纹。
4. `write_file(path, content)`：创建或整体覆盖 UTF-8 文本文件，可创建工作区内父目录；单次内容上限 1 MiB。创建要求目标仍不存在，覆盖现有文件要求已通过 `read_file` 观察当前版本。
5. `edit_file(path, old_string, new_string, replace_all=false)`：执行字面量替换；默认要求唯一匹配，`replace_all=true` 时至少匹配一次。必须先通过 `read_file` 观察文件，且写入前版本仍一致。
6. `run_command(argv, cwd=".", timeout_seconds=30, purpose="other")`：`argv` 必须是非空字符串数组，使用 `shell=False`，`cwd` 必须位于工作区；允许的超时范围为 1-120 秒。`purpose` 为 `inspect`/`verify`/`other`，只表达本次命令在 agent 轨迹中的用途，不改变进程权限。stdout/stderr 分别保留 head 4,000 + tail 6,000 字符并报告省略量。

所有工具返回统一、JSON-serializable 的结果。成功至少为 `{ "ok": true, "data": ... }`；失败至少为 `{ "ok": false, "error": { "code", "message", "retryable", "recovery_hint"? } }`。`glob`、`grep`、`read_file` 使用各自的领域上限，`run_command` 使用 head-tail 保留；不得用一个无语义的全局字符串截断破坏行号、退出状态或错误信息。

每个工具注册为项目自有的 `ToolSpec`，至少包含 `name`、`description`、闭合 JSON schema、`effect`（`READ`/`WRITE`/`EXECUTE`）、参数校验器和 handler。无需实现通用 JSON Schema 引擎；provider schema 与项目自有校验器必须由同一 ToolSpec 暴露，并用契约测试防止二者漂移。Registry 只负责目录、schema materialization 与 lookup，不负责循环状态。

AgentLoop 不得越过 `ToolExecutor` 直接调用 handler。每个调用严格经过以下顺序，且每个阶段可单测：

```text
assistant ToolCall
  -> 校验 call_id / 重复与取消等 loop guard
  -> 解析 JSON object
  -> Registry lookup
  -> ToolSpec 参数校验与规范化
  -> ToolPolicy(effect, normalized_args) => ALLOW | DENY
  -> 发出 tool_started
  -> handler.execute
  -> ToolOutcome 归一化与领域化保留
  -> 确定性 JSON model rendering
  -> 追加同 call_id 的 tool message
  -> 发出 tool_finished
```

MVP 的 ToolPolicy 是无交互、可注入的策略接口：默认允许工作区边界内的六个已注册工具，显式拒绝返回 `POLICY_DENIED` 工具结果；不得把审批 UI 或环境变量读取塞进 AgentLoop。测试用 policy 必须能拒绝一个原本合法的 WRITE/EXECUTE 调用，证明策略先于副作用执行。循环级 call ID、重复调用、组取消与配对仍由 AgentLoop 管理，工具参数/权限/执行和结果归一化由 ToolExecutor 管理。

同一工具调用必须有三种分离的表示：内部 `ToolOutcome` 保留结构化语义；model rendering 以稳定 JSON 发送给模型；事件/CLI 只展示脱敏短摘要。不得为方便打印而破坏模型结果，也不得把完整大输出复制进事件。

实现轻量 `FileObservationTracker`：只有成功的 `read_file` 才建立文件观察，版本使用对原始字节计算的 SHA-256；`glob`/`grep` 不授权写入。覆盖或编辑前重新计算版本，不一致返回 `FILE_STALE` 并提示重新读取；未观察返回 `FILE_NOT_OBSERVED`。创建、覆盖或编辑使用同目录临时文件、flush/close 后 `os.replace`，成功后刷新观察版本；参数或版本校验失败时原文件必须保持不变。该机制是单进程下的尽力新鲜度防护，不得宣称为跨进程原子 CAS。

所有文件发现、搜索和读写都必须对工作区根目录与目标做规范化检查，拒绝绝对路径、`..` 越界及经符号链接逃逸。`run_command` 只提供进程级超时和工作目录约束，不得宣称实现完整安全沙箱；README 必须提示仅在可信或一次性工作区运行。

### 3.4 AgentLoop 状态机、上下文与终止策略

AgentLoop 必须实现为显式、可测试的有限状态机，而不是散落在 CLI 中的 `while True`。主路径如下：

```text
INITIALIZING
  -> READY
  -> REQUESTING_MODEL
  -> HANDLING_RESPONSE
       |-- 无 tool call + 非空文本 --> CHECKING_COMPLETION
       |                                  |-- 允许完成 --> COMPLETED
       |                                  `-- 需验证提醒 -> READY
       |-- 无 tool call + 空文本 ----> FAILED(PROTOCOL_ERROR)
       `-- 有 tool calls -----------> EXECUTING_TOOLS
                                          |
                                          `-> READY

任意非终态 -- Ctrl+C --> INTERRUPTED
任意策略上限触发 ------> FAILED(<明确 stop_reason>)
```

#### 3.4.1 状态、计数和返回值

- `LoopPhase` 至少包含 `INITIALIZING`、`READY`、`REQUESTING_MODEL`、`HANDLING_RESPONSE`、`EXECUTING_TOOLS`、`CHECKING_COMPLETION`、`TERMINAL`；非法状态迁移必须在测试中失败，不能静默继续。
- `RunStatus` 为 `SUCCESS`、`ERROR` 或 `INTERRUPTED`。AgentLoop 只返回一个结构化 `RunResult`，至少包含 `status`、`stop_reason`、`final_text?`、`step_count`、`provider_attempt_count` 和 `tool_call_count`；CLI 根据它决定退出码和展示。
- `VerificationStatus` 为 `NOT_APPLICABLE`、`VERIFIED`、`FAILED` 或 `NOT_RUN`。RunResult 还必须返回 `verification_status`、去重后的 `mutated_paths` 和最近一次验证命令的脱敏摘要/退出码（若存在），使“做了修改”与“修改已验证”不会混成一个无法核对的成功字符串。
- `StopReason` 至少覆盖 `FINAL_ANSWER`、`MAX_STEPS`、`MODEL_ERROR`、`PROTOCOL_ERROR`、`CONTEXT_OVERFLOW`、`TOOL_FAILURE_LIMIT`、`REPEATED_TOOL_CALL` 和 `INTERRUPTED`。
- 一个 step 是“一次逻辑模型请求及其返回的整组 tool calls”。`step_count` 在冻结本次请求并准备首次发送前加一；同一请求的 API 重试只增加 `provider_attempt_count`，不增加 step、不改变历史。
- 默认 `max_steps=20`，CLI 可设置为 1-50。若第 20 个 step 返回工具调用，应完成并记录该工具组；回到 READY 后在发起第 21 次模型请求前以 `MAX_STEPS` 终止。

#### 3.4.2 规范循环

1. **初始化**：验证配置和工作区，创建 canonical history，按顺序追加 system prompt 与原始 user task，随后发出 `run_started` 事件并进入 READY。初始化失败不得调用模型。
2. **请求准备**：READY 先检查 step 上限，再由 ContextManager 从 canonical history 构造独立的 request view。默认字符预算为 120,000；canonical history 不得被改写。投影器必须保留 system prompt、原始 user task、每个 tool-call/result 协议骨架、最近两个逻辑 step、最近的错误结果和每个文件最近一次成功 `read_file` 窗口；较早的成功工具正文按确定性规则替换为包含工具名、资源键、原字符数及省略量的标记，命令输出继续保留 head/tail。若保护项本身仍超限，则以 `CONTEXT_OVERFLOW` 终止。投影对相同输入必须生成相同输出，并由测试证明 canonical history 未变。
3. **模型请求**：冻结 request view 后进入 REQUESTING_MODEL。同一冻结请求对连接错误、超时、HTTP 429/5xx 最多做 3 次总尝试；退避等待可注入。失败尝试、半截输出和异常文本不追加历史，耗尽后以 `MODEL_ERROR` 终止。
4. **响应处理**：ModelClient 将响应标准化为一个 `AssistantTurn`。AgentLoop 进入 HANDLING_RESPONSE，并将该 assistant turn 完整追加 canonical history 一次。若 tool calls 为空：空文本以 `PROTOCOL_ERROR` 结束；非空文本进入 CHECKING_COMPLETION。若文本与 tool calls 同时存在，文本保留但不能提前结束。
5. **工具组执行**：进入 EXECUTING_TOOLS，按模型返回顺序串行处理全部调用。每个非空、在本 assistant turn 内唯一的 `tool_call_id` 必须恰好对应一个 tool result，且所有结果必须在下一次模型请求前追加。无效 JSON、非 object 参数、未知工具和工具校验失败都生成同 ID 的结构化错误结果；它们不应中止同组其余调用。
6. **轮次结算**：工具组中至少一个工具成功则 `consecutive_failed_tool_rounds` 清零，否则加一；命令成功启动但退出码非零仍算工具执行成功。计数达到 3 时以 `TOOL_FAILURE_LIMIT` 终止，否则回到 READY。

#### 3.4.3 完成验证策略

- 成功的 `write_file`/`edit_file` 记录变更路径并推进内存中的 `workspace_revision`。只有在最新 revision 之后完成、且 `purpose="verify"` 的 `run_command` 才构成验证尝试；退出码 0 为 `VERIFIED`，非零为 `FAILED`，没有尝试为 `NOT_RUN`。这是一项可解释的完成证据规则，不得宣称能证明测试命令本身充分。
- 若没有变更，非空最终文本以 `NOT_APPLICABLE`、`FINAL_ANSWER` 正常完成。若有变更且已 VERIFIED，也正常完成。
- 若有变更但为 FAILED/NOT_RUN，且本 run 尚未发送完成提醒并仍有剩余 step，CompletionPolicy 不丢弃当前 assistant 文本，而是追加一个标记 `source=completion_policy` 的内部控制消息，明确要求运行相关测试/检查或说明无法验证，发出 `completion_deferred` 事件并回到 READY。适配器可把该内部消息降低为带 `[completion-policy]` 前缀的 user role，不能冒充原始用户输入。
- 每个 run 最多延迟完成一次，禁止形成验证提醒死循环。提醒后模型再次给出非空最终文本，或当前已无剩余 step 时，允许结束但 RunResult 必须如实保留 FAILED/NOT_RUN；CLI 以醒目标记展示“完成但未验证/验证失败”。这一情况仍是 agent 给出最终答复的 `SUCCESS/FINAL_ANSWER`，不能伪造成测试通过。

#### 3.4.4 重复、取消与半组结果

- 在每次工具分派前，用“工具名 + 深度排序后 JSON 参数”计算调用签名。连续第 3 次相同签名仍执行，但在该工具组结果之后追加模型可见的换路提醒；连续第 5 次不再执行该调用，为它追加 `REPEATED_TOOL_CALL` 错误结果，为同组尚未分派的调用追加 `ABORTED_BEFORE_DISPATCH` 结果，然后终止。
- Ctrl+C 或注入的取消信号应令当前 `run_command` 尝试终止其拥有的子进程；当前调用追加 `TOOL_ABORTED`，同组未分派调用追加 `ABORTED_BEFORE_DISPATCH`，随后返回 `RunStatus.INTERRUPTED`、`StopReason.INTERRUPTED`，CLI 退出码为 130。不得留下仍由本进程拥有的孤儿子进程。
- tool call ID 为空或同一 assistant turn 内重复属于协议错误。为保持历史可诊断，应先为无法安全分派的调用生成 `PROTOCOL_ERROR` 结果，再以同名 stop reason 终止，不向模型发起下一次请求。

#### 3.4.5 必须保持的不变量

- canonical history 只追加，不因上下文裁剪而原地改写；request view 是每步临时派生物。
- 下一次模型请求之前，每个 assistant tool call 恰有一个同 ID 的 tool result；禁止孤立 tool result、漏结果或重复结果。
- ModelClient SDK 类型只存在于适配器边界；AgentLoop、ContextManager 和测试只使用项目内部类型。
- ToolExecutor/ToolRegistry 的可预期失败进入模型可见工具结果；模型请求、协议、上下文和用户取消等运行级失败进入 `RunResult`，两者不得混淆。
- AgentLoop 不直接读取环境变量、不直接 `print`、不直接解析 SDK 响应；ModelClient、ToolExecutor、ContextManager、CompletionPolicy、事件接收器和退避 sleeper 均通过构造参数注入。
- 每个事件至少包含同一 run 内单调递增的 `sequence`、`run_id`、事件类型、step 和脱敏 payload；事件协议至少定义 `run_started`、`step_started`、`model_retry`、`assistant_received`、`tool_started`、`tool_finished`、`completion_deferred`、`run_finished` 八类，并在对应生命周期发生时发出。CLI 只消费事件并脱敏展示，事件中不得包含 API key 或完整大工具输出。`run_finished` 在每个已开始 run 中恰好出现一次且 sequence 最大。

### 3.5 CLI、日志与凭据

- 提供控制台命令：`uv run coding-agent --workspace <path> [--model <name>] [--base-url <url>] [--max-steps <n>] "<task>"`。
- `--help` 不读取凭据、不访问网络。正常完成退出码为 0；配置、协议、循环或工具基础设施失败退出码为 1；用户中断为 130。
- 日志应展示步骤编号、模型调用、工具名称、简短参数摘要、工具结果状态和最终答复，禁止打印 API key、完整环境变量或无法控制长度的大段工具输出。
- 提供 `.env.example` 但不自动提交真实 `.env`；`.gitignore` 必须覆盖常见凭据文件、虚拟环境、缓存和构建产物。

## 4. 实现步骤

1. 初始化 `pyproject.toml`、`uv.lock`、`src/` 包结构、CLI entry point、`.gitignore` 和无密钥的 `.env.example`；先确保 `coding-agent --help` 可运行。
2. 定义内部消息、tool call/result、`LoopPhase`、`RunStatus`、`StopReason`、`VerificationStatus`、`RunResult`、AgentEvent 和异常类型，明确 SDK 对象只能存在于生产 ModelClient 适配器边界内。
3. 实现配置加载与校验；API key 只从环境读取，模型和 base URL 可由环境读取并由非敏感 CLI 参数覆盖。
4. 实现 ToolSpec、稳定错误结构、ToolRegistry、ToolPolicy、ToolExecutor、FileObservationTracker 及六个本地工具；先用契约测试固定 schema/validator，再完成策略拒绝无副作用、路径越界、搜索/读取预算、编辑前读取、陈旧版本、原子写入和子进程超时测试。
5. 实现 OpenAI-compatible ModelClient、响应标准化和有限 API 重试；用 fake SDK client 覆盖成功、多个 tool calls、无效参数和错误分类。
6. 实现 ContextManager 的 canonical append-only history、tool-call 关联和资源感知 request projection；不得依赖服务端状态，也不得原地破坏 canonical history。
7. 按 3.4 实现 AgentLoop 状态机、消息不变量、计数、重复/取消策略、CompletionPolicy 和带单调序号的结构化事件，再接入 CLI 事件输出、验证标记、退出码及 Ctrl+C 处理。
8. 编写一个完全离线的端到端测试：Scripted/Fake Model 依次要求 glob、grep、读取文件、精确编辑、以 `purpose="verify"` 运行测试并返回最终答复，真实调用临时工作区工具，证明闭环可重复运行且 RunResult 为 VERIFIED；另测一次未验证完成被有界延迟。
9. 补齐边界与错误路径测试，运行格式化、lint 和全量测试，修复所有失败或警告。
10. 编写 `README.md`，说明架构、运行命令、配置、工具、终止条件、安全边界、测试命令和已知限制；不得编写最终提交用 `README.txt`。
11. 若执行环境已有合法凭据，使用一次性演示工作区完成一次真实模型 smoke test，并在 feedback 中记录脱敏命令、模型名和结果；若没有凭据，按 acceptance 的 N/A 规则记录并留给后续任务。
12. Developer Agent 创建 `feedback/task_001_feedback.md`，逐项列出实现文件、测试命令与结果、live smoke 状态、已知限制和任何依赖变化，并登记 `feedback/INDEX.md` 为 `待评估`。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 责任边界 |
| --- | --- | --- |
| `pyproject.toml`、`uv.lock` | 新增 | Python/uv 元数据、运行与开发依赖、CLI entry point、ruff/pytest 配置 |
| `.gitignore`、`.env.example` | 新增 | 排除凭据与生成物；示例文件不得含有效凭据 |
| `src/coding_agent/__init__.py`、`__main__.py` | 新增 | 包入口与 `python -m coding_agent` 入口 |
| `src/coding_agent/cli.py`、`config.py` | 新增 | 参数、环境配置、日志、退出码；不包含 agent 核心逻辑 |
| `src/coding_agent/models.py` | 新增 | 内部消息、调用、状态/验证枚举、停止原因、运行结论与事件数据结构 |
| `src/coding_agent/model_client.py` | 新增 | ModelClient 协议、OpenAI-compatible 适配器、重试和响应标准化 |
| `src/coding_agent/context.py` | 新增 | append-only canonical history、tool-call 关联、资源感知 request view 和字符预算压缩 |
| `src/coding_agent/events.py` | 新增 | AgentEvent 接收器协议及 CLI 可消费的事件定义/辅助实现 |
| `src/coding_agent/agent.py`、`completion.py` | 新增 | 显式 AgentLoop 状态机、调用编排、完成验证、消息不变量、停止和错误策略 |
| `src/coding_agent/prompt.py` | 新增 | 可审阅的系统提示，禁止硬编码密钥或演示答案 |
| `src/coding_agent/tools/` | 新增 | ToolSpec/Registry/Policy/Executor、稳定错误、路径守卫、观察/版本策略、发现/搜索、文件和命令工具 |
| `tests/` | 新增 | 配置、模型解析、上下文、工具、终止策略和离线端到端测试 |
| `README.md` | 新增 | 开发仓库使用和架构说明；不是最终提交的 `README.txt` |
| `feedback/task_001_feedback.md`、`feedback/INDEX.md` | 新增 / 修改 | Developer Agent 的实现证据与待评估登记 |

不得修改 `AGENT_MASTER.md`、`AGENT_DEV.md`、既有 guide 模板、原始题目 PDF 或本任务范围外的协作文件。不得把原始题目 PDF、真实 `.env` 或本地演示密钥加入提交。

## 6. 验收标准

- [ ] A1. `uv sync --all-groups` 在干净环境完成，`uv run coding-agent --help` 与 `uv run python -m coding_agent --help` 均退出 0 且不访问模型 API。
- [ ] A2. 缺少必需配置时 CLI 在首次模型调用前以退出码 1 失败并指出缺失字段；输出不包含任何 API key。
- [ ] A3. AgentLoop 按 3.4（含 CHECKING_COMPLETION）的显式状态机运行并返回带 verification 的结构化 RunResult；SDK 响应已转换为内部类型，canonical history 保持 append-only，下一模型请求前每个 tool call 恰有一个同 ID 结果，事件 sequence 单调且 `run_finished` 恰好一次。
- [ ] A4. `glob`、`grep`、`read_file`、`write_file`、`edit_file`、`run_command` 均以 ToolSpec 注册，具有闭合 JSON schema、匹配的 validator、effect、统一结果和稳定错误 code；调用经过 ToolExecutor 管线，注入 policy 拒绝 WRITE/EXECUTE 时无副作用并回传同 ID 的 `POLICY_DENIED`。
- [ ] A5. 文件工具拒绝绝对路径、`..` 和符号链接越界；覆盖/编辑实施先读后改和 SHA-256 版本检查，未观察、版本陈旧或匹配数不符时原文件不变，成功变更使用原子替换。
- [ ] A6. `glob`、`grep` 和 `read_file` 分别落实路径/匹配/行窗口与预览上限，保留行号、总量或省略信息；无匹配与无效参数能够区分。
- [ ] A7. `run_command` 使用 `shell=False`，验证工作目录、超时、`purpose` 和 head-tail 输出保留，并把非零退出码与 stdout/stderr 作为观察结果返回；只有最新变更之后 `purpose="verify"` 的命令影响 VerificationStatus。
- [ ] A8. ContextManager 只做确定性的资源感知 request projection，保留错误、最近轮次和每文件最新读取且不改写 canonical history；step 与 provider attempt 分别计数。正常/已验证完成、未变更完成、未验证完成的一次延迟与有界放行、文本伴随工具、多个/重复 ID 调用、无效参数、未知工具、API 重试耗尽、连续失败、重复调用提醒/终止、上下文溢出、空响应、最大轮数、半组取消和 Ctrl+C 均有状态迁移、配对结果、RunResult 与事件测试。
- [ ] A9. 离线端到端测试使用 Fake/Scripted Model 和真实临时工作区工具，覆盖“glob - grep - read - edit - `purpose=verify` run - 最终答复”，RunResult 为 VERIFIED，且不访问网络。
- [ ] A10. `uv run pytest -q` 全部通过；测试不得依赖执行顺序、真实 API、开发者机器绝对路径或仓库外文件。
- [ ] A11. `uv run ruff format --check .` 与 `uv run ruff check .` 均退出 0。
- [ ] A12. 依赖清单中不存在 agent 框架、Agent SDK、服务端代码/文件工具或未说明的新依赖，`uv.lock` 与 `pyproject.toml` 一致。
- [ ] A13. `README.md` 给出 AgentLoop 状态图、工具调用管线、上下文投影、完成验证、step/retry 计数口径、消息不变量、架构、安装、配置、运行、工具、终止策略、测试、安全边界和已知限制，所有命令可复制；项目未宣称具备完整命令沙箱或充分验证证明。
- [ ] A14. 仓库跟踪文件与 diff 中不存在凭据、真实 `.env`、原始题目 PDF、缓存、构建产物或与 task_001 无关的实现改动。
- [ ] A15. Developer feedback 提供逐项证据和实际命令输出摘要；有合法凭据时还须记录一次脱敏 live smoke test，无凭据时按下述 N/A 规则记录。

## 7. 风险与注意事项

- **API 兼容差异**：不同 OpenAI-compatible 服务可能忽略 `parallel_tool_calls` 或返回略有差异的对象。通过 ModelClient 适配边界和内部类型隔离，MVP 只承诺经测试的一个兼容端点，不在 AgentLoop 中散布厂商分支。
- **参考实现边界**：工具语义参考本机 `E:/DS/deepseek-harness` 的文件、搜索、观察策略、shell、输出保留、重试与重复调用设计；核对时 HEAD 为 `47f943859bef60e4160492346772ded9b24f765a`，许可证为 MIT。task_001 不得导入该项目、复制其框架代码或把它加入运行依赖，只能根据公开行为重新实现本任务明确列出的简化语义。
- **搜索规模**：MVP 的 glob/grep 使用标准库以保证零额外运行依赖，性能不与 ripgrep 后端等同。通过目录排除、文件/匹配/预览上限控制资源；引入可替换 ripgrep 后端属于后续优化。
- **命令安全**：`shell=False` 降低 shell 注入风险，但任意可执行文件仍可能访问工作区外资源。必须如实声明信任边界，真实演示只使用一次性工作区。
- **上下文预算**：字符预算不是精确 token 计数，优点是无模型专用 tokenizer 依赖；README 应说明这是 MVP 的确定性近似方案。
- **完成验证的边界**：`purpose="verify"` 是 agent 声明的命令意图，退出码 0 只证明该命令成功，不证明测试集合充分。RunResult 必须保存命令摘要与状态，后续评测再判断选择是否合理。
- **写文件风险**：`write_file` 会整体覆盖文件，覆盖和 `edit_file` 都必须先读取并校验 SHA-256 版本；该检查仍存在跨进程 TOCTOU 窗口，因此只声明尽力防护，不声明事务或安全沙箱。
- **真实 API 不可用**：无凭据时离线测试仍可验收代码结构，但最终录制前必须创建后续任务完成 live smoke；不得用伪造日志代替。
- **仓库合规**：当前最早提交早于本地 PDF 到达时间，题目正式发布时间未知。最终提交前必须由项目负责人确认仓库创建时间合规；若不合规，应创建新的公开仓库并从新仓库首个提交开始保留完整历史，不得伪造或改写时间。
- **回滚**：本任务只新增实现文件和修改索引/上下文。若技术方案失败，应回退未推送的 task_001 实现提交或通过新提交撤销，不得重写已推送历史；Master Agent 不代写修复。

## 8. 执行、验证与提交办法

实现期间按以下检查点推进，每个检查点保持可运行并形成语义清晰的 Git 提交；一旦推送，不得 amend、rebase、squash 或 force-push 改写历史：

1. 工程骨架、配置和 CLI help。
2. 内部模型类型、ModelClient 与 ToolSpec/ToolExecutor/本地工具。
3. ContextManager、AgentLoop、CompletionPolicy 和终止策略。
4. VERIFIED 离线端到端测试、边界测试和 README。
5. 仅在有凭据时追加 live smoke 修正，不提交凭据或原始演示工作区。

Developer Agent 在交付反馈前必须依次执行：

```powershell
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run coding-agent --help
uv run python -m coding_agent --help
```

如任何命令失败，不得把任务报告为完成；应修复后重跑，或在 feedback 中标记受阻并给出最小复现。task_001 验收通过后，再由 Master Agent 按 PROJECT_CONTEXT 的演进路线创建真实模型/评测、仓库理解、执行隔离等任务；演示案例、最终 `README.txt`、视频、答辩提纲和提交包作为阶段性交付并行安排，不作为项目能力建设的终点。

## 9. 首次验收整改要求（2026-08-27）

首次 Master 验收结论为“需整改”。标准命令全部通过（126 passed, 1 skipped），但以下三项仍属于原 A5/A8 范围，不能移入后续功能任务：

1. **搜索时的符号链接逃逸**：`grep` 不得直接读取 `os.walk` 发现但解析后位于 workspace 外的文件；`glob` 也不得把此类文件作为可用结果返回。对每个候选文件使用统一路径守卫或等价的 canonical containment 检查，且不得跟随目录链接逃逸。新增可创建链接时的 grep/glob 回归测试；平台无法创建链接时允许该用例 skip，但实现代码仍必须有明确守卫。
2. **观察资源键规范化**：`read_file` 建立观察、返回 path 和 AgentLoop/ContextManager 记录资源时必须使用与 write/edit 相同的规范化相对路径。新增 `read_file("./a.txt")` 后以同一路径和 `a.txt` 两种写法分别 edit/write 的测试，均不得错误返回 `FILE_NOT_OBSERVED`。
3. **取消后禁止开始副作用**：在 `ToolExecutor.prepare`（含 policy）返回后、发出 `tool_started`/调用 handler 前再次检查取消状态。若此时已取消，当前及剩余未开始调用均写入一个 `ABORTED_BEFORE_DISPATCH` 结果，不执行 handler；`tool_call_count` 对每个模型调用只计一次。新增 policy 阶段触发取消的 WRITE 测试，断言目标文件不存在、结果配对、状态 INTERRUPTED、计数准确。

整改不得改变 tool schema、既定状态机、验收口径或新增依赖。Dev 完成后更新 `feedback/task_001_feedback.md` 的整改证据，把 `feedback/INDEX.md` 重新置为 `待评估`，并重新执行全部标准命令。
