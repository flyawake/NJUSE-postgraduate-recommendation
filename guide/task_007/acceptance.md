# 任务编号：task_007 验收标准

## 作用域与召回

- [ ] M1. 用户在会话 A 明确保存 workspace 事实，切换到同工作区的新会话 B 后相关提问能召回，且来源 conversation/turn 可点击查看。
- [ ] M2. global、workspace、conversation scope 边界正确；两个路径别名指向同一 canonical workspace 时共享，两个不同工作区绝不串用。
- [ ] M3. 每个 turn 最多执行一次记忆检索，后续 AgentLoop step 使用同一 projection snapshot；工具/SSE 事件不会重复触发查库。
- [ ] M4. 结果去重、稳定排序并符合 top-k、单条和总预算；FTS5 缺失时关键词回退仍可用且结果确定。

## 用户控制与生命周期

- [ ] C1. “记住”显式请求创建 confirmed entry；自动提取只创建 candidate，未批准前不被检索或注入。
- [ ] C2. Memory mode 可按会话/工作区关闭；关闭后不检索、不提取、不写候选，重新开启不会篡改历史状态。
- [ ] C3. 编辑、冲突、supersede、拒绝、单条删除和 scope reset 语义明确并可恢复诊断；删除后重启仍不可召回。
- [ ] C4. 记忆中心支持搜索、筛选、来源、编辑、批准/拒绝和删除；本轮采用记录说明使用了什么、来自哪里。

## 安全与解释性

- [ ] S1. 服务端阻止 API key、token、密码、私钥、`.env` 值、大段日志和超限 payload 进入记忆；数据库、API、DOM、日志和截图均无秘密泄漏。
- [ ] S2. 记忆以低优先级非指令参考注入，恶意 memory prompt 不能越过系统策略、工具策略或 workspace 边界。
- [ ] S3. 注入内容只有可展示的记忆摘要和来源，不包含模型隐藏思维，也不以“记忆”名义保存 reasoning 原文。
- [ ] S4. reset/delete 对正文、FTS 索引和运行时缓存一致生效，并提供不含秘密正文的审计结果。

## 质量门禁

- [ ] T1. Python 覆盖 migration、FTS/fallback、稳定排序、冲突、幂等、隔离、删除和秘密拒绝。
- [ ] T2. Vitest/RTL 覆盖记忆中心、候选审批、开关和来源展示；Playwright 覆盖跨会话召回与删除闭环。
- [ ] T3. 2000 条混合作用域记忆数据集下无越界召回，性能与资源占用通过 task_008 的发布预算。
- [ ] T4. task_001-task_006 全量回归、构建、安全、打包和生产静态资源检查通过。

