# 公开视频回放故障链路审查

时间：2026-09-07 04:24（Asia/Shanghai）
范围：真实视频、真实 YOLO26 ONNX、`ApplicationRuntime`、真实 OpenAI Agents SDK 的本地故障路径。

## 方法

执行入口为 `scripts/failure_acceptance.py`。脚本直接构造测试配置，不读取 `.env`，使用
`harness/artifacts/wikimedia-squeezing-open-bottle.webm` 原速循环回放，不打开摄像头。它让操作系统
在 `127.0.0.1` 分配一个临时 TCP 端口，保持端口已绑定但从不监听；因此 SDK 的请求实际进入本机
网络栈并收到连接拒绝，同时端口不会在测试过程中被别的进程占用。第二个场景把
`daily_auto_limit` 设为零，在发起请求前由本地额度逻辑短路。

```text
.venv/bin/python scripts/failure_acceptance.py \
  --video harness/artifacts/wikimedia-squeezing-open-bottle.webm
```

机器可读结果位于被 Git 忽略的
`harness/artifacts/failure-acceptance-0953b785/summary.json`，未记录密钥、提示词或私人图像。

## 观察结果

| 场景 | 通知来源 | 持久化状态 | 请求次数 | 故障后视觉仍有新鲜帧 | 当前画面有效 |
|---|---|---|---:|---|---|
| 本机 TCP 连接拒绝 | `fallback` | `fallback_notified` | 1 | 是 | 是 |
| 当日自动额度为零 | `fallback` | 未预留运行记录 | 0 | 是 | 是 |

连接拒绝场景只发生一次 SDK 请求尝试，没有 SDK 重试；失败后关注任务收到一次本地降级提醒。
额度为零场景未预留 Agent 运行记录，累计请求次数为零，同样写入一次本地降级提醒。两个场景都在
提醒写入后继续等到新鲜视觉观察数增长，并确认 `get_current_scene` 仍为当前，说明 Agent 队列的
故障处理没有停止视觉线程。

## 结论与边界

这两个实际本机故障注入覆盖了连接拒绝、自动额度短路、降级通知和视觉线程隔离。它们没有向
百炼或任何外部地址发送请求，不能代表百炼服务故障、DNS/超时行为、操作系统断网或真实 USB
摄像头故障。后几项仍需各自的真实条件验收。
