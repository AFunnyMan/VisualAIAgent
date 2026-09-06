# 阶段 01 上下文

2026-09-07，Asia/Shanghai。阶段 01 使用已锁定的 `openai-agents==0.22.0` 和
`openai==3.8.0` 实现单一 Agent。接口依据官方 OpenAI 文档与本地已安装 SDK 源码核对；
SDK 的一轮等于一次模型调用，因此 `Runner.run(max_turns=3)` 是三次模型请求的硬边界。
自动事件可能依次用三轮完成场景、事件和通知工具；若通知已由工具成功持久化，Runner 因缺少第四轮
最终文字而触发上限时，服务依据工具结果记为完成并生成确定性确认文字，不再错误执行 fallback。

Agent 只注册架构规定的七个 `function_tool`。业务依赖通过 `RunContextWrapper` 注入，模型只看到
`ToolResult` 的 JSON 文字、时间与证据编号；截图字节和本地路径不进入模型输入。模型必须显式配置
API 地址、模型和 Key；无配置时不构造模型适配器。离线 Fake Model 直接实现 SDK `Model` 接口，
用于验证真实 Runner 工具循环，但不替代真实 API 验收。

请求层同时关闭 OpenAI 客户端重试和 SDK runner 重试，单次运行整体超时使用配置值，Runner tracing
显式关闭。`preserve_raw_usage=True` 用于判断提供商是否实际返回 usage；任一响应缺字段时，对应聚合值
保持 `None`，不采用 SDK 归一化后的零值冒充实测用量。
Hook 在每次已完成模型响应后保存原始 usage；后续请求失败时保留已知 partial token，并以
`usage_complete=false` 防止 UI 将部分值解释为完整总量。

历史上下文读取最近五个完整 user/assistant 交互并保持配对。当前运行内的 function call 和 tool output
由 Runner 原样维持；业务数据库仅持久化安全的工具名称、成功状态、计数和业务 ID 摘要，不保存提示词、
Key、API URL 或异常正文。

自动事件运行复用阶段 04 的持久化配额接口，以配置时区计算日期。事件上下文限定唯一 watch/event；
Agent 必须调用当前场景工具，并由历史查询命中目标 event 或证据后才允许通知工具写入。模型不可用、
超限、超时、失败或未通知时，使用持久化事件重新核验并写入标为 `fallback` 的本地规则提醒；该结果
不记作 Agent 成功。

官方依据：[Agents 模型与工具编排](https://developers.openai.com/api/docs/guides/agents/models)、
[Responses function tools](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)。
真实提供商、模型能力和实际 usage 尚未验证，留待显式 `live_api` 条件具备后执行。
