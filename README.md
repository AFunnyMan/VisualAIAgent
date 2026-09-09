# 视觉记忆 · VisualAIAgent

普通 USB 摄像头、本地物品记忆和一个真正调用业务工具的 Agent。识别手机、杯子、瓶子，保留最后看到的时间、区域和截图；通过对话或页面建立关注，相关事件触发一次提醒。

**源码功能、离线回归及千问真实工具调用已通过；公开视频已贯通本地视觉与 Agent 提醒。USB 摄像头场景和 Windows 11 实机验收仍待用户辅助。** 具体证据与状态以 [build-log](harness/build-log.md) 为准，不把公开图片或假模型测试视为实机通过。

2026-09-09已按用户授权完成两轮本地场景微调试验，过程、数据划分、参数调整和未通过的结果单独记录于[训练日志](训练日志.md)。实验模型尚未替换正式模型；本地识别质量仍需改进。

## 启动

需要 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。使用原生 Apple 芯片 Mac 或 Windows 11 x64；uv 自动准备 Python 3.11。8GB 无独显是验收基线，尚未证明为最低配置。

```sh
git clone https://github.com/AFunnyMan/VisualAIAgent.git
cd VisualAIAgent
uv sync --locked --group dev
```

本次开发机已在项目内安装 uv 和依赖。可直接运行：

```sh
sh scripts/start-mac.sh
```

其他 Mac 安装 uv 后也使用该脚本。Windows 在项目根目录运行：

```powershell
.\scripts\start-windows.ps1
```

也可在两个平台直接运行 `uv run --locked streamlit run app.py --server.address 127.0.0.1`。页面地址为 `http://127.0.0.1:8501`，只监听本机。先启动页面、准备模型，再点击“开始观察”。停止按钮释放摄像头；终端 `Ctrl+C` 退出整个应用。同一数据目录不能同时运行第二个实例。

## 模型准备

本开发机已准备 `models/yolo26n-e2e.onnx`，可直接使用。Git 不包含权重，新克隆需要在独立导出环境中准备模型：

Mac：

```sh
UV_PROJECT_ENVIRONMENT=.export-venv uv sync --locked --group export --no-dev
UV_PROJECT_ENVIRONMENT=.export-venv uv run --locked --group export --no-dev python scripts/prepare_model.py
```

Windows PowerShell：

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.export-venv'
uv sync --locked --group export --no-dev
uv run --locked --group export --no-dev python scripts/prepare_model.py
Remove-Item Env:UV_PROJECT_ENVIRONMENT
```

脚本从 Ultralytics 官方来源下载 YOLO26n，导出静态 FP32 ONNX，比较相同预处理下的 PyTorch/ONNX 输出，通过后生成 [模型清单](model_manifests/yolo26n-e2e.onnx.json)。导出包含时间元数据，各次导出的文件校验值可能不同；应以该次成功校验生成的清单为准，不能手工绕过校验。运行环境只用 ONNX Runtime CPU，不加载 PyTorch。模型来源和许可见 [第三方说明](docs/third-party-notices.md)。

## Agent 配置

本开发机已在被忽略的 `.env` 配置阿里云百炼 `qwen-flash`，可直接启动。接入方式及自动验证见 [千问说明](docs/qwen.md)。新克隆请复制 `.env.example` 为本机 `.env`，填写以下三项，随后重启应用：

```dotenv
VAA_API_KEY=你的本地密钥
VAA_API_BASE_URL=提供商的完整API基础地址
VAA_AGENT_MODEL=已开通且支持工具调用的模型名称
VAA_API_MODE=responses
```

兼容 Chat Completions 的提供商可设 `VAA_API_MODE=chat_completions`，工具能力仍需真实验证。没有三项完整配置时明确显示“Agent 未连接”，不选用隐含模型、不调用云端；画面、历史与手动关注仍可使用。不要在聊天、提交或截图中包含 Key。

常用设置：`VAA_CAMERA_INDEX=0`、`VAA_SAMPLE_INTERVAL=1`（或 `2` 秒省电档）、`VAA_CONFIDENCE=0.35`、`VAA_DAILY_AUTO_LIMIT=20`、`VAA_API_TIMEOUT_SECONDS=30`、`VAA_TIMEZONE=Asia/Shanghai`。运行整体模型超时默认为 30 秒；每次最多三轮模型请求，网络自动重试关闭。自动额度和用户请求分别记录，未知或部分 Token 用量明确标注。

## 使用

1. 将物品置于固定、光线充足的摄像头画面，选择设备编号并开始观察。三次连续有效采样确认出现；已出现的物品连续五秒有效未检测到才产生缺失事件。
2. 在“历史与证据”查看最后看到的时间、左/中/右区域和本地截图。同类多个物品展示候选，历史不代表当前一定仍在。
3. 连接 Agent 后可问“最后在哪里看到手机？”、“杯子持续未检测到时，在半小时内提醒我”、“取消刚才的关注”。展开工具记录检查实际执行结果。
4. 也可在“关注与提醒”直接创建和取消任务。只有匹配的建立后事件才唤醒 Agent；提醒一次后任务完成。API 不可用或超限时，有事实依据的本地提醒明确标为降级。

断连、暂停、停止、过期或采样不足都表示当前未知，不据此推断被拿走。只支持预训练三类别，不识别是谁的物品、不判断谁移动了它、不录像或上传画面。

目标太小时，可在侧栏调整“采集清晰度”和“观察范围”，确保物品完整出现在预览中。更改设置后点击“开始观察”会先停止旧采集再应用；改变取景会重新确认当前物品，历史保留。只观察预览范围，范围外不能据此推断。分辨率更高不保证识别更准；当前杯子的具体改进与剩余漏检见[实机复验](docs/camera-remediation-2026-09-08.md)。

## 验证与故障排查

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked python scripts/doctor.py
```

默认测试禁止网络连接和物理摄像头访问，真实 API 用例单独排除。完整验收命令、真实模型显式开关、30 分钟性能记录和固定场景评分见 [测试说明](docs/testing.md) 与 [用户辅助验收](docs/user-acceptance.md)。GitHub Actions 定义了原生 macOS/Windows 离线检查；托管 runner 不能替代 Windows 11 摄像头实机验收。

- **模型启动失败**：先执行模型准备脚本；检查路径、相邻名称的模型清单和可选 `VAA_MODEL_SHA256` 是否一致。
- **黑屏、断连或权限错误**：检查系统摄像头权限、设备占用和编号。断连/异常时点击“开始观察”会先释放旧连接再重开；停止失败会明确报错，不能叠加启动。若系统能识别设备但反复重连仍失败，在启动应用的终端按 `Ctrl+C` 后按原方式重新启动，保留原数据目录；只刷新浏览器不会重建应用进程。应用不会将故障计入缺失时间。
- **Agent 未连接/调用失败**：检查三项配置、API 模式、工具调用能力和账户可用性；页面保留安全的状态及工具摘要，原始秘密不写入日志。
- **已有应用实例**：先退出原进程；不要删除运行中的锁文件来绕过互斥。
- **数据库或磁盘故障**：停止应用并保留整个数据目录；迁移会先备份，未知 schema 拒绝写入。不要删除数据库来掩盖问题。
- **ONNX Runtime 缓存警告**：当前 macOS wheel 可能在导入时提示 telemetry cache 不可写；应用关闭遥测并忽略其临时旁路文件，不影响已验证的本地推理。

## 数据与开发入口

默认本地数据位于 `data/`，其中 SQLite 将视觉事实、任务通知、对话和用量分表保存。普通观察与事件按七天保留；最后观察和仍被任务/通知引用的证据受保护。历史对话仅保留最近五个完整交互。退出应用后备份整个数据目录；不要只复制正在写入的 SQLite 主文件。

| 内容 | 入口 |
|---|---|
| 当前进度与证据 | [build-log](harness/build-log.md) |
| 目标、顺序与阶段 | [GOALS](GOALS.md)、[PLANS](PLANS.md) |
| 架构和业务接口 | [architecture](docs/architecture.md) |
| 测试与用户辅助验收 | [testing](docs/testing.md)、[验收步骤](docs/user-acceptance.md) |
| 开发约定与恢复 | [AGENTS](AGENTS.md)、[PROMPTS](PROMPTS.md) |
| 原始规划（不改写） | [基线](docs/project-plan-baseline.md) |
| 来源与第三方许可 | [references](docs/references.md)、[notices](docs/third-party-notices.md) |

远程仓库：[AFunnyMan/VisualAIAgent](https://github.com/AFunnyMan/VisualAIAgent)。项目自身发布许可证尚未选择；依赖与模型保留各自许可证，ONNX 导出不会改变模型许可。私人画面、数据库、Key、权重、环境和完整运行日志不提交。

当前透明杯难例可尝试侧栏“增强杯子检测（本地复查）”，每张画面最多追加一次本地识别，默认关闭；本机有效设置是720p完整画面。它不保证所有杯子或姿态都成功，也不能消除桶/锅误报，详见[实测与限制](docs/cup-scale-recheck-2026-09-08.md)。
