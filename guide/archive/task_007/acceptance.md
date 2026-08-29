# 任务编号：task_007 验收标准

## 作用域与召回

- [x] M1. 用户在会话 A 明确保存 workspace 事实，切换到同工作区的新会话 B 后相关提问能召回，且来源 conversation/turn 可点击查看。
- [x] M2. global、workspace、conversation scope 边界正确；两个路径别名指向同一 canonical workspace 时共享，两个不同工作区绝不串用。
- [x] M3. 每个 turn 最多执行一次记忆检索，后续 AgentLoop step 使用同一 projection snapshot；工具/SSE 事件不会重复触发查库。
- [x] M4. 结果去重、稳定排序并符合 top-k、单条和总预算；FTS5 缺失时关键词回退仍可用且结果确定。

## 用户控制与生命周期

- [x] C1. “记住”显式请求创建 confirmed entry；自动提取只创建 candidate，未批准前不被检索或注入。
- [x] C2. Memory mode 可按会话/工作区关闭；关闭后不检索、不提取、不写候选，重新开启不会篡改历史状态。
- [x] C3. 编辑、冲突、supersede、拒绝、单条删除和 scope reset 语义明确并可恢复诊断；删除后重启仍不可召回。
- [x] C4. 记忆中心支持搜索、筛选、来源、编辑、批准/拒绝和删除；本轮采用记录说明使用了什么、来自哪里。

## 安全与解释性

- [x] S1. 服务端阻止 API key、token、密码、私钥、`.env` 值、大段日志和超限 payload 进入记忆；数据库、API、DOM、日志和截图均无秘密泄漏。
- [x] S2. 记忆以低优先级非指令参考注入，恶意 memory prompt 不能越过系统策略、工具策略或 workspace 边界。
- [x] S3. 注入内容只有可展示的记忆摘要和来源，不包含模型隐藏思维，也不以“记忆”名义保存 reasoning 原文。
- [x] S4. reset/delete 对正文、FTS 索引和运行时缓存一致生效，并提供不含秘密正文的审计结果。

## 质量门禁

- [x] T1. Python 覆盖 migration、FTS/fallback、稳定排序、冲突、幂等、隔离、删除和秘密拒绝。
- [x] T2. Vitest/RTL 覆盖记忆中心、候选审批、开关和来源展示；Playwright 覆盖跨会话召回与删除闭环。
- [x] T3. 2000 条混合作用域记忆数据集下无越界召回，性能与资源占用通过 task_008 的发布预算。
- [x] T4. task_001-task_006 全量回归、构建、安全、打包和生产静态资源检查通过。

## 数据、索引与排序证据

- [x] I1. 中英文、代码标识符、路径词、大小写和 CJK bigram fixtures 有固定 analyzer 输出；FTS 与 fallback 对核心查询返回同一正确 top result。
- [x] I2. candidate/superseded/rejected/deleted 不出现在 active retrieval；同一 supersede chain/近重复 content 只注入一条。
- [x] I3. 稳定排序在同分时以 entry id 收敛；重复运行、重启和 FTS rebuild 后 top-k 顺序相同。
- [x] I4. FTS index 与 facts 人为制造不一致后 self-check 能重建；不修改 canonical Conversation 数据。

## 故障与攻击测试

- [x] F1. memory content 包含伪 XML 闭合、system 指令、越界命令、私钥块、高熵 token 和 `.env` assignment 时无法逃逸数据边界或进入数据库/模型请求。
- [x] F2. create/edit/approve/delete/reset 在每个写点失败都保持 entry、索引、source、event 一致；stale version 不覆盖新事实。
- [x] F3. turn 首次检索后立即编辑/delete 相关 memory，本 turn 保持已审计 snapshot，下一 turn 才使用新状态；UI 能解释该时序。
- [x] F4. candidate extractor timeout、非法 JSON、模型拒绝和服务不可用不改变主 turn 终态，也不产生 confirmed memory。

## 定量门槛

- [x] B1. 默认 top-k≤6、单条注入≤1,200 字符、总 memory projection≤6,000 字符；超限按 rank 整条省略并记录数量。
- [x] B2. 2,000 entries 混合作用域 fixture 的 warm query p95≤50 ms、cold query p95≤150 ms（约定测试机/SQLite backend），结果无跨 scope。
- [x] B3. 每 turn 的数据库检索调用数≤1；2,000 个 SSE/model/tool event 不增加调用数。
- [x] B4. hard delete/reset 完成后 facts content、source excerpt、terms、FTS、runtime cache 中的正文匹配数为 0。

## 交付证据矩阵

| 证据 | 必须包含 |
| --- | --- |
| schema/生命周期 | scope、status、version chain、source、usage、delete 行为 |
| retrieval report | analyzer、backend、query、候选、rank reason、budget、耗时 |
| request audit | 注入 block、转义、优先级说明、off 模式无 block |
| UI E2E | 显式保存、跨会话召回、来源、编辑/supersede、删除、不可再召回 |
| security audit | secret patterns、prompt injection、日志/DOM/DB 扫描结果 |
