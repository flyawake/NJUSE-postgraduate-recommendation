# coding-agent

一个完全本地实现核心循环的编程智能体（coding agent）MVP：模型只负责决策，项目代码负责对话历史、工具定义与执行、输出解析、循环终止和错误处理。它使用普通 `openai` Python 客户端和 OpenAI-compatible Chat Completions tool calling，不依赖任何 agent 框架或 Agent SDK。

> 本实现是可审计的教学/原型内核。`run_command` 会在本机直接执行任意可执行文件，**不是安全沙箱**，只能在可信或一次性工作区中运行。

## 安装

要求 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --all-groups
```

运行时依赖为 `openai` 客户端与本地 GUI 服务（FastAPI/Starlette + Uvicorn），开发期额外有 `pytest`、`ruff`、`httpx`；锁文件为 `uv.lock`。Node.js 只用于前端开发与构建：运行 GUI 不需要安装 Node，已构建的静态资源随 Python 包分发。

## 配置

### 方式一：GUI profile（推荐）

`uv run coding-agent ui` 打开设置页后，选择 provider（OpenAI / DeepSeek / 自定义兼容服务），填写 base URL、model 与凭据引用并保存。profile 与凭据保存在用户目录 `CODING_AGENT_HOME/config.json` 与 `credentials.json`（默认 `~/.coding-agent/`，不会写入工作区）。凭据只写不读：界面永远只显示“已配置/来源/可写性”，API 与日志从不回显明文。

只读命令行查看 profile：

```powershell
uv run coding-agent config list
uv run coding-agent config show <profile-id>
```

### 方式二：legacy 环境变量（CLI 回退）

```powershell
$env:OPENAI_API_KEY = "sk-..."        # 必需（无 profile 时）
$env:OPENAI_MODEL = "your-model"      # 必需（无 profile 时）
$env:OPENAI_BASE_URL = "https://..."  # 可选，OpenAI-compatible 网关
```

解析优先级为 **显式 profile > 当前激活 profile > legacy `OPENAI_*`**；显式/激活 profile 无效时直接失败，不会静默切换 provider。`.env.example` 展示了全部变量格式；真实 `.env` 已被 `.gitignore` 排除。凭据引用 `<ref>` 也可由环境变量提供：`openai` 映射到 `OPENAI_API_KEY`，其他引用映射到 `CODING_AGENT_CRED_<REF>`；环境命中时 GUI 写入被拒绝（只读）。

## 运行

### 本地图形界面

```powershell
uv run coding-agent ui                 # 自动选择可用端口并打开浏览器
uv run coding-agent ui --port 5173     # 指定端口
uv run coding-agent ui --no-browser    # 仅打印地址（自动化测试用）
```

服务只监听 `127.0.0.1`。页面在没有工作区、API key 或网络时也能打开并进入设置；启动服务器不会触发任何模型请求。

首次使用流程（约 1 分钟）：

1. 启动 `coding-agent ui`，如无 profile 会自动出现 onboarding；选择 provider → 填写 URL/model → 写入凭据引用与凭据 → 保存。
2. 在新任务页输入工作区路径（校验通过后显示“工作区可用”）、选择或继承 profile。
3. 输入编程任务，点击“开始运行”（或 `Ctrl+Enter`）。
4. 实时观察活动流：工具调用按连续组显示，当前执行项保持展开，成功的连续操作完成后折叠为“已完成 N 项操作”；右侧运行详情展示状态、逻辑步数、模型请求数、工具调用数、耗时、验证状态与变更文件。
5. 运行中出现问题可点击“取消运行”（变为“正在取消…”，幂等）。运行完成后顶部显示 `VERIFIED / NOT_APPLICABLE / FAILED / NOT_RUN`，最终答复位于活动流末端，页面可再次开始新任务（当前版本不承诺多轮会话）。
6. 页面任意时刻刷新或断线重连后，会从服务端快照与事件流恢复。

页面默认简体中文，可在右上角切换完整英文；主题支持跟随系统/浅色/深色。

### CLI（回退与脚本化入口）

```powershell
uv run coding-agent --workspace <path> "<programming task>"
uv run coding-agent --workspace <path> --profile <profile-id> --model <name> --base-url <url> --max-steps 20 "<task>"
uv run python -m coding_agent --workspace <path> "<task>"
```

`--model`/`--base-url` 只覆盖本次运行、不写回；不提供 `--api-key`。退出码：`0` 正常完成（含“完成但未验证”的如实标注），`1` 配置/协议/循环/工具基础设施失败，`130` 用户中断。`--help`、`ui --help`、`config --help` 不读取凭据、不访问网络。

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
  -> 再次检查取消（policy 后、handler 前，已取消则记 ABORTED_BEFORE_DISPATCH）
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

文件安全机制：拒绝绝对路径、`..` 越界和符号链接逃逸；搜索工具对 `os.walk` 的每个候选文件做逐候选 canonical containment 检查，`grep` 不读取、`glob` 不返回解析后位于 workspace 外的符号链接文件，也不跟随目录链接。只有成功 `read_file` 才建立观察（路径键统一规范化，`./a.txt` 与 `a.txt` 视为同一文件），覆盖/编辑前重新计算 SHA-256，未观察返回 `FILE_NOT_OBSERVED`、版本变化返回 `FILE_STALE`；写入走同目录临时文件 + flush/close + `os.replace`，成功后刷新观察。该机制是单进程下的尽力新鲜度防护，**不是跨进程 CAS 或沙箱**。

### 本地 GUI 服务（task_002）

GUI 是 AgentLoop 的展示与控制适配层，不复制或重写内核：

```text
React/TypeScript UI (Vite production assets)
  <-> typed JSON API + SSE
FastAPI local app server (loopback only)
  -> RunController (单 active run，worker 线程 + 取消 Event + 有界事件存储)
       -> resolve_connection -> ModelClientFactory -> 既有 AgentLoop
       -> AgentEvent 适配 -> 白名单 DTO -> SSE 订阅者
  -> ProviderCatalog / ProfileStore / CredentialService
```

- `RunController` 在受控 worker 线程中运行同步的 `AgentLoop.run`，HTTP 事件循环不被模型/工具调用阻塞；取消只设置既有 cancellation seam，最终产生唯一 terminal snapshot；重复 start 返回稳定 `run_already_active` 冲突，不创建第二个 AgentLoop。
- 事件存储有数量与字符双重上限；事件 ID 单调，快照为事实基线，SSE 断线重连按 ID 去重、落后于保留尾时强制 reset 并重取快照。
- API 错误使用稳定 `code` + 用户可读 `message` + 不含 secret 的 `field`；前端从不解析 message 判断逻辑。
- Profile 存取：`config.json` 顶层固定 `version:1/active_profile/profiles`；profile ID 创建后不可改名；`wire_api` 当前唯一允许 `openai_chat_completions`；ModelClientFactory 按 wire API 分派，AgentLoop 中不存在 provider 名称分支。
- 凭据：`credentials.json` 与 config 分离，只有 `ref -> secret`，读取接口只返回 `configured/source/writable`；写入用同目录临时文件 + flush/fsync + `os.replace`（POSIX 目录 0700 / 文件 0600，尽力而为）；损坏文件拒绝写入并保留原文件。
- 安全：仅监听 loopback；校验 Host/Origin；状态变更必须携带随机会话令牌（`X-Coding-Agent-Token`，由 `/api/bootstrap` 下发，前端仅在内存中保存）；不配置宽泛 CORS；CSP `default-src 'self'` 禁止外部脚本与 CDN；profile 的 base URL 仅接受绝对 HTTP(S)（HTTP 仅限 loopback、无 userinfo/query/fragment）；workspace 在启动 run 前解析为已存在目录并交给既有路径守卫。
- 前端：TypeScript + React + Vite + Tailwind（全部视觉值来自 design tokens）+ Radix Primitives（Dialog/Select/Tabs/Collapsible/AlertDialog）+ Lucide；TanStack Query 负责 snapshot/config，SSE 增量事件进入独立 reducer；i18n 资源完整 zh-CN/en-US；面板随视口切换（窄屏检查器为抽屉）；状态（空闲/运行中/完成/失败/取消、验证通过/失败/未验证/无需验证）均配图标与文字，不只依赖颜色。

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
- Ctrl+C 会尝试终止当前 `run_command` 拥有的子进程，当前调用返回 `TOOL_ABORTED`，同组未分派调用返回 `ABORTED_BEFORE_DISPATCH`，随后以 `INTERRUPTED` 结束，CLI 退出码 130。取消不仅在每次分派前检查，还在 policy/prepare 返回后、handler 启动前再次检查，保证已取消的 WRITE/EXECUTE 不产生副作用，且每个模型调用只计数一次。

## 测试

全部 Python 测试离线运行，使用 fake/scripted model 与真实临时工作区工具，不访问网络、不依赖真实 API 或开发者机器路径：

```powershell
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run coding-agent --help
uv run python -m coding_agent --help
```

前端（Node 22+；`npm ci` 生成可复现安装）：

```powershell
npm ci
npm run typecheck
npm run lint
npm test -- --run
npm run gen:api      # 从后端 OpenAPI 重新生成 TS 类型（schema.json/schema.d.ts）
npm run check:api    # 校验重新生成无 diff
npm run build        # 输出到 src/coding_agent/web/static（随 wheel 分发）
npm run test:e2e     # Playwright + Fake Model：生产 build 闭环、取消/重连、secret 零泄漏
```

覆盖：配置与 CLI、模型响应标准化与重试分类、上下文投影与 canonical 不变性、六工具契约与边界（路径越界、预算、观察版本、原子替换、超时/取消、head-tail 保留）、AgentLoop 全部终止分支、重复调用提醒/终止、半组取消、以及“glob → grep → read → edit → purpose=verify run → 最终答复”的离线端到端闭环（`RunResult.verification_status == VERIFIED`）；task_002 另有 profile/凭据/URL 校验/原子写入、RunController 单 run/取消/有界事件/终态唯一、Web API DTO 与安全（Host/Origin/会话令牌/CSP/secret 零泄漏）、SSE 重连去重、前端组件状态与 i18n 资源完整性测试。

## 已知限制

- 字符预算是确定性近似，不是精确 token 计数。
- `glob`/`grep` 使用标准库，性能不与 ripgrep 等同；ripgrep 后端属于后续路线。
- SHA-256 观察是单进程尽力新鲜度防护，存在跨进程 TOCTOU 窗口；不宣称事务或 CAS。
- `run_command` 无安全沙箱：任意可执行文件可能访问工作区外资源。只应在可信或一次性工作区使用。
- 本地 GUI 服务只监听 `127.0.0.1`：任何本机进程/网页仍可能发起请求，因此状态变更要求随机会话令牌且页面使用 CSP；普通网页无法可靠返回本机目录绝对路径，工作区使用路径输入加后端校验。
- 凭据文件是用户目录内的本地明文 JSON（Windows 上没有 OS 密钥串级别的加密），推荐使用环境变量；不宣称加密存储。
- GUI 仅支持一个 provider 协议：`openai_chat_completions`（OpenAI/DeepSeek/自定义网关皆为 Chat Completions 兼容）；不支持 Anthropic Messages、OpenAI Responses 或 OAuth。
- 首版每个应用进程只允许一个 active run；“最近运行”仅限当前进程内有界记录，不承诺跨进程历史。
- 完成验证证据是“agent 声明的 verify 命令 + 退出码”，不是测试充分性证明。
