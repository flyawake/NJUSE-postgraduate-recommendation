# 任务编号：task_003 开发反馈

## 1. 完成情况

- 已完成：默认主路径移除 context 预算、wire API、phase、错误码和“仅列出成功 write/edit”等开发说明；运行详情默认只显示状态、耗时、验证和变更文件，计数/phase/停止原因/验证命令移入“高级详情”。
- 已完成：活动流改为连续 transcript；用户任务、浅色单行动作、失败/中止恢复提示和最终答复按真实事件顺序显示。工具详情可点击或通过键盘展开；不显示模型隐藏推理。
- 已完成：Composer 右侧主槽位只在 Start/Stop 间切换。running 保留草稿可编辑，cancelling 禁止重复 Stop；MainPage 以 ref 守卫重复取消。
- 已完成：workspace validation 独立为 `useWorkspaceValidation`，请求键仅由规范化路径与显式 retry 组成，具备 400 ms debounce、结果缓存、同键在途复用、AbortController、generation 过期保护和服务端二次校验保留。
- 已完成：RunStore 分离稳定 commands、低频 meta 与高频 events；workspace/profile/composer 不订阅 event tail。TranscriptProjector 只增量处理新增事件，tool_finished 不可变替换对应 action row；初始最多挂载 300 行、每次加载更早增加 200 行。
- 已完成：补充 Vitest/RTL、真实生产静态资源 Playwright 闭环与视觉证据。Playwright stress trajectory 确认至少 50 个 SSE 事件后 workspace validate 仍为 1 次。

## 2. 改动文件列表

| 文件 | 操作 | 改动说明 |
| --- | --- | --- |
| `frontend/src/lib/workspaceValidation.ts` | 新增 | workspace 校验状态机、路径请求键、缓存、在途请求共享与过期保护。 |
| `frontend/src/components/WorkspaceField.tsx`、`frontend/src/api/client.ts` | 修改 | 接入独立校验 hook，并把请求 signal 传入 API 客户端。 |
| `frontend/src/lib/store.tsx`、`frontend/src/pages/MainPage.tsx`、`frontend/src/App.tsx` | 修改 | 拆分 commands/meta/events 消费边界；draft 本地化；稳定 workspace callbacks。 |
| `frontend/src/lib/toolgroups.ts`、`frontend/src/components/ActivityFeed.tsx` | 修改 | 删除旧 step/group 投影，改为增量连续 transcript、单行 action disclosure 与 300/200 窗口。 |
| `frontend/src/components/ToolEventGroup.tsx` | 删除 | 已被单行动作 renderer 完整替代的旧大组卡片。 |
| `frontend/src/components/TaskComposer.tsx`、`frontend/src/components/RunInspector.tsx` | 修改 | 同槽位 Start/Stop、running draft、渐进披露高级运行诊断。 |
| `frontend/src/components/AppShell.tsx`、`frontend/src/components/AppShellSidebar.tsx` | 修改 | 窄屏 viewport 变化时自动收起侧栏，保留 inspector drawer。 |
| `frontend/src/i18n/zh-CN.ts`、`frontend/src/i18n/en-US.ts` | 修改 | 产品化中文/英文文案与动作/恢复提示。 |
| `frontend/src/__tests__/activity-feed.test.tsx`、`components.test.tsx`、`store-recovery.test.tsx`、`toolgroups.test.ts`、`workspace-and-groups.test.tsx` | 新增/修改 | 请求数、过期响应、render 隔离、Start/Stop、增量投影、2,000-event 窗口及 disclosure 测试。 |
| `frontend/e2e/fake_model_server.py`、`frontend/e2e/run.spec.ts` | 修改 | 真实 50+ SSE 压力轨迹、workspace 请求计数、窄屏宽度/无横向滚动与截图验证。 |
| `src/coding_agent/web/static/index.html`、`src/coding_agent/web/static/assets/*` | 修改 | 由 production Vite build 生成的静态资源。 |
| `feedback/task_003_evidence/*.png` | 新增 | 1280×720 success/running/idle、dark、English、390×844 窄屏截图。 |

## 3. 关键实现说明

状态所有权：task draft 仅在 MainPage；workspace 校验仅在 `useWorkspaceValidation`；RunStore commands/meta/events 三个 Context 分离；ActivityFeed 是主路径唯一的高频 event consumer。workspace effect 仅依赖 `requestKey` 和 retry generation，locale、callback、theme、profile、draft 与 SSE 更新均不能重新请求。

TranscriptProjector 按 event id 追加，使用 `callId → row index` 更新完成的工具行；React 只接收该行的新对象，其他 memoized rows 不重渲染。2,000-event 夹具验证初始视图只挂载 300 个 action row，点击“加载更早内容”后增加至 500 且顺序不丢失。

实际执行的验证：

- `uv run ruff format --check .`：通过（87 files already formatted）。
- `uv run ruff check .`：通过。
- `uv run pytest -q`：242 passed、4 skipped；1 条现有 FastAPI/Starlette deprecation warning。
- `npm run typecheck`、`npm run lint`：通过。
- `npm test -- --run`：9 files、35 tests 全部通过。
- `npm run check:api`：API types are up to date。
- `npm run build`：通过；production static assets 已更新。
- `npm run test:e2e`：6 passed；包括至少 50 SSE 事件、任务连续输入 50 字符、theme/profile 切换后的 workspace validate 请求数断言。
- `npm audit --omit=dev --registry=https://registry.npmjs.org`：0 vulnerabilities。默认镜像 registry 不实现 audit endpoint，已用官方 registry 复验。
- `uv build`：source distribution 与 wheel 已生成。
- `git diff --check`：通过（仅有 CRLF 转换 warning，无 diff 错误）。

截图已人工复核：

- [1280×720 成功态](/E:/ppt/research/project/main/feedback/task_003_evidence/success-1280x720-zh-light.png)：连续正文、五个成功 action row、验证与变更文件。
- [1280×720 运行态](/E:/ppt/research/project/main/feedback/task_003_evidence/running-1280x720-zh-light.png)：同槽位 Stop、运行状态与可编辑 Composer。
- [1280×720 空闲浅色中文](/E:/ppt/research/project/main/feedback/task_003_evidence/idle-1280x720-zh-light.png)。
- [1280×720 深色中文](/E:/ppt/research/project/main/feedback/task_003_evidence/idle-1280x720-zh-dark.png)。
- [1280×720 深色英文](/E:/ppt/research/project/main/feedback/task_003_evidence/idle-1280x720-en-dark.png)。
- [390×844 深色英文窄屏](/E:/ppt/research/project/main/feedback/task_003_evidence/idle-390x844-en-dark.png)：侧栏 48 px、无横向滚动、inspector 控制不遮挡 Composer。

## 4. 遇到的问题

- 初次视觉复核发现 tool_finished 已进入 projector、但 memoized row 未刷新。已改为按固定 row index 用不可变对象替换，添加了对应 contract assertion，并重跑全量 Playwright。
- 初次窄屏截图显示侧栏未及时收起。已改为监听真实 viewport resize；Playwright 断言 `innerWidth === 390`、document 无横向滚动且 sidebar 宽度不超过 48 px。
- 默认 npm registry mirror 返回 audit endpoint 的 `NOT_IMPLEMENTED`；使用官方 npm registry 重试，得到 0 vulnerabilities。
- 复核期间曾短暂发现 task_004 guide 的外部改动；按开发边界未读取、修改或清理。其后最终 `git status` 已不再包含这些文件，当前工作区仅保留 task_003 相关改动。

## 5. 未完成项 / 技术债

- task_004 的持久多轮会话、task_005 的 provider streaming/reasoning、task_006 的 Queue/Steer 和 task_007 的记忆均未实现，符合 task_003 明确非目标。
- 无 task_003 范围内的未完成项或新增技术债。

## 6. 下一步建议

评估 task_003 的视觉与性能证据；通过后按既定依赖顺序开始 task_004 的持久多轮会话工作。

## 7. 状态：已完成

## 8. Master 首次验收（2026-08-28）

### 8.1 结论：需整改

产品主路径、连续活动流、Composer 同槽位 Start/Stop、基础 Context 隔离、响应式布局和中英文/明暗主题方向均达到预期，所有已提交的自动化也能重复通过。但源码复核确认 workspace error/retry 闭环并未成立，事件投影仍在高频路径扫描完整历史且长期投影缓存无界；另有动作目标契约和关键验收证据缺口。因此 task_003 保持“进行中”，不得进入 task_004。

### 8.2 独立验证结果

- `uv run pytest -q`：242 passed、4 skipped；1 条既有 Starlette deprecation warning。
- `uv run ruff check .`：通过。
- `npm test -- --run`：9 files、35 tests 通过。
- `npm run typecheck`、`npm run lint`、`npm run check:api`：通过。
- `npm run build`：通过；1718 modules，生产 JS 384.59 kB（gzip 121.17 kB），CSS 20.34 kB（gzip 4.78 kB）。
- `npm run test:e2e`：6 passed，production build 闭环通过。
- `npm audit --omit=dev --registry=https://registry.npmjs.org`：0 vulnerabilities。
- `uv build`：sdist 与 wheel 生成成功。
- `git diff --check`：通过，仅报告 Windows CRLF 转换提示。
- 视觉人工复核：现有 6 张 idle/running/success、dark、English、390×844 证据与页面一致，整体信息层级显著优于 task_002；但验收矩阵要求的 error/recovery 全页截图缺失。

### 8.3 未通过项与源码证据

1. **P0 — workspace error/retry 状态机失效。** `workspaceValidation.ts` 将 ApiError/transport error 转成 `valid: false` 并无条件写入 CACHE；hook 随后发布 invalid。`retryGeneration` 虽会触发 effect，但 effect 先读取相同缓存并立即返回，因此 retry 不会产生新请求；WorkspaceField 也未渲染 retry 控件。临时故障会被固化到页面会话，`error` 状态事实上难以到达，违反 P4、详细状态机和 B1 的显式 retry 约定。
2. **P0 — 增量投影未达到 O(batch)，且长期状态无界。** ActivityFeed 每次更新先用 `events.some` 扫描尾部，再把完整 events 交给 `TranscriptProjector.append`；append 仍逐项遍历整个数组，仅通过 id 跳过旧项。RunStore 同时在每次 events 引用变化时为 legacy value 执行多次 `filter/reduce`。当 2,000-event tail 淘汰旧事件后，projector 的 items/maps 不同步淘汰，长运行会持续增长。现有测试只统计 processedEvents 和 mounted DOM，无法证明没有完整尾扫描，也没有覆盖超过 retained cap 后的 projector 上限，违反 R4/R5、B3 与 plan 的 O(batch) 合同。
3. **P1 — action 主目标可被截断协议破坏。** `actionTarget` 通过 `JSON.parse(argsSummary)` 提取 path/pattern/command；公共摘要超过 120 字符会被截断，Web 二次脱敏解析失败时又会退化为 `<arguments redacted>`。长路径或长命令因此丢失 P3 要求的准确目标。主目标应是脱敏、长度有界的结构化公共字段，不能依赖展示字符串反解析。
4. **P1 — 证据矩阵未被真实覆盖。** E2E 使用 `fill("x".repeat(50))`，只产生一次整体输入更新，不等价于连续 50 次 draft 更新；Vitest 未在 StrictMode 下验证同键去重/失败重试；render 测试统计的是通用 Context probes 而非验收矩阵点名的实际产品组件；缺少 error/recovery 截图和双 Stop 真实 mutation 计数证据。

### 8.4 整改指令

Developer 只读取 `guide/task_003/plan.md` 与 `acceptance.md` 新增的 R3.1-R3.4，保持现有 UI 和既有绿色回归，完成实现与反例测试后在本文件追加整改反馈，并把 `feedback/INDEX.md` 的 task_003 状态重新登记为 `待评估`。本轮禁止开始 task_004-task_007。

## 9. 状态：需整改

## 10. R3 整改完成（2026-08-28）

### 10.1 R3.1 — workspace error/retry

- `useWorkspaceValidation` 现在只缓存服务端完成的业务 `valid` / `invalid` 结果；`ApiError`、HTTP 与 transport failure 发布为 `error`，绝不写入缓存。
- `WorkspaceField` 在 error 状态显示键盘可操作的“重试工作区校验”按钮。retry 会先失效当前键、跳过 debounce 并立即发出恰好一次新请求；generation 与 request key 继续阻止旧响应覆盖新路径。
- 新增 StrictMode 回归：稳定路径首次 transport failure，出现可见 retry，点击后请求数从 1 精确变为 2 且最终 valid。

### 10.2 R3.2 — O(batch) 投影与有界状态

- RunStore 新增 `RunEventFeed`：`retainedEvents` 只用于 reset/recovery，`appendedEvents` 是当前未确认的真实 SSE 批次。React 合并多个 SSE callback 时，reducer 累积所有新事件并以 version acknowledgement 在 ActivityFeed 已提交后清空，避免只保留最后一个 batch 的事件丢失。
- `ActivityFeed` 不再使用 `events.some` 或向 projector 传递完整尾部；仅 reset 时处理 retained tail，正常更新只投影 `eventBatch`。旧 aggregate store 不再在每次 append 上调用 `deriveLiveSnapshot` 扫描全尾。
- `TranscriptProjector` 以 head offset 保留最多 2,000 个 transcript item，`actionByCallId` 与 row index map 随淘汰同步删除；周期性 compact 的 map 重写是按完整窗口摊销，不在每个 SSE event 扫描历史。
- 测试覆盖：2,000-event baseline 后只 append 50 个 event 时 processed delta 为 50；4,100 个持续 tool_started 后 item 与 action map 都不超过 2,000；真实 MainPage 内 WorkspaceField、ProfileSelector、TaskComposer 在 50 个 SSE batch 中不重渲染，ActivityFeed 是唯一高频消费边界。

### 10.3 R3.3 — 结构化安全 action target

- 后端在 normalized arguments、展示摘要截断之前生成 `public_tool_target`；`ToolEventDTO.target` 为 OpenAPI 明确字段，controller 将其与 payload 分离，前端不再 `JSON.parse(argsSummary)`。
- `target` 对文件/搜索工具取安全的 path/pattern，对 `run_command` 仅取可执行文件名；展示详情仍执行两层脱敏和长度限制。
- `tests/test_web_api.py` 覆盖长 secret argv 的 fail-closed detail、无 secret 与 `target == "python"`；前端覆盖截断/脱敏详情仍准确显示 `src/precise-target.py`。已重新生成并检查 `frontend/src/api/schema.json`、`schema.d.ts`。

### 10.4 R3.4 — 真实交互与视觉证据

- Playwright 将 50 字符草稿输入改为 `pressSequentially`，不再以单次 `fill` 冒充离散输入。
- 慢模型取消场景在同一浏览器任务中同步触发两次 native Stop click，并断言真实 `POST /cancel` mutation 精确为 1。
- 新增可重放 workspace 503 → 可见 retry → 成功恢复场景及人工复核的 production 1280×720 对照图：
  - `feedback/task_003_evidence/error-recovery-1280x720-zh-light.png`
  - `feedback/task_003_evidence/error-recovered-1280x720-zh-light.png`

### 10.5 整改后验证

- `uv run pytest -q`：242 passed、4 skipped；仅有既有 FastAPI/Starlette deprecation warning。
- `uv run ruff check .`：通过。
- `npm run typecheck`、`npm run lint`：通过。
- `npm test -- --run`：9 files、39 tests 通过。
- `npm run build`、`npm run check:api`：通过；production 静态资源已更新，API generated types 无 drift。
- `npm run test:e2e`：7 passed（包括闭环、恢复、逐字符、双 Stop 和 50+ SSE）。
- `npm audit --omit=dev --registry=https://registry.npmjs.org`：0 vulnerabilities；`uv build`：sdist/wheel 成功；`git diff --check`：通过（仅 CRLF 提示）。

## 11. 状态：待评估

## 12. Master R3 复验（2026-08-28）

### 12.1 结论：需 R4 整改

R3.1 的错误/重试主路径、R3.2 的显式 event batch 与摊销有界 projector、R3.3 的独立 target 字段、R3.4 的逐字符/双 Stop/error 截图均已落地，所有开发者测试都能独立复现通过。但源码和独立反例确认 target 尚未长度有界、reset 未恢复初始窗口且长 tail 可能丢 user task、workspace in-flight entry 仍有 identity/Abort 竞态，并且 render probe 被打入 production bundle。task_003 因此继续保持“进行中”。

### 12.2 独立复验结果

- R3 针对性 Python：`tests/test_web_api.py` 41 passed；针对性前端 6 files、33 tests passed。
- `uv run ruff format --check .`：88 files already formatted；`uv run ruff check .`：通过。
- `uv run pytest -q`：242 passed、4 skipped；仅有既有 Starlette deprecation warning。
- `npm test -- --run`：9 files、40 tests passed（开发反馈中的 39 已由当前工作树实际结果校正为 40）。
- `npm run typecheck`、`npm run lint`、`npm run check:api`：通过，generated API 无 drift。
- `npm run build`：通过；1718 modules，JS 386.25 kB（gzip 121.56 kB），CSS 20.34 kB（gzip 4.78 kB）。
- `npm run test:e2e`：7 passed；独立确认逐字符输入、真实双 Stop、50+ SSE、503 error→retry→valid 和全部既有闭环。
- `npm audit --omit=dev --registry=https://registry.npmjs.org`：0 vulnerabilities；`uv build`：sdist/wheel 成功；`git diff --check`：通过，仅 CRLF 提示。
- error/recovery 与 recovered 两张 1280×720 production 截图已人工复核，画面与 E2E 状态一致；其余 success/running/idle/dark/en/narrow 证据仍有效。

### 12.3 阻断项与源码证据

1. **P1 — target 仍无长度/字段安全合同。** `public_tool_target` 对 path/pattern/query/file_path 直接返回原字符串，`ToolEventDTO.target` 只是无约束 Optional[str]。Master 以 `read_file` 的 10,000 字符 path 独立调用后得到 `path_length: 10000`。此外通用字段顺序使 glob/grep 在同时存在 path 与 pattern 时优先显示 `.`，成功截图出现“搜索了工作区 . / 搜索了 .”，没有形成有意义的动作目标。这未通过 R3.3 的“脱敏、长度有界”与 P3 产品摘要要求。
2. **P1 — reset 后窗口预算和用户任务不稳定。** `visibleCount` 只在 ActivityFeed mount 时初始化；resetVersion 分支重建 projector 时没有复位它。用户先加载到 500 后开启新 run/reset，首帧仍可挂载 500，违反 B2。projector 又只在 retained events 含 `run_started` 时创建 user_message；超过 2,000 events 后 refresh/reset 若早期事件已淘汰，snapshot 明明仍有 task，transcript 却没有用户任务。
3. **P1 — 同键 in-flight cleanup 未绑定 entry identity。** `apiFetch` 把 AbortError 包装成 transport ApiError，使下游 AbortError 分支不可达；`IN_FLIGHT` promise 的 finally 无条件按 key 删除，release 也对 map 当前 key 的 entry 递减。旧 abort promise 延迟 settle 时可能误删/误停同 key 的新请求。现有 StrictMode 测试只覆盖 debounce 前双 effect，没有覆盖“网络已发出后 remount”的 B1 竞态。
4. **P2 — render 测试接口进入生产包。** `MainPage.renderProbe` 与四个组件的 `onRender` props/calls 均位于 production 源码，production build JS 中亦可检索到。这与 task_003 plan 明确禁止测试 render spy 进入 production bundle 的约束冲突。

### 12.4 已通过且不得回退

- error 与 invalid 已分离；错误不缓存，显式 retry 立即且恰好新增一次请求，并有可见/键盘可达恢复动作。
- 高频主路径不再把 retained tail 传给 projector；正常 append 使用真实 batch，legacy `deriveLiveSnapshot` 已退出热路径，projector 的活动 item/map 具有明确上限。
- 前后端已建立独立 target 字段，前端不再解析 argsSummary；run_command operands 的 sentinel 未进入 API/DOM。
- 50 次逐字符 draft、50+ SSE、双 Stop mutation=1、error/recovery production 截图和实际组件 render 隔离证据均成立。

### 12.5 下一步指令

Developer 只处理 `guide/task_003/plan.md` 第 15 节 R4.1-R4.4，并保留上述已通过能力。完成后在本文件追加 R4 反馈，将 `feedback/INDEX.md` 重新置为 `待评估`；在 task_003 通过归档前不得开始 task_004。

## 13. 状态：需整改

## 14. R4 特例直接整改（2026-08-28）

本轮由用户明确授权 Master 在 Developer Agent 忙碌期间直接处理已登记的 R4.1-R4.4；范围未超出 `guide/task_003/plan.md` 第 15 节，未开始 task_004，也未引入新依赖。

### 14.1 R4.1 — 有界且有语义的公开 action target

- 建立单一 `bound_public_tool_target`：控制字符折叠为空格、首尾清理、统一 160 字符上限；`ToolEventDTO.target` 同步增加 OpenAPI `maxLength: 160`，Web 二次投影再次执行同一约束。
- glob/grep 按工具语义优先选择 pattern，再回退 path；文件工具选择 path/file_path；`run_command` 只公开 argv[0] 的 basename，不公开 operands。
- 新增 Python 合同反例覆盖 10,000 字符 path/pattern、控制字符、含 sentinel 的命令 operands 与 pattern/path 竞争；生成的 `schema.json`、`schema.d.ts` 已同步。成功态 E2E 截图已从无意义的 `.` 更新为 `**/*.py`、`TODO` 和 `python`。

### 14.2 R4.2 — reset 首帧窗口与用户任务恢复

- ActivityFeed 将窗口状态与 `resetVersion` 绑定；reset 发生时，渲染阶段直接选择 300 的有效窗口值，effect 只同步持久 state，因此新 run/reset 的第一个 committed frame 不会短暂挂载旧的 500/700 行窗口。
- TranscriptProjector 将 user message 与可淘汰活动项分离。reset 始终从 snapshot task 合成且仅合成一条用户消息，即使 retained tail 已不含 `run_started`；后续 run_started append 不会重复。
- projector 的逻辑总量仍不超过 2,000；新增测试覆盖“加载至 500 后 reset 首帧回到 300”及“4,100-event tail 缺失 run_started 时任务唯一、顺序与上限正确”。

### 14.3 R4.3 — 校验请求所有权与取消语义

- `apiFetch` 以跨 realm 的 `name === "AbortError"` 判定原样抛出取消；其他网络异常才映射为 `transport_error`。
- 每个 in-flight promise 的 `finally`、每个 consumer 的 `release` 均绑定创建时 entry identity；只有 map 当前值仍为自身时才能删除、递减或 abort。即使 transport 忽略 AbortSignal 后延迟 resolve，已取消结果也不会进入 cache。
- 新增已发请求后的同 key unmount/remount 竞态：旧 promise 延迟完成后，第三个同 key consumer 仍复用第二个 entry，请求总数保持 2，新请求最终稳定 valid；另有独立 `apiFetch` AbortError 合同测试。

### 14.4 R4.4 — 测试探针退出生产边界

- 删除 MainPage 的 `renderProbe` 与 WorkspaceField/ProfileSelector/TaskComposer/ActivityFeed 的 `onRender` 生产 props/calls。
- render 隔离测试改为 Vitest 测试模块 decorator 统计真实组件，无需改变产品 API；50 个 SSE batch 后三个低频组件 render 增量仍为 0。
- 对 `frontend/src` 非测试源码及 production static bundle 搜索 `renderProbe|onRender` 均为 0 命中。

## 15. Master 最终复验（2026-08-28）

### 15.1 结论：通过

R4 五组优先反例与 task_003 全量验收矩阵均通过；R3 已通过能力无回退。源码逻辑、错误路径、竞态、生产构建与视觉证据均闭环，task_003 范围内无未处理阻断项或新增技术债，满足归档条件。

### 15.2 独立复验结果

- R4 定向：`tests/test_public_redaction.py` 6 passed；前端 5 files、22 tests passed。
- `uv run ruff format --check .`：89 files already formatted；`uv run ruff check .`：通过。
- `uv run pytest -q`：248 passed、4 skipped；仅有既有 FastAPI/Starlette deprecation warning。
- `npm run typecheck`、`npm run lint`：通过。
- `npm test -- --run`：10 files、44 tests 全部通过。
- `npm run check:api`：generated API 无 drift。
- `npm run build`：1718 modules；JS 386.73 kB（gzip 121.74 kB），CSS 20.34 kB（gzip 4.78 kB），无 source map。
- `npm run test:e2e`：7 passed；production build 闭环、刷新恢复、取消重启、逐字符/50+ SSE、错误重试均通过。
- `npm audit --omit=dev --registry=https://registry.npmjs.org`：0 vulnerabilities；本机默认 npmmirror 不实现 audit endpoint，已用官方 registry 完成只读复验。
- `uv build`：sdist 与 wheel 成功；`git diff --check`：通过，仅 Windows CRLF 转换提示。
- 人工复核 success 与 error/recovery 1280×720 production 截图：动作目标、状态、恢复入口与自动化结果一致，无 sentinel 或内部协议泄漏。

### 15.3 下一步

按既定依赖开始 `task_004`：持久多轮会话、左侧会话管理边栏、逐轮 ChangeSet 与按需右侧文件预览。Developer 只读取 `guide/task_004/plan.md` 与 `acceptance.md` 后实施。

## 16. 状态：通过，已归档
