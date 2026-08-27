# coding-agent

一个完全本地实现核心循环的编程智能体（coding agent）MVP：模型只负责决策，项目代码负责对话历史、工具定义与执行、输出解析、循环终止和错误处理。它使用普通 `openai` Python 客户端和 OpenAI-compatible Chat Completions tool calling，不依赖任何 agent 框架或 Agent SDK。

> 本实现是可审计的教学/原型内核。`run_command` 会在本机直接执行任意可执行文件，**不是安全沙箱**，只能在可信或一次性工作区中运行。

## 安装

要求 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --all-groups
```

依赖仅包含运行时的 `openai` 客户端，以及开发期的 `pytest`、`ruff`；锁文件为 `uv.lock`。

## 配置

凭据只从环境变量读取，API key 不支持命令行明文参数，也不会写入日志：

```powershell
$env:OPENAI_API_KEY = "sk-..."        # 必需
$env:OPENAI_MODEL = "your-model"      # 必需
$env:OPENAI_BASE_URL = "https://..."  # 可选，OpenAI-compatible 网关
```

`.env.example` 展示了变量格式；真实 `.env` 已被 `.gitignore` 排除。

## 运行

```powershell
uv run coding-agent --workspace <path> "<programming task>"
uv run coding-agent --workspace <path> --model <name> --base-url <url> --max-steps 20 "<task>"
uv run python -m coding_agent --workspace <path> "<task>"
```

退出码：`0` 正常完成（含“完成但未验证”的如实标注），`1` 配置/协议/循环/工具基础设施失败，`130` 用户中断。`--help` 不读取凭据、不访问网络。

## 架构

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
            -> ToolRegistry -> 六个本地工具
            -> ToolOutcome normalizer + model renderer
       -> CompletionPolicy
       -> EventSink
```

关键边界：SDK 类型只存在于 `model_client.py`；AgentLoop 不直接读取环境变量、不 `print`、不解析 SDK 响应；canonical history 与模型请求投影分离；策略与执行分离；可预期工具失败进入模型可见工具结果，模型/协议/上下文/取消等运行级失败进入 `RunResult`。

### AgentLoop 状态机

```text
INITIALIZING
  -> READY
  -> REQUESTING_MODEL
  -> HANDLING_RESPONSE
       |-- 无 tool call + 非空文本 --> CHECKING_COMPLETION
       |                                  |-- 允许完成 --> TERMINAL
       |                                  `-- 需验证提醒 -> READY
       |-- 无 tool call + 空文本 ----> TERMINAL(PROTOCOL_ERROR)
       `-- 有 tool calls -----------> EXECUTING_TOOLS -> READY

任意非终态 -- Ctrl+C --> INTERRUPTED
任意策略上限触发 ------> TERMINAL(明确 stop_reason)
```

非法迁移直接抛错。`LoopPhase`：`INITIALIZING / READY / REQUESTING_MODEL / HANDLING_RESPONSE / EXECUTING_TOOLS / CHECKING_COMPLETION / TERMINAL / INTERRUPTED`。

**step 与 provider attempt 口径**：一个 step 是“一次逻辑模型请求及其返回的整组 tool calls”。`step_count` 在冻结本次请求、准备首次发送前加一；同一冻结请求对连接错误、超时、HTTP 429/5xx 最多做 3 次总尝试，每次尝试只增加 `provider_attempt_count`，失败尝试与半截输出不追加历史。

### 工具执行管线

每个调用严格经过：

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

同一个调用有三种分离表示：内部 `ToolOutcome` 保留结构化语义；`model_content` 以稳定 JSON 发给模型；事件/CLI 只展示脱敏短摘要。

| 工具 | effect | 关键语义 |
| --- | --- | --- |
| `glob(pattern, path=".")` | READ | 标准库实现；最多 100 项；跳过 `.git/.venv/node_modules` 与常见缓存/构建目录；报告省略量 |
| `grep(pattern, path=".", include?)` | READ | UTF-8 正则搜索；最多 200 条、每行预览 ≤2000 字符；无匹配是成功空结果，无效正则是参数错误 |
| `read_file(path, offset=1, limit=200)` | READ | 1-based 行号；limit ≤500、窗口 ≤50 KiB；返回 `total_lines`、`next_offset` 与 SHA-256 指纹 |
| `write_file(path, content)` | WRITE | 创建或整体覆盖；父目录自动创建；单次 ≤1 MiB；覆盖必须先观察当前版本 |
| `edit_file(path, old_string, new_string, replace_all=false)` | WRITE | 字面量替换；默认唯一匹配；`replace_all=true` 至少匹配一次；先读后改 + 版本新鲜度 |
| `run_command(argv, cwd=".", timeout_seconds=30, purpose="inspect\|verify\|other")` | EXECUTE | `shell=False`；cwd 必须在工作区内；超时 1-120 秒；stdout/stderr 各保留 head 4000 + tail 6000 字符并报告省略量；非零退出码仍是成功观察 |

所有结果统一为 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": {"code", "message", "retryable", "recovery_hint"?}}`，错误 code 稳定且属于本项目。

文件安全机制：拒绝绝对路径、`..` 越界和符号链接逃逸；只有成功 `read_file` 才建立观察，覆盖/编辑前重新计算 SHA-256，未观察返回 `FILE_NOT_OBSERVED`、版本变化返回 `FILE_STALE`；写入走同目录临时文件 + flush/close + `os.replace`，成功后刷新观察。该机制是单进程下的尽力新鲜度防护，**不是跨进程 CAS 或沙箱**。

### 上下文投影

`canonical history` 只追加、永不改写；每步由 ContextManager 生成独立的临时 request view。默认字符预算 120,000（近似值，不声称精确 token 计数）。投影确定性保留：system prompt、原始 user task、所有 tool-call/result 协议骨架、最近两个逻辑 step、最近的错误结果、每个文件最近一次成功的 `read_file` 窗口；较早的成功工具正文按确定性规则替换为 `{ok, omitted, tool, resource, original_chars, omitted_chars}` 标记。若保护项本身仍超预算，以 `CONTEXT_OVERFLOW` 终止。

### 完成验证与终止

- 成功的 `write_file`/`edit_file` 记录变更路径并推进内存中的 workspace revision；只有最新 revision 之后、`purpose="verify"` 的 `run_command` 才构成验证尝试：退出码 0 → `VERIFIED`，非零 → `FAILED`，没有尝试 → `NOT_RUN`，没有变更 → `NOT_APPLICABLE`。`purpose` 只是 agent 声明的意图，退出码 0 只证明该命令成功，**不证明测试集合充分**。
- 有变更但未验证时，CompletionPolicy 最多延迟完成一次：追加 `source=completion_policy`、以 `[completion-policy]` 前缀呈现的内部控制消息并回到 READY；无剩余 step 或提醒后模型再次给出非空最终文本时允许结束，但 `RunResult` 如实保留 `FAILED`/`NOT_RUN`，CLI 醒目标注“完成但未验证/验证失败”。
- 相同“工具名 + 深度排序后 JSON 参数”签名连续第 3 次：仍执行并追加模型可见换路提醒；连续第 5 次：不再执行，追加 `REPEATED_TOOL_CALL` 错误结果，同组未分派调用追加 `ABORTED_BEFORE_DISPATCH`，随后终止。
- 连续 3 个工具轮次全部失败 → `TOOL_FAILURE_LIMIT`。默认 `max_steps=20`（CLI 可设 1-50），第 20 步工具组执行完后阻止第 21 次请求。
- `StopReason`：`FINAL_ANSWER / MAX_STEPS / MODEL_ERROR / PROTOCOL_ERROR / CONTEXT_OVERFLOW / TOOL_FAILURE_LIMIT / REPEATED_TOOL_CALL / INTERRUPTED / INTERNAL_ERROR`。

### 事件

每个事件含 run 内单调递增 `sequence`、`run_id`、类型、step 和脱敏 payload。至少八类：`run_started / step_started / model_retry / assistant_received / tool_started / tool_finished / completion_deferred / run_finished`；`run_finished` 在每个已开始 run 中恰好出现一次且 sequence 最大。事件中不含 API key 或完整大工具输出。

### 消息不变量

- canonical history 只追加，上下文裁剪只作用于每步临时派生的 request view。
- 下一次模型请求之前，每个 assistant tool call 恰有一个同 ID 的 tool result；重复/空 call ID 属协议错误并终止，且为诊断起见补齐全部结果。
- Ctrl+C 会尝试终止当前 `run_command` 拥有的子进程，当前调用返回 `TOOL_ABORTED`，同组未分派调用返回 `ABORTED_BEFORE_DISPATCH`，随后以 `INTERRUPTED` 结束，CLI 退出码 130。

## 测试

全部测试离线运行，使用 fake/scripted model 与真实临时工作区工具，不访问网络、不依赖真实 API 或开发者机器路径：

```powershell
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run coding-agent --help
uv run python -m coding_agent --help
```

覆盖：配置与 CLI、模型响应标准化与重试分类、上下文投影与 canonical 不变性、六工具契约与边界（路径越界、预算、观察版本、原子替换、超时/取消、head-tail 保留）、AgentLoop 全部终止分支、重复调用提醒/终止、半组取消、以及“glob → grep → read → edit → purpose=verify run → 最终答复”的离线端到端闭环（`RunResult.verification_status == VERIFIED`）。

## 已知限制

- 字符预算是确定性近似，不是精确 token 计数。
- `glob`/`grep` 使用标准库，性能不与 ripgrep 等同；ripgrep 后端属于后续路线。
- SHA-256 观察是单进程尽力新鲜度防护，存在跨进程 TOCTOU 窗口；不宣称事务或 CAS。
- `run_command` 无安全沙箱：任意可执行文件可能访问工作区外资源。只应在可信或一次性工作区使用。
- 首版只实现一个 OpenAI-compatible Chat Completions 适配器；流式输出、会话持久化、审批 UI、容器隔离、repository map 等均不在 MVP 范围。
- 完成验证证据是“agent 声明的 verify 命令 + 退出码”，不是测试充分性证明。
