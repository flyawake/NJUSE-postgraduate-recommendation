# 任务编号：task_002 验收标准

## 前置条件

- [ ] task_001 已通过 R1-R3 复验并标记为已完成；未满足时 task_002 不得进入“进行中”。

## 图形应用验收点

- [ ] A1. `uv run coding-agent ui` 在没有 workspace、API key 和网络时可启动并打开应用；默认且本任务内只能监听 loopback，`--no-browser` 可用于自动化测试，关闭服务后端口正常释放。
- [ ] A2. Vite production 静态资源随 Python 包分发，不依赖 CDN、外网、Node dev server 或运行时 npm install；在干净环境执行 `uv sync` 后即可打开 GUI。
- [ ] A3. 主页面可输入合法 workspace、选择 profile、输入任务、开始和取消；未配置/非法路径在 worker 创建前显示可恢复错误，运行中再次 start 返回稳定冲突。
- [ ] A4. Playwright 使用 Fake Model 和真实临时 workspace 完成 GUI 闭环：glob、grep、read、edit、`purpose=verify`、最终答复。页面展示 phase、status、verification、stop reason、step/attempt/tool count、工具组和 VERIFIED 结果。
- [ ] A5. RunController 不阻塞 HTTP，取消使用既有 cancellation seam，最终 terminal snapshot 唯一。刷新/SSE 断线后能从 snapshot/event ID 恢复，重复事件去重，慢客户端不会造成事件无界增长。
- [ ] A6. 工具组默认折叠已完成的连续 action、保持当前 action 可见；事件和页面不展示隐藏推理，错误使用稳定 code 与可恢复提示，输出遵守预算。

## UI/UX 与语言验收点

- [ ] A7. 1280×720 无横向滚动；左侧导航、中央活动流、右侧检查器层级明确，窄屏检查器变为抽屉。idle/running/success/error/interrupted 与 verification 不只靠颜色区分。
- [ ] A8. 支持 system/light/dark；颜色、间距、圆角、阴影、字体和 motion 来自 design tokens。页面没有套用 Codex、Cline、OpenHands 的品牌资产或逐像素视觉复制。
- [ ] A9. 默认 `zh-CN`，完整提供 `en-US` 切换；所有用户文案来自 i18n 资源。自然中文优先，必要技术名词保留准确英文，UI locale 不强制改变模型回复语言。
- [ ] A10. 键盘可以完成主要流程，focus indicator 可见；Dialog/Select/Tabs/Collapsible 具有正确语义与焦点管理；运行状态用 `aria-live`/status 等价机制播报，自动滚动不会抢走用户焦点。
- [ ] A11. 首次无 profile 时 onboarding 可完成 provider、URL/model、credential 和保存；用户可退出后从设置页继续。空态、加载、配置缺失、断线、失败和取消均有设计，不以 toast 作为唯一错误载体。

## Provider 与配置验收点

- [ ] A12. GUI 设置页离线支持 OpenAI、DeepSeek、Custom profile 的列出、创建、编辑、激活和删除；profile 有稳定 ID、display name、`wire_api=openai_chat_completions`、合法 URL、非空 model 和可选 credential ref。
- [ ] A13. password input 只允许 set/replace/unset credential；读取只返回 `configured/source/writable`。env 优先且遮蔽本地时只读；config/API/DOM/事件/日志/异常/截图无 secret。
- [ ] A14. `CODING_AGENT_HOME/config.json` version=1 严格加载并原子保存，credentials 独立原子保存；损坏/未知版本和注入写入失败保留原文件。POSIX 尽力 0700/0600，Windows 文档不宣称加密。
- [ ] A15. 配置优先级为显式 profile > active profile > 无 profile 时 legacy `OPENAI_*` fallback。错误不静默 fallback；ModelClientFactory 按 wire API 分派，AgentLoop 无 provider 名称分支。
- [ ] A16. URL 校验接受 HTTPS 与 loopback HTTP，拒绝相对 URL、非 HTTP(S)、userinfo、query、fragment 和非 loopback HTTP；workspace 在 run 前解析为已存在目录，继续使用既有路径守卫。

## API、安全、兼容与质量验收点

- [ ] A17. health/bootstrap/run/snapshot/events/cancel/profile/credential 等价 API 使用稳定 JSON DTO/error code；OpenAPI schema 可生成 TypeScript 类型且重新生成无 diff，前端不解析 message 推断状态。
- [ ] A18. 服务拒绝非法 Host/Origin 和跨站状态修改，不设置宽泛 CORS；使用 same-origin + 随机启动会话令牌或等价 CSRF 防护，并提供限制外部资源的 CSP。
- [ ] A19. 原 `coding-agent --workspace ... "task"` 和 `python -m coding_agent` 保持兼容；`coding-agent --help`、`ui --help`、`config --help` 均无需 key/workspace/网络，CLI 与 GUI 共用 resolver/factory/AgentLoop。
- [ ] A20. Python 单测完全离线覆盖 profile/credential/controller/API/事件/并发/取消/安全；Vitest/RTL 覆盖组件状态与交互；Playwright Fake Model 测试从生产 build 入口执行。task_001 全套测试继续通过。
- [ ] A21. Python sync/Ruff/pytest 与前端 npm ci/typecheck/lint/test/build/E2E 全部退出 0；Python、npm 锁文件一致，新增依赖在 feedback 中说明必要性和许可证。
- [ ] A22. README 给出 GUI 启动、首次 profile 配置、任务运行、取消、CLI fallback 和录屏演示步骤；明确 Chat Completions 范围、本地凭据明文边界、loopback 服务和 `run_command` 非完整沙箱。feedback 提供 1280×720 脱敏截图和逐项证据。

## 自动验收命令

```powershell
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run coding-agent --help
uv run python -m coding_agent --help
uv run coding-agent ui --help
uv run coding-agent config --help
npm ci
npm run typecheck
npm run lint
npm test -- --run
npm run build
npm run test:e2e
git status --short
git diff --check
```

## Master 人工验收流程

1. 用临时 `CODING_AGENT_HOME` 和假 key 打开 GUI，走 onboarding，创建 DeepSeek 与 Custom profile；确认 config 只含 ref，credential 只写不读。
2. 在 1280×720 浏览器用 Fake Model 运行完整编程任务，观察活动流、工具折叠组、右侧检查器、验证与最终结果；运行中刷新并检查恢复。
3. 再运行慢 Fake Model并点击取消，确认没有新的副作用工具开始、终态为 INTERRUPTED、页面可重新开始。
4. 切换 zh-CN/en-US、system/light/dark，使用键盘走完核心流程，并检查窄屏 drawer。
5. 检查 DOM、网络响应、服务日志、Playwright trace，搜索特征 secret，必须零命中；断网加载页面且无外部请求。
6. 从非法 Origin/Host 模拟状态请求，确认被拒绝；关闭 GUI 后用旧 CLI 做 Fake Model 回归。

## Live smoke 的 N/A 规则

- 有合法凭据时，在一次性 workspace 用 GUI 完成一次真实任务，只记录 provider/profile ID、model、base URL host、脱敏工具轨迹、终态和截图；不得记录 key。
- 无合法凭据时允许 `N/A - 无外部凭据`，但 Fake Model 图形闭环、profile→credential→client 映射和浏览器展示必须完整通过。最终演示录制前仍需真实模型 smoke。

## 2026-08-27 Master 源码复验整改要求

本轮结论为“需整改”。以下各项均属于原 A3-A22 范围，不扩大 task_002 目标；完成后必须再次提交当前任务反馈并由 Master 复验。

- [ ] R2.1 **公开事件真正脱敏**：建立集中、字段级的 public-event redaction。`write_file.content`、`edit_file.old_string/new_string` 不得进入事件；`run_command.argv` 与验证命令不得原样暴露潜在 token/password/key 参数；不得因异常类型为 `AssertionError` 而把 `str(exc)` 写入 API。使用 sentinel secret 经真实 AgentLoop → snapshot/SSE → DOM 路径验证零命中，覆盖成功、工具失败和 worker 异常。
- [ ] R2.2 **运行中事实准确**：运行中 snapshot/inspector 必须随事件更新 logical step、provider attempt、tool count 与当前 phase，不能在已经执行工具时仍显示全 0/`READY`；尚无验证结论时不得显示“无需验证”。新增中间态 controller/API/组件/E2E 断言，不只断言终态计数。
- [ ] R2.3 **活动流保持事件时序与折叠语义**：用统一有序 feed 表示保留 step/retry/tool group/completion 的真实交错顺序，禁止先渲染全部普通 item、再统一渲染工具组。已完成成功组在运行中默认折叠，只有当前执行组与失败/中止组强制展开；终态仍可检查全部详情。
- [ ] R2.4 **刷新与 SSE 恢复闭环**：从 snapshot 初始化后订阅必须携带正确 `lastEventId`，或以一次原子 reset 正确重置 baseline；空 reset 不得保留一个会过滤重放事件的旧 ID。收到 reset 时重取 snapshot，流在未收到 `end` 却正常 EOF 时也要进入断线并重连。Playwright 必须在第一个工具完成后刷新，最终仍按序看到全部 5 个工具、无丢失/重复，并覆盖落后于 retained tail。
- [ ] R2.5 **配置与 URL 语义一致**：legacy `OPENAI_BASE_URL` 和 GUI/profile 走同一个 URL validator，远程 HTTP、userinfo、query、fragment 与非法端口都必须拒绝；保留合法 HTTPS 和 loopback HTTP。ProfileStore 的 create/update/delete/activate 使用 copy-on-write 或失败回滚，写盘失败后内存视图与磁盘都保持原状态；严格加载时拒绝未知/类型错误/内嵌 ID 不一致字段。新增失败注入与 legacy CLI/API 测试。
- [ ] R2.6 **精确 same-origin/Host 防护**：按合法 Host 语法解析 IPv4/IPv6 与数字端口，拒绝畸形 Host；Origin 必须与当前请求的 scheme/host/effective port 精确一致，不得接受另一个 loopback 端口、userinfo/path 等伪造值。禁用默认外链 Swagger UI，或改为完全本地且受同一 CSP 约束。补齐不同端口、非法端口、IPv6 与畸形头测试。
- [ ] R2.7 **完整双语与关键交互状态**：前端按稳定 error code 映射 zh-CN/en-US 文案，不直接把后端中文 `message` 显示在英文 UI；invalid workspace/profile/credential 时按钮与 `Ctrl+Enter` 都不得发起 start；继承 active profile 时 selector 显示真实选中项；onboarding 对空 credential ref 有可完成路径；按 UI 规格保持 credential input 为 password；移除窄屏可聚焦按钮祖先上的 `aria-hidden` 并验证 drawer 焦点管理。
- [ ] R2.8 **证据与文档诚实一致**：更新 README 中关于“脱敏、实时计数、完成即折叠、重连恢复、same-origin”的描述，使其与修复后的实现一致；`.env.example` 不得暗示项目会自动加载 `.env`，除非实现并测试该能力。重新生成 1280×720 截图，使用不含用户名/用户目录的演示 workspace；running 截图必须显示非零计数、正确 phase 和 active profile，截图/trace/日志/DOM sentinel secret 零命中。

整改复验除原自动验收命令外，必须新增并执行：

```powershell
npm audit --omit=dev --registry=https://registry.npmjs.org
uv build
git diff --check 49db10c..HEAD
```

## 第二次整改复验追加要求（R3，2026-08-27）

R2 提交的标准自动化门禁全部通过，但 Master 通过源码审查和独立反例确认，R2.1-R2.8 尚未闭环。下列要求是本任务下一轮唯一整改入口，不改变 task_002 的既定产品范围：

- [ ] R3.1 **命令事件按值 fail-closed 脱敏**：`run_command` 的 `argv[1:]` 中，除不携带值且独立验证为安全的 option flag 外，任何 operand/value 都不得原样公开。`NAME=value`、`--header=value`、`--option=value` 只能保留键/旗标并把值改为 `***`，不能以“键名未命中敏感正则”为由保留整个参数。验证命令使用同一逻辑。新增真实 AgentLoop → snapshot/SSE → DOM 测试，sentinel 至少分别放入普通 `NAME=value`、非敏感命名的 `--flag=value`、独立 argv operand，以及成功/失败验证命令；README 的“其余参数一律脱敏”必须与实现一致。
- [ ] R3.2 **当前 phase 与 inspector 同步而非轮询旧快照**：controller 必须表示 Agent 实际正在进行的阶段；模型请求已阻塞时不能仍为 `READY`。前端收到 SSE step/tool 等事件后，inspector 的 step/attempt/tool/phase 必须同步推进，不得出现 feed 已到 Step 2 而 inspector 仍是 1/1/1、旧 phase 的 4 秒窗口。增加“第二次模型请求受控阻塞”的 controller/API/组件/E2E 中间态断言，明确期待 `REQUESTING_MODEL`、step=2、attempt=2。
- [ ] R3.3 **工具组不得跨非工具事件吞并时序**：遇到 `step_started`、`model_retry`、`completion_deferred`、terminal 等非工具边界时结束当前连续工具组。跨两步的轨迹必须渲染为 `task → step1 → group(step1) → step2 → group(step2) → terminal`，不能把 step2 工具放进位于 Step 2 之前的旧 group。修正现有测试中把 `[1,2]` 两步工具放在单组仍当作“真实顺序”的错误断言，并覆盖 retry/completion 边界。
- [ ] R3.4 **SSE reconnect/reset/终止状态单调**：重连必须读取最新 `lastEventId`（使用同步 ref 或等价机制），不得从只依赖 `[runId, snapshot.state]` 的陈旧闭包读取初始 ID。收到显式 `end` 后的正常 EOF 不得再触发 `onError`、断线横幅或重连。reset 后重取 snapshot 与随后 replay 必须单调合并，较旧 HTTP 响应不得覆盖已经到达的 SSE 事件。增加可控 deferred fetch/SSE 的前端测试，以及终态无“实时连接中断”与 retained-tail 重置的浏览器断言。
- [ ] R3.5 **严格 config 根类型与版本类型**：`config.json` 根必须是 object，`version` 必须是非 bool 的整数 1；`null`、array、`true` 等类型错误统一转换为稳定、可恢复的 `ProfileError/config_corrupt`，不得泄漏 `TypeError/AttributeError` 或被当作 version 1 接受。保留已经完成的 CRUD copy-on-write 与 legacy URL 共用 validator，并新增文件加载/API 级反例测试。
- [ ] R3.6 **Host/Origin authority 真正严格解析**：Host 本身必须拒绝 userinfo、path、query、fragment、空白和其他非 authority 形态；不能接受 `user@localhost`、`localhost/path`、`localhost?x=y` 或 `localhost#frag`。effective port 必须按 scheme 计算（HTTP=80、HTTPS=443），`https://localhost` 不得与 `https://localhost:80` 判为同源。保留 IPv4/IPv6/localhost 正常分支及 Swagger/ReDoc 禁用，补齐 TestClient/API 反例。
- [ ] R3.7 **credential 空引用语义与所有 start 入口一致**：不得再提示“credential_ref 留空即可使用环境变量”，除非 resolver 对该 profile 确实实现并测试了确定、无歧义的环境变量映射；否则 onboarding/profile form 应引导用户填写 ref，且能从空 ref 状态继续编辑并完成可运行配置。`handleStart` 本身必须复核 task/workspace/profile/credential readiness，使 InlineError 的“重试”等旁路也不能在无效状态发送 `POST /api/runs`。新增英文界面与请求计数测试。
- [ ] R3.8 **重新生成真实一致的演示证据**：`03-verified-final.png` 必须实际显示 SUCCESS/FINAL_ANSWER/VERIFIED、正确工具数与最终答复，不能是 ERROR/MAX_STEPS/20 次工具调用；终态不得带断线重连横幅。深色和英文截图也不得用一个失败/错配状态冒充交付结果。反馈逐张描述必须与画面一致，并继续保证 1280×720、无用户名目录、secret/trace/log/DOM 零命中。

R3 复验继续执行 R2 的全部标准命令，并额外提交上述每个独立反例的原始输出或自动化测试名。仅“原测试仍全绿”不能替代这些反例。
