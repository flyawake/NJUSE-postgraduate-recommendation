# 任务编号：task_008

## 1. 任务目标

对 task_003-task_007 的产品化改造执行最终集成、性能、安全、恢复、可访问性、真实模型评测与交付演练，形成可复现的发布候选和面试证据。此任务不继续堆叠新概念，而是把现有能力证明为稳定、可解释、可演示的完整产品。

## 2. 背景与上下文

- 单个任务的局部测试不能证明长会话、后台运行、队列、reasoning 与 memory 组合后的因果顺序和资源边界。
- 当前真实外部模型 smoke 因没有合法凭据记为 N/A；最终产品发布前必须至少用一个用户配置的真实服务完成端到端验证，不能只依赖 Fake Model。
- 题目要求公开仓库、1000 汉字以内 README.txt 和 2 分钟以内视频，且 2026-09-02 24:00 后不得推送。发布门禁必须早于截止时间完成，给录制和回退留出缓冲。

## 3. 技术约束

- 固定评测同时覆盖内核成功率和产品行为，不用“测试数量”代替任务成功：至少包含只读诊断、单文件修复、多文件修复、命令失败恢复、取消、Queue/Steer、多轮追问、会话恢复和记忆召回。
- Fake Model 是确定性回归基线；真实 provider smoke 只使用用户本地凭据，不打印、不录屏、不提交 key。
- 发布性能预算必须在约定测试机、production build 与固定数据规模上测量并保留原始证据；不得用开发服务器热更新结果代替。
- 数据 schema migration 可前向升级并在失败时保留原数据库备份；服务异常退出后 Conversation、Queue、Memory 不重复消费或静默丢失。
- 默认 UI 不展示请求体、上下文字符数、状态机 phase 等开发信息；高级诊断区可按需查看脱敏详情。
- 安全审查覆盖本地 Host 边界、CSP、凭据、路径规范化、命令执行、SSE/API 脱敏、会话导出和 memory poisoning。
- 所有最终宣称都必须有测试、截图、日志摘要或可重复命令；未完成项明确降级，不以文案掩盖。

## 4. 实现步骤

1. 建立固定离线评测清单和评分器，记录成功/失败、最终验证、step/provider attempt、工具错误、重复调用、上下文用量、首 token/总耗时。
2. 完成长会话和并发压力场景：2000 条 transcript event、2000 条 memory、多个后台 conversation、连续 Queue/Steer、SSE 断连恢复。
3. 定义并验证性能预算：静态首屏、输入响应、workspace validation 次数、事件批处理、DOM 节点数、数据库检索、进程内存和取消时延。
4. 执行 crash/restart/schema migration/损坏配置演练；验证 inbox claim、canonical history、partial reasoning 和 memory projection 的恢复语义。
5. 完成安全与隐私 threat model、秘密扫描、依赖审计、CSP/Host/API 检查、恶意工具输出和记忆注入用例。
6. 完成键盘、焦点、屏幕阅读器名称、对比度、缩放和窄屏测试；中文与英文界面不出现截断或开发占位文本。
7. 用至少一个真实 OpenAI-compatible 或已实现 native wire provider 完成“读取—修改—运行验证—多轮追问”的 GUI smoke，并保存脱敏证据。
8. 从全新目录/环境执行安装、production 静态资源、wheel/sdist、CLI 兼容、GUI 启动和离线演示；核验开源依赖许可证。
9. 冻结 release candidate，编写 1000 汉字以内 README.txt、架构/限制说明和 2 分钟演示脚本，预演后再录制最终视频。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `evals/` / 固定 fixtures | 新增 | 可复现任务集与评分结果 |
| Python/前端/E2E 测试 | 修改 | 集成、压力、恢复、安全、a11y |
| migration / recovery | 修改 | 备份、幂等恢复与错误提示 |
| production 配置 / CSP | 修改 | 发布安全和资源边界 |
| `README.md`、`README.txt` | 修改/新增 | 开发说明与最终精简说明 |
| `docs/` 或 guide 证据 | 新增 | threat model、评测与演示脚本 |
| build/package workflow | 修改 | clean install、静态资源和产物核验 |

## 6. 验收标准

- [ ] task_003-task_007 的所有功能验收与全量回归在 release commit 上一次性通过，不使用分散历史结果拼接结论。
- [ ] 固定离线任务集输出可复现指标和失败分类；关键场景不存在消息乱序、重复工具副作用、跨会话/工作区泄漏。
- [ ] production build 下输入、validation、SSE、长 transcript、数据库与取消时延满足书面预算，且有原始可复查证据。
- [ ] crash/restart/migration 演练不丢 canonical Conversation、Queue 和 confirmed Memory，不重复 claim；失败时提供可恢复错误与数据库备份。
- [ ] 安全、秘密扫描、依赖审计、Host/CSP、prompt/memory injection 和导出隐私门禁全部通过。
- [ ] 至少一个真实模型从 GUI 完成真实编程任务和多轮追问；证据脱敏且模型/provider/wire API 声明准确。
- [ ] clean install、wheel/sdist、生产静态资源、CLI fallback 和 GUI 启动在新环境复现；评审运行不要求安装 Node。
- [ ] README.txt 不超过 1000 汉字，视频不超过 2 分钟/200 MB；内容覆盖真实任务、AgentLoop、工具链、多轮/队列和一项关键工程决策。

## 7. 风险与注意事项

- task_008 是发布门禁，不是容纳延期功能的兜底任务。未通过的功能应回到对应任务整改或明确从版本范围移除。
- 真实模型具有非确定性，验收关注协议正确、工具因果顺序与可恢复性；固定成功率比较仍以离线脚本和多次重复为准。
- 截止时间不可逆。建议至少提前 24 小时冻结代码，之后只做演示和材料核验；任何远端仓库时间合规问题必须在冻结前解决。
- 演示视频不得显示 API key、本机私人路径、未脱敏会话或其他工作区记忆。

## 8. 最小发布范围与冻结规则

### 8.1 Release Candidate 必须包含

- task_001-task_007 已验收的适用项，以及一份在同一 commit 上重跑的全量证据。
- 本地 GUI、CLI fallback、provider profile、持久多轮、streaming/Think、Queue/Steer 和 P0 显式记忆的完整主路径。
- 确定性 Fake Model 回归、至少一个真实 provider GUI smoke、固定任务评测、性能/恢复/安全/a11y 报告。
- 干净安装产物、公开仓库合规检查、README.txt 和最终演示视频。

### 8.2 冻结后允许/禁止

- 冻结后只允许修复 release-blocking bug、文案错误、秘密泄漏和材料问题；每个变更必须重跑受影响门禁。
- 禁止在 task_008 临时增加 MCP、多智能体、仓库索引、沙箱、云同步等新能力。
- 任何 task_003-task_007 功能若无法满足数据/安全不变量，应整体关闭 capability 并诚实说明，不能保留“偶尔可用”的按钮。
- 发布结论只针对一个明确 commit SHA 和 production build hash；工作区未提交变化不能进入证据。

## 9. 验收架构与证据框架

```text
Versioned eval manifest
        │
        ├─ Kernel/API pytest ─────────┐
        ├─ Frontend unit/contract ────┤
        ├─ Production Playwright ─────┤
        ├─ Crash/migration harness ───┤──► Evidence bundle + release report
        ├─ Performance benchmark ─────┤
        ├─ Security/a11y audit ───────┤
        └─ Live provider smoke ───────┘
```

采用：

- **Test pyramid + contract tests**：多数边界由离线 Python/TS 验证，少量关键组合由 production E2E。
- **Versioned manifest**：任务输入、fixture repo、fake stream、期望事实和评分规则全部版本化。
- **Evidence as data**：每次 run 输出 JSON result，再由报告器生成 Markdown 摘要，避免人工复制互相矛盾的数字。
- **Failure taxonomy**：协议、模型、工具、上下文、并发、恢复、UI、安全分别归类，不只给 pass rate。
- **Release checklist**：代码、数据、产物、仓库、材料均需明确 owner/证据/结论。

## 10. 固定评测集设计

### 10.1 任务类型

| ID 类别 | 场景 | 核心评分 |
| --- | --- | --- |
| DIAG | 只读定位一个确定缺陷 | 找到正确文件/原因，无写入 |
| EDIT1 | 单文件小修复 | diff 正确、目标测试通过 |
| EDITN | 跨文件实现与回归 | 多文件一致、验证完整 |
| FAIL | 首个命令/工具失败后恢复 | 错误被利用、无重复死循环 |
| CANCEL | model/tool/verification 阶段停止 | 有界终止、事实可恢复 |
| MULTI | 三轮追问同一问题 | 使用前文、无跨会话污染 |
| QUEUE | 三条 Queue + reorder | 严格逐轮 FIFO |
| STEER | streaming 中请求 Steer | 下一安全边界或无损 demote |
| RESTART | active turn/queue 时重启 | interrupted、不重放、队列不丢 |
| MEMORY | A 保存、B 召回、删除 | scope/来源/删除正确 |

### 10.2 Manifest

每个 eval case 使用 JSON/YAML（优先项目现有能力，无需新 runtime parser）描述：

- `id/version/fixture_hash`、初始 repo 构造、user turns、fake model script/provider profile。
- 允许工具与预期关键调用、禁止副作用、预期最终文件 hash/测试命令。
- 最大 step/attempt/tool call/耗时预算。
- expected conversation/inbox/memory facts 和 UI assertion tags。
- scorer 权重与 blocking conditions；安全/跨 scope/重复副作用一票否决。

报告至少输出：case status、最终验证、step/provider attempt/tool call、重复签名、工具失败、context chars/token usage（可得）、TTFT、总耗时、失败分类、artifact path。

### 10.3 Fake 与 Live 的分工

- Fake Model 精确控制 fragment、tool calls、retry、race 和错误，作为 release blocking deterministic suite。
- Live smoke 验证真实网络协议、profile/credential、模型工具选择和用户体验，不用一次成功替代 deterministic tests。
- Live 编程任务应可快速验证、无私人数据、允许重置；至少重复 2 次，记录成功次数与非确定性差异。

## 11. 性能预算与测量方法

所有数字在 feedback 标明 CPU/RAM/OS、Python/Node/浏览器版本、production asset hash；本表是默认硬门槛，若环境必须调整需先修订 guide。

| 指标 | Fixture | 目标/硬门槛 | 测量 |
| --- | --- | --- | --- |
| workspace validate | stable path + 50 draft/SSE | 0 新请求 | Playwright network counter |
| 输入响应 | 2,000 transcript events | keydown→paint p95≤50 ms，max≤100 ms | browser Performance marks |
| mounted transcript | 2,000 events | 初始≤350 items，按页有界 | DOM query |
| stream render | 2,000 tiny chunks | React/coalesced commit≤100，正常≤20/s | instrumented store |
| conversation list | 200 conversations, page 50 | warm API p95≤100 ms | Python benchmark |
| memory retrieval | 2,000 entries | warm p95≤50 ms，cold p95≤150 ms | repository benchmark |
| queue mutation | 100 items | common mutation p95≤100 ms | API benchmark |
| change summary | 100 changed files | collapsed DOM 有界，展开/点击 p95≤100 ms | Playwright marks |
| artifact preview | 20,000-line capped diff | 首个 hunk≤300 ms，滚动/切文件可用 | API + browser marks |
| cancel acknowledgement | active stream | UI/HTTP ack≤250 ms | Playwright/API marks |
| cancel terminal | cancellable model/tool | ≤5 s；不可中断命令按政策单列 | worker timestamps |
| SSE reconnect | retained cursor | 首个一致 snapshot/event≤2 s | disconnect proxy fixture |
| initial local UI | warm service | usable Composer≤2 s | Playwright navigation marks |

- 运行 benchmark 前预热一次，至少 20 次样本（重型 crash 除外），同时报告 median/p95/max，不只挑最好值。
- 测量代码不得进入默认用户 UI；可通过 test build flag/diagnostic endpoint 暴露计数。
- 内存检查使用 Python `tracemalloc`/平台资源指标和浏览器 heap 可用信息，关注多轮后是否持续单调增长；若无法跨平台给绝对值，至少 10 次创建/删除循环后无不可回收对象线性增长。

## 12. Crash、恢复与 migration 矩阵

| 注入点 | 重启后期望 |
| --- | --- |
| migration backup 后/schema step 中 | 原库可打开或明确使用备份；version 不半前进 |
| turn starting/worker 未启动 | rejected/interrupted，无幽灵 running |
| provider partial reasoning/text | attempt abandoned/interrupted，partial 可诊断，不入 canonical final |
| assistant tool-call 已存/结果未全 | synthetic unknown result 配对，不重放副作用 |
| Queue claim/next turn insert | item 与唯一 turn 对应，或 blocked/queued |
| Steer pending/safe claim | delivered 一次或 demote Queue，不丢 |
| Memory edit/index update | facts/index 同版本，必要时可重建 |
| delete/reset 中间 | 事务全有或全无，无正文残留孤儿 |

实现 harness 时使用子进程、barrier/failpoint 和临时 agent home/workspace；Windows/Linux 路径差异进入测试，不用 `taskkill`/随机 sleep 猜测时机。

## 13. 安全与隐私 threat model

### 13.1 资产与信任边界

- 资产：provider credential、workspace 文件、Conversation/canonical history、visible reasoning、Queue draft、Memory、日志与导出。
- 边界：浏览器↔loopback FastAPI、FastAPI↔SQLite/credential store、AgentLoop↔provider、AgentLoop↔ToolExecutor/workspace。
- 攻击输入：恶意网页跨站请求、恶意 workspace 文件/工具输出、provider error、memory prompt injection、路径链接、超大 SSE/DTO。

### 13.2 必测威胁

| 类别 | 用例/门禁 |
| --- | --- |
| Web local service | Host/Origin/session token、CORS、CSRF-like mutation、CSP、非 loopback bind 拒绝 |
| Credential | 只写不读、日志/异常/DOM/截图/导出扫描、profile 删除语义 |
| Workspace | canonical path、symlink/reparse、编辑前读取、新鲜度、命令 cwd/取消 |
| Provider/SSE | fail-closed payload whitelist、超大/未知 event、SDK error 脱敏 |
| Conversation | ID/version/资源归属、archive/delete、DB 文件权限与备份 |
| Artifact preview | change ID 层级归属、无任意 path read、历史 blob 完整性、大小限制、delete/GC |
| Queue/Steer | 重放 idempotency、跨 conversation item ID、重复副作用 |
| Memory | secret scanner、scope isolation、XML/JSON escape、低信任注入、hard delete |
| Export/材料 | 默认脱敏、显式 raw 警告、私人绝对路径与 reasoning 内容检查 |

- 使用仓库 secret scan + 定向 canary：测试 key 写入受保护入口后，扫描 DB/log/DOM/screenshot/export 是否出现原值。
- dependency audit 同时记录 Python 与 npm；新增许可不明确或高危漏洞需升级/替换/移除，不能只注明“开发依赖”。

## 14. 可访问性、国际化与视觉 QA

- 键盘路径覆盖：新建/切换会话、ContextBar、Composer、Start/Stop、Think、ActionRow、QueueDock、rename/archive/delete、Memory Center。
- Dialog/Drawer 打开时正确 focus trap，关闭回到触发器；危险确认有可理解标题和取消默认焦点。
- streaming/queue 状态不逐 token骚扰 screen reader；aria-live 只播关键状态变化。
- 所有 icon-only action 有 accessible name/tooltip，状态不只靠颜色；错误、running、queued、steer 有文字或图标形状差异。
- 200% browser zoom、320px 宽、1280×720、长中文/英文、超长路径/模型名均无不可操作遮挡；文本允许合理换行/ellipsis 并可查看完整值。
- 视觉截图使用 production build，覆盖 light/dark、zh-CN/en-US、idle/running/Think/Queue/error/success/Memory。

## 15. Clean install、打包与兼容

### 15.1 干净环境流程

1. 从 release commit 新 clone 到不含缓存/配置的新目录。
2. `uv sync --all-groups`，运行 Python/前端生成与测试；校验 lock 未漂移。
3. `npm ci` 后 typecheck/lint/test/build/audit；确认构建产物同步进 Python package。
4. 构建 wheel/sdist，在独立 venv 安装 wheel；不依赖源码 checkout 启动 GUI/CLI。
5. 无 Node 环境启动已安装 GUI，完成 Fake Model smoke；缺 config 时 onboarding 正常。
6. 使用旧 task_002 config/profile 副本升级，验证 migration；再用全新 home 验证首次启动。

### 15.2 兼容口径

- CLI 旧环境变量 fallback、旧 Chat profile 和已有 credential reference 必须继续可用。
- 新 DB schema 只前向；README 明确备份和数据位置。
- Windows 是主演示平台，至少用 CI/可用环境验证 Linux path/权限；平台不支持项必须有稳定 skip 原因而非静默忽略。
- 静态资源 hash、Python package version、bootstrap server version 对齐，避免浏览器缓存旧 schema。

## 16. 发布报告与提交材料

建议生成 `docs/release/`（不含秘密/私人绝对路径）：

- `RELEASE_REPORT.md`：commit、环境、能力矩阵、已知限制、所有门禁结论。
- `eval-results.json` + 摘要：固定任务逐项数据。
- `performance.json` + 摘要：fixture、样本、median/p95/max。
- `THREAT_MODEL.md`：资产/边界/威胁/缓解/残余风险。
- `MIGRATION.md`：schema version、备份、失败恢复。
- `ARTIFACT_REVIEW.md`：TurnChangeSet coverage、快照预算、历史 diff、隐私/删除边界。
- `DEMO_SCRIPT.md`：2 分钟时间轴、输入、预期步骤、失败回退。

README.txt 在 1000 汉字内只包含评审真正需要的信息：项目一句话、仓库 URL、环境/启动命令、provider 配置入口、演示能力、核心设计和必要安全说明。详细开发文档留 README.md/docs，不挤入提交文本。

视频建议时间轴：

| 时间 | 内容 |
| --- | --- |
| 0–15s | 产品目标、选择已有会话/workspace/profile |
| 15–70s | 提交真实修复，展示 Think 摘要、平面工具活动、文件修改和测试 |
| 70–95s | 运行中 Queue/Steer 或多轮追问，证明不是单次脚本 |
| 95–110s | 会话切换/记忆召回中的一项产品能力 |
| 110–120s | 验证结果、AgentLoop/工具链一句话架构、仓库地址 |

必须预备一个完全离线 Fake Model 演示模式，但正式视频优先使用已验证真实 provider；离线 fallback 要明确标注，不能冒充真实模型。

## 17. 实施批次和最终决策

### 批次 A：冻结 manifest 与 baseline

先在未优化版本跑出基线和失败项，冻结 eval fixture/hash/评分；禁止优化后更换题目让指标变好。

### 批次 B：集成/性能/恢复整改

按 blocking failure 回到对应 module 修复，保留前后对比；任何功能语义变化先修订原 task acceptance。

### 批次 C：安全/a11y/package/live smoke

安全与真实 provider 都通过后才产生 RC。live 凭据问题由用户配置解决，代码不得加入临时 key。

### 批次 D：RC freeze 和材料

在目标日期前冻结 SHA，完成 clean install、远端合规、README.txt、视频预演/录制、zip 内容核验；材料生成后不再修改代码，除非重新执行完整受影响门禁。

最终结论只能为 `release-ready` 或 `not-release-ready + blocking items`，不得以局部测试通过宣布完成。
