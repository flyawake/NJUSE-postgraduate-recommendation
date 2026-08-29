# task_009：联网检索与聊天附件

## 1. 目标

在不启动 task_008 的前提下，为现有 Coding Agent 增加两项完整产品能力：

1. Agent 可通过自研函数工具执行互联网搜索并读取选定网页，结果可审计、可截断且不突破本地工具策略。
2. GUI 会话可上传图片和常见文件；附件持久化到 agent home，随 turn 展示，并按 Chat Completions/Responses 各自协议投影给模型。

Task 8 的发布与评测必须在本任务归档后再开始。

## 2. 不包含范围

- 不使用 OpenAI 内置 web search、Files API、file search、Code Interpreter 或 Agent SDK。
- 不实现网页登录、表单提交、浏览器自动化、JavaScript 执行或任意下载器。
- 不把附件复制进 workspace，不自动修改用户文件。
- 不承诺所有 OpenAI-compatible 网关都支持图片或 `input_file`；不支持时必须返回诚实的 provider 错误。
- 不开始 task_008 的 release、真实模型 smoke、README.txt 或视频工作。

## 3. 架构与安全边界

### 3.1 网络工具

- 在 ToolRegistry 注册 `web_search` 与 `web_fetch`，均为 READ effect，仍经过 AgentLoop 调用 ID、重复、取消和 ToolExecutor policy 管线。
- `web_search` 使用无密钥 HTTPS 搜索后端，输出 title/url/snippet；`web_fetch` 只读取公开 HTTP(S) 文本页面，提取可见文本。
- URL 必须拒绝凭据、非 HTTP(S)、localhost、私网、link-local、multicast、保留地址与 DNS rebinding；每次 redirect 重新校验。
- 连接/读取超时、响应字节数、结果数与正文字符数有硬上限；不记录 query 之外的秘密请求头，不执行页面脚本。
- 网页内容作为不可信观察结果返回，系统提示明确网页指令不能覆盖系统、工具或 workspace 策略。

### 3.2 附件

- SQLite 增量迁移新增 attachment metadata；二进制存放在 agent home 的专用目录并使用随机 ID/原子写入，不存进 workspace 或公开事件。
- 上传 API 使用原始二进制 body，避免新增 multipart 依赖；服务端忽略浏览器路径，只保存净化后的 basename、声明 MIME 与 sniff 后允许类型。
- 单文件≤10 MiB、每 turn≤4 个、总计≤20 MiB；支持 PNG/JPEG/GIF/WebP、PDF、文本/代码、常见文档/表格/演示 MIME。空文件、伪装 MIME、路径名、超限与跨 conversation ID 均 fail-closed。
- 上传后 attachment 只能由所属 conversation 的下一次 turn 原子 claim；幂等重试不能重复关联，未使用上传可删除。
- canonical UserMessage 只保存 attachment ref/metadata，不保存 base64；ContextManager 通过只读 loader 构建 detached request view。当前附件及最近一个附件消息可重新投影，旧二进制不会进入公共 DTO、日志或 SSE。
- 图片转换为 Chat `image_url` data URL / Responses `input_image`；文本代码优先转换为有界 text part；PDF/富文档等转换为 Chat `file.file_data` / Responses `input_file.file_data`。adapter 之外不出现 provider-specific shape。
- conversation hard delete 同步清理 metadata 与专用附件目录；附件下载/预览 API 必须验证 conversation ownership、只返回原始 MIME，并设置安全响应头。

### 3.3 前端

- Composer 提供可访问的附件按钮、隐藏 file input、拖放/粘贴图片支持、上传进度/错误、附件 chip、图片缩略图与移除操作。
- 已提交 turn 在 transcript 展示附件；图片可本地预览，文件显示名称/大小/类型，不把 base64 放入 bootstrap/SSE DOM 文本。
- busy Queue/Steer 首版不携带附件：运行中选择附件时明确禁用 Queue/Steer 提交，避免改变 task_006 Inbox schema 语义。

## 4. 主要改动范围

- `src/coding_agent/tools/`：network guard、search/fetch tool。
- `src/coding_agent/models.py`、`context.py`、`model_client.py`：provider-neutral attachment ref 与 adapter 映射。
- `src/coding_agent/conversations/`：schema、attachment repository/service、turn 原子关联、删除 GC。
- `src/coding_agent/web/`：上传/读取/删除 DTO 与路由、安全头。
- `frontend/src/`：API client、Composer/Transcript 附件 UI、i18n。
- `tests/`、Vitest、Playwright Fake Model：离线网络、SSRF、持久化、协议映射、UI 与生产闭环。

## 5. 验证顺序

1. 网络 validator/SSRF/redirect/上限与 search/fetch parser 定向测试。
2. attachment schema migration、原子 claim、幂等、删除、跨会话拒绝与 request projection 测试。
3. Chat/Responses 图片、文本、PDF payload 精确测试。
4. API、RTL、Fake Model production E2E：上传图片/文件→发送→模型确认收到→刷新仍展示；联网搜索→读取来源→最终回答含来源。
5. Ruff、pytest、typecheck、lint、Vitest、OpenAPI、build、Playwright、wheel/sdist、秘密/静态资源检查。

## 6. 风险与回滚

- 公共搜索 HTML 可能变化：parser 必须有 fixture，失败返回稳定错误；工具实现隔离，可单独禁用/替换 backend。
- base64 增加请求体和 token 成本：使用严格本地上限、只投影最近附件、UI 提示 provider 会接收附件。
- 自定义网关多模态兼容不一：不伪造支持，provider 4xx 由现有 adapter 安全映射。
- 工作区已有未提交 UI/配置改动：不得 reset/checkout；所有重叠文件采用小块补丁并按提交范围审计。

## 7. 完成条件

以同目录 `acceptance.md` 全部勾选、反馈归档、全量门禁通过和本地提交为完成；不得 push，不得启动 task_008。
