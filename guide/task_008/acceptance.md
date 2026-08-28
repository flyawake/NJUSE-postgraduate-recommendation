# 任务编号：task_008 验收标准

## 集成与评测

- [ ] E1. release commit 上 task_003-task_007 的 Python、typecheck/lint、Vitest、production build、Playwright 和安全门禁一次性通过。
- [ ] E2. 固定评测至少覆盖诊断、单/多文件修复、失败恢复、取消、Queue/Steer、多轮恢复、会话隔离和记忆召回，并输出版本化指标。
- [ ] E3. 多后台会话、三条 Queue、一条 Steer、取消、断连和重启的组合场景保持 canonical 顺序且无重复副作用。
- [ ] E4. 至少一个用户配置的真实 provider 从 GUI 完成读取、修改、验证和追问；保存不含凭据的模型/wire API/结果证据。

## 性能与恢复

- [ ] P1. production build 中输入 50 字符不会触发 workspace validation；2000 个 transcript event 下输入与滚动无明显阻塞，DOM 有界。
- [ ] P2. 连续模型 delta 经过批处理，React render、事件投影和 validation 调用次数满足书面预算，测试结果可重复。
- [ ] P3. 2000 条 memory 的同 workspace 查询稳定、预算有界且不跨 scope；多个后台会话不会使主会话输入退化到不可用。
- [ ] P4. 进程在 stream、tool、queue claim 和 migration 关键点异常退出后可恢复；不丢事实、不重复消费，失败升级前自动保留数据库备份。

## 安全、可访问性与产品质量

- [ ] S1. 凭据、请求头、私钥、`.env`、reasoning 原始敏感片段不进入日志、DOM、数据库导出、截图、README 或视频。
- [ ] S2. Host/CSP/path/tool policy、SSE/API fail-closed 脱敏、会话删除/导出和 memory poisoning threat cases 通过。
- [ ] S3. 键盘、焦点、screen-reader label、对比度、200% 缩放和窄屏可用；zh-CN/en-US 无缺失 key、截断或开发占位文案。
- [ ] S4. 默认界面只显示用户产品信息；高级诊断必须主动打开，详情经过脱敏并可复制必要摘要。
- [ ] S5. Artifact preview 无任意 path read/跨 conversation IDOR；历史源码不进入 bootstrap/SSE/log/默认导出，conversation delete 后 refs/GC 符合 task_004 承诺。

## 发布与材料

- [ ] R1. 在干净环境完成依赖同步、测试、production build、wheel/sdist 安装、GUI/CLI 启动；最终 GUI 不依赖 Node。
- [ ] R2. 依赖锁、许可证、audit、秘密扫描和仓库状态通过；公开仓库创建/首提交时间满足题目规则。
- [ ] R3. `README.txt` 不超过 1000 汉字，运行命令、仓库地址、环境要求准确；压缩包只含规定文件。
- [ ] R4. 最终 MP4 不超过 2 分钟/200 MB，展示真实 GUI 编程任务与验证，并能支持对 AgentLoop、工具链和产品语义的现场答辩。

## 证据完整性

- [ ] X1. 所有报告标明同一 release commit SHA、production asset hash、schema version、OS/CPU/RAM、Python/Node/browser 版本。
- [ ] X2. eval/performance 的原始 JSON 与 Markdown 数字一致；失败 case 仍保留，不只提交成功截图。
- [ ] X3. release worktree clean，生成类型/静态资源/lockfile 无漂移；从 wheel 安装后的版本与仓库 version 一致。
- [ ] X4. canary secret 在 DB、日志、DOM、截图、导出、README.txt、视频帧 OCR/人工检查中均无原值。
- [ ] X5. 已知限制逐项说明影响、触发条件和降级；不可用 capability 在 UI 隐藏/禁用，不保留误导入口。

## 性能硬门槛

- [ ] B1. 2,000-event transcript 下输入 keydown→paint p95≤50 ms、max≤100 ms；初始 mounted transcript items≤350。
- [ ] B2. 2,000 tiny stream chunks 的持久 checkpoint≤100、React/coalesced commit≤100，正文/Think 字符完整。
- [ ] B3. 200 conversation list warm API p95≤100 ms；2,000 memory warm query p95≤50 ms/cold≤150 ms；100 queue item mutation p95≤100 ms。
- [ ] B4. active stream cancel HTTP/UI acknowledgement≤250 ms，可取消 worker terminal≤5 s；不支持即时终止的外部命令有明确状态和测试。
- [ ] B5. SSE retained-cursor reconnect≤2 s 恢复一致 snapshot/event；连续断连 3 次无重复字符、event 或 Queue claim。
- [ ] B6. 100-file change summary 默认有界挂载且展开/打开 p95≤100 ms；20,000-line 上限 diff 首个 hunk≤300 ms，超限/binary 不进行无界渲染。

## Crash/recovery 必过点

- [ ] C1. migration、turn start、provider partial、tool group、terminal commit、Queue claim、Steer claim、Memory edit/delete 各关键点均有 failpoint 子进程测试。
- [ ] C2. 每次恢复后执行数据库 invariant checker：FK、active uniqueness、canonical pairing、inbox claim、memory index 无错误。
- [ ] C3. 恢复绝不自动重放可能有副作用的 tool/turn；未知结果以用户可见、可继续诊断的状态表示。
- [ ] C4. migration 失败的备份路径已验证位于 agent home、可人工恢复，不覆盖更新后的当前数据库。

## 最终人工演练

1. 按 README.txt 在干净环境启动，无 Node、无源码路径依赖。
2. 从 GUI 新建会话、配置 provider、完成真实修改/测试、运行中 Queue 或 Steer、追问并切换会话。
3. 关闭并重启应用，确认 Conversation/Queue/Memory 状态；完成归档/删除且 workspace 文件不受影响。
4. 断网/错误 key/无效 workspace 各演练一次，界面给出用户文案和恢复入口，不显示内部异常。
5. 按最终脚本完整录制一次计时，确保 2 分钟内无需剪掉关键等待或泄密画面。
