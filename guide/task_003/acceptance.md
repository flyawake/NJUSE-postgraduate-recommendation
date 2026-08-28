# 任务编号：task_003 验收标准

## 产品界面与文案

- [x] P1. 默认主页面只出现最终用户需要的任务、状态、动作、验证、变更与恢复信息；协议名、内部计数、context 预算和错误码位于高级详情/About。
- [x] P2. 用户消息与 assistant 正文形成连续文档流；step 不再拥有独立大卡片或重复标题。
- [x] P3. glob/grep/read/edit/run 等动作使用浅色单行摘要，包含准确动词、目标、状态与可用耗时；点击/键盘可展开脱敏详情。
- [x] P4. 失败、取消、断线、验证未通过仍有明确文字和恢复动作，不只靠颜色或 toast。
- [x] P5. zh-CN/en-US 文案完整且自然；没有复制 Codex、DSH 或其他产品的品牌资源与专有文案。

## Composer 与布局

- [x] C1. Composer 输入区为视觉主体，主按钮位于右侧；workspace/profile 使用紧凑上下文栏，不持续占用两列大表单空间。
- [x] C2. 同一主控制槽位在 idle/ready/running/cancelling 间切换 Start/Stop 状态；Stop 仅在运行后出现，重复点击和快捷键行为幂等。
- [x] C3. 1280×720 无横向滚动，窄屏 inspector/drawer 不遮挡 Composer；system/light/dark 均保持对比度与 focus indicator。

## 性能与组件边界

- [x] R1. task draft 连续输入 50 个字符时，`POST /api/workspace/validate` 新增请求数为 0。
- [x] R2. 同一路径有效后注入至少 50 个 SSE event、展开动作、切换 theme/profile 时，workspace validate 新增请求数为 0。
- [x] R3. workspace 真实改变一次只产生一次 debounce 后校验；旧响应不能覆盖新值，相同规范化 path 可复用缓存。
- [x] R4. 自动化 render 计数证明 WorkspaceField、ProfileSelector 与 Composer 不订阅无关 event payload；ActivityFeed 不在每个 delta 上重建全部历史 DOM。
- [x] R5. 长轨迹至少 2,000 个事件时交互仍可用，同时挂载 DOM 有明确上限，向上读取历史不丢顺序。

## 质量门禁

- [x] Q1. Vitest/RTL 覆盖请求计数、render 隔离、Start/Stop、action disclosure 和 i18n；Playwright 从 production build 验证真实浏览器请求数与布局。
- [x] Q2. `uv run pytest -q`、Ruff、API 类型检查、typecheck、lint、Vitest、build、Playwright、audit、wheel 与 `git diff --check` 全部通过。
- [x] Q3. feedback 提供 1280×720 idle/running/success、窄屏、深色和英文脱敏截图，并逐张说明与画面一致。

## 人工验收

1. 打开 Network 面板，验证输入任务和运行事件不会重复请求 workspace validate。
2. 对照用户提供截图检查连续正文、浅色动作行和展开详情，不接受逐 step 大卡片换皮。
3. 只用键盘完成 workspace 选择、任务输入、Start、Stop、展开动作和打开高级详情。

## 实现证据矩阵

| 证据 | 必须包含 | 不接受 |
| --- | --- | --- |
| workspace 请求测试 | 初次有效 1 次；50 字 draft 0 次；50 SSE 0 次；locale/theme/profile 0 次；真实路径变化 1 次 | 只 mock hook、关闭 StrictMode |
| render 隔离测试 | Event append 前后 WorkspaceField/ProfileSelector/Composer 的 render 增量；Activity projector 处理条数 | 只说“用了 memo” |
| 长列表测试 | 2,000 events 的 mounted row/DOM 上限、加载更早、顺序、底部跟随 | 直接截断并丢弃历史 |
| 视觉证据 | 1280×720 idle/running/error/success、窄屏、dark、en-US | 只给局部组件截图 |
| 源码说明 | 状态所有权、effect 依赖、event projector、Start/Stop transition | 只报告测试通过 |

## 定量门槛

- [x] B1. 初始可验证路径稳定后，除显式 retry 外，同一 `requestKey` 在组件 remount/StrictMode 下最多产生一个在途或一次有效网络请求。
- [x] B2. 2,000-event fixture 初始只挂载不超过 350 个 transcript item；每次“加载更早”增加不超过 250 个且顺序严格递增。
- [x] B3. 一次包含 50 个新事件的 batch 不得触发 50 次全 feed 重建；projector contract 证明只处理新增 batch，tool finish 原位更新对应 row。
- [x] B4. running→cancelling 的重复 Stop 点击只产生一次 cancel mutation；terminal/idle 不渲染可点击 Stop。

## Master 首验整改验收（2026-08-28）

- [x] R3.1a. transport/server failure 显示为 error 而非 invalid，错误结果不形成无法绕过的 session cache；WorkspaceField 提供可见、键盘可达的 retry。
- [x] R3.1b. React StrictMode 测试证明同一稳定 requestKey 首次最多一次有效网络请求；首次失败后显式 retry 准确新增一次请求并可恢复为 valid。
- [x] R3.2a. 2,000-event baseline 追加一个 50-event batch 时，ActivityFeed/projector 只迭代和处理新增 50 项；非 reset 路径不扫描完整 retained tail。
- [x] R3.2b. 连续接收超过 2,000 events 后，RunStore tail、projector items 与 callId 索引均有明确上限，且保留 300/200 的挂载与加载顺序合同。
- [x] R3.2c. 高频 append 路径不再为 legacy RunStore value 对全事件尾执行 `filter/reduce`；实际 WorkspaceField/ProfileSelector/TaskComposer render 增量有自动化断言。
- [x] R3.3. 超过 120 字符的已脱敏工具参数仍能通过结构化公共字段显示准确主目标；不从可截断 argsSummary 反解析，且敏感值不泄漏。
- [x] R3.4a. Playwright 使用逐字符方式产生 50 次 task draft 更新，workspace validate 新增请求数为 0；50+ SSE、theme、locale、profile 后仍为 0。
- [x] R3.4b. running 状态重复 Stop 的真实 cancel mutation 计数为 1；error/recovery 具有 1280×720 production build 截图和可重复自动化场景。

## Master R3 复验整改验收（2026-08-28）

- [x] R4.1a. public `ToolEventDTO.target` 对所有工具执行字段级脱敏、控制字符处理与固定长度上限；10,000 字符 path/pattern/query 不得原样进入 DTO，secret operands 不得进入 target。
- [x] R4.1b. glob/grep 的产品摘要按工具语义选择有信息量的 target，不因 `path="."` 固定覆盖 pattern；前端仍不解析可截断 argsSummary。
- [x] R4.2a. ActivityFeed 已加载到 500/700 后发生 reset/new run，首个提交重新只挂载 300 个 transcript item；后续每次加载准确增加不超过 200。
- [x] R4.2b. retained tail 不含 `run_started` 时，snapshot task 仍恢复为且仅恢复为一条 user message，后续 SSE append 不重复，事件顺序与 2,000-item 上限保持正确。
- [x] R4.3a. fetch AbortError 原样传播；旧 in-flight entry 的 finally/release 只能操作自身，不能删除、递减或 abort 同 key 的后继 entry。
- [x] R4.3b. 已发请求后的同 key unmount/remount 延迟竞态测试通过，新请求稳定进入 valid，旧 promise 延迟 settle 不改变新状态或去重表。
- [x] R4.4. production 组件 API、源码与构建 bundle 不包含 `renderProbe`/`onRender` 测试回调；测试侧仪器仍证明三个实际低频组件在 50 SSE batch 下 render 增量为 0。
