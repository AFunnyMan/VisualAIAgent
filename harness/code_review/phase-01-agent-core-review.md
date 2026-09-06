# 阶段 01 Agent 核心审查

2026-09-07T03:00:34+08:00。审查范围为 `visual_ai_agent/agent.py`、`tests/agent` 以及与
MemoryStore/WatchService 的阶段 03/04 接口集成；依赖现场版本为 `openai-agents==0.22.0`。

审查确认产品只有一个 SDK Agent，工具集合恰好为架构规定的七个业务函数，没有 Shell、任意 SQL、
代码执行、托管工具或 Agent handoff。无模型配置时不会构造或调用模型；显式注入仅用于离线 SDK
边界测试。客户端和 SDK 重试均为零，Runner 最多三轮且有有限超时，默认关闭 tracing 和敏感 trace。

事件通知边界在工具实现中强制限制本轮 watch/event ID，并要求当前场景和目标事件证据两类复查，
不能仅依赖提示词。WatchService 再次验证任务状态、有效期、事件匹配和去重。模型最终回答但未写入
通知、请求异常或自动额度不足均进入带来源标记的持久事实 fallback，不会把 processing 任务静默留住。

审查中修复两项集成问题：本地工具并发限制为一，保证同一轮场景/证据复查在 notify 前完成；所有
用户运行补入配置时区的 `local_date`，使 UI 每日 usage 汇总不漏掉主动请求。异常持久化只使用稳定错误
代码，离线用例验证带伪 Key/query 的异常正文不会进入结果或 agent_runs。

追加审查发现事件在禁止并行工具时可能用满三轮完成 notify、但没有第四轮生成 final。实现现以通知工具
的持久化成功作为完成证据，并用确定性本地确认文字结束；不得改标 fallback。运行 Hook 同时保留上限或
后续请求失败前已返回的 partial usage，并通过 `usage_complete` 区分完整与部分统计。AgentService 提供
异步 `close()`，由运行线程在关闭事件循环前释放 SDK 模型 transport 与 AsyncOpenAI 连接池。

实际验证：`.venv/bin/ruff check visual_ai_agent/agent.py tests/agent` 退出 0；
`.venv/bin/pytest -q tests/agent -m 'not live_api and not camera'` 为 12 passed、3 deselected；
显式 live 命令在未设置 `VAA_RUN_LIVE_API=1` 时为 3 skipped，未产生付费请求。真实 API 与 Windows
平台未执行，因此阶段 01 的真实模型验收仍未完成。当前离线范围未发现阻断下游集成的问题。
