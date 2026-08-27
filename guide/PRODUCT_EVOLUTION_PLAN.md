# 产品演进总计划（2026-08-28）

## 目标

把当前“可运行的 Coding Agent 图形壳”升级为真正面向用户的本地产品：界面克制、输入始终可用、运行过程可理解；每个会话拥有独立的多轮上下文，运行中消息具备明确的 Queue/Steer 语义；模型可公开提供的 reasoning/summary 能流式展示；长期记忆由用户控制；最终通过真实模型、性能、安全和恢复门禁。

本计划是 task_003-task_008 的总入口。详细实现边界与可勾选证据以各任务的 `plan.md`、`acceptance.md` 为准。

## 已确认的问题与根因

1. **界面仍像调试台**：步骤上下文字符数、状态机术语和大面积圆角操作框占据主视图；开始、停止和发送动作缺少统一状态语义。
2. **workspace validation 被无关渲染触发**：`WorkspaceField` 的 effect 依赖 `onValidated`，`MainPage` 每次 render 都创建新的 inline callback；输入 task 或收到 SSE 都会重渲染父级并改变 callback identity，因而清理 debounce、重新校验未变化的路径。
3. **运行投影粒度过大**：页面消费完整 RunStore，事件增长会使主页面和活动 feed 重建；长会话缺少有界 DOM/虚拟化策略。
4. **“think”没有协议边界**：当前 Chat Completions 非流式路径只在整轮结束后返回最终可见结果，既没有统一 model stream event，也没有区分 DeepSeek `reasoning_content`、OpenAI reasoning summary 与普通文本。
5. **Run 被当成会话**：没有稳定 Conversation/Turn 身份和持久事实源，因此切换、后台运行、多轮上下文、队列与恢复都无法可靠叠加。
6. **聊天历史不是长期记忆**：缺少跨会话作用域、来源、确认、冲突和删除语义，直接自动总结会把错误或秘密永久固化。

## 产品信息架构

### 左侧：会话导航

- 顶部只保留“新建会话”和搜索；列表展示标题、workspace、最近更新时间、运行/排队状态。
- 行菜单提供重命名、归档、删除；归档单独筛选，可恢复，删除二次确认。
- 切换会话不取消后台运行；运行状态和未读完成结果在对应行可见。
- 设置、记忆中心和归档入口固定在底部，不与聊天消息混排。

### 中部：连续 transcript

- 用户消息、assistant 正文、reasoning summary 和工具活动按发生顺序构成连续文档流。
- 工具活动默认是一行浅色图标 + 动词摘要，例如“读取了 3 个文件”“运行了测试”“修改了 1 个文件”；点击展开参数、脱敏输出、耗时和结果。
- 不再为每一步创建占满宽度的大圆角卡片；只有错误、权限确认、需要用户决定的状态使用有边界的 callout。
- `Think` 是可折叠块，默认收起、显示“思考中/思考摘要”；只展示 provider 明确返回且允许展示的 reasoning/summary，绝不伪造或暴露隐藏 chain-of-thought。
- 时间、step、context/token、provider attempt 等移入高级详情；普通用户只看到结果、必要状态和恢复动作。

### 底部：常驻 Composer

- 输入框保持可用并保留草稿；附件/上下文入口在左，主要动作在右。
- idle 时右侧主动作是发送/开始；运行后同一槽位切换为停止，避免两个互相竞争的按钮。
- busy 且已有草稿时，右侧控制组显示“加入队列”主动作、“插入当前轮”次动作和紧凑停止；提交被 Host 接受前不清空草稿。
- 队列以 Composer 上方的紧凑 dock 展示，支持编辑、删除、排序和手动 Steer；未被 claim 的消息不得伪装成已发送聊天气泡。
- 默认 Enter 行为随 idle/busy 语义一致，Shift+Enter 换行；Queue/Steer 快捷键可配置且 tooltip、按钮和无障碍名称一致。

### 顶部与详情

- workspace 与 provider profile 使用紧凑上下文按钮/弹层，而不是常驻开发表单；无效配置在原位给出用户可操作的错误。
- 右侧 inspector 默认关闭，只在查看工具详情、运行统计或诊断时打开；移动/窄屏使用 drawer。
- 高级模式才展示 run ID、event sequence、context budget、raw error code 等调试信息，且全部脱敏。

## 核心状态与数据边界

```text
Conversation（持久、可切换）
  ├─ Turn（一次用户输入到 terminal）
  │   └─ Run / AgentLoop steps（执行事实与流式事件）
  ├─ Inbox（Queue / Steer，Host 权威）
  └─ Context projection（历史 + 本轮 memory snapshot）

Memory（独立服务）
  ├─ global scope
  ├─ canonical workspace scope
  └─ conversation scope
```

- SQLite append-only event/fact source 是 Conversation、Inbox 和 Memory 的持久事实源；React store 只做 projection，不能成为唯一真相。
- Queue 等待完整 turn terminal 后严格 FIFO、一次一条创建新 turn；Steer 只在 AgentLoop 下一安全边界进入，错过窗口原子降级为 Queue。
- provider wire format 只存在于 adapter 内；AgentLoop 消费统一的 text/reasoning/tool/error/usage stream event。
- memory 每个 turn 最多检索一次并固定为 snapshot；SSE/tool 事件不能触发重复检索或 workspace validation。

## 任务拆分与依赖

| 顺序 | 任务 | 交付焦点 | 前置 | 不在本任务做 |
| --- | --- | --- | --- | --- |
| 1 | [task_003](task_003/plan.md) | 产品文案、Codex 风格平面 feed、Composer Start/Stop、渲染隔离与 validation 根因修复 | task_002 | 不伪造会话/队列/reasoning |
| 2 | [task_004](task_004/plan.md) | Conversation/Turn 持久化、多轮上下文、切换/命名/归档/删除、后台运行 | task_003 | 不用前端数组模拟持久队列 |
| 3 | [task_005](task_005/plan.md) | provider-neutral streaming、可展示 reasoning/summary、折叠 Think | task_004 | 不暴露隐藏思维、不假称所有 provider 支持 |
| 4 | [task_006](task_006/plan.md) | durable Queue/Steer、AgentLoop 安全插入、busy Composer | task_004、task_005 | 不把 Queue 当即时中断 |
| 5 | [task_007](task_007/plan.md) | 可控跨会话记忆、来源/作用域/审批/删除 | task_004；建议在 task_006 后 | 不默认自动记住全部聊天 |
| 6 | [task_008](task_008/plan.md) | 集成、性能、安全、恢复、真实模型、交付材料 | task_003-task_007 | 不接收未规划的新功能 |

只有一个任务处于“进行中”。每项必须经源码逻辑复验和生产 E2E 验收后，下一项才进入实施，避免多个开发者同时改写 Conversation/AgentLoop/Composer 的共同边界。

## 统一非功能预算

- workspace 路径不变时，task 输入、模型 delta、工具事件和详情展开均产生 **0 次** validation 请求；路径稳定后只校验一次，显式重试除外。
- 高频 stream delta 必须合并后提交 UI，不能一 token 一次让整个页面 render；RunStore 使用 selector 拆分。
- 长 transcript 的 mounted DOM 有界；2000 个 event 仍可输入、停止、切会话和查看最近内容。
- Conversation、Queue 与 confirmed Memory 在 refresh/reconnect/restart 后不丢失、不重复消费。
- 所有 provider/工具/SSE/数据库错误都有稳定 machine code、用户文案和高级诊断详情；默认界面不出现 Python exception 或内部术语。
- 中文和英文、键盘和屏幕阅读器、窄屏与 200% 缩放都必须进入 E2E/a11y 门禁。

## 参考设计中吸收的语义

- DeepSeek Harness：append-only session log、可恢复/分叉/重放的 session 生命周期、分层 stream chain 与插件化边界。
- Codex：连续 transcript、浅色可展开工具活动；thread 生命周期；queued messages 与 pending steers 分离。
- DSH Conversation UI：busy 输入时 Queue/Steer 是两个不同 gesture，Host queue snapshot 是权威，Steer 失败回 Queue。
- DeepSeek API：`reasoning_content` 与最终 `content` 分流，并对工具调用轮次历史提出明确约束。
- OpenAI Responses streaming：reasoning summary/text delta 作为独立事件处理，而非从普通文本猜测。

这些项目只作为公开语义与交互经验来源；本项目继续使用普通厂商客户端和自研 AgentLoop/ToolExecutor/Context/Conversation 实现，不引入 agent 框架。

## 发布决策

- task_003-task_006 是产品核心 P0/P1；task_007 以“可控、可删、可解释”的最小范围交付，不为了展示而引入云向量库。
- task_008 的真实模型 smoke 是最终 release blocker；如果外部服务不可用，应保留 Fake Model 演示但不得宣称真实 provider 已通过。
- 建议 2026-09-01 前冻结 release candidate，至少预留一天完成远端时间合规核验、README.txt、录屏、压缩包和回退。

