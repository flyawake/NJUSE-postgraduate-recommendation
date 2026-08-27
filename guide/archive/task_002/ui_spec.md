# task_002 UI / UX 规格

## 1. 设计目标

界面首先要让评审者在两分钟内看懂三件事：用户交给 Agent 什么任务、Agent 正在做什么、结果是否经过验证。信息密度接近开发工具，但不能退化为原始日志浏览器。

设计原则：

1. **任务优先**：默认焦点是任务与结果，不是 provider 参数。
2. **过程可验证**：工具行为、错误、验证与计数可检查，但隐藏推理和秘密不可见。
3. **渐进披露**：摘要默认清晰，详细参数/输出按需展开。
4. **稳定胜过炫技**：长运行、刷新、断线、取消和失败状态必须可靠；motion 只用于表达状态变化。
5. **原创但有行业语法**：使用熟悉的 sidebar、activity feed、inspector、settings，不复制参考产品品牌和像素布局。

## 2. 桌面信息架构

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│  Product mark   Workspace: E:\demo\repo          Profile: DeepSeek   ⚙  ◐ │
├──────────────┬───────────────────────────────────────┬───────────────────────┤
│ 新任务       │  Activity / Conversation              │ Run Inspector         │
│              │                                       │                       │
│ 当前运行     │  用户任务卡                            │ RUNNING               │
│  running     │                                       │ Step 4 · Attempt 5    │
│              │  Agent 简短公开消息                    │ Tools 5 · 00:18       │
│ 最近运行*    │                                       │ Verification NOT_RUN  │
│              │  ▾ 3 项工具已完成                      │                       │
│              │    ✓ glob     src/**/*.py              │ Changed files         │
│ 模型设置     │    ✓ grep     TODO                      │  M src/app.py         │
│ 关于/安全    │    ◌ read     src/app.py                │                       │
│              │                                       │ Error / recovery      │
│              │  [状态与错误的 inline banner]          │                       │
│              ├───────────────────────────────────────┤                       │
│              │ 描述你希望 Agent 完成的编程任务…       │                       │
│              │ [开始运行]                    [取消]    │                       │
└──────────────┴───────────────────────────────────────┴───────────────────────┘
```

`最近运行` 在 task_002 只表示当前进程内的有界运行记录，不承诺跨进程历史；若工期紧张可隐藏，但不得显示不可用占位入口。

## 3. 关键页面

### 3.1 空态 / 新任务

- 中央显示一句明确价值说明和 2-3 个短示例任务，不使用营销大段文字。
- 顶部 workspace/profile 未就绪时显示 inline setup card；主按钮跳转到具体缺失项。
- task textarea 支持 `Ctrl+Enter` 开始；输入为空时按钮 disabled 并有可感知说明。

### 3.2 运行态

- 顶部状态徽标和 inspector 同步，但只有一个事实源。
- 新事件在用户位于底部时自动跟随；用户上滚后停止自动滚动并显示“回到最新”，不得抢焦点。
- 当前工具卡展开并显示进度；连续完成的工具折叠成组，标题如“已完成 3 项操作”。失败工具保持展开。
- 取消为次要危险操作，点击后立即变为“正在取消…”，保持 disabled 直到终态，重复请求幂等。

### 3.3 完成态

- 最终答复位于中央流末端，顶部明确显示 `VERIFIED / NOT_APPLICABLE / FAILED / NOT_RUN`。
- inspector 总结 stop reason、计数、耗时和变更文件；FAILED/NOT_RUN 不得使用与成功相同的绿色完成视觉。
- 提供“新任务”主按钮；不在 task_002 声称可以继续多轮会话。

### 3.4 设置 / Onboarding

- provider preset 用三张简洁选择卡：OpenAI、DeepSeek、自定义兼容服务。
- profile 表单按“名称 → URL → 模型 → 凭据引用/凭据”顺序，实时校验但只在字段附近显示错误。
- API key 输入始终为 password 类型；已配置时显示来源和可写性，不显示固定星号长度以免暗示 secret 长度。
- env 遮蔽时字段只读，显示“由环境变量提供”；不允许写入一个不会生效的本地值。
- 删除 profile 使用 AlertDialog，并说明不会自动删除共享 credential。

## 4. 视觉语言

- 使用中性石墨灰背景、低饱和蓝/青作为主强调；success/warning/error 使用语义色且始终配合图标与文字。
- 默认采用系统主题；深色主题适合录屏，但浅色必须同样完整。
- 字体使用系统 UI font stack，代码/路径/计数使用系统 monospace；不从网络加载字体。
- 基础间距单位 4px；主要间距 8/12/16/24/32。控件高度不少于 36px，主要按钮 40px。
- 圆角与阴影克制，工具卡靠边框和层级区分；避免全屏渐变、玻璃拟态、霓虹和无意义脉冲。
- motion 120-200ms，只用于 collapse、drawer、状态切换；遵守 `prefers-reduced-motion`。
- 所有视觉值通过 CSS variables/design tokens 暴露，Tailwind 映射 token，不在组件散落任意颜色值。

## 5. 组件与状态契约

| 组件 | 必需状态 |
| --- | --- |
| `AppShell` | desktop / narrow，sidebar collapsed，inspector drawer |
| `WorkspaceField` | empty / valid / invalid / checking |
| `ProfileSelector` | loading / ready / missing credential / unavailable |
| `TaskComposer` | idle / invalid / ready / running / cancelling |
| `RunStatusBadge` | idle / running / completed / error / interrupted |
| `VerificationBadge` | verified / not-applicable / failed / not-run |
| `ActivityFeed` | empty / streaming / paused-autoscroll / terminal / disconnected |
| `ToolEventGroup` | running / completed / contains-error / expanded / collapsed |
| `ToolCard` | prepared / running / success / error / aborted |
| `RunInspector` | no-run / active / terminal |
| `InlineError` | validation / configuration / transport / run failure + recovery |
| `ProfileForm` | create / edit / dirty / saving / error / saved |
| `CredentialField` | absent / local-writable / env-readonly / replacing / clearing |

## 6. 语言规范

默认 `zh-CN`，同时完整提供 `en-US`。

推荐中文：

- `新任务`、`开始运行`、`取消运行`、`正在取消…`
- `工作区`、`模型配置`、`运行详情`、`变更文件`
- `验证通过`、`无需验证`、`验证失败`、`尚未验证`
- `工具调用`、`查看详情`、`回到最新`

保留英文的标识：provider/profile/model ID、API、URL、SSE、AgentLoop、工具名和稳定 error code。帮助文本首次出现时可给中文解释，不能把代码标识翻译掉。

禁止文案：

- “思维链”“内部推理”等暗示展示隐藏推理的入口。
- “绝对安全”“完整沙箱”“完全验证”等超出实现能力的声明。
- 只有“发生错误”而无恢复建议的死路提示。

## 7. 可访问性与键盘

- 优先原生语义；复杂 Dialog/Select/Tabs/Collapsible 使用经过可访问性验证的 primitives。
- 所有图标按钮有可访问名称，表单字段有 label/description/error 关联。
- `:focus-visible` 清晰且不被 sticky composer 遮挡；Escape 关闭 drawer/dialog，但不得意外取消 run。
- 运行进度用温和的 `role=status`/`aria-live=polite` 汇总播报，不逐 token/逐事件轰炸读屏。
- 错误 inline banner 可被读屏发现；toast 只做补充。
- 文本和交互控件以 WCAG 2.2 AA 为目标，状态不只依赖颜色。

## 8. 演示镜头设计

两分钟演示建议：

1. 5-10 秒：打开 GUI，展示已配置的 DeepSeek/OpenAI-compatible profile 与工作区。
2. 10-20 秒：输入真实任务并开始，突出一键启动。
3. 45-60 秒：活动流实时出现 glob/grep/read/edit/verify，已完成操作折叠，inspector 计数更新。
4. 15-20 秒：展示 VERIFIED 最终答复和变更文件摘要。
5. 10-15 秒：快速打开设置，证明 provider/URL/model 可编辑且 credential 不回显。

演示主题、窗口大小、浏览器缩放和示例任务必须提前固定；不在正式录屏时首次验证布局。
