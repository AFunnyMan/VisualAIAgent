# 阶段 00 上下文

2026-09-07，Asia/Shanghai。用户已从仅文档改为授权全路线实施及并行开发；随后限定子 Agent 最高为 Sol，已中断先前继承模型的子任务并改为 Sol。

现场为 Darwin arm64，仅 PATH 中的 Python 3.12 可用，uv 不存在。uv 通过 PyPI 安装到项目 `.tools`，由 uv 下载 Python 3.11 到 `.python`，不改变全局环境。缓存及工具目录被 Git 忽略。跨平台依赖解析与真实 Windows 运行分开记录。

配置统一使用 `VAA_` 环境变量；显式 API 地址、名称、Key 全部具备才连接 Agent；Key 不参与 repr。`.env` 只在启动时加载。应用运行依赖不含训练链，模型导出单独使用 `.export-venv`。

公共领域记录位于 `visual_ai_agent/models.py`，各开发 Agent 独占模块及测试目录；root 负责共享配置、集成与总文档。现行接口以 `docs/architecture.md` 与代码为准。
