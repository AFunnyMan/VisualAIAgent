# 文档、开源组件与来源

查阅日期：2026-09-07。以下来源已在规划阶段阅读；软件未安装、模型未下载、运行能力未验证。软件/API 版本将在对应实施阶段记录实际验证值，不按网页示例默认模型运行。

## Codex 开发工作流

| 来源 | 使用方式 |
|---|---|
| [Iterating Development Workflows with Codex](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex) | 区分目标、计划、阶段上下文、执行证据和审查；按本项目授权调整推进节奏 |
| [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | 根目录保存持久指引，链接详细材料；新文件在当前会话显式读取 |
| [Using PLANS.md for multi-hour problem solving](https://developers.openai.com/cookbook/articles/codex_exec_plans) | 借鉴持续更新、可恢复和可观察验收；文章已归档，不沿用旧模型/API 推荐 |

本项目不照搬文章的逐阶段等待批准：用户选择在已授权连续实施范围内验证后推进；但当前任务明确只有文档和 Git，完成后停止。AGENTS.md 是 Codex 识别的指引，其余文件为项目约定，不是平台强制文件。

## 选定的复用组件

| 组件 | 官方来源 | 首版用途 | 许可与验证状态 |
|---|---|---|---|
| OpenAI Agents SDK | [仓库](https://github.com/openai/openai-agents-python)、[示例](https://github.com/openai/openai-agents-python/tree/main/examples)、[模型适配](https://developers.openai.com/api/docs/guides/agents/models) | 单 Agent、函数工具、执行结果、会话与用量 | MIT；选定版本待阶段 00/01 核验 |
| Supervision | [仓库](https://github.com/roboflow/supervision)、[许可](https://github.com/roboflow/supervision/blob/develop/LICENSE.md) | 检测结果统一表示、框/标签与区域辅助 | MIT；不引入 Roboflow 云端作为前置条件 |
| Ultralytics YOLO26n | [模型说明](https://docs.ultralytics.com/models/yolo26/)、[类别](https://docs.ultralytics.com/datasets/detect/coco/) | 预训练模型与 ONNX 导出 | AGPL-3.0/企业许可；导出不会改变原许可；权重与校验待记录 |
| ONNX Runtime | [Python API](https://onnxruntime.ai/docs/api/python/api_summary)、[线程控制](https://onnxruntime.ai/docs/performance/tune-performance/threading.html) | CPU 推理、低线程数与禁用自旋 | 实际 wheel/版本与双平台支持待核验 |
| Streamlit | [局部刷新](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment) | 本地 UI，刷新不重建工作实例 | 实际版本待核验 |

OpenCV 用于本地采集与图像处理；SQLite 使用 Python 标准库接入；uv 用于环境及锁文件。依赖选定后补充版本、来源和第三方许可清单，不以本文的摘要替代原许可证。

## 参考而不引入

- [Frigate](https://github.com/blakeblackshear/frigate)：MIT，借鉴事件、快照和保留设计；[Windows 非官方支持](https://docs.frigate.video/frigate/installation/)，不整套移植。
- [Pydantic AI](https://pydantic.dev/docs/ai/overview/)：结构化工具与多提供商可行备选，当前统一使用 Agents SDK。
- [smolagents](https://github.com/huggingface/smolagents)：工具调用教学参考，不叠加第二个框架。
- [LandingAI VisionAgent](https://github.com/landing-ai/vision-agent)：仓库标记弃用，主要生成视觉程序且涉及多个云端服务，不作为本项目基础。

## 环境选型依据

- [Docker Desktop 虚拟机](https://docs.docker.com/desktop/features/vmm/)：Mac/Windows 的 Linux 容器运行在 Linux 虚拟机内。
- [Docker USB/IP](https://docs.docker.com/desktop/features/usbip/)：需额外配置，官方不保证所有 USB 设备；键盘示例不等于摄像头已验证。
- [WSL USB 接入](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)：需要 usbipd-win 等配置，附加期间设备不能同时由 Windows 使用。
- [Docker 设备映射](https://docs.docker.com/engine/containers/run/)和[多平台构建](https://docs.docker.com/build/building/multi-platform/)：适合后续原生 Ubuntu 部署评估；架构和设备驱动仍需核验。

据此选择原生开发，Docker 非必需。此为项目取舍，不声称 Docker 在所有环境都更慢，也不声称容器化会降低 Token。

## 后续维护

每次实际采用新版本记录包名、版本/提交、原许可证位置、模型来源与 SHA-256、验证平台和证据链接。只复用所需组件，优先安装依赖而非复制整仓库；如复制示例代码，保留来源、许可和改动说明。链接失效时先找官方替代入口，不能把旧网页内容当成现行 API。
