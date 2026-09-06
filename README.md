# AI 视觉智能体 · VisualAIAgent

普通 USB 摄像头 + 本地视觉记忆 + 真实工具调用 Agent。

**当前状态：全路线实施中。** 已建立 Python 3.11 隔离环境、配置和基础验证，视觉、记忆、Agent 与页面正在开发；实机和真实 API 结果另行记录。

## 计划中的首版

- 识别手机、杯子、瓶子，保存最后出现的时间、画面区域和证据。
- 自然语言查询、创建/取消关注，相关事件触发 Agent 复查并提醒。
- 本地持续检测无需云端 Token；模型仅在请求和相关事件时使用必要文字。
- 原生支持 Apple 芯片 Mac 与 Windows 11 x64，8GB 无独显为首轮测试基线。

技术路线：Python 3.11、OpenCV、YOLO26n ONNX、ONNX Runtime CPU、Supervision、SQLite、OpenAI Agents SDK、Streamlit。原生环境使用 uv，Docker 不作为首版依赖。

## 文档入口

| 需要了解 | 阅读 |
|---|---|
| 保留的原始完整计划 | [计划基线](docs/project-plan-baseline.md) |
| 产品目标与边界 | [GOALS.md](GOALS.md) |
| 阶段顺序与前置条件 | [PLANS.md](PLANS.md) |
| 当前实际进展 | [build-log](harness/build-log.md) |
| 给 Codex 的工作约定 | [AGENTS.md](AGENTS.md) |
| 后续任务入口 | [PROMPTS.md](PROMPTS.md) |
| 技术接口与 Docker 决策 | [架构说明](docs/architecture.md) |
| 如何测试与验收 | [验证说明](docs/testing.md) |
| 官方来源与开源复用 | [来源说明](docs/references.md) |

后续可要求执行某个阶段，或授权连续实施路线图。安装、运行和演示说明将在对应功能实际验证后补充；当前不要把测试文档中的计划命令当作已实现入口。

## 仓库与数据

远程目标：[AFunnyMan/VisualAIAgent](https://github.com/AFunnyMan/VisualAIAgent)。重要且经过验证的步骤使用 Git 提交记录。私人摄像头数据、Key、运行数据库、模型权重和虚拟环境不进入仓库。

项目自身的发布许可证尚未选择，不默认声明整个项目为 MIT。各复用组件保留自身许可，尤其模型转为 ONNX 不改变原模型许可。商业闭源发行不属于当前任务。

## 已建立的开发入口

安装 uv 后在项目根目录执行 `uv sync --locked --group dev`，使用 `uv run pytest tests/foundation` 验证基础配置。当前开发机的项目专用 uv 位于 `.tools/bin/uv`；通过 `UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv run --locked python scripts/doctor.py` 查看不含秘密的环境摘要。

复制 `.env.example` 为 `.env` 填写本地配置，API 的地址、模型和 Key 三项齐全才启用 Agent。未提供时仍允许开发和离线验证。
