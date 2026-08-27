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
