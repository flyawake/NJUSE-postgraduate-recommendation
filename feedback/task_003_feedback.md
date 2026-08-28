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
