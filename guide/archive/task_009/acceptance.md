# task_009 验收标准：联网检索与聊天附件

## 联网检索

- [x] W1. AgentLoop 可调用 `web_search` 得到有界 title/url/snippet，并调用 `web_fetch` 读取选定公开网页正文；工具结果和 UI 活动流可追溯。
- [x] W2. 非 HTTP(S)、URL 凭据、localhost、IPv4/IPv6 私网/link-local/multicast/保留地址、初次 DNS 与 redirect rebinding 均被无副作用拒绝。
- [x] W3. 搜索/抓取具有连接/读取超时、redirect、响应字节、结果数和正文字符上限；HTML 脚本/样式不执行、不进入可见正文。
- [x] W4. 网络失败、429/5xx、非文本响应、解析失败返回稳定错误 code 与恢复提示，不导致主 loop 崩溃或泄露请求头。
- [x] W5. 网页正文被标记为不可信观察；页面中的系统提示、工具命令或越界路径不能改变 ToolPolicy/系统策略。

## 附件数据与 API

- [x] A1. 图片/文件原始 body 上传到 agent home 专用目录，SQLite 保存 metadata/ownership/state；schema 增量迁移和重启恢复不影响既有 Conversation。
- [x] A2. 文件名净化、MIME/sniff、空文件、单文件 10 MiB、每 turn 4 个、合计 20 MiB、允许类型与跨 conversation ownership 均由服务端 fail-closed。
- [x] A3. start turn 在同一事务原子 claim attachment；幂等重试不重复关联，失败不留下半关联 canonical message，未使用附件可删除。
- [x] A4. conversation hard delete 后 attachment metadata 与专用文件正文均删除；读取/预览接口对其他 conversation 返回 404/403 且无 IDOR。
- [x] A5. public DTO/SSE/log 只含 id、净化名称、MIME、kind、size 等 metadata；base64、agent-home 绝对路径和附件正文不进入日志或默认 DOM 文本。

## 模型投影

- [x] M1. provider-neutral UserMessage attachment ref 可持久化/恢复；ContextManager detached view 不改 canonical history，只加载当前/最近允许的附件。
- [x] M2. Chat Completions 图片为 `image_url` data URL、文件为 `file.file_data`；Responses 图片为 `input_image`、文件为 `input_file`，文本/代码使用有界文本 part。
- [x] M3. 同一附件不会在一个 provider request 重复，图片/文件顺序与 UI 顺序一致；附件大小不计入字符串预算但受独立二进制预算。
- [x] M4. 不支持多模态/文件输入的 provider 失败时保持 turn 可诊断终态，不降级为假装读过文件。

## 前端与闭环

- [x] U1. Composer 支持选择、拖放及粘贴图片，展示上传中/失败/可移除 chip；图片有缩略图，文件有名称/大小/类型。
- [x] U2. 已提交 turn 刷新后仍展示附件 metadata/预览；键盘、焦点、zh-CN/en-US、深色和窄屏均可用。
- [x] U3. busy Queue/Steer 不携带附件且界面明确禁用/解释；取消或切 conversation 不会把附件发送到错误会话。
- [x] U4. production E2E 覆盖图片+文本文件上传→发送→Fake Model 验证真实 payload→刷新展示；真实公网 smoke 覆盖 web_search→web_fetch 的来源读取链路。

## 质量门禁

- [x] T1. Python 覆盖网络解析/SSRF/redirect/限额、migration/claim/idempotency/delete/IDOR、Chat/Responses payload 与上下文边界。
- [x] T2. Vitest/RTL 覆盖附件选择/上传/移除/错误/已提交展示；Playwright 覆盖生产静态资源完整闭环。
- [x] T3. Ruff、pytest、typecheck、ESLint、Vitest、OpenAPI sync、Vite build、Playwright、wheel/sdist、diff/秘密扫描全部通过。
- [x] T4. 工作区先存改动被保留；Task 8 文件除依赖备注外未实现、未执行发布任务；仅创建本地提交，不 push。
