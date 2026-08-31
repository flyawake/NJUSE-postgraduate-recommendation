编程智能体项目说明

一、Git仓库地址
https://github.com/flyawake/NJUSE-postgraduate-recommendation

二、运行方法
环境要求：Python 3.10+、uv、nodejs；
1. 克隆仓库并进入项目目录。
2. 安装依赖：uv sync --all-groups
3. 启动界面：uv run coding-agent ui
4. 浏览器打开本地页面，在“模型设置”中配置OpenAI、DeepSeek或兼容服务的模型、接口地址和凭据，然后新建对话、选择工作区并输入编程任务。
命令行也可运行：uv run coding-agent --workspace <工作区路径> "<编程任务>"

三、特色功能
项目自行实现显式AgentLoop、模型输出解析、上下文管理、工具协议、本地执行、循环终止和错误处理。Agent可自主搜索和读取代码、创建或编辑文件、运行测试及命令，并根据工具结果继续决策。
对话采用只追加的规范历史，每轮生成有字符、Token和请求字节预算的模型上下文；工具调用与结果按完整分组持久化到SQLite，异常重启不会重放未完成操作。系统具有最大步数、重复调用、连续失败和取消保护，并追踪文件修改后的测试验证状态。
本项目支持后台任务、Queue/Steer（任务队列和插话引导）、跨会话Memory、附件、联网检索、文件Diff及历史恢复等功能。每个对话可独立设置本机命令为“每次询问、默认允许或默认拒绝”，设置可跨刷新和重启保存；开启默认允许前会显示风险警告。

四、说明
文件与命令均在本机执行，不依赖服务端代码执行功能。命令运行需要用户授权，允许执行后程序仍可能访问工作区外文件、网络和继承的环境变量，请仅在可信或一次性工作区中使用。

技术栈：
项目后端和 Agent 核心使用 Python、FastAPI 与 SQLite，前端使用 React、TypeScript 和 Vite。测试采用 pytest、Vitest 和 Playwright。
测试用模型：deekseek v4 flash
