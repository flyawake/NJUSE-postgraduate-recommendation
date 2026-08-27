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

## 发布与材料

- [ ] R1. 在干净环境完成依赖同步、测试、production build、wheel/sdist 安装、GUI/CLI 启动；最终 GUI 不依赖 Node。
- [ ] R2. 依赖锁、许可证、audit、秘密扫描和仓库状态通过；公开仓库创建/首提交时间满足题目规则。
- [ ] R3. `README.txt` 不超过 1000 汉字，运行命令、仓库地址、环境要求准确；压缩包只含规定文件。
- [ ] R4. 最终 MP4 不超过 2 分钟/200 MB，展示真实 GUI 编程任务与验证，并能支持对 AgentLoop、工具链和产品语义的现场答辩。

