# AI 视觉智能体 · VisualAIAgent

普通 USB 摄像头 + 本地视觉记忆 + 真实工具调用 Agent。

**当前状态：文档准备阶段，业务代码尚未开发。** 本次仅建立文档和 Git 里程碑，后续开发由用户主动启动。尚无可运行应用、已安装依赖、已下载模型或通过的产品测试。

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
