# task_007 Master 验收证据

验收日期：2026-08-29（Asia/Shanghai）
环境：Windows，本地 SQLite/Fake Model，生产 Vite 静态资源；无外部真实凭据。

## 结论

`guide/archive/task_007/acceptance.md` 的 M/C/S/T/I/F/B 条目全部通过。Task 7 已归档；未开始 Task 8。

## 数据、生命周期与检索

- schema v8→v13 增量、幂等迁移保留 Conversation；Memory facts/source/terms/FTS/usage/events/idempotency/scope-version 均在同一数据库链。
- create/edit/approve/reject/delete/reset 的事实、来源、active index、audit 与幂等记录单事务；DB trigger 拒绝非法 scope/status/source/版本链迁移。
- confirmed-only active retrieval；candidate/superseded/rejected/deleted 不进入 FTS/terms。FTS 人为损坏后启动 self-check 重建，canonical Conversation 不变；terms fallback 返回相同正确 top result。
- global/workspace/conversation canonical scope 隔离；稳定排序最终以 entry id 收敛；投影 top-k≤6、单条≤1,200、总 escaped block≤6,000，超限整条省略。
- 每 turn 只检索一次并缓存 snapshot；2,000 个事件不会增加调用。首次投影后 edit/delete 不改变本 turn usage，下一 turn 使用新状态。

## 安全与删除

- API key、Bearer、密码、私钥、`.env` assignment、高熵 token、大段日志/超限 payload 在服务边界 fail-closed；候选输入在模型调用前先做 secret 检查。
- memory 仅以 XML 转义的 `<memory_context trust="untrusted_reference">` 低优先级参考注入；伪闭合标签/system 指令不能越界，reasoning/tool output/credential 不进入候选输入。
- hard delete 遍历完整 supersede chain；facts content、source excerpt、terms、FTS 与 runtime projection 正文匹配为 0。历史 usage/event 仅保留 scope/kind/title/source id/hash 等无正文审计快照。

## 自动化与性能

| 门禁 | 结果 |
| --- | --- |
| Ruff format / lint | 通过（121 files） |
| Python 全量 | 375 passed, 4 skipped |
| Memory 安全/事务/索引/删除定向 | 9 passed |
| 2,000 entries 性能 | 1 passed；warm p95≤50ms、cold p95≤150ms，无跨 scope |
| TypeScript / ESLint | 通过 |
| Vitest/RTL | 61 passed（16 files） |
| OpenAPI schema | 同步、无 diff |
| Vite production build | 通过 |
| Playwright production E2E | 12 passed |
| Python sdist/wheel | 通过 |
| git diff --check | 通过 |

依赖清单未变化，按 handoff 规则不重复执行 npm audit。

## UI 与请求审计

- E2E：`/remember` 显式保存 workspace confirmed fact → 新会话请求实际包含 memory block → usage 显示标题/来源 → 点击来源回原 conversation → hard delete → 再开新会话请求无 memory block、usage 为 0。
- Fake Model 对“项目技术栈是什么”依据真实请求中是否存在 `<memory_context>` 作确定性回答，避免用工作区文件状态代替召回证据。
- 人工生产浏览器：1280×720 中文浅色、390×844 英文深色通过；搜索/筛选/作用域开关/来源/编辑/删除控件无溢出，窄屏导航后抽屉自动收起。
- 删除确认明确说明正文、来源摘要和搜索索引永久移除，历史 turn 仅保留无正文 usage 审计。
