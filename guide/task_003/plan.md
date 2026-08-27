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

