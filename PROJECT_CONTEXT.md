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

task_002 规划新增本地图形层：前端使用 TypeScript、React、Vite、Tailwind CSS 与可访问 UI primitives，Python 侧使用轻量 ASGI 服务、类型化 JSON API、SSE 和 RunController 适配既有 AgentLoop。Node.js 只用于前端开发、测试和 production build；最终静态资源随 Python 包分发，评审者运行 GUI 不应额外安装 Node。前端以 Vitest/React Testing Library 和 Playwright Fake Model 闭环验证，UI 默认简体中文并提供完整英文切换。

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

当前 task_001 实现只从进程环境读取 `OPENAI_API_KEY`、`OPENAI_MODEL` 和可选 `OPENAI_BASE_URL`：通过兼容 base URL 可以连接非 OpenAI 的 Chat Completions 服务，但只有一个连接槽，没有 provider/profile 身份、持久化设置、图形界面或 `.env` 自动加载。已规划 `task_002` 将项目升级为本地图形化 Coding Agent，并提供用户级多服务商 profile、独立凭据存储和 GUI 设置页，同时保留旧环境变量作为兼容 fallback。最终还必须在 `README.txt` 中给出精简且可复现的运行方法。

## 代码规范

Python 代码必须通过 Ruff format、Ruff lint 和 pytest。task_002 前端还必须通过 TypeScript typecheck、lint、Vitest、Vite production build 和 Playwright Fake Model E2E，且使用锁文件保证可重复安装。核心模块使用清晰类型标注，SDK 类型不得泄漏出 ModelClient 适配边界；前端只消费脱敏的公开 DTO，不解析自由文本判断 Agent 状态。所有凭据必须通过环境变量或未入库配置提供；API key 等凭据不得出现在仓库、日志、DOM、测试截图、`README.txt` 或演示视频中。测试默认离线并使用 fake model/client，不能依赖真实 API 或开发者机器的绝对路径。

## 当前进度

截至 2026-08-27，`task_001` 可靠内核已通过 Master 整改复验并归档。首验发现的 grep 符号链接边界、read 观察键规范化和 policy 后取消竞态三项 A5/A8 缺口均已修复；独立复现与标准命令通过，结果为 128 passed、4 skipped（均因当前 Windows 无符号链接创建权限，符合既定 skip 规则）。方案已研究本机 DeepSeek Harness，并对照 Codex、OpenCode、Gemini CLI、OpenHands、SWE-agent 与 Aider 的公开架构资料，吸收工具管线、事件生命周期、策略边界、历史投影和完成验证方面的设计经验。项目不依赖或复制这些 agent 框架，而以普通模型客户端和自研内核实现明确、可测试的语义。

`task_002` 已进入实现与整改复验阶段：React/TypeScript/Vite 本地 GUI、FastAPI/RunController/SSE、provider profile、独立 credential store、生产静态资源打包及 Fake Model 图形闭环均已落地，标准 Python/前端/Playwright 门禁可运行。第二次 Master 源码复验结论仍为“需整改”，当前唯一入口是 `guide/task_002/acceptance.md` 的 R3.1-R3.8，重点闭环命令 inline-value 脱敏、实时 phase/inspector、跨 step 活动分组、SSE 单调恢复与终止、严格 config/Host 解析、credential/start 语义及真实一致的演示截图。第一阶段仍只支持现有 `openai_chat_completions` wire API，不夸大为已支持 Anthropic/Responses 等原生协议。

可运行、可测试的端到端 Coding Agent 内核首版已经通过验收。下一阶段推进本地图形应用与多服务商配置、真实模型加固、仓库智能、隔离执行、评测与最终交付，不以完成考核材料作为项目能力建设的终点。

## 演进路线

1. **P0 可靠内核（task_001，已归档）**：显式 AgentLoop、规范消息配对、ToolExecutor/ToolPolicy、六个本地工具、资源感知上下文投影、变更后验证门槛、结构化事件和离线端到端测试；R1-R3 整改复验已通过。
2. **P1 图形化应用与多服务商配置（task_002）**：提供 React/TypeScript 本地 GUI、实时运行/工具时间线、开始与取消、用户级 provider profile、独立 credential provider 和 ModelClientFactory；保留 CLI/legacy 环境变量兼容，不把界面状态或 provider 分支耦合进 AgentLoop。
3. **P1 真实模型与质量评测**：用至少一个真实 OpenAI-compatible 模型完成可复现 smoke test；建立固定任务集，记录成功率、测试结果、步数、重试、工具错误、重复调用和上下文用量，基于证据调优提示与策略。
4. **P2 仓库理解与编辑质量**：引入可替换的 ripgrep 搜索后端、简洁 repository map、语言/符号级上下文和更稳健的补丁编辑；保持工具协议不变，避免把索引逻辑耦合进 AgentLoop。
5. **P3 执行与权限边界**：提供 read-only/auto-edit/approval 等策略模式及 Docker/受限进程运行时适配器；本地直接执行继续作为明确标注的可信工作区模式。
6. **P4 可扩展产品层**：在稳定内核上评估流式 UI、会话持久化、MCP、插件和多智能体；只有在单 Agent 基线和评测显示真实收益时才引入。

## 已知约束

- 截止时间为 2026-09-02 24:00（北京时间，即 2026-09-03 00:00）；截止后不得再向公开仓库推送新提交。
- 允许并鼓励使用 AI 工具辅助开发，但参评者必须对每一处设计负责。
- Git 仓库必须是题目发布后新建的公开 GitHub 或 Gitee 仓库；必须保留完整提交历史，不得压缩或改写已推送历史。仓库地址写入提交物中的 `README.txt`。
- 最终提交物共三项：公开 Git 仓库、1000 汉字以内的 `README.txt`、2 分钟以内的演示视频。视频必须为 mp4 且不超过 200 MB，应展示 agent 完成一个真实编程任务并简要讲解实现。
- 上传内容仅包含以参评者姓名命名的 zip；zip 内只放 `README.txt` 与视频，不包含仓库副本。提交可重复，以最后一次为准。
- 面试将现场播放视频、要求简述设计方案并围绕 agent 运行机制和设计决策提问。
- 当前远端为 `git@github.com:flyawake/NJUSE-postgraduate-recommendation.git`，最早提交为 2026-08-26 15:09:37+08:00。最终交付前必须由项目负责人确认该时间晚于题目正式发布时间；若不满足，必须迁移到题目发布后新建的公开仓库并从新仓库开始保留真实完整历史。
