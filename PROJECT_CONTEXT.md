# 项目上下文

> 本文件是 Master Agent 与 Developer Agent 共享的唯一长期项目上下文。项目启动后应由项目负责人补充，并在技术栈、约束或进度发生变化时保持更新。

## 项目名称

软件工程专业推免项目：构建编程智能体。

## 项目目标

个人独立设计并实现一个可与大语言模型交互的编程智能体（coding agent）。智能体应能自主读取和写入本地文件、执行本地命令，并通过多轮“模型决策 - 工具执行 - 结果回传”完成用户交付的真实编程任务；产品形态可类似简化版 Claude Code、Codex、OpenCode 或 DeepSeek Harness。

项目不仅需要可运行，还需要能够在面试中解释并辩护关键设计决策。核心自研范围至少包括：对话历史与上下文管理、工具定义与本地执行、模型输出解析、循环终止条件和错误处理。

考核要求只是最低验收线，不是项目的设计上限。长期使命是形成一个可靠、可观察、可扩展且由评审者能够完全解释的本地 Coding Agent 内核：它应在真实代码库中先取得证据再修改、修改后主动验证、失败时可恢复、全过程可追踪，并能以稳定接口继续接入更好的仓库理解、执行隔离、模型适配和评测能力。任何增强都必须用任务成功率、测试通过率、无效工具调用数、步数/成本或故障可诊断性证明价值，不能以堆叠框架和功能数量代替工程质量。

## 技术栈

项目确定使用 Python 3.10+、`uv`、`src/` package layout、`pytest` 和 `ruff`。生产模型接入使用普通 `openai` Python 客户端的 OpenAI-compatible Chat Completions tool calling；本地代码自行维护消息历史、调用关联、上下文预算、工具执行、停止策略和错误恢复。AgentLoop 必须是显式有限状态机，区分逻辑 step 与 provider attempt，保持 append-only canonical history 和 tool-call/result 一一配对，并输出带单调事件序号的结构化 RunResult/事件。工具调用先经过 AgentLoop 的调用 ID、重复和取消守卫，再进入独立 ToolExecutor 的“解析/校验 - 策略判定 - 执行 - 结果归一化 - 模型呈现”管线；AgentLoop 不直接调用具体工具。ContextManager 保持事实历史与模型请求投影分离，压缩时优先保留错误、最近轮次和每个文件的最新观察。文件发生实际变更后，CompletionPolicy 在允许最终完成前执行一次有界验证检查，并把验证状态写入 RunResult。

运行时第三方依赖原则上仅保留普通 `openai` 客户端，其他核心能力优先使用标准库。MVP 工具确定为 `glob`、`grep`、`read_file`、`write_file`、`edit_file` 和 `run_command`，并实现编辑前读取、SHA-256 版本新鲜度、原子替换、领域化输出上限及重复调用保护。

task_002 已新增本地图形层：前端使用 TypeScript、React、Vite、Tailwind CSS 与可访问 UI primitives，Python 侧使用 FastAPI/ASGI、类型化 JSON API、SSE 和 RunController 适配既有 AgentLoop。Node.js 只用于前端开发、测试和 production build；最终静态资源随 Python 包分发，评审者运行 GUI 不应额外安装 Node。前端以 Vitest/React Testing Library 和 Playwright Fake Model 闭环验证，UI 默认简体中文并提供完整英文切换。

2026-08-28 产品演进方案确定新增但尚未实现的边界：以标准库 SQLite append-only fact/event source 承载 Conversation、Turn、Inbox 与 Memory；ModelClient 在 adapter 内兼容不同 wire API，向 AgentLoop 输出统一的 text/reasoning/tool/usage/error 流事件；Queue 与 Steer 是不同的持久领域状态，Steer 只在 AgentLoop 安全边界注入；长期记忆通过独立 MemoryService 按 global/workspace/conversation 作用域向 ContextManager 提供有预算、可追溯的投影。上述均保持自研 AgentLoop/ToolExecutor/ContextManager 为核心，不引入 agent 框架。

禁止使用任何 agent 框架或 Agent SDK，包括但不限于 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI。允许使用模型厂商的普通 API 客户端库、OpenAI 兼容网关和模型原生 tool calling 接口。不得依赖 API 服务端托管的代码执行或文件工具，如 Code Interpreter、Files API。

## 目录结构说明

- `src/coding_agent/`：CLI、配置、内部数据模型、模型适配器、上下文管理、AgentLoop、系统提示和本地工具。
- `src/coding_agent/web/`：task_002 的本地 GUI 服务、RunController、公开 DTO 与已构建静态资源；不得复制 AgentLoop。
- `frontend/`：task_002 的 React/TypeScript/Vite 源码、设计系统、i18n 和前端测试。
- `tests/`：配置、模型解析、上下文、工具、终止策略和离线端到端测试。
- `guide/` 与 `feedback/`：Master/Developer 任务管理资料，不承载业务实现。
- `README.md`：开发仓库的安装、架构、使用、安全边界与测试说明。
- 原始题目 PDF 仅作为本地需求来源，不得加入公开仓库提交。

## 运行 / 构建方式

计划使用以下标准命令，task_001 实现后以仓库实际结果为准更新：

```powershell
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run coding-agent --workspace <path> "<programming task>"
```

CLI 仍兼容从进程环境读取 `OPENAI_API_KEY`、`OPENAI_MODEL` 和可选 `OPENAI_BASE_URL`。GUI 已提供用户级多服务商 profile、可编辑 base URL、独立 credential store 与设置页；当前实际 wire API 仍是 `openai_chat_completions`，不能把兼容 base URL 夸大为已原生支持 Anthropic/Responses 等协议。task_005 将在 adapter 边界增加经测试的原生流式协议。最终还必须在 `README.txt` 中给出精简且可复现的运行方法。

## 代码规范

Python 代码必须通过 Ruff format、Ruff lint 和 pytest。task_002 前端还必须通过 TypeScript typecheck、lint、Vitest、Vite production build 和 Playwright Fake Model E2E，且使用锁文件保证可重复安装。核心模块使用清晰类型标注，SDK 类型不得泄漏出 ModelClient 适配边界；前端只消费脱敏的公开 DTO，不解析自由文本判断 Agent 状态。所有凭据必须通过环境变量或未入库配置提供；API key 等凭据不得出现在仓库、日志、DOM、测试截图、`README.txt` 或演示视频中。测试默认离线并使用 fake model/client，不能依赖真实 API 或开发者机器的绝对路径。

## 当前进度

截至 2026-08-27，`task_001` 可靠内核已通过 Master 整改复验并归档。首验发现的 grep 符号链接边界、read 观察键规范化和 policy 后取消竞态三项 A5/A8 缺口均已修复；独立复现与标准命令通过，结果为 128 passed、4 skipped（均因当前 Windows 无符号链接创建权限，符合既定 skip 规则）。方案已研究本机 DeepSeek Harness，并对照 Codex、OpenCode、Gemini CLI、OpenHands、SWE-agent 与 Aider 的公开架构资料，吸收工具管线、事件生命周期、策略边界、历史投影和完成验证方面的设计经验。项目不依赖或复制这些 agent 框架，而以普通模型客户端和自研内核实现明确、可测试的语义。

`task_002` 本地图形应用与多服务商配置已通过第三次 Master 源码复验并归档。React/TypeScript/Vite GUI、FastAPI/RunController/SSE、provider profile、独立 credential store、生产静态资源打包及 Fake Model 图形闭环均已落地；R3 进一步闭环了 AgentEvent 源头 fail-closed 脱敏、实时 phase/inspector、跨 step 活动分组、SSE cursor/reset/end 单调恢复、严格 config/Host 解析、credential/start 语义与真实成功演示证据。最终证据为 Python 238 passed/4 skipped、Vitest 33、Playwright 5、npm audit 0；外部真实模型 smoke 因当前环境无合法凭据按规则记为 N/A，并列为下一阶段首要验证。第一阶段仍只支持现有 `openai_chat_completions` wire API，不夸大为已支持 Anthropic/Responses 等原生协议。

可运行、可测试的端到端 Coding Agent 内核与本地图形应用首版均已通过验收。2026-08-28 已完成下一阶段详细工程规划，详见 `guide/PRODUCT_EVOLUTION_PLAN.md`。task_003 产品化界面与性能边界已通过最终源码复验：用户化文案/布局、连续平面活动流、Composer Start/Stop、workspace validation 隔离与竞态恢复、O(batch) 有界事件投影、安全结构化 action target 和生产测试边界均已闭环；最终证据为 Python 248 passed/4 skipped、Vitest 44、Playwright 7、audit 0。task_004-task_008 依次覆盖持久多轮会话、流式可展示 reasoning、Queue/Steer、可控跨会话记忆和最终发布门禁。task_004 的最终应用壳已经进一步确定为 Conversation 左侧主边栏、中间连续 transcript、默认关闭的右侧 Artifact Preview；每个 terminal turn 末尾显示净文件 ChangeSet，历史 diff 使用内容寻址的 turn-scoped before/after artifact，不能用当前 workspace/git diff 冒充。六个任务 guide 已补全目标/非目标、分层架构、领域模型、接口和事件、状态机、SQLite 事务、并发与恢复、前端状态所有权、实施批次、定量预算、故障注入、测试矩阵和回滚入口。下一实施任务为 task_004，后续任务不得用前端临时状态提前伪实现。

## 演进路线

1. **P0 可靠内核（task_001，已归档）**：显式 AgentLoop、规范消息配对、ToolExecutor/ToolPolicy、六个本地工具、资源感知上下文投影、变更后验证门槛、结构化事件和离线端到端测试；R1-R3 整改复验已通过。
2. **P1 图形化应用与多服务商配置（task_002，已归档）**：提供 React/TypeScript 本地 GUI、实时运行/工具时间线、开始与取消、用户级 provider profile、独立 credential provider 和 ModelClientFactory；保留 CLI/legacy 环境变量兼容，不把界面状态或 provider 分支耦合进 AgentLoop；A1-A22、R2、R3 已通过。
3. **P0 产品化界面与性能边界（task_003，已完成，待归档）**：已完成普通界面产品化、连续 transcript 与平面可展开工具活动、Composer Start/Stop、workspace validation 隔离与同键竞态恢复、O(batch) 有界投影、安全结构化 action target 和响应式视觉证据。
4. **P0 持久多轮会话（task_004，未开始）**：建立 Conversation/Turn/Run 与 SQLite append-only 事实源，完成独立上下文、切换、命名、归档/恢复、删除、后台运行和 crash recovery。
5. **P0 流式模型与可展示 reasoning（task_005，未开始）**：统一 provider stream event，分别适配 DeepSeek `reasoning_content`、OpenAI Responses reasoning summary 等公开输出；折叠 Think 不暴露或伪造隐藏 chain-of-thought。
6. **P1 运行中输入（task_006，未开始）**：实现 Host 权威、持久、严格 FIFO 的 Queue，以及只在下一安全 step 边界进入、失败回 Queue 的 Steer；完善 busy Composer 与队列管理。
7. **P1 可控记忆（task_007，未开始）**：以 MemoryService、SQLite FTS5、作用域、来源、候选审批、冲突和删除实现跨会话知识共享；默认不自动永久保存全部聊天。
8. **P0 发布与评测（task_008，未开始）**：全量集成、固定任务集、性能/安全/恢复/a11y、真实模型 GUI smoke、clean install 和最终 README.txt/视频门禁。
9. **后续仓库理解与隔离执行**：在 task_008 基线数据上评估 repository map、符号上下文、补丁编辑、read-only/approval/沙箱运行时；只有固定评测证明收益时才进入新任务。

## 已知约束

- 截止时间为 2026-09-02 24:00（北京时间，即 2026-09-03 00:00）；截止后不得再向公开仓库推送新提交。
- 允许并鼓励使用 AI 工具辅助开发，但参评者必须对每一处设计负责。
- Git 仓库必须是题目发布后新建的公开 GitHub 或 Gitee 仓库；必须保留完整提交历史，不得压缩或改写已推送历史。仓库地址写入提交物中的 `README.txt`。
- 最终提交物共三项：公开 Git 仓库、1000 汉字以内的 `README.txt`、2 分钟以内的演示视频。视频必须为 mp4 且不超过 200 MB，应展示 agent 完成一个真实编程任务并简要讲解实现。
- 上传内容仅包含以参评者姓名命名的 zip；zip 内只放 `README.txt` 与视频，不包含仓库副本。提交可重复，以最后一次为准。
- 面试将现场播放视频、要求简述设计方案并围绕 agent 运行机制和设计决策提问。
- 当前远端为 `git@github.com:flyawake/NJUSE-postgraduate-recommendation.git`，最早提交为 2026-08-26 15:09:37+08:00。最终交付前必须由项目负责人确认该时间晚于题目正式发布时间；若不满足，必须迁移到题目发布后新建的公开仓库并从新仓库开始保留真实完整历史。
