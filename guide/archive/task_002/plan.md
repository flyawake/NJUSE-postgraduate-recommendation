# 任务编号：task_002 - 本地图形化 Coding Agent 与多服务商配置

## 1. 任务目标

把 task_001 的命令行内核升级为可直接演示完整工作流的本地图形化应用。用户启动 `coding-agent ui` 后，应能在浏览器界面中选择工作区、输入编程任务、选择模型 profile、启动或取消运行，并实时看到 Agent 状态、工具调用轨迹、验证状态和最终答复；设置界面应能创建和编辑 OpenAI、DeepSeek 或自定义 OpenAI-compatible profile，以及安全地写入或清除凭据。

CLI 必须保留，作为脚本化、测试和故障回退入口。GUI 是 AgentLoop 的展示与控制适配层，不复制或重写 AgentLoop、ToolExecutor、ContextManager 和 CompletionPolicy。

本任务的最小图形化交付不是“给配置命令套一个网页”，而是一个可以完成以下演示闭环的应用：

```text
打开本地应用
  -> 选择 workspace 与模型 profile
  -> 输入真实编程任务并开始
  -> 实时观察模型/工具/验证事件
  -> 必要时取消
  -> 查看最终结果、状态、计数和工作区变更摘要
```

本任务不包含：

- 公网部署、多用户账户、云同步、移动端或 Electron/Tauri 原生安装包。
- 在页面中实现完整 IDE、Monaco 编辑器、Git 客户端或任意文件管理器。
- Anthropic Messages、OpenAI Responses、Azure/Bedrock/Vertex/Codex OAuth 等原生协议或认证；本阶段只承诺 `openai_chat_completions` wire API。
- 会话跨进程永久保存、MCP、插件、多智能体或远程执行。
- task_001 的 R1-R3 整改。task_002 只有在 task_001 通过复验后才能进入“进行中”。

## 2. 设计研究与取舍

UI 可学习成熟项目的信息架构和交互经验，但不得复制品牌、图标组合、文案或源码：

- [Codex App Server](https://learn.chatgpt.com/docs/app-server) 把富客户端建立在稳定的 thread/turn/item 生命周期、流式 agent events、取消与最终状态之上；本项目对应为 RunController、公开事件 DTO、事件流和唯一终态。
- [Codex code review](https://learn.chatgpt.com/docs/code-review) 与 [integrated terminal](https://learn.chatgpt.com/docs/integrated-terminal) 把对话、变更检查和验证放在同一工作空间中；本项目采用“运行流 + 检查器”双层信息结构，但 task_002 不实现完整终端模拟器。
- [OpenHands Agent Canvas](https://github.com/OpenHands/OpenHands) 使用 React/TypeScript 前端、独立 Agent Server/类型化客户端、REST snapshot 后接实时事件、折叠连续 action group、设置页和 mock-LLM 浏览器端到端测试；本项目吸收接口分层、重连与事件折叠经验。
- [Cline](https://github.com/cline/cline) 的图形客户端使用侧栏、对话区、任务输入、设置与 onboarding 分区；本项目借鉴信息层级，不复制其视觉风格。
- [React TypeScript 官方文档](https://react.dev/learn/typescript)、[Vite 官方文档](https://vite.dev/guide/)、[Radix accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility) 与 [WCAG 2.2 状态消息说明](https://www.w3.org/WAI/WCAG22/Understanding/status-messages) 作为实现和可访问性依据。

选型结论：

- **前端语言**：TypeScript，而非无类型 JavaScript。AgentEvent、RunSnapshot、Profile 和错误对象都用判别联合/生成类型表示，避免 UI 通过自由文本猜状态。
- **UI 框架**：React，适合事件驱动、组件化的长运行 Agent 界面，也与当前优秀开源 Coding Agent 前端的实践一致。
- **构建工具**：Vite。开发期使用快速 dev server，发布时输出静态资源，由 Python 应用直接托管。
- **样式与组件**：Tailwind CSS + 项目自有 design tokens；Dialog、Tabs、Select、Tooltip、Collapsible 等复杂交互优先使用 Radix Primitives；图标使用 Lucide。不得直接套用其他产品主题。
- **数据层**：REST snapshot/config 使用 TanStack Query；SSE 增量事件进入独立 reducer/store。API TypeScript 类型从后端 OpenAPI schema 生成并提交一致性检查。
- **前端测试**：Vitest + React Testing Library；关键演示闭环使用 Playwright + Fake Model。
- **后端**：FastAPI/Starlette 体系 + Uvicorn，提供本地 JSON API、OpenAPI schema、静态资源和 SSE。不得使用 Agent SDK 或后端 UI 框架代替本项目内核。

本机已有 Node.js 22 与 npm，可支持该构建链。生产运行只读取已构建并随 Python 包分发的静态资源；评审者运行 GUI 不应被要求安装 Node。

## 3. 产品与视觉规格

详细界面结构、状态、文案和视觉约束见 [ui_spec.md](ui_spec.md)。核心产品要求如下。

### 3.1 启动与导航

- 新增 `uv run coding-agent ui`，默认只监听 `127.0.0.1` 的可用端口并自动打开系统浏览器；提供 `--port`、`--no-browser`，task_002 不支持非 loopback host。
- 页面在没有 workspace、模型 key 或网络时仍能打开并进入设置；启动服务器不能触发模型请求。
- 左侧导航负责“新任务/当前运行”和“模型设置”；中央是任务与 Agent 活动流；右侧检查器展示运行状态、计数、验证和变更文件摘要。
- 1280×720 为录屏主视口；窄屏时右侧检查器变为抽屉，左栏可折叠。支持明暗主题，遵循系统偏好并允许手动切换。

### 3.2 主运行页

- workspace 路径输入、profile 选择、多行任务输入、开始和取消。
- 状态摘要：phase、status、verification、stop reason、logical steps、provider attempts、tool-call count、elapsed time。
- 活动流：用户任务、可公开的 Agent 文本、工具组、验证和错误。连续成功工具可折叠成 action group；当前执行项保持展开。
- 工具卡只显示工具名、脱敏参数摘要、时间、结果状态和有界预览；不展示隐藏推理、API key、完整环境或无界命令输出。
- 页面刷新或事件流重连后从服务端 snapshot 恢复当前 run 的已知状态和有界事件。
- 变更摘要只依据明确的成功 write/edit 事件列出文件；不得伪造完整 diff。完整 diff viewer 留给后续任务。

### 3.3 设置与首次使用

- 首次打开且没有可用 profile 时显示简短 onboarding，引导“选择 provider → URL/模型 → 凭据 → 保存 → 开始任务”，随时可退出设置。
- 设置页可列出、创建、编辑、激活、删除 OpenAI、DeepSeek、Custom profile。
- credential 只允许通过 password input set/replace/unset；读取端只显示 `configured/source/writable`，永远不回显明文。
- 保存前显示脱敏预览；取消或校验失败时原配置不变。
- 保留脚本化只读命令 `coding-agent config list/show`；不再要求终端交互式 `configure` 向导。

### 3.4 界面自然语言

- 默认界面语言为简体中文 `zh-CN`，这是当前演示与主要用户场景最清晰的选择；同时提供完整 `en-US`，可在设置中切换。
- 所有用户可见字符串进入 i18n 资源，组件中不散落中文/英文硬编码。语言偏好只保存为非敏感本地 UI preference。
- “工作区、开始运行、取消、验证通过”等动作使用自然中文；provider、profile、model、API、AgentLoop 等必须保留的技术名词在帮助文本中给出准确英文，避免生硬直译。
- 模型最终答复使用什么语言由用户任务和系统提示决定，不与 UI 语言强耦合。

## 4. 技术架构

### 4.1 前后端边界

```text
React/TypeScript UI
  <-> typed JSON API + SSE
FastAPI local app server (loopback only)
  -> RunController (one active run, worker + cancellation Event)
       -> ConfigResolver -> ModelClientFactory -> existing AgentLoop
       -> AgentEvent adapter -> bounded event store/SSE subscribers
  -> ProviderCatalog / ProfileStore
  -> CredentialService
```

- AgentLoop 可保持同步；RunController 在受控 worker 中运行，HTTP 事件循环不得被模型或工具调用阻塞。
- 首版每个应用进程只允许一个 active run。重复 start 返回稳定冲突错误，不创建第二个 AgentLoop。
- RunController 把现有结构化 AgentEvent 转成白名单公开 DTO；SDK 类型、内部异常和 secret 不得进入 API。
- SSE 使用单调 event ID；客户端以 snapshot 为事实基线再合并增量事件，按 ID 去重。事件存储有条数和字符上限，慢客户端不能无限占用内存。
- 取消端点只设置 AgentLoop 已有 cancellation seam；最终必须产生唯一 terminal snapshot。

### 4.2 HTTP/API 最小契约

至少提供等价语义的接口，具体路径可调整但必须由 OpenAPI 和测试固定：

- `GET /api/health`：无需配置，返回应用版本与 idle/running。
- `GET /api/bootstrap`：返回脱敏 profiles、active profile、当前 run snapshot、locale/theme preferences 和能力信息。
- `POST /api/runs`：校验 workspace/task/profile 后启动 run，返回 run ID；错误时 worker 未创建。
- `GET /api/runs/{id}`：返回 snapshot、计数、最终答复和有界事件。
- `GET /api/runs/{id}/events`：SSE 增量事件，支持 last event ID。
- `POST /api/runs/{id}/cancel`：幂等请求取消。
- profile CRUD/activate 与 credential set/unset：只接收必要字段并返回脱敏 descriptor。

API 错误使用稳定 `code`、用户可读 `message` 和不含 secret 的 `field`；校验失败为 4xx，内部错误为脱敏 5xx。前端不得解析 message 判断逻辑。

### 4.3 Profile 与普通配置

- 默认用户目录 `~/.coding-agent/`，测试通过 `CODING_AGENT_HOME` 覆盖；不得写入 workspace。
- `config.json` 顶层固定 `version: 1`、`active_profile` 与 `profiles` object。
- profile 至少包含 `provider_id`、`display_name`、`wire_api`、`base_url`、`model`、可选 `credential_ref`；profile ID 创建后不可原地重命名。
- `wire_api` 当前唯一允许值为 `openai_chat_completions`。ProviderCatalog 提供 OpenAI、DeepSeek、Custom；不硬编码易过时的模型清单。
- ModelClientFactory 按 wire API 选择 adapter，AgentLoop 中不得出现 provider 名称分支。
- 运行优先级：显式 profile > active profile > 无 profile 时 legacy `OPENAI_*` fallback。显式/活动 profile 无效时失败，不静默换 provider。
- CLI 的 `--model`、`--base-url` 只覆盖本次运行，不写回；不新增 `--api-key`。

### 4.4 凭据分离

- 定义 `CredentialRef`、`ResolvedCredential(value, source)`、`CredentialInfo(configured, source?, writable)` 与 CredentialProvider/Service。
- 解析优先级为进程环境 > 用户级本地凭据文件；每次创建连接时重新解析。
- 环境命中的 ref 只读并遮蔽本地值，descriptor 为 `source=env,writable=false`；GUI set/unset 必须拒绝并解释。
- 本地凭据保存在 `~/.coding-agent/credentials.json`，仅保存 ref→非空 secret。不存在读取明文 API。
- config、API、事件、异常、日志、测试快照和 DOM 均不得含 secret。
- 本地文件使用同目录临时文件、flush/fsync、`os.replace`；POSIX 尽力设置目录 0700、文件 0600。Windows README 如实说明它是用户目录内的本地明文存储，不宣称等同 OS keychain，推荐环境变量。

### 4.5 本地服务安全

- provider URL 必须为无 userinfo/query/fragment 的绝对 HTTP(S) URL；远程地址要求 HTTPS，HTTP 只允许 loopback。
- workspace 在启动 run 前解析为已存在目录，继续交给 task_001 Workspace 守卫；GUI 不弱化边界。
- 服务只监听 loopback，校验 Host/Origin；状态变更请求使用 same-origin 与不可预测启动会话令牌或等价 CSRF 防护。
- 不配置宽泛 CORS；CSP 禁止外部脚本，静态资源不得连接 CDN。session token、credential 和完整环境不得写日志。
- GUI 明确提示 `run_command` 是可信工作区执行而非完整沙箱。

## 5. 实施步骤与交付切片

1. **前置复验**：task_001 通过 R1-R3 后，task_002 才能进入进行中。
2. **M2.1 图形垂直切片**：搭建 TypeScript/React/Vite 与 FastAPI，完成 `coding-agent ui`、RunController、snapshot/SSE、Fake Model 的“启动—事件—结束”闭环。此时界面只需一页但必须真实驱动 AgentLoop。
3. **M2.2 配置闭环**：实现 ProviderCatalog、ProfileStore、CredentialService、ModelClientFactory 和 GUI onboarding/settings，保留 legacy fallback。
4. **M2.3 演示级体验**：按 ui_spec 完成工具折叠组、运行检查器、主题、zh-CN/en-US、错误恢复、刷新重连和 responsive layout。
5. **M2.4 质量门禁**：生成 OpenAPI TypeScript 类型；完成 Python 单测、Vitest 组件测试和 Playwright Fake Model 全栈测试；构建静态资源并随 wheel 分发。
6. 更新 README，给出 GUI 首次配置、演示流程、CLI fallback、本地服务/凭据/命令执行边界。
7. 人工在 1280×720 完成一次脱敏录屏预演。有合法凭据时再做真实模型 smoke，否则按 N/A 记录。

## 6. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `frontend/` | 新增 | React/TypeScript/Vite 源码、i18n、组件、测试与构建配置 |
| `package.json`、`package-lock.json` | 新增 | 固定前端依赖和 npm scripts；禁止仅提交浮动安装命令 |
| `src/coding_agent/provider_config.py` | 新增 | ProviderProfile、catalog、ProfileStore 与原子持久化 |
| `src/coding_agent/credentials.py` | 新增 | credential providers/service 与 descriptor |
| `src/coding_agent/config.py` | 修改 | profile/legacy 优先级与 ResolvedModelConnection |
| `src/coding_agent/model_client.py` | 修改 | ModelClientFactory；现有 adapter 行为不变 |
| `src/coding_agent/web/app.py`、`controller.py`、`schemas.py` | 新增 | 本地服务、API、运行控制、事件存储与公开 DTO |
| `src/coding_agent/web/static/` | 新增/生成 | Vite production assets，随 Python wheel 分发，不含 source map |
| `src/coding_agent/cli.py` | 修改 | `ui`、`config`、`--profile` 与旧入口兼容 |
| `pyproject.toml`、`uv.lock` | 修改 | ASGI 依赖与包内静态资源；每项新增依赖需说明 |
| `.env.example`、`README.md` | 修改 | GUI、profiles、legacy fallback 与安全边界 |
| `tests/test_provider_config.py`、`test_credentials.py` | 新增 | schema、优先级、原子性、遮蔽与脱敏 |
| `tests/test_web_api.py`、`test_run_controller.py` | 新增 | API、事件、并发、取消、重连与安全边界 |
| `feedback/task_002_feedback.md`、`feedback/INDEX.md` | 后续新增/修改 | Dev 实现证据；任务开始后创建 |

不得修改 task_001 的既定 AgentLoop/工具语义来迁就 UI。不得提交用户 config/credentials、真实 key、浏览器缓存、含敏感绝对路径的截图或从参考产品复制的资源。

## 7. 验收标准

- [ ] A1. `coding-agent ui` 在无 workspace/key/网络时启动本地 GUI，生产页面无需 Node/CDN，服务只监听 loopback。
- [ ] A2. GUI 可选择 workspace/profile、输入任务、开始与取消；同一进程只允许一个 active run，配置错误不创建 worker。
- [ ] A3. Playwright + Fake Model 完成“glob/grep/read/edit/verify/最终答复”图形闭环，页面实时显示稳定事件、工具摘要、状态、计数和 VERIFIED 结果。
- [ ] A4. 刷新/SSE 重连可恢复有界 snapshot；运行中 action group 正确更新，慢客户端不会造成无界内存。
- [ ] A5. UI 符合 ui_spec：1280×720 无横向滚动、明暗主题、明确空态/运行/成功/失败/取消、键盘可用、状态消息可感知。
- [ ] A6. zh-CN 和 en-US 完整可切换；无散落用户文案硬编码，不把 UI locale 强制写进模型答复。
- [ ] A7. GUI settings/onboarding 可管理 OpenAI、DeepSeek、Custom profile 与只写 credential；所有读取面只见 descriptor。
- [ ] A8. profile/config/credential/factory、URL 校验、env 遮蔽、原子写入和 legacy fallback 满足本计划，AgentLoop 无 provider 分支。
- [ ] A9. API 采用稳定脱敏 DTO/error code，OpenAPI→TypeScript 类型可重复生成；API/DOM/日志/事件无 secret。
- [ ] A10. Host/Origin/CSRF/CSP、workspace 和 loopback 安全测试通过，GUI 不宣称完整沙箱。
- [ ] A11. 原 CLI 与 help 保持兼容；Python 测试、Ruff、TypeScript typecheck、lint、Vitest、Vite build、Playwright Fake Model E2E 全部通过。
- [ ] A12. README 可复制地说明一键启动、首次配置、演示路径、CLI fallback 与限制；feedback 提供逐项证据和脱敏截图。

## 8. 风险与注意事项

- **前端不能复制内核**：Web/controller 只适配已有事件和取消接口；出现第二套 Agent 状态机即视为架构失败。
- **Node 只属于构建链**：最终用户从仓库运行已构建 GUI 不应额外安装 Node；修改前端时才需要 `npm ci`。
- **本地 Web 仍有攻击面**：恶意网页可能探测 localhost，loopback、Host/Origin、CSRF、CSP 和随机会话令牌不能省略。
- **浏览器目录限制**：普通网页无法可靠返回任意本机目录绝对路径。首版使用明确路径输入与后端校验，不伪造原生目录选择体验。
- **多 provider 不等于多原生协议**：三个 provider 都走 Chat Completions compatibility；UI 必须显示 wire API。
- **凭据文件不是 keychain**：Windows 本地 JSON 不具备等价加密保证；如实提示，未来再接 DPAPI/keyring provider。
- **演示稳定性优先**：页面刷新、配置缺失、模型错误和取消都必须可恢复；动画不能影响事件顺序和终态。
- **回滚**：GUI/profile 通过独立模块和 factory 接入；故障时 CLI 内核仍可运行，通过新提交撤销，不改写历史。
