# 任务编号：task_001 验收标准

## 功能验收点

- [x] A1. `uv sync --all-groups` 在干净环境完成，`uv run coding-agent --help` 与 `uv run python -m coding_agent --help` 均退出 0 且不访问模型 API。
- [x] A2. 缺少必需配置时 CLI 在首次模型调用前以退出码 1 失败并指出缺失字段；输出不包含任何 API key。
- [x] A3. AgentLoop 实现 `INITIALIZING → READY → REQUESTING_MODEL → HANDLING_RESPONSE → EXECUTING_TOOLS/CHECKING_COMPLETION/TERMINAL` 显式状态机并返回带 VerificationStatus 的结构化 RunResult；SDK 响应已转换为内部类型，canonical history 保持 append-only，下一模型请求前每个 tool call 恰有一个同 ID 结果。事件 sequence 在 run 内单调递增，`run_finished` 恰好发出一次且为末事件。
- [x] A4. `glob`、`grep`、`read_file`、`write_file`、`edit_file`、`run_command` 均以 ToolSpec 注册，具有闭合 JSON schema、匹配的 validator、READ/WRITE/EXECUTE effect、统一结果和稳定错误 code。调用按“loop guard → 参数解析/校验 → policy → handler → outcome/model rendering”执行；注入 policy 拒绝 WRITE/EXECUTE 时无副作用并回传同 call ID 的 `POLICY_DENIED`。
- [x] A5. 文件工具拒绝绝对路径、`..` 和符号链接越界；覆盖/编辑实施先读后改和 SHA-256 版本检查，未观察、版本陈旧或匹配数不符时原文件不变，成功变更使用同目录临时文件加 `os.replace`。
- [x] A6. `glob` 最多返回 100 个路径；`grep` 最多返回 200 条带文件和行号的匹配且每行预览最多 2,000 字符；`read_file` 使用 1-based offset、最多 500 行/50 KiB 的带行号窗口。三者均报告总量、省略或继续读取信息，并区分成功空结果与参数错误。
- [x] A7. `run_command` 使用 `shell=False`，验证工作目录、1-120 秒超时和 `purpose=inspect|verify|other`；stdout/stderr 分别按 head 4,000 + tail 6,000 字符保留并报告省略量，非零退出码仍作为成功获得的观察结果返回。只有最新文件变更后 `purpose="verify"` 的命令影响 VerificationStatus。
- [x] A8. ContextManager 对临时 request view 做确定性的资源感知投影，保留协议骨架、错误、最近两个逻辑 step 和每文件最新 `read_file` 窗口，canonical history 不被改写；逻辑 step 与 provider attempt 分别计数。正常 VERIFIED 完成、无变更 NOT_APPLICABLE 完成、FAILED/NOT_RUN 的一次延迟与有界放行、文本伴随工具、多个/重复 ID 调用、无效参数、未知工具、API 重试耗尽、连续 3 个失败轮次、相同调用达到第 3 次时提醒/第 5 次时终止、上下文溢出、空响应、第 20 步工具组后阻止第 21 次请求、半组取消和 Ctrl+C 均有状态迁移、配对结果、RunResult 与结构化事件测试。
- [x] A9. 离线端到端测试使用 Fake/Scripted Model 和真实临时工作区工具，覆盖“glob - grep - read - edit - `purpose=verify` run - 最终答复”，RunResult 为 VERIFIED，且不访问网络。

## 非功能验收点

- [x] A10. `uv run pytest -q` 全部通过；测试不依赖执行顺序、真实 API、开发者机器绝对路径或仓库外文件。
- [x] A11. `uv run ruff format --check .` 与 `uv run ruff check .` 均退出 0。
- [x] A12. `pyproject.toml` 和 `uv.lock` 一致；运行时依赖仅有普通 `openai` 客户端，不存在 agent 框架、Agent SDK、服务端代码/文件工具或未说明的新依赖。
- [x] A13. `README.md` 给出 AgentLoop 状态图、工具调用管线、上下文投影、完成验证、step/retry 计数口径、消息不变量、架构、安装、配置、运行、工具、终止策略、测试、安全边界和已知限制，命令可复制，且未宣称具备完整命令沙箱或充分验证证明。
- [x] A14. 仓库跟踪文件与 task diff 中不存在 API key、真实 `.env`、原始题目 PDF、缓存、构建产物或无关实现改动。
- [x] A15. `feedback/task_001_feedback.md` 逐项提供实现位置、验证命令与结果摘要、依赖变化和已知限制，并在 `feedback/INDEX.md` 登记为 `待评估`。

## 验收命令

```powershell
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run coding-agent --help
uv run python -m coding_agent --help
git status --short
git diff --check
```

Master Agent 验收时还应抽查 AgentLoop 非法迁移、canonical/request-view 分离、资源感知投影、step/retry 计数、完成验证延迟上限、工具组中断配对、事件序号/唯一终态、ToolSpec schema/validator、policy 先于副作用、三种结果表示、路径守卫、观察版本、原子替换、输出保留、重复调用检测、错误分支和依赖清单，不以 feedback 的自我声明代替代码与测试证据。

## Live smoke test 的 N/A 规则

- 若 Developer Agent 的执行环境存在合法、未泄露的模型凭据，必须在一次性工作区运行一次真实编程任务，并在 feedback 中仅记录脱敏命令、模型名、工具轨迹摘要和结果。
- 若执行环境没有合法凭据，task_001 可将 live smoke 标记为 `N/A - 无外部凭据`，但必须证明生产 ModelClient 的请求/响应映射已由 fake SDK client 测试。Master Agent 验收通过后必须创建后续 live smoke/演示任务，最终交付前该项不得继续为 N/A。
- 任何 live smoke 都不得输出、录屏或提交 API key；不得对不受信任仓库开放 `run_command`。

## 范围外判定

GUI/TUI、多智能体、长期记忆、向量检索、repository map、AST/LSP、会话持久化、流式输出、多厂商专用适配、完整操作系统沙箱、交互审批、MCP、最终 `README.txt`、视频与 zip 不属于 task_001。不得以缺少这些功能判定本任务失败，也不得在本任务中顺带实现。

## 首次验收整改复验

- [x] R1. `grep` 不读取、`glob` 不返回经符号链接解析后位于 workspace 外的候选文件；目录链接也不能绕过边界。实现有逐候选 canonical containment 守卫，并有可用平台上的自动化测试。
- [x] R2. `read_file("./a.txt")` 记录并返回规范化资源键；随后以 `./a.txt` 或 `a.txt` 执行 write/edit 都能识别同一观察版本。
- [x] R3. 取消在 policy/prepare 阶段到达时，WRITE handler 不执行，当前及剩余调用均产生 `ABORTED_BEFORE_DISPATCH` 配对结果，最终状态为 INTERRUPTED，`tool_call_count` 等于该组模型调用数且不重复计数。

R1-R3 是 A5/A8 的具体复验，不增加 task_001 功能范围；任一未通过时任务仍为“需整改”。
