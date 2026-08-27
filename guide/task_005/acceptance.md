# 任务编号：task_005 验收标准

## Streaming contract

- [ ] S1. 自有 stream event 类型覆盖 text、reasoning/summary、tool-call fragment、usage、refusal、done/error，SDK 类型不泄漏出 adapter。
- [ ] S2. final `AssistantTurn` 与已接受 delta 聚合结果一致；canonical history 只追加一次完整 assistant message。
- [ ] S3. Chat Completions 可按 index 聚合交错/并行 tool fragments；Responses 可聚合 function call/output item 和 reasoning summary。
- [ ] S4. 首 delta 前失败可重试；partial 后失败生成 abandoned attempt 且新 attempt 独立；cancel 关闭 provider stream。

## Think 与产品展示

- [ ] T1. provider 返回 `reasoning_content` 时，UI 显示实时 Think 块；OpenAI Responses 只展示 reasoning summary/明确可见内容。
- [ ] T2. 不支持 reasoning、关闭 reasoning 或 provider 未返回内容时，不显示虚构推理；只显示诚实结构化进度。
- [ ] T3. Think 默认折叠，可点击/键盘展开，显示 streaming/完成/中止与耗时；不抢焦点、不强制滚动。
- [ ] T4. 正文与 Think 延迟批量渲染，长输出无 O(n²) 拼接、明显输入卡顿或无界 DOM。

## Provider/Profile

- [ ] P1. profile 支持 `openai_chat_completions` 与 `openai_responses`，reasoning mode/effort/stream 参数按 capability 校验。
- [ ] P2. DeepSeek thinking tool-use 的 reasoning content 在需要的 sub-request 中正确回传；普通 provider 不收到未知参数。
- [ ] P3. 旧 config/profile 可确定性迁移，损坏配置 fail-closed，credential 读取/DOM/事件仍无 secret。

## 恢复与质量

- [ ] R1. refresh、SSE reconnect/reset 和 server snapshot 可恢复完整 streaming text/Think，无重复、缺失、跨 attempt 污染。
- [ ] R2. 至少 2,000 个小 delta 经 server/client coalescing 后的持久化批次数、React commit 数和 DOM 数有明确上限。
- [ ] R3. Fake Model production E2E 覆盖 reasoning→text→tool→reasoning→final、cancel、partial failure/retry 与无 reasoning fallback。
- [ ] R4. task_001-task_004 全套、Ruff、API types、typecheck、lint、Vitest、build、E2E、audit、wheel、diff check 全部通过。

## Live smoke

- 有合法 DeepSeek/OpenAI 凭据时按 profile capability 至少跑一次真实 streaming/reasoning/tool-use；只记录脱敏 host/model/wire API/终态。
- 无合法凭据可标记 N/A，但不能省略两种 wire adapter 的离线协议夹具与浏览器闭环。

