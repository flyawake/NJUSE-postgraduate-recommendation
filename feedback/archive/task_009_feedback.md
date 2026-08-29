# task_009 反馈：联网检索与聊天附件

结论：通过。Task 8 保持未开始；未 push。

## 1. 完成范围

- 默认 ToolRegistry 新增 READ effect 的 `web_search` 与 `web_fetch`，仍经过 AgentLoop guard、ToolPolicy、ToolExecutor、结构化结果和公共活动事件。
- 标准库 SafeHttpClient 不使用环境代理，将每一跳连接固定到已验证公网 IP；拒绝非 HTTP(S)、URL 凭据、非 80/443 端口、任一非公网 DNS 结果与 redirect rebinding，并限制超时、redirect、压缩后响应字节和可见正文。
- 搜索后端经真实公网 smoke 确认 Bing HTML 可用；搜索/抓取解析器均有离线 fixture。`web_fetch` 不执行脚本并剔除 script/style/noscript/svg/template。
- SQLite 增量升级至 schema v14，attachment metadata 绑定 conversation/turn；二进制进入 `CODING_AGENT_HOME/attachments` 专用原子 CAS。turn 与 attachment claim、首条 canonical group 在同一事务提交。
- canonical UserMessage 只保存 provider-neutral attachment ref，不保存 base64。ContextManager 仅在 detached request view 载入最近附件：图片/二进制文件使用 data URL，UTF-8 文本最多内联 50,000 字符。
- Chat Completions 映射为 `image_url` / `file.file_data` / text part；Responses 映射为 `input_image` / `input_file` / `input_text`。不支持该输入的 provider 继续走既有可诊断模型错误，不伪装成功。
- GUI 支持选择、拖放、剪贴板文件，上传中/错误、chip、图片缩略图、名称/大小、移除、attachment-only turn、刷新后 transcript 展示；busy Queue/Steer 不携带附件。

## 2. 源码验收与直接整改

- 真实联网 smoke 首次发现 DuckDuckGo HTML 在当前网络超时，切换为同样无密钥、可解析且当前环境可直连的 Bing HTML 后端；`web_fetch(https://example.com/)` 与实际搜索均成功。
- 增加逐 redirect DNS 复验与 429 retryable 反例，确认请求不携带 Authorization/Cookie 等秘密头。
- 增加跨 conversation IDOR、pending delete、claim 后不可删除、幂等重试、hard delete metadata/CAS GC、canonical 无 base64、Chat/Responses 精确 payload 反例。
- production build 人工浏览器检查覆盖桌面与 390×844。窄屏发现原有浮动侧栏按钮覆盖 Composer 发送按钮，已移除冗余浮动 sidebar toggle（顶栏入口保留），复测附件和发送按钮均可操作，控制台无 warning/error。
- Fake Model Playwright 新增图片 + 文本文件上传、真实多模态 payload 检查、最终答复和刷新后附件展示；同时按现有“终局 workflow 默认折叠”设计更新旧 reasoning E2E 的展开步骤，完整 13 场景通过。

## 3. 最终证据

- Ruff format check：130 files already formatted。
- Ruff lint：通过。
- Python：396 passed，4 skipped；skip 仍为 Windows 符号链接权限环境项。
- Vitest：18 files / 68 tests 通过。
- Playwright production Fake Model：13 passed，其中 task_009 附件闭环 1 场景。
- TypeScript typecheck、ESLint、OpenAPI sync、Vite production build：通过。
- `uv build`：wheel 与 sdist 均成功。
- `git diff --check`：通过。
- 高风险私钥/API key 模式扫描：无命中。
- 真实公网 smoke：`web_search("Python official documentation")` 返回 2 条结果；`web_fetch("https://example.com/")` 返回可见正文。

## 4. 已知边界

- `web_search` 依赖搜索站点 HTML 结构，fixture 能捕获解析退化，但后端仍可能随站点改版需要替换。
- 网络工具只读公开文本，不执行 JavaScript、不登录、不提交表单，也不是通用浏览器。
- 附件能力按 OpenAI 官方 Chat/Responses 输入协议实现；第三方 OpenAI-compatible 网关是否支持图片/文件由其自身决定，失败会诚实终止为 provider error。
- 单附件 10 MiB、每 turn 4 个、合计 20 MiB；busy Inbox 首版不接受附件。
