# 任务编号：task_005 验收标准

## Streaming contract

- [x] S1. 自有 stream event 类型覆盖 text、reasoning/summary、tool-call fragment、usage、refusal、done/error，SDK 类型不泄漏出 adapter。
- [x] S2. final `AssistantTurn` 与已接受 delta 聚合结果一致；canonical history 只追加一次完整 assistant message。
- [x] S3. Chat Completions 可按 index 聚合交错/并行 tool fragments；Responses 可聚合 function call/output item 和 reasoning summary。
- [x] S4. 首 delta 前失败可重试；partial 后失败生成 abandoned attempt 且新 attempt 独立；cancel 关闭 provider stream。

## Think 与产品展示

- [x] T1. provider 返回 `reasoning_content` 时，UI 显示实时 Think 块；OpenAI Responses 只展示 reasoning summary/明确可见内容。
- [x] T2. 不支持 reasoning、关闭 reasoning 或 provider 未返回内容时，不显示虚构推理；只显示诚实结构化进度。
- [x] T3. Think 默认折叠，可点击/键盘展开，显示 streaming/完成/中止与耗时；不抢焦点、不强制滚动。
- [x] T4. 正文与 Think 延迟批量渲染，长输出无 O(n²) 拼接、明显输入卡顿或无界 DOM。

## Provider/Profile

- [x] P1. profile 支持 `openai_chat_completions` 与 `openai_responses`，reasoning mode/effort/stream 参数按 capability 校验。
- [x] P2. DeepSeek thinking tool-use 的 reasoning content 在需要的 sub-request 中正确回传；普通 provider 不收到未知参数。
- [x] P3. 旧 config/profile 可确定性迁移，损坏配置 fail-closed，credential 读取/DOM/事件仍无 secret。

## 恢复与质量

- [x] R1. refresh、SSE reconnect/reset 和 server snapshot 可恢复完整 streaming text/Think，无重复、缺失、跨 attempt 污染。
- [x] R2. 至少 2,000 个小 delta 经 server/client coalescing 后的持久化批次数、React commit 数和 DOM 数有明确上限。
- [x] R3. Fake Model production E2E 覆盖 reasoning→text→tool→reasoning→final、cancel、partial failure/retry 与无 reasoning fallback。
- [x] R4. task_001-task_004 全套、Ruff、API types、typecheck、lint、Vitest、build、E2E、audit、wheel、diff check 全部通过。

## Live smoke

- 有合法 DeepSeek/OpenAI 凭据时按 profile capability 至少跑一次真实 streaming/reasoning/tool-use；只记录脱敏 host/model/wire API/终态。
- 无合法凭据可标记 N/A，但不能省略两种 wire adapter 的离线协议夹具与浏览器闭环。

## 协议边界与故障注入

- [x] F1. 每种 stream event 的非法顺序、重复 start/done、未知 tool index、finish 后 delta、缺 id/name 均 fail-closed 为稳定 protocol error。
- [x] F2. Unicode、JSON escape 和 tool arguments 在任意 chunk 边界聚合后逐字节/字符等价，不因 O(n²) 重建截断。
- [x] F3. 在首 chunk 前、reasoning 中、text 中、tool arguments 中和 done 后注入 timeout/cancel；attempt、canonical、partial UI 终态符合文档。
- [x] F4. reasoning mode off 时 provider request、public SSE、snapshot、DOM 和导出都不含 reasoning display text。
- [x] F5. opaque/encrypted provider reasoning 只用于允许的 continuation，不经过 `str()`、日志、DTO 或错误信息泄漏。

## 定量门槛

- [x] B1. 2,000 个瞬时 1-character fake chunks 经 coalescing 后，持久化 checkpoint 不超过 100 次、React delta commit 不超过 100 次；terminal 内容完整。
- [x] B2. coalescer 正常前台刷新频率不超过 20 次/秒；terminal/error/cancel 最后一次更新不等待普通 timer。
- [x] B3. snapshot + cursor 恢复后的 `reasoning/text` 与服务端 accumulator 完全一致，重复连接 3 次仍无重复字符。
- [x] B4. cancel 请求被 Host 接受后不再接纳新的 provider delta；adapter stream/context manager 被关闭，worker 有明确有界退出证据。

## 交付证据矩阵

| 证据 | 必须包含 |
| --- | --- |
| adapter contract | 两种 wire API 的事件映射表、capability、错误/finish mapping |
| fixture tests | text、raw visible reasoning、summary、refusal、单/并行 tool fragments、usage、异常顺序 |
| canonical audit | partial/abandoned 不入模型历史，成功 assistant 只 append 一次，tool pairing 完整 |
| 性能报告 | 原始 chunk 数、coalesced event、DB checkpoint、React commit、最终字符数 |
| UI 截图/视频 | collapsed/expanded Think、streaming text、cancel、retry abandoned、no-reasoning fallback |
