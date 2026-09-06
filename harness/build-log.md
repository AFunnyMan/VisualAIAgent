# 实际进度与验证证据

本文件只记录观察到的事实。业务计划见 [PLANS](../PLANS.md)，原始正文见 [基线](../docs/project-plan-baseline.md)。时间使用 Asia/Shanghai（UTC+08:00）。

## 当前范围

用户于 2026-09-07 追加授权按文档完成整个项目，持续完成所有无需用户辅助测试的内容，允许多个子 Agent 并行；随后限定子 Agent 最高为 Sol。重要步骤正常提交及同步至既有远程，不发布部署。原仅文档任务已结束，以下历史记录保留。

## 阶段状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| 文档准备 | 完成 | 21 份 Markdown 和忽略规则已检查、提交并同步；最终交接记录随本条后续提交保存 |
| 00 项目基础 | 完成 | 锁定安装、关键依赖导入与 8 项基础测试通过；见下方实际命令 |
| 01 Agent 核心 | 进行中 | 七工具 SDK 已实现并离线验证；真实 API 待配置 |
| 02 本地视觉 | 进行中 | 官方模型已导出及参考对比；真实摄像头/Windows 待验 |
| 03 视觉记忆 | 进行中 | 状态机、SQLite、证据与清理已实现并离线验证 |
| 04 事件驱动 Agent | 进行中 | 关注、去重、限额及恢复已实现，真实闭环待验 |
| 05 用户界面 | 进行中 | 本地页面、后台队列及浏览器创建/取消已验证 |
| 06 验证与交接 | 进行中 | 总回归/文档/辅助验收脚本进行中；实机项不冒充通过 |

## 2026-09-07T02:19:56+08:00 — 初始现场与 Git 检查

- 观察：项目目录为空，运行环境为 Darwin arm64；初始无本地 Git 仓库。
- 远程：普通沙箱请求因 DNS 限制失败；获准网络访问后 `git ls-remote --symref ... HEAD` 与完整 `git ls-remote ...` 均退出 0 且无引用输出，说明此次检查未发现远程分支或标签。
- 操作：`git init -b main` 成功建立本地仓库；保留用户已有 Git 身份配置，未修改全局设置。
- 验证限制：未验证摄像头、Python 依赖、模型接口或产品运行。

## 2026-09-07 — 文档里程碑一：保留原始计划

- 变更：将最近完整计划正文保存为 docs/project-plan-baseline.md，去除会话的外层 proposed_plan 标签，正文作为历史基线保留。
- 检查：`git diff --cached --check` 退出 0；提交只包含基线文件。
- 提交：`3180005` — `docs: preserve approved project plan baseline`。
- 远程：已配置 origin 为用户提供的 HTTPS 仓库地址；截至此条记录尚未推送。
- 说明：基线中“不包含自动提交”的原历史约定由用户本次明确要求提交的指令替代；现行约定写入 AGENTS.md，不改写历史基线。
- 下一步：完成阶段文件与文档检查，提交第二个文档里程碑并验证远程同步。

## 2026-09-07T02:33:18+08:00 — 文档里程碑二：阶段计划与本地验证

- 变更：创建工作约定、目标、路线图、任务入口、README、架构/测试/来源说明、七个阶段计划以及上下文/审查/产物记录规范；添加 .gitignore。
- 实际检查：一次性 Python 检查退出 0；当时共 20 个 Markdown、69 个有效本地链接、7 个有必要章节的阶段计划；无应用文件、无阶段 context，无检测到的疑似凭据。
- 基线：与提交 3180005 的文件逐字节一致，SHA-256 为 c28566e288f0ec7f3018699e951a8173166f21b03322719e8ae00e9d6c25692c。
- 忽略规则：git check-ignore 对数据库、模型、.env、虚拟环境、私人产物五种代表路径全部匹配，退出 0。
- 审查：[documentation-bootstrap](code_review/documentation-bootstrap.md)，未发现当前文档范围内阻断提交的问题；新增审查和日志在暂存前再次检查。
- 未运行：所有软件测试、真实模型 API、摄像头与双平台产品验证；原因是本次仅授权文档/Git。
- 下一步：提交文档脚手架，正常推送并核对远程引用，然后记录交接并停止。

## 2026-09-07T02:35:01+08:00 — 暂存清单发现与修复

- 第二个提交：a914a6a — docs: add phased implementation workflow and project guidance，包含 14 个文件。此提交的 git diff --cached --check 退出 0。
- 发现：七份阶段文档未进入提交；git check-ignore -v 显示通用 build/ 规则同时匹配 harness/build。此前 21 个 Markdown/71 个本地链接检查通过仅证明工作树文件完整，不代表提交完整。
- 修复：将构建产物忽略改为根目录 /build/ 与 /dist/；不强制暂存被忽略文件，不绕过隐私规则。
- 追加验证：最终提交前核对七份阶段文档及全部 Markdown 均进入索引，并保留数据库/密钥/模型忽略策略。审查记录同步保留此问题及修复理由。
- 下一步：提交修复及阶段文件，推送完整文档后记录交接。

## 2026-09-07T02:37:58+08:00 — 文档验证、远程同步与交接

- 提交：e56fd5f — docs: include all phase plans and verify tracked artifacts，纳入全部七份阶段计划并修复忽略规则。
- 最终文档检查：一次性检查退出 0；21 个 Markdown、71 个有效本地链接、7 个阶段计划、22 个受版本控制文件。所有本地文件链接目标均在索引中，原始基线保持不变。
- 忽略检查：阶段文档不再被忽略；根目录构建产物、数据库、模型、Key、虚拟环境和私人产物仍被忽略。git diff --cached --check 退出 0。
- 推送过程：HTTPS 因缺少可读取的用户名凭据失败；SSH 22 端口连接关闭；使用现有 SSH 身份通过 GitHub 官方 443 端口认证为 AFunnyMan。
- 远程配置：origin 读取地址保留用户提供的 HTTPS 地址，推送地址为 ssh://git@ssh.github.com:443/AFunnyMan/VisualAIAgent.git。未创建密钥，未更改全局 SSH 或凭据配置。
- 已观察结果：git push -u origin main 退出 0，远程新建 main，并建立 origin/main 跟踪；当前产品代码和测试仍未实施。
- 交接：本次文档/Git 任务完成。阶段 00—06 全部未开始。下一步等待用户主动启动阶段 00 或指定其他已满足前置条件的任务，不自动安装依赖、下载模型或运行 API/摄像头。
- 记录范围：此条描述截至 e56fd5f 推送的证据；本条与最终文档审查补充会再形成一个交接提交。最终提交后的远程引用与工作树检查在交付回复中报告。

## 状态维护约定

状态使用未开始、进行中、阻塞、完成。计划存在不代表阶段开始；模拟验证不代表真实模型或实机通过。只有全部必需验收和审查证据齐全才能完成阶段。

后续活动记录追加时间、授权范围、实际命令、结果、相关提交/文件、限制和下一步。更正旧结果追加说明，不删除失败记录。无证据不写通过；文档完成不代表产品完成。

## 2026-09-07T02:53:00+08:00 — 全路线授权与开发启动

- 验证起点：工作树干净，HEAD 为 f5733db；未有业务代码。已读规划、架构、各阶段与测试要求。
- 授权更新：AGENTS/PLANS 记录用户全路线实施的新指令，原始计划基线不修改。
- 环境：项目内安装 uv 0.12.10，下载 CPython 3.11.16；依赖同步进行中，尚不记录安装或测试通过。
- 分工：Sol 子 Agent 分别处理视觉、记忆/关注、SDK；root 负责基础、运行时、页面、交接与集成复验。子任务共享领域类型，独占实现文件。
- 限制：未打开摄像头、未使用真实模型 Key、无 Windows 实机。上述项目不会以离线模拟代替。

## 2026-09-07T02:58:00+08:00 — 阶段 00 本地里程碑

- `UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv sync --locked --group dev` 退出 0：123 个锁定包（包含仅解析的导出组），主环境安装/检查 81 个包，未安装训练链。
- `.venv/bin/pytest tests/foundation` 退出 0：8 passed；验证无配置未连接、配置范围、时间戳必须含时区、进程锁互斥与释放。
- 基础文件 Ruff 自动修正 import/format 后检查；关键 `agents/cv2/onnxruntime/supervision/streamlit` 实际导入成功。沙箱内系统缓存不可写有警告，导入退出 0，未访问摄像头/真实 API。
- `scripts/doctor.py` 退出 0：Darwin 25.6.0 arm64、Python 3.11.16；Agent 未配置，模型此时尚未存在。包版本由 uv.lock 锁定。
- 远程 `git ls-remote ssh://git@ssh.github.com:443/AFunnyMan/VisualAIAgent.git refs/heads/main` 确認仍为 f5733db，未发现未知远程更新。
- 审查：[阶段 00](code_review/phase-00-foundation.md)。后续业务模块正在独立开发，本提交只纳入基础已验证内容。

## 2026-09-07T03:08:00+08:00 — 并行模块与集成验证

- 基础提交：36f196e。后续模块尚在集成修改，不将正在变化的测试数写成最终验收。
- 首轮全仓 `.venv/bin/pytest`：51 passed（02:59 前后）；新增 UI 测试发现 fragment-only rerun 在整页执行时非法，已改用可用于两种上下文的 st.rerun()，定向 6 项测试通过。
- 后续测试发现“缺模型”用例依赖工作树恰好无模型，模型下载后会失效；已改为显式临时缺失路径，避免环境相关误判。
- 浏览器：本机 127.0.0.1:8501 页面已实际加载，未连接状态/禁用聊天可见，在专用 ui-review 数据目录创建关注后取消，页面显示已取消。没有点击摄像头启动，没有调用真实 API。
- 模型：官方 YOLO26n 导出成功。首次参考预测默认 rect=True 导致与固定正方形 ONNX 输入有差异；统一 rect=False 后五框最小 IoU 0.99999815，最大框差 0.000610px；三类公开图业务适配对比完成。详见阶段02 context。
- 审查追加修复：恢复 processing 任务重新入队；切换采样档重置确认间隔；当前时间倒退标未知；截图引用清空加索引和非空条件，避免每帧更新全部历史；正在修三轮工具通知成功后错误降级的 SDK 边界。
- 独立真实 API 测试需显式 opt-in；摄像头、固定场景、Windows 11 与 30 分钟实机性能仍未执行。

## 2026-09-07T03:17:00+08:00 — 源码功能里程碑与最终离线回归

- `.venv/bin/ruff check .` 和 `.venv/bin/ruff format --check .` 均退出 0；`.venv/bin/pytest` 退出 0：80 passed，3 deselected，实际耗时 6.89 秒。
- `.venv/bin/pytest tests/agent -m live_api -rs` 退出 0：3 skipped、12 deselected，原因是未设置显式付费开关；真实 API 未执行，不能算真实 Agent 验收通过。
- 文档检查：33 份项目 Markdown、111 个本地链接目标存在；`sh -n scripts/start-mac.sh` 与 `git diff --check` 退出 0。原始计划基线 SHA-256 仍为 c28566e288f0ec7f3018699e951a8173166f21b03322719e8ae00e9d6c25692c。
- 模型短测：`.venv/bin/python scripts/benchmark.py --image harness/artifacts/public-cup.jpg --samples 30` 退出 0；真实 ONNX CPU 连续图片推理 30 次，p50 56.08 ms、p95 90.93 ms，进程峰值 RSS 271.36 MiB。连续全速 CPU 平均 175.97%（单核 100%），不代表低频摄像头CPU；只验证短样本。证据本地路径 `data/acceptance/20260906T190659Z-f39d7d/summary.json`。
- `scripts/offline_soak.py --image harness/artifacts/public-cup.jpg --duration 600` 已启动，使用真实本地 ONNX 与明确回放输入、无关关注和零调用哨兵；截至本条仍在运行，结果待追加。
- 架构、README、测试说明、用户辅助验收、第三方原许可证与模型清单已纳入交接。GitHub 原生 macOS/Windows 离线工作流已配置，尚未宣称远程运行通过。
- 软件实现与离线检查已完成；阶段 01—06 保留真实模型/摄像头/Windows 等验收项为进行中，未删改原验收条件。审查见各阶段 code_review 和阶段05集成审查。

- 暂存检查补充：原样保存的 NumPy/PyTorch 许可证包含上游尾随空格/文件尾空行，首次 staged diff-check 因此失败。使用仅作用于 third_party/licenses 的 .gitattributes 保留原许可证字节，业务源码仍执行正常空白检查；复验后再提交。
