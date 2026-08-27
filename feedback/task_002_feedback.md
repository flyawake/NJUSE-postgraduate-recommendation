# 任务编号：task_002 开发反馈

## 1. 完成情况

对照 `guide/task_002/plan.md`、`acceptance.md` 与 `ui_spec.md` 逐项：

| 验收点 | 状态 | 证据 |
| --- | --- | --- |
| A1 `ui` 无配置可启动，loopback，`--no-browser`，端口释放 | 完成 | `src/coding_agent/web/server.py`；e2e global-setup 以 `--port 0/指定端口 --no-browser` 启动多个实例；端口由 `uvicorn.Server` 动态发现 |
| A2 Vite 产物随 Python 包分发、无 CDN/Node | 完成 | `vite.config.ts` 输出到 `src/coding_agent/web/static`；wheel 内容校验含 `coding_agent/web/static/index.html` 与 assets（无 source map）；页面 CSP 禁外部脚本，e2e 在无外网假设下从生产 build 全量通过 |
| A3 主页输入/选择/开始/取消；配置错误在 worker 前报可恢复错误；重复 start 稳定冲突 | 完成 | `POST /api/runs` 在 `RunController.start` 内先校验 workspace/task/connection 再建 worker；重复 start 返回 `run_already_active`(409)；`tests/test_run_controller.py::test_start_validates_before_worker`、`test_duplicate_start_returns_stable_conflict`；错误在 `InlineError` 展示（非 toast 唯一载体） |
| A4 图形闭环 glob/grep/read/edit/verify/最终答复 | 完成 | `frontend/e2e/run.spec.ts` 第一个用例：断言五个工具卡、VERIFIED、最终答复、工具计数 5、变更文件 hello.py |
| A5 Controller 不阻塞 HTTP、取消走既有 seam、终态唯一、重连去重、有界 | 完成 | 控制器单测（9 个）+ API 测试（SSE 流、cancel 幂等、`events_retained_from` 与 reset）；快照 `finished_at` 唯一断言；事件存储条数+字符双上限 |
| A6 工具组折叠/当前可见/无隐藏推理/稳定 code/输出预算 | 完成 | `buildFeed` + `ToolEventGroup`（流式期间展开、终止后折叠、错误组强制展开）；事件 payload 白名单（`schemas.EVENT_PAYLOAD_KEYS`）；`summary` 为内核脱敏摘要 |
| A7 1280×720 无横向滚动、三栏层级、窄屏抽屉、状态不靠颜色 | 完成 | e2e 断言 `scrollWidth <= innerWidth`；`AppShell`（`lg:` 断点抽屉 + 侧栏折叠）；所有 badge 图标+文字 |
| A8 system/light/dark + design tokens、无品牌复制 | 完成 | `styles/tokens.css` 全量 token；`ThemeProvider`；界面为原创布局 |
| A9 zh-CN/en-US 完整、i18n 资源化、UI locale 不影响模型 | 完成 | `i18n/zh-CN.ts`+`en-US.ts`（key 完全一致由 Vitest 断言）；组件无散落文案；模型语言由任务/系统提示决定 |
| A10 键盘/焦点/语义/RTL、aria-live、自动滚动不抢焦点 | 完成 | Radix primitives（Dialog/Select/Tabs/Collapsible/AlertDialog）；`Ctrl+Enter` 开始；`aria-live` 状态播报；feed 底部跟随逻辑 + “回到最新” |
| A11 onboarding 可完成并可从设置继续；空态/加载/缺配置/断线/失败/取消有设计 | 完成 | `Onboarding`（provider 卡→URL/model→凭据→保存，可随时关闭）；`InlineError` 各场景；SSE 断线 banner + 自动重连 |
| A12 离线管理 OpenAI/DeepSeek/Custom profile（增删改激活列出） | 完成 | `ProfileStore` CRUD/activate + API + `SettingsPage`；`test_provider_config.py`、`test_web_api.py::TestProfileAndCredentialApi` |
| A13 password 只 set/replace/unset；descriptor 无回显；env 遮罩只读 | 完成 | `CredentialField`（password 输入 + 显示/隐藏 + 清除）；API 只返回 `configured/source/writable`；env 命中 409 `credential_env_readonly`；e2e 断言 DOM 无 secret |
| A14 config/credentials 原子保存、损坏/未知版本保留原文件、POSIX 权限尽力 | 完成 | `storage.py`（临时文件+flush/fsync+`os.replace`，POSIX 0700/0600）；损坏与未知版本测试；Windows 文档如实说明明文 |
| A15 优先级与 fallback、失败不静默换、factory 按 wire API、AgentLoop 无 provider 分支 | 完成 | `config.resolve_connection` 显式>激活>legacy；`ModelClientFactory.create` 只按 wire_api 分派；AgentLoop 未改动 |
| A16 URL 校验、workspace 启动前校验为已存在目录 | 完成 | `validate_provider_url`（HTTPS 任意 host、HTTP 仅 loopback、无 userinfo/query/fragment）；`RunController._validate_workspace` 严格 resolve；对应单测 |
| A17 稳定 DTO/error code、OpenAPI→TS 重复生成无 diff、前端不解析 message | 完成 | `npm run check:api`（重新生成对比无 diff，本次运行通过）；`frontend/src/api/schema.json`+`schema.d.ts` 提交入仓；前端仅以 `error.code` 判断 |
| A18 Host/Origin/CSRF/CSP 安全 | 完成 | `security.py`：loopback Host 校验、状态变更携带随机会话令牌、Origin 同源校验、CSP `default-src 'self'`；单测覆盖 403 各分支；TestClient 无宽泛 CORS |
| A19 旧 CLI/`python -m coding_agent` 兼容；`--help` 无需配置；CLI 与 GUI 共用 resolver/factory | 完成 | `cli.py` 保留 legacy 解析；`--profile`/`--model`/`--base-url` 覆盖本次运行；`coding-agent --help`、`python -m coding_agent --help`、`ui --help`、`config --help` 全部通过 |
| A20 Python 离线单测 + Vitest/RTL + Playwright 生产 build | 完成 | pytest 199 passed/4 skipped（含 task_001 全套）；Vitest 18 passed；Playwright 3 passed（从 `web/static` production build 入口运行） |
| A21 全部命令退出 0、锁文件一致、新增依赖说明 | 完成 | 见下文“验证命令”；锁文件 `uv.lock` + `package-lock.json` 均已提交；依赖说明见第 2 节 |
| A22 README + feedback 证据 + 脱敏截图 | 完成 | README 新增 GUI 启动/首次配置/演示/CLI fallback/边界；截图见 `tmp/screenshots/`（不入库） |

## 2. 改动文件列表

### 新增（Python 后端）

| 文件 | 说明 |
| --- | --- |
| `src/coding_agent/storage.py` | config/credentials 共用原子 JSON 写入（临时文件+fsync+`os.replace`，POSIX 0700/0600） |
| `src/coding_agent/provider_config.py` | ProviderCatalog（openai/deepseek/custom）、ProfileStore（version=1 严格加载、CRUD/激活）、URL 校验、descriptor |
| `src/coding_agent/credentials.py` | CredentialService：env 优先解析、只写本地凭据、descriptor、env 遮罩只读、原子保存 |
| `src/coding_agent/web/__init__.py` | 包说明 |
| `src/coding_agent/web/schemas.py` | 公开 DTO（Health/Bootstrap/RunSnapshot/ToolEvent/Profile/Credential/Error）与每类事件 payload 白名单 |
| `src/coding_agent/web/controller.py` | RunController：单 active run、worker 线程 + 取消 Event、有界事件存储、SSE take_events（增量/重置）、唯一终态 |
| `src/coding_agent/web/security.py` | Host/Origin/会话令牌守卫 + CSP 与安全头 |
| `src/coding_agent/web/app.py` | FastAPI 应用：health/bootstrap/workspace validate/runs/events(SSE)/cancel/profiles/credential + 静态资源托管 |
| `src/coding_agent/web/server.py` | `coding-agent ui` 的 Uvicorn runner：端口发现、浏览器打开、优雅关闭 |
| `src/coding_agent/web/openapi_json.py` | 导出 OpenAPI JSON 供前端类型生成 |
| `src/coding_agent/web/static/**` | Vite production 产物（index.html + assets，无 source map） |

### 修改（Python 后端）

| 文件 | 说明 |
| --- | --- |
| `src/coding_agent/config.py` | 新增 `ResolvedModelConnection`、`resolve_connection`（显式/激活/legacy 优先级）、`load_config_from_connection`；`load_config` 行为不变 |
| `src/coding_agent/model_client.py` | 新增 `ModelClientFactory`（按 wire_api 分派）；既有 adapter 不变 |
| `src/coding_agent/cli.py` | 新增 `ui`、`config list/show` 子命令与 legacy `--profile`；旧入口与退出码不变 |
| `pyproject.toml` / `uv.lock` | 运行时新增 `fastapi>=0.115,<1`、`uvicorn>=0.30,<1`；开发组新增 `httpx>=0.27`（TestClient） |

### 新增（前端）

| 文件 | 说明 |
| --- | --- |
| `package.json` / `package-lock.json` | npm 脚本与锁定依赖；`frontend/` 为源码目录 |
| `vite.config.ts` / `vitest.config.ts` / `tsconfig.json` / `tailwind.config.js` / `postcss.config.js` / `eslint.config.js` / `playwright.config.ts` | 构建、类型、样式、lint 与 E2E 配置 |
| `frontend/index.html`、`frontend/src/main.tsx`、`App.tsx` | 入口与根组件 |
| `frontend/src/styles/tokens.css`、`index.css` | design tokens（明暗主题、间距、圆角、阴影、motion、系统字体）与 Tailwind 基础层 |
| `frontend/src/i18n/zh-CN.ts`、`en-US.ts`、`lib/i18n.tsx` | 完整双语文案资源与轻量 i18n Provider |
| `frontend/src/api/client.ts`、`api/schema.json`、`api/schema.d.ts` | 类型化 REST 客户端（OpenAPI 生成类型）、会话令牌管理；schema 文件提交入仓 |
| `frontend/src/lib/sse.ts`、`store.tsx`、`toolgroups.ts`、`theme.tsx`、`format.ts`、`validate.ts` | SSE 消费、RunStore（reducer+query）、活动流分组、主题、格式化、客户端校验 |
| `frontend/src/components/*` | AppShell（顶栏/侧栏/抽屉）、WorkspaceField、ProfileSelector、TaskComposer、RunStatusBadge、VerificationBadge、ActivityFeed、ToolEventGroup/ToolCard、RunInspector、InlineError、ProfileForm、CredentialField、SettingsPage、Onboarding、AboutSecurityPage |
| `frontend/src/pages/MainPage.tsx` | 主运行页（composer + feed） |
| `frontend/src/test/setup.ts`、`frontend/src/__tests__/*` | Vitest/RTL 测试（i18n 完整性、分组、组件状态、WorkspaceField 校验） |
| `frontend/e2e/fake_model_server.py`、`global-setup.ts`、`global-teardown.ts`、`run.spec.ts` | Fake Model（Chat Completions 脚本化轨迹）、E2E 环境与用例 |
| `frontend/scripts/gen-api.mjs`、`check-api.mjs` | OpenAPI→TS 生成与无 diff 校验 |

### 其他

| 文件 | 说明 |
| --- | --- |
| `README.md` | GUI 启动/首次配置/演示路径/CLI fallback/本地服务与凭据/命令执行边界 |
| `.env.example` | 补充 `CODING_AGENT_HOME` 与 `CODING_AGENT_CRED_*` 说明 |
| `tests/test_provider_config.py`、`test_credentials.py`、`test_run_controller.py`、`test_web_api.py` | 新增 71 个离线测试 |
| `feedback/task_002_feedback.md`、`feedback/INDEX.md` | 本反馈与索引登记 |

### 新增依赖说明（必要性 / 许可证）

- Python 运行时：`fastapi`（MIT，本地 JSON API/OpenAPI/SSE/静态托管必需）、`uvicorn`（MIT，ASGI 服务器，本地服务必需；未使用 `[standard]` 避免 uvloop/websockets 等平台绑定）。
- Python 开发：`httpx`（BSD-3，FastAPI TestClient 依赖，仅测试）。
- npm 运行时：`react`/`react-dom`（MIT）、`@tanstack/react-query`（MIT）、`@radix-ui/react-{dialog,select,tabs,collapsible,alert-dialog}`（MIT，可访问性 primitives）、`lucide-react`（ISC）。
- npm 开发：`vite`/`@vitejs/plugin-react`、`typescript`、`tailwindcss`/`autoprefixer`/`postcss`、`vitest`/`jsdom`/`@testing-library/*`、`eslint`/`typescript-eslint`/`eslint-plugin-react-hooks`、`@playwright/test`、`openapi-typescript`（均 MIT/ISC/Apache-2.0）。
- 未引入任何 agent 框架 / Agent SDK；新增依赖均为 plan 授权或工具链必需。

## 3. 关键实现说明

- **GUI 不复制内核**：`RunController` 通过注入 `ModelClientFactory`/默认 `AgentLoop` 组装复用 `build_default_tools/ToolExecutor/ContextManager/CompletionPolicy`；AgentLoop 仅新增 `event_sink=_EventSinkAdapter` 与既有 `is_cancelled` seam，无第二套状态机。AgentLoop/工具语义零改动（task_001 测试原样通过）。
- **事件流协议**：SSE 首条 `event: hello`；`event: reset`（客户端落后于保留尾时全量重放）；逐条 `event: <kind>`（id 单调，与 snapshot 事件同源）；`event: end`（终态且无新增）。客户端 fetch-based SSE 订阅，重连时以服务端快照 + 全量 tail 校正，按 id 去重。
- **安全**：启动随机会话令牌由 `/api/bootstrap` 下发、仅存于前端内存；状态变更必须带 `X-Coding-Agent-Token` 且 Origin 同源；Host 仅允许 loopback；CSP 由中间件对所有响应注入；API 不写 CORS 头。凭据字符串经 `CredentialService` 单向流：ref→secret 只进不出，异常处理只记录类型（`logger.warning(type(exc).__name__)`）。
- **优先级与 fallback**：`resolve_connection` 显式 profile > active profile > legacy `OPENAI_*`；profile 无凭据/无效直接失败；CLI `--model/--base-url` 仅覆盖本次运行。
- **Bounded events**：控制器保留事件连续尾（默认 2000 条 / 1M 字符双上限），`events_retained_from` 对外暴露；慢客户端收到 `reset` 而非缺失间隙。
- **前端状态**：TanStack Query 负责 bootstrap/profile/snapshot（running 时 4s 轮询兜底）；SSE 增量进入 reducer（max 2000 条本地 tail）按 `id` 去重；`buildFeed` 将 tool_started/finished 按 call_id 配对并分组成连续 action group；所有用户文案走 i18n，provider/model/API 等标识保留英文。
- **OpenAPI→TS**：`npm run gen:api` 调用 `python -m coding_agent.web.openapi_json` + `openapi-typescript`；`npm run check:api` 在临时目录重新生成并与提交版本逐字节比较，无 diff。
- **Fake Model E2E**：`fake_model_server.py` 实现 Chat Completions 兼容接口，依据“最后一个 tool 结果”驱动 glob→grep→read→edit→`python -m py_compile`(purpose=verify)→最终答复；`global-setup` 创建临时 `CODING_AGENT_HOME`/workspace，通过公开 API 建 profile 与只写凭据，从 production build 入口驱动真实浏览器。

## 4. 遇到的问题

1. **React Query 去重覆盖 queryFn**：`RunStore` 与 `App` 都以 `["bootstrap"]` 建查询，后注册者（App）的 `queryFn` 覆盖了先注册者 -> 会话令牌从未写入，页面所有状态变更 403。修复：令牌改由 `api.bootstrap()` 内部设置，任何调用方都能触发。
2. **Playwright 无法断言 1280×720 无横向滚动之外的布局细节**——无问题；但发现窗口 1280×720 时 inspector 在 `lg` 内正常展示，窄屏抽屉由 Radix Dialog 实现。
3. **e2e 协作进程残留**：Windows 下 `child.kill()` 不杀进程树，多次运行后孤儿 uvicorn/fake model 进程占资源。修复：global-teardown/teardown 回调改用 `taskkill /PID <pid> /T /F`（Windows）或 SIGKILL（POSIX）。
4. **工具组折叠时机**：初版在“当前工具完成”后立刻折叠导致运行中卡片闪烁，且终止后不再自动折叠。修复：组展开状态 = `forceOpen(运行中/含错误) || userOpen ?? (streaming || default)`——流式期间保持展开、终止后自动折叠、用户手动选择优先。
5. **冒烟用 Screenshot 泄露机器路径**：fake model 用 `sys.executable` 作为 argv 导致截图含 `C:\Users\...\Python...` 绝对路径。修复：脚本改用 `python`（PATH 解析），截图与日志不再含用户目录路径。
6. **Windows 端口 0 与 `uvicorn.Server` 端口发现**：`--port 0` 时通过 `server.servers[0].sockets[0]` 取实际端口，测试与 e2e 均用动态端口。

## 5. 未完成项 / 技术债

- **live smoke（真实模型）**：`N/A - 无外部凭据`（本机未配置 `OPENAI_API_KEY` 等）。Fake Model 图形闭环、profile→credential→client 映射与浏览器展示均已完整通过；最终演示录制前需要真实模型 smoke。
- **会话持久化**：运行记录仅限当前进程内存（“最近运行”不显示占位入口），跨进程历史属后续任务。
- **凭据存储**：Windows 为本地明文 JSON，README/关于页如实说明，不宣称等同 OS keychain；DPAPI/keyring provider 属后续。
- **OpenAPI 版本**：`openapi.json` 由运行中应用对象生成（FastAPI `app.openapi()`），未做 schema 静态化缓存，重复生成仍无 diff（已由 `check:api` 固化）。
- **前端构建产物**：`web/static` 已提交并随 wheel 分发；无 source map、无外链资源。

## 6. 下一步建议

1. 用至少一个真实 OpenAI-compatible 模型完成一次 smoke（一次性 workspace），记录 provider/model/base URL host、脱敏轨迹与终态，为最终录屏定稿。
2. 按 `ui_spec.md` 第 8 节镜头清单在 1280×720 固定窗口/缩放/示例任务录制 2 分钟演示视频；视频中设置页展示 credential 只显示来源与可写性。
3. 若评审通过，可规划：运行历史持久化与“最近运行”、真实模型 stream 输出、多 profile 并发/多 run 队列、Windows DPAPI/keyring credential provider。
4. 关注点：真实模型下工具参数摘要可能包含敏感内容时的事件脱敏规则（当前为内核 `format_args_summary` 摘要，足够但可进一步字段级脱敏）。

## 7. 状态：已完成

## 验证命令（实际运行结果）

```powershell
uv sync --all-groups                                   # 通过（31 packages, 0 issue）
uv run ruff format --check .                           # 通过（70 files already formatted）
uv run ruff check .                                    # 通过（All checks passed!）
uv run pytest -q                                       # 通过：199 passed, 4 skipped (1 warning: starlette testclient 弃用提示)
uv run coding-agent --help                             # 通过
uv run python -m coding_agent --help                   # 通过
uv run coding-agent ui --help                          # 通过
uv run coding-agent config --help                      # 通过（list/show）
npm ci                                                 # 通过（416 packages）
npm run typecheck                                      # 通过（tsc --noEmit, 0 errors）
npm run lint                                           # 通过（0 errors 0 warnings）
npm test -- --run                                      # 通过：18 passed
npm run build                                          # 通过（vite build -> web/static）
npm run check:api                                      # 通过（API types are up to date, no diff）
npm run test:e2e                                       # 通过：3 passed（生产 build 入口，Fake Model 闭环）
git status --short                                     # 见提交清单；无 config/credentials/截图/PDF/密钥
git diff --check                                       # 通过（无冲突标记/空白错误）
uv build                                               # 通过；wheel 内包含 coding_agent/web/static/（index.html + assets，无 source map）
```

### 1280×720 脱敏截图（存 `tmp/screenshots/`，不入库）

- `01-main-idle.png`：空态主页（左导航/中央活动流/右检查器）
- `02-running.png`：运行中（步骤、工具组“正在执行 5 项操作”、inspector 计数）
- `03-verified-final.png`：VERIFIED 终态（验证通过、变更文件 hello.py、最终答复、5 次工具调用）
- `04-settings.png`：设置页（profile 列表 + 编辑表单 + “保存凭据”状态，无任何 secret 回显）
- `05-dark.png`：深色主题
- `06-english.png`：完整英文界面

其中不含真实密钥、用户目录绝对路径（fake model 已改用 `python` 作为 argv[0]）或仓库外文件信息。

## 10. 审查整改记录（2026-08-27）

上一轮审查结论为“需整改”，本轮按 S1-S4 / M1-M6 及轻微项修复，未改动 `guide/`、`AGENT_*`、`PROJECT_CONTEXT.md`，未改动 task_001 内核语义。

**严重项**
- **S1 loopback 前缀绕过 + 非法端口**：新增 `src/coding_agent/netutil.py::is_loopback_host`，用 `ipaddress` 精确判断 IPv4/IPv6 字面量与 `localhost`；`provider_config.validate_provider_url` 与 `web/security` 共用该检查，并新增 `parsed.port` 数字端口校验。新增测试：域名伪装（`127.0.0.1.evil.com` 等）在 URL 校验与 Host 校验均被拒，非法端口被拒（`test_rejects_dns_names_that_resemble_loopback_prefixes`、`test_rejects_invalid_ports`、`test_loopback_looking_dns_names_rejected`）。
- **S2 CLI 未用 active profile / 未共用 factory**：`cli._resolve_config` 无条件走 `ProfileStore + resolve_connection`（显式 > 激活 > legacy），`_default_agent_factory` 改经 `ModelClientFactory.create`；`Config` 增加 `wire_api`。`CODING_AGENT_HOME` 现按规格直接指向配置目录（不再追加 `.coding-agent`）。新增 5 个 resolver 优先级/回退/损坏拒绝测试；`config list/show` 对损坏 credentials 返回稳定错误而非 traceback。
- **S3 刷新/重连不恢复 snapshot 事件**：`store.tsx` 每次 runId 首次拿到 snapshot 时 `dispatch(RESET_EVENTS, snapshot.events)`（只初始化一次，避免覆盖更新的 SSE 事件）；SSE 收到 `reset` 先清空再按序重放；导出 `runReducer` 并新增 5 个 Vitest 用例（APPEND 去重、RESET 替换/清空、CANCEL_FAILED、2000 条上限）。
- **S4 提交边界**：`543131f` 混入 Master 侧文件的既有历史无法改写；本轮提交只包含授权实现文件，不触碰 `guide/`/`AGENT_*`/`PROJECT_CONTEXT.md`。后续如何记录该过程问题，等待负责人决定。

**中等问题**
- M1 `RunController.start` 先构建 loop 再改状态，builder 抛错保持原状态（新增 `test_loop_builder_failure_leaves_controller_idle`）。
- M2 事件尾被清空且客户端落后时 `take_events` 返回 `([], True)`（新增 `test_empty_retained_tail_resets_behind_clients`）。
- M3 `run_ui` 退出时调用 `RunController.shutdown()` 取消活动 run（新增 `test_shutdown_cancels_running_worker`）。
- M4 worker 异常日志只记 `type(exc).__name__`，不再打印 `str(exc)`。
- M5 cancel 失败时 `CANCEL_FAILED` 恢复 `cancelling=false`，可重试取消。
- M6 编辑 profile 表单加 `key={editing.id}`，切换编辑对象时状态重置，不会把旧值覆盖到新对象。

**轻微项**
- i18n 硬编码移除：语言切换、credential show/hide、step 行的 “chars” 全部资源化；`api/client.ts` 的传输错误改为 locale 中立标记，新增 `lib/errorText.ts`，各错误消费点统一经 i18n 渲染。
- Onboarding 步骤指示器改为真实的两步（连接信息 → 凭据保存）。
- `bg-black/30`/`bg-black/40` 改为 `--color-overlay` token；`text-[11px]` 改为 `.text-caption` utility。
- `ProfileForm` 字段错误增加 `id` 与 `aria-describedby` 关联。
- Settings/Onboarding 增加 loading 状态提示。
- `tests/test_web_api.py` 移除 `Z:/` 绝对路径，改用 `tmp_path` 相对缺失路径。
- 新增 `.gitattributes` 对生成 bundle 声明 `-whitespace`：`git diff --check 49db10c..HEAD` 现为 0。

**整改后全量验证（实际执行，全部 exit 0）**

```powershell
uv sync --all-groups               # 0
uv run ruff format --check .       # 0（71 files already formatted）
uv run ruff check .                # 0
uv run pytest -q                   # 0（211 passed, 4 skipped；1 个 starlette TestClient 弃用 warning）
uv run coding-agent --help         # 0
uv run python -m coding_agent --help # 0
uv run coding-agent ui --help      # 0
uv run coding-agent config --help  # 0
npm run typecheck                  # 0
npm run lint                       # 0
npm test -- --run                  # 0（23 passed）
npm run build                      # 0（产物重建后 git status 干净）
npm run check:api                  # 0（类型无 diff）
npm run test:e2e                   # 0（3 passed，Fake Model 闭环/secret 零回显/取消与刷新恢复）
git diff --check                   # 0
git diff --check 49db10c..HEAD     # 0
```

4 个 skip 均为平台无法创建符号链接的用例；截图为整改后重新生成，全部 1280×720，字节扫描无 secret/用户目录路径。
