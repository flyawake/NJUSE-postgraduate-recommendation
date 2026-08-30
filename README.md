# coding-agent

一个完全本地实现核心循环的编程智能体（coding agent）MVP：模型只负责决策，项目代码负责对话历史、工具定义与执行、输出解析、循环终止和错误处理。它使用普通 `openai` Python 客户端，支持 OpenAI-compatible Chat Completions 与 OpenAI Responses，不依赖任何 agent 框架或 Agent SDK。

> 本实现是可审计的教学/原型内核。GUI 为每个对话持久保存 `run_command` 策略：每次询问、默认允许或默认拒绝；选择“每次询问”时会在启动进程前弹出一次性权限确认。允许后仍是在宿主机直接执行，**不是操作系统安全沙箱**。CLI/直接库调用属于显式可信模式，只应在可信或一次性工作区运行。

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
2. 从左栏点击“新对话”，选择工作区与 profile；对话会按 workspace 分组，并可搜索、重命名、归档、恢复或确认后永久删除。
3. 在底部 Composer 输入编程任务并点击右侧“开始运行”。同一对话可连续追问，模型上下文来自持久化 canonical history；切换到其他对话不会取消后台 turn。
4. 中栏以连续平面展示工具活动、验证状态和最终答复。运行中同一按钮槽切换为“取消运行”；左栏用状态点显示后台运行。
5. 每个 terminal turn 的末尾显示本轮净文件变化。点击文件才会打开右侧只读审查栏，可切换 Diff/修改前/修改后/当前文件；历史内容来自不可变快照，当前文件继续变化时会提示 divergence。关闭审查栏后中栏自动恢复宽度。
6. 对话、turn、canonical message、公开事件和文件快照跨页面刷新及服务重启保持；崩溃时未完成 turn 只恢复为 `INTERRUPTED`，不会自动重放命令或文件写入。

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
            -> ToolRegistry -> 六个 workspace 工具 + web_search/web_fetch
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

同一个调用有三种分离表示：内部 `ToolOutcome` 保留结构化语义；`model_content` 以稳定 JSON 发给模型；公开事件/CLI 经**字段级脱敏**——`write_file.content`、`edit_file.old_string/new_string` 永不进入事件，`run_command.argv` 只保留可执行名与安全旗标、其余参数以 `***` 呈现，worker 异常文本一律不进入 API。

| 工具 | effect | 关键语义 |
| --- | --- | --- |
| `glob(pattern, path=".")` | READ | 标准库实现；发现第 101 个匹配即停止，扫描项上限 50,000；跳过 `.git/.venv/node_modules` 与常见缓存/构建目录；省略量标明为下界 |
| `grep(pattern, path=".", include?)` | READ | 逐行 UTF-8 正则搜索；发现第 201 条即停止；单文件≤2 MiB、整次扫描≤64 MiB/10,000 文件、每行搜索≤20,000 字符，结果≤200 条 |
| `read_file(path, offset=1, limit=200)` | READ | 流式哈希和逐行窗口；文件≤16 MiB、limit ≤500、窗口≤50 KiB；返回 `total_lines`、`next_offset` 与 SHA-256 指纹 |
| `write_file(path, content)` | WRITE | 创建或整体覆盖；父目录自动创建；单次 ≤1 MiB；覆盖必须先观察当前版本 |
| `edit_file(path, old_string, new_string, replace_all=false)` | WRITE | 字面量替换；默认唯一匹配；`replace_all=true` 至少匹配一次；先读后改 + 版本新鲜度 |
| `run_command(argv, cwd=".", timeout_seconds=30, purpose="inspect\|verify\|other")` | EXECUTE | `shell=False`；GUI 在进程启动前应用当前对话持久策略（每次询问/默认允许/默认拒绝）；cwd 必须在工作区内；超时 1-120 秒；后台持续排空 stdout/stderr、内存中各只保留 head 4000 + tail 6000 字符；非零退出码仍是成功观察 |
| `web_search(query, max_results=5)` | READ | 搜索公开互联网；结果数≤10，只返回有界 title/url/snippet；网页结果始终视为不可信观察 |
| `web_fetch(url, max_chars=12000)` | READ | 仅抓取公开 HTTP(S) 文本页；拒绝凭据、非标准端口、本机/私网/保留地址及重定向 rebinding；不执行脚本，响应≤1 MB、正文≤20,000 字符 |

所有结果统一为 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": {"code", "message", "retryable", "recovery_hint"?}}`，错误 code 稳定且属于本项目。

文件安全机制：拒绝绝对路径、`..` 越界和符号链接逃逸；搜索工具对 `os.walk` 的每个候选文件做逐候选 canonical containment 检查，`grep` 不读取、`glob` 不返回解析后位于 workspace 外的符号链接文件，也不跟随目录链接。只有成功 `read_file` 才建立观察（路径键统一规范化，`./a.txt` 与 `a.txt` 视为同一文件），覆盖/编辑前重新计算 SHA-256，未观察返回 `FILE_NOT_OBSERVED`、版本变化返回 `FILE_STALE`；写入走同目录临时文件 + flush/close + `os.replace`，成功后刷新观察。该机制是单进程下的尽力新鲜度防护，**不是跨进程 CAS 或沙箱**。

### 持久多轮 GUI 服务（task_004）

GUI 是 AgentLoop 的展示与控制适配层，不复制或重写内核：

```text
React/TypeScript UI (Vite production assets)
  <-> typed JSON API
FastAPI local app server (loopback only)
  -> ConversationService（生命周期、事务与兼容 API）
       -> SQLiteConversationRepository（state.db 事实源）
       -> RuntimeRegistry（每会话 active turn、workspace lease、全局 worker 上限）
       -> CanonicalJournal + AgentLoop
       -> ToolChangeCollector -> content-addressed ArtifactStore
  -> ProviderCatalog / ProfileStore / CredentialService
```

- `ConversationService` 是 Web 执行的唯一编排入口；旧 `/api/runs` 在兼容期只把一个持久 turn 投影成旧 DTO，不维护第二套 worker/history/event。CLI 继续直接适配既有 AgentLoop。
- `CODING_AGENT_HOME/state.db` 使用 SQLite foreign keys、WAL、busy timeout、显式 schema version 与事务。创建 turn、分配 ordinal、持久化首条 user canonical group 和更新 activity 原子提交；idempotency key 有持久 unique constraint。
- 每个 conversation 同时最多一个 active turn；相同 canonical workspace 整轮互斥，不同 workspace 进入默认上限为 2 的 `ThreadPoolExecutor`。页面 selection 与 runtime 解耦。
- 文件变化按 turn 的首个 before 与最终 after 合并，成功 write/edit 是 confirmed 证据，`run_command` 使用有文件数/字节/时间预算的 workspace probe 补充副作用；超预算 fail-closed 为 incomplete，但不阻止主 turn 完成。
- 文本 artifact 单个最多 1 MiB、单 turn 新增快照默认最多 20 MiB，CAS 按 SHA-256 去重并校验读取完整性；delete 事务清理引用，启动时可重试物理 GC。preview 只接受 conversation→turn→change 的层级 ID，不接受任意路径读取。
- API 错误使用稳定 `code` + 用户可读 `message` + 不含 secret 的 `field`；前端从不解析 message 判断逻辑。
- Profile 存取：`config.json` 顶层固定 `version:1/active_profile/profiles`；profile ID 创建后不可改名；`wire_api` 支持 `openai_chat_completions` 与 `openai_responses`；ModelClientFactory 按 wire API 分派，AgentLoop 中不存在 provider 名称分支。
- 凭据：`credentials.json` 与 config 分离，只有 `ref -> secret`，读取接口只返回 `configured/source/writable`；写入用同目录临时文件 + flush/fsync + `os.replace`（POSIX 目录 0700 / 文件 0600，尽力而为）；损坏文件拒绝写入并保留原文件。
- 安全：仅监听 loopback；Host 必须为语法合法的 loopback 主机（IPv4/IPv6 字面量或 localhost，端口必须为数字，畸形 Host 直接 403）；Origin 必须与当前请求的 scheme/host/effective port **精确一致**；状态变更必须携带随机会话令牌；CSP 禁止外部脚本与 CDN。Composer 的命令权限选择写入 SQLite，按 conversation 隔离并在刷新/重启后恢复。切换到 `allow` 前必须在风险警告框中二次确认，取消不会修改持久设置；`ask` 在 `Popen` 前由内存权限 broker 暂停并展示完整 argv/cwd，`deny` 在进程创建前拒绝，`allow` 保留直接执行能力。挂起时改为允许/拒绝会立即处理当前申请。允许后进程仍可能访问工作区外文件、网络和继承环境，因此界面明确不把它表述为沙箱。
- 前端：TypeScript + React + Vite + Tailwind（全部视觉值来自 design tokens）+ Radix Primitives（Dialog/Select/Tabs/Collapsible/AlertDialog）+ Lucide；TanStack Query 负责持久 snapshot 与按 cursor 拉取事件，投影器只增量处理新 event；legacy `/api/runs` 继续保留 SSE 兼容；i18n 资源完整 zh-CN/en-US，侧栏和文件审查在窄屏分别进入可访问 drawer。
- 聊天附件：Composer 支持选择、拖放与粘贴 PNG/JPEG/GIF/WebP，以及 PDF、UTF-8 文本/代码和常见 Office 文件。单文件≤10 MiB、每轮≤4 个且合计≤20 MiB；二进制保存在 `CODING_AGENT_HOME/attachments/`，SQLite/canonical history 只保存引用与元数据。turn 创建事务原子认领附件；ContextManager 在 detached request view 中将图片/文件映射到 Chat Completions 或 Responses 的对应输入格式，文本附件最多内联 50,000 字符。

本机数据边界：`state.db` 保存对话、模型消息和运行事件，`artifacts/sha256/` 保存历史文件审查所需的有界快照，`attachments/sha256/` 保存聊天附件；它们均为本地明文，不由应用主动同步。模型上下文与用户选择的附件仍会发送给用户选择的 provider。归档只是可恢复隐藏；永久删除会清理该对话及不再被其他 turn 引用的快照/附件，绝不删除 workspace 项目文件。

### 上下文投影

`canonical history` 只追加、永不改写；每步由 ContextManager 生成独立的临时 request view。预算分三层：258,000 字符用于确定性历史压缩；每个 profile 配置真实 `context_window_tokens`（默认 128,000，并预留 8,000 tokens 给输出），发送前做 provider-neutral token 估算；完整请求体（包括 base64）另设 32 MiB 字节上限。图片按有界视觉输入估算，普通文件按内联编码保守估算，但二者的完整 base64 都计入请求字节。任一保护项超过相应预算即以 `CONTEXT_OVERFLOW` 终止，不把超大请求交给 provider。

### 完成验证与终止

- 成功的 `write_file`/`edit_file` 记录变更路径并推进内存中的 workspace revision；只有最新 revision 之后、`purpose="verify"` 的 `run_command` 才构成验证尝试：退出码 0 → `VERIFIED`，非零 → `FAILED`，没有尝试 → `NOT_RUN`，没有变更 → `NOT_APPLICABLE`。`purpose` 只是 agent 声明的意图，退出码 0 只证明该命令成功，**不证明测试集合充分**。
- 有变更但未验证时，CompletionPolicy 最多延迟完成一次：追加 `source=completion_policy`、以 `[completion-policy]` 前缀呈现的内部控制消息并回到 READY；无剩余 step 或提醒后模型再次给出非空最终文本时允许结束，但 `RunResult` 如实保留 `FAILED`/`NOT_RUN`，CLI 醒目标注“完成但未验证/验证失败”。
- 相同“工具名 + 深度排序后 JSON 参数”签名连续第 3 次：仍执行并追加模型可见换路提醒；连续第 5 次：不再执行，追加 `REPEATED_TOOL_CALL` 错误结果，同组未分派调用追加 `ABORTED_BEFORE_DISPATCH`，随后终止。
- 连续 3 个工具轮次全部失败 → `TOOL_FAILURE_LIMIT`。默认 `max_steps=20`（CLI 可设 1-50），第 20 步工具组执行完后阻止第 21 次请求。
- `StopReason`：`FINAL_ANSWER / MAX_STEPS / MODEL_ERROR / PROTOCOL_ERROR / CONTEXT_OVERFLOW / TOOL_FAILURE_LIMIT / REPEATED_TOOL_CALL / INTERRUPTED / INTERNAL_ERROR`。

### 事件

每个事件含 run 内单调递增 `sequence`、`run_id`、类型、step 和字段级脱敏 payload。至少八类：`run_started / step_started / model_retry / assistant_received / tool_started / tool_finished / completion_deferred / run_finished`；`run_finished` 在每个已开始 run 中恰好出现一次且 sequence 最大。写文件内容、编辑字符串与命令敏感参数不会进入事件，worker 异常文本不会进入 API。

### 消息不变量

- canonical history 只追加，上下文裁剪只作用于每步临时派生的 request view。
- 下一次模型请求之前，每个 assistant tool call 恰有一个同 ID 的 tool result；重复/空 call ID 在写入 journal 前即判为协议错误，非法 assistant 组和诊断结果只留在本轮内存、绝不进入持久 canonical history。
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
npm run test:e2e     # Playwright + Fake Model：多轮、后台切换、文件审查、生命周期、窄屏生产闭环
```

覆盖：配置与 CLI、模型响应标准化与重试分类、上下文投影与 canonical 不变性、六工具契约与边界、AgentLoop 全部终止分支，以及“glob → grep → read → edit → purpose=verify run → 最终答复”的离线闭环；GUI 另覆盖 profile/凭据/URL、安全边界、Conversation CRUD、多轮隔离、原子 turn、崩溃恢复、并发幂等/workspace lock、201 条分页、ChangeSet、command probe、artifact 完整性与精确 GC、前端组件/i18n，以及 production Fake Model 下的三轮对话、后台切换、归档恢复删除、历史 diff 和 320px drawer。

## 已知限制

- token 预算是 provider-neutral 保守估算，不等同于 provider 的精确 tokenizer；profile 必须填写模型实际 context window，另有独立请求字节上限兜底。
- `glob`/`grep` 使用标准库，性能不与 ripgrep 等同；ripgrep 后端属于后续路线。
- SHA-256 观察是单进程尽力新鲜度防护，存在跨进程 TOCTOU 窗口；不宣称事务或 CAS。
- `run_command` 仍无操作系统级沙箱：GUI 的“每次询问/默认拒绝”可防止静默执行，但用户选择“默认允许”或批准单次命令后，任意可执行文件仍可能访问工作区外资源。CLI/库调用是可信模式。
- 本地 GUI 服务只监听 `127.0.0.1`：任何本机进程/网页仍可能发起请求，因此状态变更要求随机会话令牌、Host/Origin 精确同源校验且页面使用 CSP；普通网页无法可靠返回本机目录绝对路径，工作区使用路径输入加后端校验。
- 凭据文件是用户目录内的本地明文 JSON（Windows 上没有 OS 密钥串级别的加密），推荐使用环境变量；不宣称加密存储。
- GUI 支持 `openai_chat_completions` 与 `openai_responses`；尚不支持 Anthropic Messages 或 OAuth，DeepSeek profile 只允许其 Chat Completions 兼容协议。
- Queue/Steer 只在工具组已完整提交且没有工具执行的安全边界送入模型上下文，不会中断正在进行的模型请求或工具调用；跨会话 Memory 使用本地词项/FTS 检索，不提供 embedding 语义检索。
- 默认允许最多 2 个不同 workspace 的后台 turn；相同 workspace 采用保守整轮互斥，不区分只读与写入任务。
- 完成验证证据是“agent 声明的 verify 命令 + 退出码”，不是测试充分性证明。
