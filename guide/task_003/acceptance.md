# 任务编号：task_003 验收标准

## 产品界面与文案

- [ ] P1. 默认主页面只出现最终用户需要的任务、状态、动作、验证、变更与恢复信息；协议名、内部计数、context 预算和错误码位于高级详情/About。
- [ ] P2. 用户消息与 assistant 正文形成连续文档流；step 不再拥有独立大卡片或重复标题。
- [ ] P3. glob/grep/read/edit/run 等动作使用浅色单行摘要，包含准确动词、目标、状态与可用耗时；点击/键盘可展开脱敏详情。
- [ ] P4. 失败、取消、断线、验证未通过仍有明确文字和恢复动作，不只靠颜色或 toast。
- [ ] P5. zh-CN/en-US 文案完整且自然；没有复制 Codex、DSH 或其他产品的品牌资源与专有文案。

## Composer 与布局

- [ ] C1. Composer 输入区为视觉主体，主按钮位于右侧；workspace/profile 使用紧凑上下文栏，不持续占用两列大表单空间。
- [ ] C2. 同一主控制槽位在 idle/ready/running/cancelling 间切换 Start/Stop 状态；Stop 仅在运行后出现，重复点击和快捷键行为幂等。
- [ ] C3. 1280×720 无横向滚动，窄屏 inspector/drawer 不遮挡 Composer；system/light/dark 均保持对比度与 focus indicator。

## 性能与组件边界

- [ ] R1. task draft 连续输入 50 个字符时，`POST /api/workspace/validate` 新增请求数为 0。
- [ ] R2. 同一路径有效后注入至少 50 个 SSE event、展开动作、切换 theme/profile 时，workspace validate 新增请求数为 0。
- [ ] R3. workspace 真实改变一次只产生一次 debounce 后校验；旧响应不能覆盖新值，相同规范化 path 可复用缓存。
- [ ] R4. 自动化 render 计数证明 WorkspaceField、ProfileSelector 与 Composer 不订阅无关 event payload；ActivityFeed 不在每个 delta 上重建全部历史 DOM。
- [ ] R5. 长轨迹至少 2,000 个事件时交互仍可用，同时挂载 DOM 有明确上限，向上读取历史不丢顺序。

## 质量门禁

- [ ] Q1. Vitest/RTL 覆盖请求计数、render 隔离、Start/Stop、action disclosure 和 i18n；Playwright 从 production build 验证真实浏览器请求数与布局。
- [ ] Q2. `uv run pytest -q`、Ruff、API 类型检查、typecheck、lint、Vitest、build、Playwright、audit、wheel 与 `git diff --check` 全部通过。
- [ ] Q3. feedback 提供 1280×720 idle/running/success、窄屏、深色和英文脱敏截图，并逐张说明与画面一致。

## 人工验收

1. 打开 Network 面板，验证输入任务和运行事件不会重复请求 workspace validate。
2. 对照用户提供截图检查连续正文、浅色动作行和展开详情，不接受逐 step 大卡片换皮。
3. 只用键盘完成 workspace 选择、任务输入、Start、Stop、展开动作和打开高级详情。

