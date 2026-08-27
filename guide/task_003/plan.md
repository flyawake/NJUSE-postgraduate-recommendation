# 任务编号：task_003

## 1. 任务目标

把 task_002 的“可用 GUI”重构为真正面向最终用户的产品界面，并消除输入任务或接收运行事件时重复触发 workspace 校验的性能缺陷。

本任务交付：精简产品文案、扁平化对话/动作时间线、重新布局底部 Composer、同槽位 Start/Stop 控件、稳定组件边界、可量化的渲染与请求回归测试。暂不实现多轮会话、消息队列、Steer、模型流式输出或记忆；这些由 task_004-task_007 承接。

## 2. 背景与上下文

- 用户提供的 Codex 截图采用“正文 + 浅色单行动作摘要 + 点击展开详情”的连续文档流；当前页面为每个 step 和单工具组绘制完整圆角框，视觉噪声过高。
- 当前主页面直接展示 `provider profile`、context 字符数、step/phase/stop reason 等开发/协议词；这些信息可以保留在“运行详情/高级信息”，但不能占据默认用户路径。
- 已确认性能根因：`MainPage` 每次 render 都创建新的 `WorkspaceField.onValidated`/`onChange` 函数，`WorkspaceField` 的 validation effect 又依赖 `onValidated`。任务输入、RunStore SSE 更新或父组件状态变化会改变函数身份，导致 debounce 清理并重新校验同一路径。
- `MainPage` 同时订阅完整 RunStore，`ActivityFeed` 每个事件重新对全部事件执行 `buildFeed`；随着长会话增长，父级重渲染和 O(n) 重建会继续放大。
- 参考 Codex/DSH 的信息密度与交互原则，但不得复制品牌、图标组合、专有文案或逐像素布局。

## 3. 技术约束

- 继续使用 React 18、TypeScript、Tailwind、Radix 与现有 design tokens；不得为普通布局引入新的 UI 框架。
- workspace 校验只能由“规范化路径或 locale 真正变化”触发；回调身份、task draft、SSE、theme、profile 和 inspector 更新不得触发网络校验。
- 通过稳定 callback/ref、组件拆分、context selector 或拆分 Store Context 隔离高频事件；不得依赖无意义的大面积 `useMemo` 掩盖根因。
- 默认界面只展示用户可行动信息；诊断字段必须进入可展开的“详细信息”，错误码仅在复制详情或高级区域出现。
- Start 与 Stop 占用 Composer 右侧同一主控制槽位：idle/ready 为发送，running 为空 draft 时为 Stop，cancelling 为禁用进行态；task_006 再扩展 busy draft 的 Queue/Steer 分段动作。
- 活动流不得显示隐藏推理；task_005 上线前只展示 assistant final text、用户消息和结构化工具动作。
- 保留 zh-CN/en-US、键盘操作、焦点管理、窄屏 drawer、生产静态打包及 CLI 兼容。

## 4. 实现步骤

1. 建立文案清单，将默认页面文字分为“用户任务”“可行动状态”“高级诊断”“安全说明”；删除重复说明，把协议/安全边界移入设置或 About。
2. 重排应用壳：会话区占主视觉，workspace/profile 变为紧凑上下文栏或 Composer 附属设置；右侧 inspector 默认只显示状态、耗时、验证与变更文件，高级指标折叠。
3. 重写活动流 renderer：assistant 内容使用无外框正文块；工具调用以浅色图标、动词、目标、状态、耗时单行呈现；点击行展开参数/结果；turn 间仅用留白或细分隔线，不再为每个 step 绘制大卡片。
4. 重构 Composer：多行输入占主体，主按钮右对齐；同一槽位根据状态呈现 Start/Stop；运行前设置不与输入区争夺纵向空间；保留明确 focus ring、快捷键和 aria-live。
5. 把 workspace state/validation 抽到独立 hook/组件边界；稳定 `onValidated`，以规范化 path 作为唯一请求键，并对相同已验证 path 复用结果。
6. 拆分高频 RunStore 消费者，使 workspace/profile/composer 不订阅 events 数组；ActivityFeed 只接收增量投影或缓存后的 feed，长轨迹限制同时挂载的 DOM 数量但保留向上加载/展开能力。
7. 添加 Vitest/RTL 请求计数、render 计数和 feed 语义测试；Playwright 捕获 task typing、50 个 SSE event、theme 切换期间的 workspace validate 请求数。
8. 生成 1280×720、窄屏、深色和英文截图，人工检查用户文案、视觉层级、按钮状态与无横向滚动。

## 5. 涉及文件 / 模块

| 文件 / 模块 | 预期改动 | 说明 |
| --- | --- | --- |
| `frontend/src/App.tsx`、`pages/MainPage.tsx` | 修改 | 状态归属、稳定 callback、页面布局 |
| `frontend/src/components/WorkspaceField.tsx` | 修改/拆分 | 校验 hook、缓存与请求去重 |
| `frontend/src/components/TaskComposer.tsx` | 重构 | 右侧统一 Start/Stop 控件与产品态文案 |
| `frontend/src/components/ActivityFeed.tsx` | 重构 | 扁平连续时间线、增量/窗口化渲染 |
| `frontend/src/lib/toolgroups.ts` | 修改或替换 | 从“大组卡片”转为有序 action row 投影 |
| `frontend/src/lib/store.tsx` | 修改 | 拆分高频/低频订阅边界 |
| `frontend/src/components/RunInspector.tsx` | 修改 | 默认产品信息与高级诊断分层 |
| `frontend/src/i18n/*` | 修改 | 删除开发说明式文案，补齐产品文案 |
| `frontend/src/__tests__/*`、`frontend/e2e/*` | 修改/新增 | 网络计数、render 隔离、视觉和交互回归 |

## 6. 验收标准

- [ ] 默认主路径不展示 context 字符预算、wire API、内部 phase 名、稳定错误码或“仅列出成功 write/edit”等开发说明；这些信息只在用户主动打开的高级详情/About 中出现。
- [ ] 活动流符合连续文档流：无逐 step 大圆角框，工具动作是可点击的浅色单行摘要，展开后仍能查看脱敏参数、结果和失败恢复提示。
- [ ] Composer 主动作在右侧同一槽位切换 Start/Stop；未运行时不显示 Stop，运行时不会同时出现两个同级 Start/Cancel 按钮。
- [ ] 修改 task draft、接收至少 50 个 SSE event、切换 theme/profile 或展开工具详情均不会对未变化的 workspace 再发校验请求。
- [ ] workspace path 每次实际变化只产生一次 debounce 后请求；过期响应不能覆盖新 path，重复相同规范化 path 使用缓存。
- [ ] 高频事件不导致 WorkspaceField/ProfileSelector/Composer 无关重渲染；测试给出明确 render/request 上限。
- [ ] 1280×720、窄屏、深色、英文、键盘与屏幕阅读器状态均通过；不复制 Codex/DSH 品牌资产。
- [ ] Python/前端/API 类型/build/Playwright/task_002 回归全部通过，无新增不必要依赖。

## 7. 风险与注意事项

- UI 重构容易同时碰触 task_004/006 的会话与 Composer 状态。task_003 只定义可扩展视觉槽位，不提前伪造 Queue/Steer。
- React StrictMode 会在开发测试中重复执行 effect；请求去重必须在实际逻辑层成立，不能通过关闭 StrictMode 规避。
- 不能为了“更像 Codex”隐藏失败、验证或变更事实；应降低视觉噪声而非降低可观察性。
- 大轨迹窗口化必须保留事件顺序、键盘可达性和复制能力，不能只截断数据。

## 8. 最小交付范围与明确非目标

### 8.1 本任务必须交付

- 一套默认面向用户的 Application Shell：上下文栏、连续 transcript、Composer、按需 Inspector。
- 一套事件到产品活动行的确定性投影，不再由 React 组件直接解释任意 payload。
- workspace validation 的单一状态所有者、请求去重、过期响应保护和可测请求计数。
- 高频/低频状态订阅拆分，保证 draft 和 workspace 组件不消费 event 数组。
- idle/ready/running/cancelling/terminal 下可解释且无冲突的 Start/Stop 行为。
- 中文、英文、明暗主题、1280×720 与窄屏的生产构建截图证据。

### 8.2 本任务禁止提前实现

- 不创建假的 conversation list，不把单个 run 改名为“会话”冒充多轮能力。
- 不在前端数组实现 Queue/Steer；运行中 draft 只保留在输入框，相关语义留给 task_006。
- 不从 assistant 字数、工具名称或日志猜测 reasoning；Think 由 task_005 的协议事件驱动。
- 不引入 Zustand/Redux/虚拟列表库、第二套 design system 或新的 Markdown renderer；若现有依赖无法满足，先在 feedback 证明必要性。
- 不改变 AgentLoop、ToolExecutor、provider 配置格式、服务端 run 语义和 CLI 行为。

## 9. 目标前端架构与实现模式

### 9.1 分层结构

```text
App
├─ ProductShell（主题、语言、页面导航；低频）
│  ├─ ContextBar（workspace/profile 摘要；低频）
│  ├─ TranscriptViewport
│  │  ├─ TranscriptDocument（用户/assistant/状态）
│  │  └─ ActivityRows（高频事件投影；有界挂载）
│  ├─ RunInspector（按需、低频 snapshot）
│  └─ TaskComposer（本地 draft + 稳定 command）
├─ WorkspaceSelectionStore（路径与校验；独立）
├─ RunCommandContext（start/cancel/refetch；稳定引用）
├─ RunMetaContext（run id/state/terminal summary；低频）
└─ RunEventStore（append/reset；高频）
```

采用以下模式：

- **Container/Presenter**：页面容器负责选取状态，WorkspaceField、TaskComposer、ActionRow 只接收最小 props。
- **State colocation**：draft 留在 Composer/页面本地；workspace 放独立 store；事件留在 event store，不把所有状态提升到 App。
- **Command/Query separation**：`startRun/cancelRun` 与 `snapshot/events` 分开提供，command identity 不随 event 改变。
- **Incremental projector**：SSE event 只更新对应 feed row；只有 SSE reset 才从 retained events 全量重建一次。
- **Explicit UI state machine**：按钮、标签和 enabled 状态由枚举映射，禁止散落的多个布尔表达式。
- **Progressive disclosure**：普通 transcript 是产品信息；协议/计数/错误码进入 detail disclosure。

### 9.2 状态所有权

| 状态 | 唯一所有者 | 消费者 | 持久化 | 禁止行为 |
| --- | --- | --- | --- | --- |
| task draft | Composer/页面本地 state | Composer | 否 | 写入 RunStore、触发 workspace 校验 |
| workspace raw path | WorkspaceSelectionStore | ContextBar、Composer readiness | 当前任务沿用现有方式 | 由 SSE 更新 |
| workspace validation | WorkspaceSelectionStore | WorkspaceField、Start guard | session cache | 依赖 callback identity/locale 发请求 |
| active profile | 现有 profile query/config | ContextBar、Start guard | 服务端 config | 订阅 events |
| run metadata | React Query `run` snapshot | Status、Inspector、Composer | 服务端 | 与 2,000 events 组成一个大 Context value |
| run events | RunEventStore | Transcript activity projection | 服务端 retained tail | 让 Workspace/Composer 读取整个数组 |
| disclosure/scroll | 对应组件本地 | 对应组件 | 否 | 写入服务端或导致全页 render |

### 9.3 不新增状态库的实现框架

- 保留 TanStack Query 负责 bootstrap、snapshot 和 mutation。
- 将当前单个 `RunStoreContext` 拆为稳定 command context、低频 meta context；高频事件使用 React 自带 `useSyncExternalStore` 或等价的最小 selector store。
- Event store 对外只提供 `subscribe()`、`getSnapshot()`、`append(batch)`、`reset(batch)`；snapshot 引用仅在其内容实际变化时更新。
- ActionRow 使用稳定 `event/row id` 作为 key；不得用数组 index 或每次 render 新建随机 key。
- React Profiler/render spy 只作为测试仪器，不进入 production bundle。

## 10. Workspace validation 详细设计

### 10.1 状态机

```text
empty
  └─ 有非空输入 → debouncing
debouncing
  ├─ 输入再次变化 → debouncing（取消旧 timer/request）
  └─ 400 ms → checking
checking
  ├─ 成功有效 → valid(resolvedPath, requestKey)
  ├─ 成功无效 → invalid(errorCode, requestKey)
  ├─ 网络失败 → error(errorCode, requestKey)
  └─ 输入变化 → debouncing；旧响应丢弃
valid/invalid/error
  ├─ 同 requestKey → 复用，不发请求
  ├─ 新 requestKey → debouncing
  └─ 显式 retry → checking
```

### 10.2 请求键与并发规则

- `requestKey` 由 trim 后的用户路径生成；Windows 只规范分隔符和无意义尾分隔符，不在前端擅自解析符号链接或大小写，canonical path 以服务端 `resolved_path` 为准。
- effect 依赖只能包含 `requestKey` 和显式 retry generation；`t`、`onValidated`、draft、profile、theme、events 均不得进入请求依赖。
- hook 保存 `AbortController` 与单调 request generation；新请求取消旧请求，响应必须同时匹配 generation 与当前 requestKey 才能提交。
- 缓存记录 `{requestKey, resolvedPath, valid, errorCode}`；同一页面会话内重复值命中缓存。错误文案由 `errorCode + locale` 在 render 时翻译，切语言不重发请求。
- Start guard 使用结构化状态 `status === valid && validatedKey === currentKey`，不能只保存一个易陈旧的 boolean。
- API 仍在 start 时重新执行服务端 canonical validation，前端缓存只优化 UX，不成为安全边界。

## 11. Transcript 与活动投影详细设计

### 11.1 统一展示模型

前端新增只服务于展示的判别联合 `TranscriptItem`：

```text
user_message | assistant_message | action_row | status_notice |
verification_notice | terminal_notice | load_older
```

`action_row` 至少包含稳定 id、step、action kind、动词、主目标、状态、耗时（若可得）、detail payload reference。DTO 到 `TranscriptItem` 的映射集中在 projector，React 组件不得再次根据自由文本判断状态。

### 11.2 动作文案映射

| 内核工具/事件 | 默认摘要示例 | 展开详情 |
| --- | --- | --- |
| `glob` / `grep` | “搜索了工作区”或“搜索了 `pattern`” | 脱敏参数、命中数量/截断说明 |
| `read_file` | “读取了 `path`” | 行区间、字符数、脱敏摘要 |
| `write_file` / `edit_file` | “修改了 `path`” | 操作类型、版本检查、结果摘要 |
| `run_command` | “运行了命令” | 脱敏命令摘要、exit code、输出摘要 |
| retry | “正在重试模型请求” | attempt、稳定原因 |
| verification | “已验证更改”/“验证未通过” | 状态与恢复建议 |
| terminal | “任务已完成/已停止/未完成” | 用户可行动的下一步 |

开发字段 `step`、`char_count`、`budget`、`phase` 不成为默认独立行。它们只在高级详情中出现。

### 11.3 增量和长列表策略

- Projector 保存 `lastProjectedEventId`、有序 item 列表和 `callId → actionRowId` 索引。
- `tool_started` 创建 running row；对应 `tool_finished` 原位更新，不再对全部 group 执行 `find`/重建。
- 新 event batch 的复杂度应接近 O(batch)，SSE reset 才允许 O(retained events)。
- 数据层保留服务端允许的 2,000 events；视图初始挂载最近 300 个展示项，点击“加载更早内容”每次增加 200，保持顺序和键盘焦点。
- 自动滚动只在用户原本位于底部阈值内时发生；用户向上阅读时出现“回到最新”，delta 不抢滚动位置。
- disclosure 的展开 state 按 item id 保存；条目被窗口卸载再加载时可恢复本页面会话内状态。

## 12. Composer 与布局状态机

| 页面状态 | 前置条件 | 右侧主槽 | 输入框 | 次要行为 |
| --- | --- | --- | --- | --- |
| invalid | workspace/profile 无效 | 禁用发送 | 可编辑 | 原位显示修复入口 |
| ready | 配置有效、无 active run | Start/发送 | 可编辑 | Enter 发送、Shift+Enter 换行 |
| starting | start mutation pending | spinner +“正在开始” | 保留且暂时禁用提交 | 重复触发幂等 |
| running | active run | Stop | 可继续编辑 draft | 不发送；task_006 才定义 queue |
| cancelling | cancel 已请求 | 禁用“正在停止” | 可编辑 | 不重复发 cancel |
| terminal | run terminal | Start/发送新任务 | 可编辑 | 旧结果继续可见 |

- 主槽固定在 Composer 右下，宽度变化不能让输入文本跳动；按钮使用 icon + 可见/aria label。
- 运行中输入不得被清空或误发；页面给出克制说明“当前版本将在本轮结束后允许发送”，task_006 落地后删除。
- Workspace/profile 放入 Composer 上方紧凑 ContextBar：有效时显示名称/路径尾段；点击打开 popover/drawer 编辑，错误时才展开完整修复表单。
- 1280×720 下 transcript 保持最大阅读宽度约 760–900 px；Inspector 占侧栏而非压缩 Composer。窄屏统一为 drawer。

## 13. 实施批次、验证与回滚

### 批次 A：性能根因修复

先完成 WorkspaceSelectionStore、请求状态机与 RunStore 拆分，保持旧 UI。提交请求计数/render 证据后再进入视觉改造；这样可把性能回归与 CSS 改动分开定位。

### 批次 B：展示投影和连续 transcript

先写 projector contract tests，再替换 ToolEventGroup/ActivityFeed renderer。旧 group renderer 在新 E2E 通过前保留为一个可删除的内部 fallback，不建立长期 feature flag。

### 批次 C：Composer/Application Shell

接入 ContextBar、统一 Start/Stop 和 responsive Inspector，完成 i18n/a11y/截图。最后删除旧开发说明和废弃样式。

### 证据要求

- feedback 必须给出 Network 请求计数、render spy/Profiler 摘要、2,000-event 测试结果和 production Playwright 截图。
- 记录新增依赖；期望为 0。若非 0，必须说明 bundle、许可证、安全与现有能力为何不足。
- 回滚单位按批次提交；不得用回滚整个 task 的方式恢复 workspace 校验缺陷。
