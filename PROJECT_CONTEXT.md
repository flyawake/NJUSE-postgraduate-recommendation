# 项目上下文

> 本文件是 Master Agent 与 Developer Agent 共享的唯一长期项目上下文。项目启动后应由项目负责人补充，并在技术栈、约束或进度发生变化时保持更新。

## 项目名称

软件工程专业推免项目：构建编程智能体。

## 项目目标

个人独立设计并实现一个可与大语言模型交互的编程智能体（coding agent）。智能体应能自主读取和写入本地文件、执行本地命令，并通过多轮“模型决策 - 工具执行 - 结果回传”完成用户交付的真实编程任务；产品形态可类似简化版 Claude Code、Codex、OpenCode 或 DeepSeek Harness。

项目不仅需要可运行，还需要能够在面试中解释并辩护关键设计决策。核心自研范围至少包括：对话历史与上下文管理、工具定义与本地执行、模型输出解析、循环终止条件和错误处理。

考核要求只是最低验收线，不是项目的设计上限。长期使命是形成一个可靠、可观察、可扩展且由评审者能够完全解释的本地 Coding Agent 内核：它应在真实代码库中先取得证据再修改、修改后主动验证、失败时可恢复、全过程可追踪，并能以稳定接口继续接入更好的仓库理解、执行隔离、模型适配和评测能力。任何增强都必须用任务成功率、测试通过率、无效工具调用数、步数/成本或故障可诊断性证明价值，不能以堆叠框架和功能数量代替工程质量。

## 技术栈

项目确定使用 Python 3.10+、`uv`、`src/` package layout、`pytest` 和 `ruff`。生产模型接入使用普通 `openai` Python 客户端，并在自研 adapter 边界支持 OpenAI-compatible Chat Completions 与 OpenAI Responses 两种 wire API；SDK 类型不得越过 adapter，本地代码自行维护消息历史、调用关联、上下文预算、工具执行、停止策略和错误恢复。AgentLoop 必须是显式有限状态机，区分逻辑 step 与 provider attempt，保持 append-only canonical history 和 tool-call/result 一一配对，并输出带单调事件序号的结构化 RunResult/事件。工具调用先经过 AgentLoop 的调用 ID、重复和取消守卫，再进入独立 ToolExecutor 的“解析/校验 - 策略判定 - 执行 - 结果归一化 - 模型呈现”管线；AgentLoop 不直接调用具体工具。ContextManager 保持事实历史与模型请求投影分离，压缩时优先保留错误、最近轮次和每个文件的最新观察。文件发生实际变更后，CompletionPolicy 在允许最终完成前执行一次有界验证检查，并把验证状态写入 RunResult。

运行时第三方依赖原则上仅保留普通 `openai` 客户端，其他核心能力优先使用标准库。MVP 工具确定为 `glob`、`grep`、`read_file`、`write_file`、`edit_file` 和 `run_command`，并实现编辑前读取、SHA-256 版本新鲜度、原子替换、领域化输出上限及重复调用保护。

task_002 已新增本地图形层：前端使用 TypeScript、React、Vite、Tailwind CSS 与可访问 UI primitives，Python 侧使用 FastAPI/ASGI、类型化 JSON API、SSE 和 ConversationService 适配既有 AgentLoop；旧 `/api/runs` 仅作为兼容投影。Node.js 只用于前端开发、测试和 production build；最终静态资源随 Python 包分发，评审者运行 GUI 不应额外安装 Node。前端以 Vitest/React Testing Library 和 Playwright Fake Model 闭环验证，UI 默认简体中文并提供完整英文切换。

2026-08-28 产品演进方案确定的长期边界：以标准库 SQLite append-only fact/event source 承载 Conversation、Turn、Inbox 与 Memory；ModelClient 在 adapter 内兼容不同 wire API，向 AgentLoop 输出统一的 text/reasoning/tool/usage/error 流事件；Queue 与 Steer 是不同的持久领域状态，Steer 只在 AgentLoop 安全边界注入；长期记忆通过独立 MemoryService 按 global/workspace/conversation 作用域向 ContextManager 提供有预算、可追溯的投影。Conversation/Turn 与统一模型流事件已分别在 task_004/task_005 落地；Inbox/Memory 仍由后续任务实现。上述均保持自研 AgentLoop/ToolExecutor/ContextManager 为核心，不引入 agent 框架。

禁止使用任何 agent 框架或 Agent SDK，包括但不限于 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI。允许使用模型厂商的普通 API 客户端库、OpenAI 兼容网关和模型原生 tool calling 接口。不得依赖 API 服务端托管的代码执行或文件工具，如 Code Interpreter、Files API。

## 目录结构说明

- `src/coding_agent/`：CLI、配置、内部数据模型、模型适配器、上下文管理、AgentLoop、系统提示和本地工具。
- `src/coding_agent/web/`：本地 GUI、公开 DTO、ConversationService 路由与已构建静态资源；旧 RunController 仅保留兼容测试，不得成为生产第二事实源或复制 AgentLoop。
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

CLI 仍兼容从进程环境读取 `OPENAI_API_KEY`、`OPENAI_MODEL` 和可选 `OPENAI_BASE_URL`。GUI 已提供用户级多服务商 profile、可编辑 base URL、独立 credential store 与设置页；adapter 现支持 `openai_chat_completions` 与 `openai_responses` 两种经测试的原生流式协议，并按 provider capability 限制 reasoning mode/effort。OpenAI-compatible base URL 不等同于原生支持 Anthropic 等其他 wire API，不得夸大兼容范围。最终还必须在 `README.txt` 中给出精简且可复现的运行方法。

## 代码规范

Python 代码必须通过 Ruff format、Ruff lint 和 pytest。task_002 前端还必须通过 TypeScript typecheck、lint、Vitest、Vite production build 和 Playwright Fake Model E2E，且使用锁文件保证可重复安装。核心模块使用清晰类型标注，SDK 类型不得泄漏出 ModelClient 适配边界；前端只消费脱敏的公开 DTO，不解析自由文本判断 Agent 状态。所有凭据必须通过环境变量或未入库配置提供；API key 等凭据不得出现在仓库、日志、DOM、测试截图、`README.txt` 或演示视频中。测试默认离线并使用 fake model/client，不能依赖真实 API 或开发者机器的绝对路径。

## 当前进度

截至 2026-08-27，`task_001` 可靠内核已通过 Master 整改复验并归档。首验发现的 grep 符号链接边界、read 观察键规范化和 policy 后取消竞态三项 A5/A8 缺口均已修复；独立复现与标准命令通过，结果为 128 passed、4 skipped（均因当前 Windows 无符号链接创建权限，符合既定 skip 规则）。方案已研究本机 DeepSeek Harness，并对照 Codex、OpenCode、Gemini CLI、OpenHands、SWE-agent 与 Aider 的公开架构资料，吸收工具管线、事件生命周期、策略边界、历史投影和完成验证方面的设计经验。项目不依赖或复制这些 agent 框架，而以普通模型客户端和自研内核实现明确、可测试的语义。

`task_002` 本地图形应用与多服务商配置已通过第三次 Master 源码复验并归档。React/TypeScript/Vite GUI、FastAPI/RunController/SSE、provider profile、独立 credential store、生产静态资源打包及 Fake Model 图形闭环均已落地；R3 进一步闭环了 AgentEvent 源头 fail-closed 脱敏、实时 phase/inspector、跨 step 活动分组、SSE cursor/reset/end 单调恢复、严格 config/Host 解析、credential/start 语义与真实成功演示证据。最终证据为 Python 238 passed/4 skipped、Vitest 33、Playwright 5、npm audit 0；外部真实模型 smoke 因当前环境无合法凭据按规则记为 N/A，并列为下一阶段首要验证。第一阶段仍只支持现有 `openai_chat_completions` wire API，不夸大为已支持 Anthropic/Responses 等原生协议。

可运行、可测试的端到端 Coding Agent 内核与本地图形应用首版均已通过验收。2026-08-28 已完成下一阶段详细工程规划，详见 `guide/PRODUCT_EVOLUTION_PLAN.md`。task_003 产品化界面与性能边界已通过最终源码复验：用户化文案/布局、连续平面活动流、Composer Start/Stop、workspace validation 隔离与竞态恢复、O(batch) 有界事件投影、安全结构化 action target 和生产测试边界均已闭环；最终证据为 Python 248 passed/4 skipped、Vitest 44、Playwright 7、audit 0。

task_004 持久多轮会话已于 2026-08-28 通过 Master 源码验收并归档：标准库 SQLite schema v3 保存 Conversation/Turn/canonical/public event/ChangeSet/Artifact 引用，支持事务幂等、投影校验、同会话/同 workspace 并发守卫和 crash→interrupted 无重放恢复；生产 `/api/runs` 通过 ConversationService compatibility adapter，不再建立第二个生产事实源。产品壳已变为左侧会话管理、中间多轮 transcript、默认关闭且按需打开的右侧历史文件审查；rename、归档/恢复、永久删除、独立 draft/scroll、后台运行徽标、逐 turn 净 ChangeSet、immutable before/after CAS、current divergence 和窄屏 drawer 均落地。最终证据为 Python 全仓 272 passed/4 skipped 加 3 项新增定向反例、Vitest 46、Playwright 完整 5 场景、npm audit 0 与 wheel 成功。跨会话记忆仍属于 task_007，不得在前端临时状态中伪实现。

task_005 流式模型与可展示 reasoning 已于 2026-08-28 通过 Master 源码验收并归档：provider-neutral `ModelStreamEvent`、严格 `TurnStreamAccumulator`、Chat Completions/Responses 双 adapter、可见 reasoning/summary、Responses opaque continuation、全局 provider attempt、SQLite v5 增量 checkpoint、snapshot/SSE 幂等恢复和折叠 Think transcript 均已闭环。前端保持 Think→tool→下一 Think→final 的真实时间顺序，取消、partial retry、断线重连三次及 2,000 delta 性能边界均有反例。最终证据为 Python 304 passed/4 skipped、Vitest 53、Playwright 9、Ruff/typecheck/lint/build/wheel 通过；真实模型 smoke 因无合法凭据按规则 N/A。

task_006 运行中输入已于 2026-08-29 通过 Master 源码验收并归档：SQLite v8 Inbox 以数据库状态迁移约束和单事务 claim/Turn/canonical/audit 创建保障 Host 权威与严格 FIFO；无 Turn 的 claimed Steer 会在重启时降级回 Queue，worker 启动失败会 blocked 并可 Retry，完成回调有每会话单消费者守卫。AgentLoop 仅在 READY-before-request 和 final-before-terminal 两个边界注入一条 Steer，并保留 canonical source/audit 区分。前端 busy Composer 的 Queue/Steer/Stop、Host snapshot 冲突收敛、draft ack、Inbox SSE/polling、zh-CN/en-US 和 100 条有界 QueueDock 已闭环。最终证据为 Python 325 passed/4 skipped、Vitest 54、Playwright 10、Ruff/typecheck/lint/API schema/build/wheel 通过；真实模型 smoke 仍因无合法凭据按规则 N/A。task_007 跨会话记忆保持未开始。

## 演进路线

1. **P0 可靠内核（task_001，已归档）**：显式 AgentLoop、规范消息配对、ToolExecutor/ToolPolicy、六个本地工具、资源感知上下文投影、变更后验证门槛、结构化事件和离线端到端测试；R1-R3 整改复验已通过。
2. **P1 图形化应用与多服务商配置（task_002，已归档）**：提供 React/TypeScript 本地 GUI、实时运行/工具时间线、开始与取消、用户级 provider profile、独立 credential provider 和 ModelClientFactory；保留 CLI/legacy 环境变量兼容，不把界面状态或 provider 分支耦合进 AgentLoop；A1-A22、R2、R3 已通过。
3. **P0 产品化界面与性能边界（task_003，已归档）**：已完成普通界面产品化、连续 transcript 与平面可展开工具活动、Composer Start/Stop、workspace validation 隔离与同键竞态恢复、O(batch) 有界投影、安全结构化 action target 和响应式视觉证据。
4. **P0 持久多轮会话（task_004，已归档）**：已建立 Conversation/Turn/Run 与 SQLite append-only 事实源，完成独立上下文、切换、命名、归档/恢复、删除、后台运行、crash recovery、逐轮 ChangeSet 与历史文件审查。
5. **P0 流式模型与可展示 reasoning（task_005，已归档）**：已实现统一 provider stream event、严格聚合器、DeepSeek/custom Chat `reasoning_content` 与 OpenAI Responses reasoning summary/opaque continuation 适配、增量 checkpoint/SSE 恢复及折叠 Think；只展示 provider 明确返回的可见内容，不暴露或伪造隐藏 chain-of-thought。
6. **P1 运行中输入（task_006，已归档）**：已实现 Host 权威、持久、严格 FIFO 的 Queue，以及只在两个 AgentLoop 安全边界进入、失败回 Queue 的 Steer；busy Composer、Host snapshot 和有界 QueueDock 已通过验收。
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
