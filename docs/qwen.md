# 阿里云百炼（千问）接入与自动验收

本机于 2026-09-07 已用用户提供的 Key 完成真实千问接入。Key 仅保存在项目根目录被 Git 忽略的 `.env`，权限为 `0600`；本文件不包含凭据。端点及模型已经实测，普通启动即可使用。

## 配置

```dotenv
VAA_API_KEY=在本地填入自己的Key
VAA_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VAA_AGENT_MODEL=qwen-flash
VAA_API_MODE=chat_completions
VAA_API_TIMEOUT_SECONDS=30
```

使用百炼的 OpenAI 兼容 Chat Completions 接口，通过现有 OpenAI Agents SDK 驱动同一个 Agent 与七个工具，无需额外 DashScope SDK。模型只收到文字事实、时间及证据编号，视频解码、ONNX 推理和截图均在本地。

官方 [地域及接入域名](https://help.aliyun.com/zh/model-studio/beijing-access-information) 说明北京兼容端点仍可用，并推荐新业务使用控制台提供的业务空间专属 API Host。当前 Key 在上述北京端点实测成功。若在其他地域创建 Key，必须使用该地域对应端点；不从 Key 猜测业务空间 ID。

官方 [Chat API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions) 与 [Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling) 描述兼容协议和工具调用流程。`qwen-flash` 为实际测试使用的模型别名；服务端没有在响应中返回其底层快照 ID，不能声称固定到了某个快照。切换模型后应重跑验收。

## 已验证行为与边界

真实 SDK 三项用例覆盖：无记录查询；自然语言创建、列表、取消；用明确测试事实触发模型复查与 Agent 来源提醒。修复后均通过，包含 11 次实际模型请求和完整 usage；最终回归仍为三项通过。原始失败、修复及确切用量见 [进度日志](../harness/build-log.md)。

首轮要求模型从七个工具中选择一个，SDK 在执行工具后恢复自由生成答复。即使提供商忽略 `tool_choice`，零工具的“已完成”答复也会被拒绝并记录失败。模型仍自主决定工具及参数；程序没有把自然语言映射为固定动作。普通闲聊也可能先读取一个业务工具，通常产生两次请求，本应用定位为视觉业务助手。

每次运行最多三次模型请求、整体三十秒超时，SDK 与 HTTP 自动重试关闭。事件复查的输入包含可靠的带时区检索窗口，模型依次读取当前场景、查事件证据、调用提醒工具。额度、超时或事实核验失败仍会明确标记本地降级提醒；不能将 fallback 算作真实 Agent 验收成功。

## 可复现命令

根目录已激活 uv 环境时：

```sh
VAA_RUN_LIVE_API=1 uv run --locked pytest tests/agent -m live_api -v
```

公开视频来源、许可、SHA 与筛选局限见 [阶段 02 上下文](../harness/context/phase-02-local-vision.md)。取得相同文件后，本地视觉与 EOF 验证不读取 Key：

```sh
uv run --locked python scripts/video_acceptance.py \
  --video harness/artifacts/wikimedia-squeezing-open-bottle.webm \
  --expected-sha256 1b032b7e29c95897dc1f47e8c8fb1e68dacc84057f184ef3c2e6442109bb6adc \
  --category bottle --interval 1
```

真实模型与视频集成（需要明确付费开关）：

```sh
VAA_RUN_LIVE_API=1 uv run --locked python scripts/live_acceptance.py \
  --video harness/artifacts/wikimedia-squeezing-open-bottle.webm \
  --category bottle --idle-category 'cell phone' --idle-seconds 1800
```

脚本创建独立的 `harness/artifacts/live-video-*/` 数据目录，使用实时循环视频、真实 ONNX、运行时工作队列和真实千问；验证查询、创建/取消、两类提醒、重复事件、断流和重启。查询会捕获实际工具结果核对类别、时间、区域和证据锚点；事件usage必须完整，HTTP请求数与数据库请求次数对账。输入显式包含七秒故障和合成空白帧，后者只验证“有效未检测到”的流程，不能冒充现实物品移出。HTTP 审计只记录请求数、工具数和文本结构，不保存 Authorization 或原始模型请求。

最后持续等待，硬断言新 Agent 运行数与模型 HTTP 请求数均为零；不靠“禁用 Key”模拟已连接待机。过程中输出每分钟进度，结果和用量落入独立摘要。失败保留原结果且返回非零，不自动重试付费操作。公开视频质量、回放性能和真实 USB 验收分别记录；最终仍需完成 [用户辅助实机验收](user-acceptance.md)。

连接拒绝与自动额度为零的本地故障验证无需真实 Key：

```sh
uv run --locked python scripts/failure_acceptance.py \
  --video harness/artifacts/wikimedia-squeezing-open-bottle.webm
```

该脚本使用真实 SDK 连接本机明确不监听的端口，分别验证一次失败后降级、额度为零时不请求模型，并确认视觉继续。它没有关闭操作系统网络，也不宣称百炼服务发生过故障。

## 费用记录

使用实际响应的 `input_tokens` / `output_tokens` 汇总，不以未知 usage 为零。当前 [qwen-flash 官方价格页](https://help.aliyun.com/zh/model-studio/qwen-flash) 的北京短上下文原价为每百万输入 Token 0.15 元、输出 Token 1.5 元；这里只用于估算，缓存、赠送额度和账户实际账单可能不同。测试总量和计算结果写入进度日志，不把估算称为扣款金额。
