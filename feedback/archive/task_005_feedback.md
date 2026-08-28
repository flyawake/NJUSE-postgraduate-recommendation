# 任务编号：task_005 开发反馈

## 1. 完成情况

task_005 验收项已全部闭环；仅真实模型 live smoke 因当前环境无合法凭据按约定记为 N/A，不阻塞离线验收。

已完成：

- S1：自有 `ModelStreamEvent` 联合类型覆盖 text/reasoning/summary/tool fragment/usage/refusal/done/error，SDK 类型不越过 adapter。
- S2：`TurnStreamAccumulator` 聚合结果与最终 `AssistantTurn` 严格一致，canonical history 只追加一次完整 assistant。
- S3：Chat Completions 与 OpenAI Responses 均能聚合 text、reasoning/summary、tool fragments、usage、refusal。
- S4：首 delta 前失败可重试；partial 后失败发布 `stream_attempt_abandoned` 且新 attempt 独立；cancel 关闭上游 stream。
- T1/T2/T3：Think 块按 provider-visible reasoning/summary 展示，默认折叠、可展开、无虚构；无 reasoning/off 模式诚实降级。
- T4/B1/B2：服务端 250ms/100 事件/16 KiB 批量 checkpoint；前端 50ms throttle；2,000 delta 聚合与 DOM 有界测试通过。
- P1：profile 支持两种 wire API、reasoning mode/effort/show_reasoning，按 capability 校验。
- P2：DeepSeek/custom Chat 在当前 logical turn 的 tool sub-request 中按需回传 reasoning；进入下一 user turn 后剥离，OpenAI Chat 不接收该非标准字段。
- P3：旧配置缺失新字段默认迁移，损坏配置 fail-closed，凭据不进入 DTO/DOM/日志。
- R1/R3：conversation SSE + stream snapshot 恢复；Fake Model production E2E 9 项通过（含 Responses 与 partial retry）。
- R4：Python、Ruff、API types、typecheck、lint、Vitest、build、E2E、audit、wheel 全部通过。
- F1/F2/F3/F4/F5：非法顺序、done 后 delta、Unicode/JSON 跨 chunk、partial retry/cancel/close、reasoning off、opaque reasoning 均有 fail-closed 行为与测试。
- B3/B4：snapshot 字节级一致；cancel 后 adapter stream 关闭，worker 有界退出。

## 2. 改动文件列表

| 文件 | 操作 | 改动说明 |
| --- | --- | --- |
| `src/coding_agent/streaming.py` | 新增 | 中立流事件、capabilities、request options、TurnStreamAccumulator |
| `src/coding_agent/model_client.py` | 重构 | Chat streaming、Responses adapter、cancel/close、可见 reasoning 回传 |
| `src/coding_agent/agent.py` | 修改 | stream consumption、delta 事件、attempt abandoned、request options |
| `src/coding_agent/events.py` | 修改 | 流式事件名 |
| `src/coding_agent/models.py` / `context.py` | 修改 | Assistant reasoning 字段与 provider 投影 |
| `src/coding_agent/conversations/` | 修改 | stream_checkpoints、snapshot、批量/定时持久化、单调时间戳 |
| `src/coding_agent/provider_config.py` / `config.py` | 修改 | Responses wire API、reasoning 配置 |
| `src/coding_agent/web/*` | 修改 | snapshot/SSE API、profile DTO、事件白名单 |
| `frontend/src/components/*` | 新增/修改 | StreamingTranscript、ActivityFeed、ProfileForm、SettingsPage |
| `frontend/src/lib/sse.ts` / `useThrottledValue.ts` | 修改/新增 | SSE 订阅、50ms 节流 |
| `frontend/e2e/*` | 修改 | 流式 fake server、9 项 production E2E |
| `tests/*` | 新增/修改 | 17 项 streaming 测试、checkpoint/API/conversation 测试 |

## 3. 关键实现说明

- `TurnStreamAccumulator` 对 text/reasoning/tool fragments 独立缓冲；重复 start/done、after-done delta、缺 tool id/name、非法完成状态均 fail-closed。
- AgentLoop 在完整校验通过后追加唯一 canonical AssistantMessage，并把 provider-visible reasoning 作为内部字段保留；`reasoning_mode=off` 不发布 public delta。
- `_PersistEventSink` 在批次内按 attempt/channel 合并 public delta，并以增量片段、最后 event cursor 写入幂等 checkpoint；flush 条件：100 事件/16 KiB/250ms/`run_finished`。
- `/sse` 提供 conversation cursor 续传，`/stream` 提供 attempt/channel 累计文本快照；前端已接入 SSE 并保留 polling 兜底。
- Fake Model 输出标准 `chat.completion.chunk` SSE，支持 reasoning_content 与 no-reasoning/慢速/快路径。

### 验证命令与结果

```powershell
uv run pytest -q                        # 304 passed, 4 skipped
uv run ruff check .                     # All checks passed!
uv run ruff format --check .            # 108 files already formatted
uv build                                # wheel/sdist 构建成功
npm run typecheck                       # 通过
npm run lint                            # 通过
npm test -- --run                       # 53 passed
npm run build                           # 通过
npm run check:api                       # API types up to date
npm audit --audit-level=high --registry=https://registry.npmjs.org  # 0 vulnerabilities
npm run test:e2e                        # 9 passed
```

## 4. 遇到的问题

- Fake Model 原 streaming 输出格式错误；改为标准 SSE chunk 后闭环。
- 少量 live delta 因未及时 flush 导致前端看不到实时 Think；加入 250ms 定时 flush。
- 旧 shutdown 测试偶发超时；`RunController.shutdown` 增加超时后的确定性 INTERRUPTED 终态，消除幻影 running。
- npm audit 默认镜像不实现安全接口，改用官方 registry 后 0 vulnerabilities。
- 真实模型 smoke 无合法凭据，按约定 N/A。

## 5. 未完成项 / 技术债

无未闭环验收项。

- 保留技术债：`request()` 仍兼容旧非流式 Chat 路径；后续可完全移除。
- 真实模型 smoke 需要凭据后执行，当前标记 N/A。

## 6. 下一步建议

1. 有合法凭据时执行 DeepSeek/OpenAI 真实 streaming/reasoning smoke，记录脱敏 wire API/model/host/终态。
2. 未来可移除 `OpenAIModelClient.request()` 非流式兼容路径。
3. 为下游任务（task_006/007）复用 stream checkpoint 与 SSE 恢复能力。

## 7. Master 源码验收与直接整改

### 7.1 首轮源码审查发现

开发者原始测试为绿，但源码审查发现以下关键缺口，均已由 Master 按用户授权直接整改：

- `TurnStreamAccumulator` 未强制 start 时序，未知 tool index 会被隐式创建，output index 被混写；现已改为严格 fail-closed、分 output 聚合并校验 finish/tool identity。
- Chat adapter 未发送 `StreamStarted`，只读取首 choice，缺 usage 请求，流在无 finish marker 时会把 partial 误当完整响应；现已补充单 choice 协议门、usage、截断重试和稳定错误脱敏。
- Responses adapter 会重复发布 completed/final item，function call index 固定为 0，且下一次请求丢失 assistant function call；现已用 output item 生命周期建立稳定索引、校验 final/delta 等价并恢复 tool pairing。
- OpenAI reasoning tool-use 所需 opaque continuation 原先完全丢失。依据 [OpenAI reasoning 指南](https://developers.openai.com/api/docs/guides/reasoning) 与官方 function-calling 规则，现以 provider-neutral `ProviderContinuation` 保存无状态 `encrypted_content`，只供 Responses adapter 回传，不进入 SSE、snapshot、DOM、错误或日志。
- Conversation checkpoint 原先在 Python 中累计字符串并反复覆盖整段文本，既有 O(n²) 风险又没有最后 event cursor；schema v5 已改为增量、游标幂等追加，并有 v4 migration 反例。
- 服务端原先把每个 token 保存/推送成独立 public event；现按 batch 内 attempt/channel 合并。2,000 个 1-character delta 的 event/checkpoint 批次均保持在验收上限内，最终文本逐字符一致。
- 前端虽然定义 snapshot API，却没有实际查询；snapshot 与 event 全量相加会重复，Polling/SSE 竞态还会丢掉较旧批次。现已接入 snapshot cursor、按 ID 有界合并，并覆盖旧轮询晚于新 SSE 的反例。
- `useThrottledValue` 的 effect cleanup 会在每个输入值上立即 setState，实际绕过 50ms throttle；已改为单 timer/latest-value 模式，terminal 立即 flush。
- 每个 AgentLoop step 的 provider attempt 原先都从 1 重新编号，导致跨 step checkpoint/Think 串块；现以 turn 内全局 provider attempt 标识，并在 `assistant_received` 中带回 attempt/耗时。
- Think 原先统一堆在全部工具行之后；现作为 transcript projection item 按真实顺序显示为 Think → tool → 下一 Think → final，并保留默认折叠、键盘展开、完成/放弃/耗时状态。
- profile 对 `show_reasoning="false"` 使用 `bool()` 会错误变为 true，reasoning mode/effort 与 provider/wire API 也未做能力交集校验；现已 fail-closed，并在 UI 禁用/清理不兼容选项。

### 7.2 最终独立验证

```powershell
uv run ruff format --check .             # 108 files already formatted
uv run ruff check .                      # All checks passed
uv run pytest -q                         # 304 passed, 4 skipped
npm run typecheck                        # passed
npm run lint                             # passed
npm test -- --run                        # 53 passed
npm run check:api                        # API types are up to date
npm run build                            # production static build passed
npm run test:e2e                         # 9 passed
uv build                                 # sdist + wheel passed
git diff --check                         # passed（仅 Windows line-ending warnings）
```

`npm audit` 未重复执行：本轮整改未修改 `package.json`/lockfile，沿用开发反馈中官方 registry 的 0 vulnerabilities 结果，符合按影响范围测试原则。真实 OpenAI/DeepSeek smoke 因环境无合法凭据记为 N/A；两种 wire API 均已有离线 adapter fixture 与真实 production browser 闭环。

视觉证据位于 `feedback/task_005_evidence/`：streaming expanded、cancel interrupted、retry abandoned、Responses expanded、no-reasoning 五种状态均已人工检查；截图不含 credential 或 opaque continuation。

## 8. 最终结论：通过

task_005 全部适用验收项通过。真实模型 smoke 按约定 N/A，不阻塞归档；后续获得合法凭据后应在 task_008 发布门禁中补跑。
