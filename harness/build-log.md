# 实际进度与验证证据

本文件只记录观察到的事实。业务计划见 [PLANS](../PLANS.md)，原始正文见 [基线](../docs/project-plan-baseline.md)。时间使用 Asia/Shanghai（UTC+08:00）。

## 当前范围

用户于 2026-09-07 追加授权按文档完成整个项目，持续完成所有无需用户辅助测试的内容，允许多个子 Agent 并行；随后限定子 Agent 最高为 Sol。重要步骤正常提交及同步至既有远程，不发布部署。原仅文档任务已结束，以下历史记录保留。

## 阶段状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| 文档准备 | 完成 | 21 份 Markdown 和忽略规则已检查、提交并同步；最终交接记录随本条后续提交保存 |
| 00 项目基础 | 完成 | 锁定安装、关键依赖导入与 8 项基础测试通过；见下方实际命令 |
| 01 Agent 核心 | 完成 | 离线边界与真实千问查询/创建/列表/取消/事件用例通过 |
| 02 本地视觉 | 进行中 | 实现/模型/公开图/CI通过；仅待USB与Windows11实机 |
| 03 视觉记忆 | 进行中 | 状态机/数据库/证据与失败恢复通过；待摄像头联合验收 |
| 04 事件驱动 Agent | 进行中 | 真实千问与视频提醒闭环通过；USB联合实测待补齐 |
| 05 用户界面 | 进行中 | 页面/浏览器真实千问创建取消通过；仅待USB完整演示 |
| 06 验证与交接 | 进行中 | 软件/真实API/回放验证持续补齐；用户辅助USB及Windows11实机待做 |

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

## 2026-09-07T03:25:00+08:00 — 远程 CI 首轮与诊断改进

- 源码提交 7692ac9 已正常推送，远程引用与本地一致。
- GitHub run 34054619017：macOS 托管 runner 安装、Ruff、格式、pytest、doctor 全部成功；Windows 安装和静态检查成功，pytest 失败。
- 公开 API 和未登录浏览器只能读取“exit code 1”，详细日志需要登录。为在无需用户凭据条件下继续修复，加入 ci_checks.py：保持原测试不变，仅在 CI 失败时把有界的无凭据测试失败详情写入可读检查注解；不扩大 workflow 权限。
- 十分钟实际回放已结束，601.12 秒、120 次检查、无无效采样、Agent/model 均零调用，峰值 RSS 353.94 MiB，平均 CPU 12.74%（单核100%），无运行错误。证据 `harness/artifacts/soak-5e0b530a/soak-summary.json`。该进程在阶段02最终停止边界修复前启动，验证当时的正常等待/推理链路；停止边界由后续失败用例验证。
- `sh scripts/start-mac.sh` 已实际完成锁定同步并启动最新页面，浏览器确认未连接页面正常；退出时服务返回0，未启用摄像头或真实API。

## 2026-09-07T03:31:00+08:00 — Windows CI 根因与并发边界修复

- CI 诊断提交 6a9e203 已同步。run 34054875358 的注解指出：Windows Python 的 asyncio Proactor 用 socket.socketpair() 建立内部唤醒管道，而该平台的标准库用 loopback connect 实现，被离线 fixture 一并拦截。
- 修复测试隔离：仅在当前线程调用标准库 socketpair 构造期间允许其内部连接；普通 loopback/外部连接仍拒绝。新增用例实际传递唤醒字节并确认构造后两类连接仍拒绝。未放开真实 API，也未删去任何用例。
- 集成复查发现每小时cleanup提交事务后释放锁再扫描文件，可能删除并发新建截图；将锁覆盖整个数据库/扫描/删除流程，新增线程事件控制的确定性交错回归，证据引用与文件均保留。
- 摄像头停止超时由VisionWorker报告后，runtime之前仍写stopped；改为返回明确失败并保留error，防止UI误称已释放。新增停止超时的运行时回归。
- `.venv/bin/pytest` 退出0：83 passed，3 deselected，7.53秒；Ruff及格式检查均退出0。后续再次推送验证Windows，尚不预填通过。

## 2026-09-07T03:36:00+08:00 — 软件交付与双平台 CI 通过

- 修复提交 ebe241a 已推送。GitHub run [34055205112](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34055205112) 对该提交的 Windows 和 macOS 两个 job 均 completed/success：锁定安装、静态检查、格式检查、离线测试与 doctor 均通过。
- 本机最终软件回归为83 passed、3 deselected；真实API显式检查为3 skipped（无付费开关/凭据），不算真实通过。无需用户辅助的源码、模型准备、离线测试、十分钟回放、本机浏览器、启动脚本、文档和CI工作已完成。
- 最后跟踪文件检查：121个项目本地Markdown链接目标均存在且在Git索引中；无被跟踪的数据库、模型权重、环境、Key或运行数据。原始基线SHA保持不变；git diff --check通过。
- 运行收尾：两个临时本机Streamlit验证进程均正常停止，回放进程退出0，临时浏览器页已关闭；没有打开真实摄像头，没有使用真实付费模型。
- 外部验收保持未完成：用户配置真实API并运行三类工具场景，USB三类各10次放入/移出及遮挡/故障/恢复，Apple芯片Mac与Windows11各30分钟实机性能，完整Agent提醒延迟。托管Windows CI不代替Windows11 USB验收。
- 审查：[最终软件交付](code_review/phase-06-handoff.md)。后续只需按 [用户辅助验收](../docs/user-acceptance.md) 补齐证据并对发现问题定向修复，不重建已完成的代码或篡改验收条件。
- 本条及最终状态文档将形成仅文档的交接提交；代码已由ebe241a双平台CI验证，文档提交不重复触发同一套CI。最终提交与远程一致性在交付回复前核对。

## 2026-09-07T04:20:00+08:00 — 百炼授权、真实失败与修复

- 用户提供百炼Key，授权查官方文档、用公开视频替代摄像头完成可自动流程。Key写入被忽略的.env，权限0600，未写入版本库、命令输出或摘要。接入地址/模型/官方依据见 [千问说明](../docs/qwen.md)。
- 北京Chat兼容端点qwen-flash短文本探测成功：1次请求，input14/output2。随后 `VAA_RUN_LIVE_API=1 .venv/bin/pytest tests/agent/test_agent_live.py -m live_api -q --tb=short --junitxml=harness/artifacts/qwen-live-initial.xml`：1 passed、2 failed、6.89秒。实际10次请求、11419 Tokens。取消场景模型零工具却声称成功；事件场景start=end，修正后耗尽三轮，正确降级但不能算Agent成功。证据qwen-live-initial.json/xml（忽略目录）。
- 修复首轮required与零工具失败保护，提供当前aware时间及事件检索窗口，不放宽三轮、不改原用例。相同入口输出到qwen-live-fixed.xml复验3 passed、9.50秒；11次请求，input13916/output454，全部usage完整。另新增零工具假取消与反序事件失败回归。
- 浏览器127.0.0.1:8502使用独立qwen-ui目录，真实千问创建杯子关注、自然语言取消，实际工具及数据库状态均一致；页面显示已取消。4次请求，input4619/output158。临时页面关闭，Streamlit退出0，没有启动USB。
- 标准1s/省电2s原速实拍瓶子视频均通过出现与EOF测试；杯/手机候选素材未检出，不算通过。素材许可、hash、命令和证据见 [阶段02context](context/phase-02-local-vision.md)。
- 完整runtime视频流程首轮已完成出现与合成空白missing的真实Agent提醒（2.318s/2.567s）、断流7秒不误报、重复事件无新提醒、重启保留历史和waiting任务。30分钟已连接模型等待从04:06开始，截至本条仍在运行，不能预填通过。
- 对答案内容追加审查发现最后位置漏区域、某次历史答复在没有missing记录时附加了未检测到类型说明。增强事实引用提示，并将live_acceptance扩展为捕获真实工具返回、检查类别/时间/区域/证据锚点、拒绝补写不存在missing事件，以及事件usage和HTTP请求对账。早期脚本的passed不能替代追加文本验收；后续结果另记。

## 2026-09-07T04:22:00+08:00 — 严格视频闭环复验

- `VAA_RUN_LIVE_API=1 .venv/bin/python scripts/live_acceptance.py --video harness/artifacts/wikimedia-squeezing-open-bottle.webm --category bottle --idle-category 'cell phone' --idle-seconds 5` 最终退出0；证据 `harness/artifacts/live-video-5102f9b9/summary.json`。实际工具返回与答案类别/时间/区域/证据锚点匹配，没有补写不存在missing事件；HTTP请求全部为文字和7个工具，和持久化run请求数对账一致。
- 两条Agent来源提醒分别为真实视频appeared、显式合成空白missing，确认到通知延迟2.776502秒/3.250473秒；断流7秒不产生missing，重启恢复历史和waiting任务，重复事件不重发。5秒等待仅作为修复定向复验，30分钟结果由另一个尚在运行的进程单独补记。
- 更早 `live-video-aff6c43a` 的脚本当时退出0，但人工复查发现历史回复附加未发生的missing说明；原证据保留，不作为最终文本通过记录。当前断言已能拒绝该问题，未通过删除标准处理。

## 2026-09-07T04:25:00+08:00 — 最终软件回归与真实API确认

- `VAA_RUN_LIVE_API=1 .venv/bin/pytest tests/agent/test_agent_live.py -m live_api -q --tb=short --junitxml=harness/artifacts/qwen-live-final.xml`：3 passed、9.17秒，11次请求，input15228/output484，证据qwen-live-final.json/xml。
- 补齐find_object与search_events两条证据核验路径都必须先复查scene；两条反序失败用例均拒绝Agent通知。此追加只修复异常顺序，正常真实视频调用路径不变。
- 本机 `.venv/bin/ruff check .`、`.venv/bin/ruff format --check .` 退出0，82份Python已格式化；`.venv/bin/pytest -q`：92 passed、3 deselected、6.73秒。原live用例未被删除或降低标准。
- 远程main只读核对仍为303a94c，无未知新提交；以下里程碑将正常推送并运行macOS/Windows离线CI。30分钟待机仍运行，稍后追加实际结果。

- 同期本地故障入口 `.venv/bin/python scripts/failure_acceptance.py --video harness/artifacts/wikimedia-squeezing-open-bottle.webm` 退出0：真实SDK收到本机TCP连接拒绝后1次请求、无重试、fallback成功；额度0时请求数0、fallback成功；两种情况提醒后真实视频仍fresh/current。无外部云请求，非百炼故障或系统断网；证据failure-acceptance-0953b785/summary.json，审查见 [回放故障](code_review/replay-failure-review.md)。

## 2026-09-07T04:29:00+08:00 — 双平台CI与请求费用核对

- 里程碑c58a169已正常同步。公开GitHub API确认 [run 34058017128](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34058017128) 的Windows/macOS两个job均completed/success；包含锁定安装、Ruff、格式、92项离线用例和doctor。托管Windows仍不代替Windows11 USB实机。
- 提交前133个Markdown本地链接目标存在且跟踪；全部跟踪文件与本地真实Key做精确字节对照，无凭据泄露；.env被忽略、mode0600；无运行数据库/视频/权重/环境纳入Git；计划基线SHA不变。首次diff-check发现新审查文档一处尾随空格，去除后复验通过再提交。
- 本轮所有百炼请求（含初始失败、修复复验、三个完整视频短流程及UI，不漏掉失败成本）累计115次，input163408/output5799 Tokens。按qwen-flash北京短上下文原价输入0.15/输出1.5元每百万Token估算0.0332097元，非实际扣款且不含开发Agent用量；缓存/赠送额度以账户账单为准。明细在harness/artifacts/qwen-test-cost-summary.json，来源见千问说明。
- 30分钟进程继续运行，已超过20分钟，等待期新增Agent及HTTP请求均为0；全部付费操作已结束，下一条只追加运行完成证据。

## 2026-09-07T04:38:00+08:00 — 三十分钟等待完成与最终交接

- `VAA_RUN_LIVE_API=1 .venv/bin/python scripts/live_acceptance.py --video harness/artifacts/wikimedia-squeezing-open-bottle.webm --category bottle --idle-category 'cell phone' --idle-seconds 1800` 正常退出0。真实视频与ONNX、实际配置千问Agent保持连接，完整等待1800.013秒；360次检查、invalid0、新Agent运行0、新HTTP请求0，峰值RSS356.90625MiB，平均CPU36.5567%（单核100%）。
- 证据harness/artifacts/live-video-0bdb843d/summary.json、idle-samples.json、completion-audit.json。每5秒抓取的推理耗时快照p50为74.93ms、p95为81.91ms；这是快照采样，不冒充逐帧性能分布。长进程在最后一次答案提示增强之前启动，仅证明未改动的视觉/等待链路；最终文本事实核验以live-video-5102f9b9严格短测为准。
- 运行后独立核对：26次HTTP请求与12条Agent运行记录的26次请求完全一致，usage全部完整；两条通知仍为Agent来源，无重复。runtime_error为null，最后观察状态为stopped，进程退出0。所有临时UI及回放进程均已结束，没有访问物理摄像头。
- 软件修复代码固定于c58a169并已双平台CI通过；本条及相关结果页仅文档更新，不重复执行相同软件检查。最终交接审查见 [千问与视频交接](code_review/qwen-video-handoff.md)。完成后正常提交同步并核对远程引用。
- 剩余用户辅助：真实USB三类各10次放入/移出及遮挡/多个候选/断开恢复，Apple芯片Mac和Windows11原生USB各30分钟，完整UI实物提醒。公开视频只有瓶子素材通过，不能声称手机/杯子视频或三类现场识别率达标；其他未执行实机项不关闭。

## 2026-09-07T05:32:00+08:00 — 调用审计与九段视频扩展测评

- 用户要求判断大模型调用次数并扩大不同类型视频测试。两个Sol开发子Agent分别搜集/复核手机与杯子素材，主Agent审计请求、测试瓶子/负样本并统一复验；没有新增百炼请求，没有打开USB或更改生产权重/0.35阈值。
- 历史严格闭环12个Agent运行/26次请求：10次用户操作各2次请求，2次事件各3次；30分钟等待新增0。115次是包含初始失败和多轮复验的开发验证累计，不是运行半小时的请求量。判断与节省方向见 [评估](../docs/recognition-assessment-2026-09-07.md)。
- 下载9段新公开视频：3手机、3不同杯型、2瓶子、1行人负样本。来源、许可、SHA及最终参数保存在 [评估清单](evaluations/video-survey-20260907.json)，视频/图片/完整输出仍被忽略。OpenCV负样本的独立原视频许可未确定，不以仓库许可替代，不分发视频。
- 初轮随机seek在rice-phone OGV中发现同一POS_MSEC随跳转历史返回不同像素。新增scripts/recognition_survey.py改为从头顺序解码、CFR帧索引采样，并全部重跑；命令为 `.venv/bin/python scripts/recognition_survey.py --video <清单中的文件> --output harness/artifacts/video-survey/<id>-sequential --duration 190`，负样本duration30。9段均退出0，最终488个每秒采样帧；开发Agent逐图复核53帧，未知/黑场/完全遮挡不算漏检。此前探索复核不作最终定量。
- 实测识别不足：夜间横瓶抽查1/6命中、近景完整变色马克杯0/6、透明茶杯3/6、小手机工位0/5。都是小样本的可见帧命中，不是整体识别率。厨房大桶在36—38秒被持续当杯子并形成38秒appeared；桌面手机仍可见却在15秒形成missing。保持现有“持续未检测到”事实措辞，也不因此声称实物移出正确。
- 定向复验 `.venv/bin/python scripts/recognition_survey.py --video harness/artifacts/video-survey/videos/rice-phone.ogv --output harness/artifacts/video-survey/phone-missing-sequential --start 7 --duration 15` 退出0，出现9秒/未检测到15秒。与完整顺序回放重叠15/15样本的检测和帧索引完全一致，复核图确认手机仍在。
- 独立官方PT/ONNX对照同一完整杯失败帧，最高cup得分0.01264465/约0.012646，框坐标一致，生产0.35下均无框；仅独立诊断查看低分，未改变生产配置。不能将单帧对照扩大为全部管线无bug。
- 最终 `.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`、`git diff --check` 均退出0；`.venv/bin/pytest -q` 为92 passed、3 deselected、7.35秒。新评估文档本地链接、9段素材哈希和原始计划基线SHA均核对通过。
- 路线图修正为本地识别质量仍有未解决问题，不能再概括成仅剩用户实机；没有降低90%目标。审查见 [扩展视频识别](code_review/recognition-survey-review.md)。本轮是完成诊断与测评，持续误分类/漏检尚未修复，USB和Windows11实机项继续开放。
- 提交前只读核对远程main仍为a8b4fcf，无未知新提交。以下将正常提交同步；远程CI结果在实际返回后核对，不预填通过。

## 2026-09-07T05:37:44+08:00 — 扩展测评同步与CI

- 测评里程碑c025f25已正常推送。GitHub [run34061467134](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34061467134) 的macOS和Windows两个job均completed/success；本机92项离线测试通过。未产生新的百炼请求。该结果验证源码与工具检查，不关闭报告中的识别质量问题或实机验收。
- 本条为仅文档记录，使用skip ci提交；源码检查结果仍对应c025f25。

## 2026-09-07T10:11:20+08:00 — 本地识别改进分析与定向对照

- 用户要求分析改善本地识别率。复查生产采集分辨率、固定形状模型适配器和事件状态机，并查询官方YOLO26型号/双检测头/切片说明。分析见 [改进方案](../docs/recognition-improvement-options.md)，尚未采用候选架构或修改生产参数。
- 对5个既有失败点顺序解码，实际运行 `.venv/bin/python harness/artifacts/recognition-options/crop_probe.py` 与 `.export-venv/bin/python harness/artifacts/recognition-options/size_probe.py`，均退出0。前者全图加4个固定60%宽高裁剪，共25次现有ONNX推理；后者相同YOLO26n PT在640/960/1280各运行，共15次推理。均0.35阈值、无云端请求。
- 裁剪未恢复四个漏检点，大桶误杯得分约0.60升至0.82，夜间瓶子产生约0.36的错误手机框；标框图已查看。更大输入也未恢复四个漏检点，桶误报在1280下消失。该结果只用于失败诊断，不是整体验证集或速度数据；全部脚本、完整输出和图保存在忽略目录。
- 建议优先对照YOLO26s640的实际能力与CPU代价，再结合高质量采集、按需ROI和事件前新鲜帧复检，最后校准分类阈值。采用s需更新现有锁定n的架构决定；本轮没有替换模型、不训练、不改变三次/五秒验收契约。无需对仅文档修改重复完整软件回归。

## 2026-09-07T10:21:53+08:00 — 识别改进资料复查与检测头对照

- 用户要求继续查资料寻找更优策略。主Agent核验YOLO26/YOLOE官方文档、已安装8.4.142源码、ByteTrack论文和Frigate设计；一个Sol子Agent独立比较MobileCLIP/CLIP/SigLIP2官方资料。未下载新模型、未训练、未调用云端视觉或百炼。结果见 [策略研究](../docs/recognition-strategy-research.md)。
- 发现同一YOLO26n权重支持两套检测头。实际执行 `.export-venv/bin/python harness/artifacts/recognition-options/head_probe.py` 退出0：五个既有失败点分别运行端到端与传统头，640/0.35不变，共10次官方PT推理；使用独立模型实例，实际end2end标志分别True/False。桌面手机15秒由无框变为正确框0.48151，图已查看；桶误杯由0.60068变为0.75149，另外三个目标仍漏检。不是ONNX导出或整体验收，生产模型未变。摘要见 [检测头对照](evaluations/head-probe-20260907.json)。
- 新候选包括按需轻量语义拒识、有限词表YOLOE全图复查、低分候选关联与静止物体定期确认。资料依据与项目推论分开记录：已有框复核不能找回无框漏检；需独立全图检查；未知率和漏报必须与误报一起评价。新组件CPU/ONNX兼容与三类效果尚未实测，不宣称已经优于基线。
- 本轮仅新增研究文档和脱敏摘要，不修改业务代码、采样/事件契约或现行架构；无需重跑不受影响的92项离线软件用例。提交前核验JSON、链接、差异和敏感信息。

## 2026-09-07T11:00:49+08:00 — 识别策略全轮离线对照完成

- 按用户“开始上述测试”，主Agent与3个Sol并行完成5检测配置（n端到端/n传统/s端到端/s传统/YOLOE）、MobileCLIP2-S0和短期低分关联。未训练、未调用百炼或USB、未更换生产模型/依赖锁；实验权重、图片、完整日志与隔离环境均在忽略目录。
- 实际导出并检查官方s权重、n/s传统内嵌NMS及s端到端。`.export-venv/bin/python scripts/prepare_detector_comparison.py`完成，随后`validate_detector_comparison.py`对5个原始顺序解码帧×4配置，20/20框数/类别一致，最大坐标差0.000245px、分数差3.82e-6。更早拼图JPEG对照另存，不作为原帧结果。
- `.venv/bin/python scripts/detector_comparison.py --profiles harness/artifacts/ab-20260907/detectors/profiles.json --videos harness/evaluations/video-survey-20260907.json --output harness/artifacts/ab-20260907/video-results`与对应`--images .../holdout/manifest.json --output .../image-results`均退出0。四YOLO26各488视频帧+30图；YOLOE独立脚本也各488/30。五配置采样时点/帧索引一致，n端到端检出数逐视频复现原基线。
- 新COCO2017 val固定seed20260907，推理前选定30图及全部295原标注；图片hash/尺寸/原始类别ID验证通过。三类GT52实例；固定.35/IoU.5原始计分：n端到端20TP/4FP/32FN，n传统21/4/31，s端到端27/7/25，s传统30/10/22，YOLOE21/8/31。非mAP/非部署域/非模型未见过数据；部分FP有标注/类别歧义，不修改GT。
- CLIP开发帧固定23提示/10组、扩边20%、margin.015，123个真实heldout候选完成复核；s传统由30/10/22降为19/3/33，其他配置亦误拒明显。两配置各29个关键视频时点中，桶3次误杯全拒，但有真手机误拒与大量无候选。图像塔重参数化及ONNX导出通过123候选验证，接受决定不一致0，最大特征差1.35e-6。
- YOLOE固定8词固化ONNX，保留mask分支。审查发现默认rect/NMS不一致，旧rect-auto结果保留，显式square/agnostic NMS后重跑；53帧PT与ORT的25框全匹配，纯ORT适配器也25/25，最低IoU.99427。桶仍误杯、工业装置误瓶、完整杯与儿童杯仍漏检。
- `.venv/bin/python scripts/association_probe.py --input harness/artifacts/ab-20260907/video-results --output harness/artifacts/ab-20260907/association.json`退出0。固定high.35/low.1/IoU.3/高分后最多2秒，36组视频回放；不能无当前候选复制旧框或无限续期。补帧伴随新增错误/波动事件，未纳入生产，不当完整ByteTrack或事件准确率。
- 所有并行推理结束后，独立进程四检测器→YOLOE→CLIP串行benchmark，5预热/30次。p50/p95 ms：n45.33/47.68、n传统45.54/48.41、s137.36/141.16、s传统137.57/146.19、YOLOE67.51/71.60、CLIP单候选完整复核46.57/48.02。RSS与计时范围分别见报告，不当稳态CPU/30分钟/Windows/8GB实机证明。
- 核验Apple官方LICENSE_MODELS，模型限研究且排除产品开发，不能以MIT代码许可推定产品可用；MobileCLIP权重不纳入产品，YOLOE文本准备所用编码器许可链需另核验。现有候选均未全面胜出；保留生产配置，识别质量与USB/Windows实机仍开放。
- 结果：[报告](../docs/recognition-ab-results-2026-09-07.md)、[可复算预测](evaluations/recognition-ab-20260907.json)、[原始标注子集](evaluations/recognition-holdout-20260907.json)、[审查](code_review/recognition-ab-review.md)。从归档预测复算五检测器/四复核的分类统计全部一致；修正归档manifest中selection相对路径并记录SHA。
- 最终`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`通过（98份Python）；`.venv/bin/pytest -q`100 passed、3 deselected、7.13秒，新增8项计分/时序失败用例。计划基线SHA不变，链接和凭据模式检查通过。远程main只读核验仍9870c67，无未知提交；本里程碑正常提交同步，远程CI在实际返回后追加。

## 2026-09-07T11:04:46+08:00 — 识别对照同步与双平台CI

- 里程碑60b598f已正常推送。公开GitHub API确认 [run34078252826](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34078252826) 的macOS与Windows两job均completed/success，head SHA为60b598f9195a7920e717af0ba2df307def9dc89a。源码本机100项离线用例通过，远程锁定依赖、Ruff/格式、离线检查及doctor通过。
- 此记录仅文档更新，使用skip ci提交；未增加百炼调用，未改变模型采用决定，也不关闭USB/Windows11实机及识别质量问题。


## 2026-09-07T17:20:31+08:00 — 第二轮资料驱动识别改进实验

- 按用户要求继续查官方资料并实际尝试。主任务完成分类阈值/重叠切片/YOLO26m探索，两个Sol分别完成RF-DETR Nano和固定输入增强，一个Luna建立新60图，另一个Sol独立审查。没有新增百炼请求、USB访问、训练或生产模型/依赖锁/事件契约改动。
- 官方依据为Ultralytics分类指标/TTA、OpenCV CLAHE、SAHI切片、RF-DETR实现与导出，参数及边界见 [第二轮报告](../docs/recognition-v2-results-2026-09-07.md)。保留原始计划SHA，未降低90%实机成功率要求。
- 新60图以seed20260908在推理前选定，排除旧30图，手机/杯/瓶各16图加12负例。原标注含155个非crowd业务实例+1个bottle crowd，所有原图SHA和尺寸核验。旧30图明确转为开发/校准，不再称未看过的留出集。审查修复构建脚本恒真的排除断言，实际产物独立核对无交集、60唯一，未修改原GT。
- 实际 `.venv/bin/python scripts/recognition_input_trial.py calibrate --profiles harness/artifacts/ab-20260907/detectors/profiles.json --manifest harness/evaluations/recognition-holdout-20260907.json --output harness/artifacts/recognition-v2-20260907/local/calibration` 完成；重新提取完整>=.05候选，按旧n .35分类FP预算选阈值。n瓶/杯/手机=.20/.35/.45，s=.35/.50/.45；冻结后以images模式、validation/manifest.json和local/calibration/frozen.json运行新图，输出local/validation。s-sliced同一入口完成新60、300次模型推理；旧30切片另保存development-sliced.json，共150次。review分支未运行，不写成通过。
- `.export-venv/bin/python harness/artifacts/recognition-v2-20260907/local/prepare_m.py` 完成官方下载与640传统头ONNX导出；同目录validate_m.py对5个原始诊断帧PT/ORT框数/类别一致，最大坐标差.00012207px、分数差7.15e-7。`.venv/bin/python .../local/m_trial.py`执行旧集FP预算校准，阈值.75/.35/.65在m自身新图推理前持久化。m为中途追加探索，不把文件mtime不足以证明的“早于读取其他模型输出”写成预注册事实。
- RF隔离环境rfdetr1.10.0/Torch2.14.0，官方Nano384/.35、不训练；run_rfdetr.py的images/videos/videos-all入口完成旧30、新60、53复核和全488时点。官方CPU ONNX导出成功，纯NumPy/Pillow/ORT适配器run_onnx.py在旧30的262框上类别/数量一致、最小IoU.99735、最大坐标差1.179px。新图和完整视频准确性来自PT，ORT仅此一致性和独立CPU计时，不冒称全视频ORT验收。来源/模型哈希与Apache-2.0许可保存在归档；初次包下载超时后从同官方URL续传完成，MD5通过。
- augmentation/run_augmentation_probe.py完成n/s×CLAHE/gamma/原图翻转六条固定路线旧30+53复核及新60；full-video入口继续s+gamma、n+CLAHE、n+flip各488时点。目录复用失败的空目录保留，修复后新目录完整成功。审查将flip单视频total_inferences由帧数修正为实际调用和，9项合计976，与根总数一致；不是重新推理或改预测。
- `.venv/bin/python scripts/recognition_input_trial.py videos --variant s-calibrated --profiles harness/artifacts/ab-20260907/detectors/profiles.json --manifest harness/evaluations/video-survey-20260907.json --calibration harness/artifacts/recognition-v2-20260907/local/calibration/frozen.json --output harness/artifacts/recognition-v2-20260907/local/s-calibrated-videos` 完成488时点。`scripts/detector_comparison.py --profiles .../local/profiles.json --only m-traditional --videos harness/evaluations/video-survey-20260907.json --output .../local/m-videos`也完成488；所有视频按CFR帧索引顺序解码。RF另从存量预测用真实EventStateMachine回放三次/五秒事件，不改契约。
- 新60图14配置完整计分见 [可复算预测](evaluations/recognition-v2-20260907.json)。当前n=56TP/21FP/99FN，s=.35为77/23/78，s分类阈值76/18/79，RF90/41/65，m探索83/27/72，切片97/62/58。独立审查逐组重算categories/totals均一致，14源文件SHA匹配。新图选择、标注、原图路径与selection SHA已归档；图片/完整视频/权重/日志/隔离环境不进Git。
- 视频实质收益：m变色杯33/35、工业手机29/30；RF变色杯35/35、儿童杯3/5、夜间瓶7/8，关键拼图已目检。RF仍有beer杯误报32帧和rice错误杯事件；s校准变色杯仍在却26秒missing；CLAHE丢失rice原有手机事件，flip新增beer杯错误事件。没有把检出占比当准确率，没有据此更换生产模型。mug/儿童杯/工业手机的改善不能推断三类全部达标。
- 全部重推理结束后，各自独立进程串行bench，固定旧图000000231831.jpg、2线程、5预热/30计时，覆盖预处理/ORT/后处理。n p50/p95=45.34/48.06ms，s=137.84/142.80，s校准137.18/141.03，m407.50/415.61，切片692.06/701.31，RF384=210.27/217.66，n+CLAHE46.67/48.85，n+flip90.56/94.62，s+gamma142.48/147.27。RF包含磁盘解码另测212.47/217.56并区分口径；RSS测量范围在报告中说明。短循环不代替1Hz稳态/30分钟/Windows11实机。
- 实验工具补充参数缺失提前拒绝、review SHA校验及解码资源释放；计分CLI增加显式--confidence以复算已按类别阈值筛选的低分框，默认.35保持原行为。新增4项切片/校准失败用例及1项低分框复算回归。`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`均退出0（104份Python）；`.venv/bin/pytest -q --junitxml=harness/artifacts/recognition-v2-20260907/pytest.xml`为105 passed、3 deselected、7.35秒。报告示例CLI实际执行，s校准各类计数一致。
- [独立审查](code_review/recognition-v2-review.md)发现项均修正并复核。首选均衡候选为s分类阈值；RF杯/手机召回有意义但瓶误报多，后续需要新部署域校准/验证及全ORT链路。此次完成所授权的研究与尝试，生产识别质量和USB/Windows11现场验收继续开放。

- 追加RF PyAV/OpenCV解码核对：9视频SHA全匹配，53帧shape一致；RGB平均绝对差0.976/255，rice OGV2.668/255、最大119。rice六点同索引均比±1更接近，未发现一帧偏移证据，但不是逐像素相同输入，报告已降为带解码差异限制的诊断对照。decoder-parity.json保存完整比对，未重跑模型。原生ci_checks入口与doctor也退出0；该入口调用105项离线回归通过。

## 2026-09-07T17:27:29+08:00 — 第二轮识别实验同步与双平台CI

- 里程碑0a2746f已正常同步；公开GitHub API核对 [run34105917312](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34105917312) 的macOS与Windows两个job均completed/success，head SHA均为0a2746f852513270295aab66e3bba6432887dd82。本机105项离线用例、Ruff/格式、ci_checks和doctor通过。
- 提交前14个相关文件JSON/本地链接/凭据模式核验通过，原始计划基线SHA不变，未暂存素材/权重/运行数据。当前追加仅记录真实CI结果，使用skip ci提交；不重复运行相同软件检查。生产识别质量和USB/Windows11实机验收仍开放。

## 2026-09-07T18:52:45+08:00 — 更优方案分析与存量预测互补性复算

- 用户本次要求分析是否有更优方案。复查已提交模型结果及视觉采集/事件代码，查询Frigate周期检测/过滤、WBF原论文、SigLIP2官方模型卡、OpenCV跟踪和SAM2官方资料；一个Sol独立研究语义复核与跟踪边界。没有新模型下载、推理、训练、百炼请求或生产改动。
- 实际 `.venv/bin/python harness/artifacts/better-strategy-analysis-20260907/complementarity.py` 退出0，复用前轮60图和两个模型的归档预测：s瓶+RF杯/手机85TP/22FP/70FN，双模型同意68/10/87，保留s框再补RF不重叠框98/46/57。GT匹配交集68、s独有8、RF独有22、都未正确匹配57。明确事后分析，不称新独立验证或模型融合实测；没有把未匹配推断成无候选。
- 官方来源与采用次序见 [后续方案分析](../docs/recognition-next-strategy.md)，来源SHA/规则/分类计数见 [互补性摘要](evaluations/recognition-complementarity-20260907.json)。建议先验证s主检测+RF周期/事件窗口复查，再评估局部硬负例拒识；不能直接求交集或并集。计算预算137+210×追加比例为粗估，不是组合benchmark。
- 本机只读检查cv2 4.14.0无TrackerKCF_create/TrackerCSRT_create/legacy；跟踪仅可建议ROI，不能刷新last_seen或补凑三次确认。SigLIP2的Apache-2.0与接口已核验，但未验证本项目ORT/CPU；SAM2官方GPU基准不作CPU证据。当前60图已看过，后续必须另设按视频/场景隔离的新验证集。
- 仅新增分析文档和脱敏摘要，不改变软件/依赖/验收条件，不重复执行105项软件回归。提交前核验来源SHA、JSON、Markdown链接、敏感信息模式及差异；生产识别质量与实机待验证项继续开放。

## 2026-09-08T17:58:52+08:00 — 真实OBSBOT摄像头接入与初检失败

- 用户明确已接入真实摄像头并要求开始验证。macOS设备清单及AVFoundation枚举确认实体OBSBOT Meet StreamCamera为index0，系统授权状态3；排除虚拟摄像头和其他设备。首次读帧请求自动权限审核超时、未执行，按审核提示重试一次后成功。
- 本机忽略目录camera-20260908/probe.py实际采集3.029秒/93帧、640×480/AVFOUNDATION，正式n模型0.35首次推理69.12ms，退出0且设备释放；图片仅留本机。初始框落在饮料罐上，旁边塑料瓶未框出，未将该单帧当准确率。用户空桌面/物品操作标签仍待提供。
- 现有benchmark.py --camera 0 --duration 60运行后退出139、未写summary；SQLite保留57条running和3条启动stopped、1条bottle appeared。这不是验收通过。原生崩溃为EXC_BAD_ACCESS/SIGSEGV，故障栈在CaptureDelegate grabImageUntilDate→VideoCapture::read，疑似停止线程提前release与采集read竞争。已交由Sol定向修复及离线线程用例，另一Sol准备用户操作标记辅助页；两者不占摄像头。修复后必须重新实测启停和持续运行。

## 2026-09-08T18:13:54+08:00 — 摄像头停止修复与真实复验

- 将VideoCapture释放移入读帧所属线程，stop只通知并join，超时保留句柄。运行时串行启停，旧工作器在完成停止清理前不得替换。停止超时及线程迟到退出的离线失败用例覆盖该约束。
- 同一benchmark.py --camera 0 --duration 60修复后退出0：60.624秒，56条唯一running/fresh、4条启动前stopped，无运行期断流/过期；p50/p95 74.54/77.36ms、峰值RSS401.72MiB、CPU均值25.06%（单核100%口径）。不是30分钟/识别正确率验收。
- restart_probe.py在同一VisionWorker/CameraSource三轮启停均退出0，每轮3有效观察，停止67.90/58.40/66.47ms，无存活线程及callback错误。runtime_probe.py两轮真实应用运行时启停也退出0，重复start均already_running、stop后worker清空/记忆stopped；停止80.27/58.78ms。实际配置Agent但未提交对话/关注，数据库agent_runs/watches/notifications均0。
- 新增仅127.0.0.1的camera_acceptance.py，用户标记实际操作时间、展示最新带框画面，使用正式本地视觉/MemoryStore，无云调用及完整录像。初版页面已通过本机浏览器读屏/截图检查。30分钟运行于2026-09-08T18:09:24+08启动，运行目录data/acceptance/obsbot-mac-20260908-1810，结果尚未完成，不能算通过；执行脚本快照及代码/模型SHA已保存，以区分后续辅助脚本健壮性修订。
- 初始保存JPEG的三个本地候选模型只作诊断：n框饮料罐；s分类阈值框塑料瓶；RF框塑料瓶同时误框饮料罐及桌面矩形配件为手机。未由单帧计算误报率/切换生产配置。用户空桌面及物品动作标签仍待提供。详细范围见[实机记录](../docs/camera-validation-2026-09-08.md)。
- 辅助台独立审查后补强：写入串行锁、完整人工标签保存、HTTP字段/类型限制、首帧起算及有效采样最大间隔判定；后续版本继续保留同帧running→stale/断连状态转换并增量持久化完整采样/事件。正在运行的30分钟使用已保存旧脚本快照，不冒充后续版本30分钟实测。新增运行时释放异常不会被stopped覆盖的失败用例，释放不明时保留实例锁。
- 最终 `.venv/bin/pytest -q --junitxml=harness/artifacts/camera-20260908/pytest-final.xml`：118 passed、3 deselected、7.70秒；Ruff检查/格式108文件及git diff --check全部通过。11个本里程碑文件敏感模式/文档链接检查通过，原始计划SHA不变。只读远程main仍ae2af9f，无未知提交。

## 2026-09-08T18:44:10+08:00 — 用户物品操作核对与Mac USB三十分钟完成

- 用户报告已放入并点击；本轮实际收到12条标记，含空桌面、手机多次放入/移出、两次待澄清的瓶子放入、一次瓶子移出和杯子放入。没有把重复/歧义标记当完整60预期事件。照片与逐帧事实只在本机复核，详见[实机记录](../docs/camera-validation-2026-09-08.md)。
- 手机首次appeared正确。18:23:54移出至18:24:04放回间10帧中，上方塑料瓶误手机6帧、无手机2帧、已放回真实手机2帧；未满足5秒无手机，缺少missing与新appeared。去掉干扰后18:27:48 missing、18:28:33 appeared，单次受控闭环正常；标记后固定38帧27检出/11未检出，不能当总体准确率。
- 杯子固定71个有效采样仅15检出；18:30:18/18:30:52/18:31:16和稍后18:37:30的4条missing证据均仍有蓝色水杯，逐张目检确认错误事件。不是摄像头断流，也不是Agent通知（本轮Agent未构造）。塑料瓶完整居中独立测试未收到后续标记，仍待完成。
- 当前Mac的30分钟进程于18:39:30正常退出0并释放摄像头，1791个fresh/running、故障0；首帧后1800.005秒、最大fresh间隔1.269秒。p50/p95推理76.35/81.65ms，峰值RSS361.88MiB，有效采样CPU均值20.20%（单核100%口径）；0云/Agent调用。summary总1796行含5条启动/结束stopped，不算运行期故障。只关闭当前Mac这次本地USB稳定性项，不关闭质量/完整UI-Agent/Windows/8GB基线。
- 在30分钟完成后执行忽略目录cup_model_probe.py，退出0；同一张目检无框杯子JPEG分别用n/s传统/RF Nano纯ORT共3次推理，s再按既有分类阈值过滤；所有配置三类框均空。输入及RF/YOLO SHA核验，未新下载/训练/换生产模型；单图诊断不当新验证集或模型排名。
- Sol增强辅助台，后续人工物品标记可保存最近新鲜原始输入PNG、点击/采集/检测时间、序号、年龄及SHA；无图/过期明确不可用，非fresh状态清空原图关联。正常采样只保留最近输入，不录像。新增3项有意义失败/时间关联用例。`.venv/bin/pytest -q --junitxml=harness/artifacts/camera-20260908/pytest-operator-review.xml` 121 passed、3 deselected、7.53秒，Ruff/格式108文件及差异检查通过。
- 新版辅助台实际`--camera 0 --backend 1200 --duration 10 --output data/acceptance/obsbot-console-smoke-20260908-1842 --port 8766`退出0，10.010秒有效覆盖、10有效观察、故障0、最大间隔1.016秒、正常停止，17条完整增量样本落盘。没有伪造物品操作标记；PNG标记路径仅fake-frame用例验证，不称真实点击验收。当前无测试摄像头占用。
- 修复提交6b0abad的GitHub工作流[run34214518296](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34214518296)已用完整head SHA只读核验completed/success。托管CI不代替Windows11 USB。此前短SHA查询为空，未当成失败或通过。

## 2026-09-08T19:57:00+08:00 — 资料驱动取景改进与真实杯子复验

- 用户要求查资料并开始解决实机误识别/漏检。查阅AXIS部署、SAHI、Frigate过滤和静止对象原始资料；依据、项目推论、失败结果及范围见[实机改进](../docs/camera-remediation-2026-09-08.md)。并行研究/实现/审查子Agent均为Sol，未使用更高成本子模型。
- 私有诊断实际执行`.venv/bin/python harness/artifacts/camera-remediation-20260908/geometry_probe.py`、`paired_camera_probe.py`、`resolution_probe.py`；4张错误missing旧图及1张空目标旧图另作同图复测。640裁剪仅一个开发JPEG恢复，4张旧错误图和新30帧杯子均失败；旋转/更大模型未证明可用。720/1080与桌面范围组合短试各12/12，单纯提高分辨率整图均0/12。不隐去失败，不把开发样本当独立准确率。
- 已实现Config/.env示例/页面三档采集清晰度和可选归一化观察范围；CameraSource裁剪新鲜帧，检测/预览/证据统一相对观察坐标。切换设备、分辨率或范围在旧worker完整停止后重建事件基线，保留last_seen/历史/关注，避免把裁掉旧目标当missing。默认模型、0.35阈值、三次/五秒不变。验收脚本、benchmark和doctor同步配置与范围记录。
- 两轮实际命令：`.venv/bin/python scripts/camera_acceptance.py --camera 0 --backend 1200 --width 1920 --height 1080 --region .25 .4166666666666667 .9375 1 --duration 120 --output data/acceptance/obsbot-crop-1080-20260908 --port 8765`；随后尺寸改为1280×720、目录改为`obsbot-crop-720-20260908`，其余相同。19:47—19:49及19:49—19:51，均退出0、120有效观察、故障0、正常释放，只一次cup appeared、0 missing、0云/Agent调用。1080杯94/120，720杯119/120；最长未检出4/1采样。p95推理82.09/81.55ms，有效采样CPU均值46.28%/32.36%（单核100%），峰值RSS442.89/406.05MiB。原始观察与事件在对应被忽略目录。仅证明这只静止杯子局部改善，不是99.2%总体识别或90%事件达标。
- 选择720p+当前桌面范围写入仅本机`.env`，保持现有Key和0600权限，不提交私人配置；代码通用默认仍640全图。没有下载新依赖、训练或云端看图。
- 独立审查发现全幅tuple文案误标，主任务已规范化并复验；主任务另外修复无效采样间隔测试、小数ROI舍入、请求尺寸误称实际尺寸等问题。详见[审查记录](code_review/camera-roi-review.md)。实际`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`、`.venv/bin/pytest -q`、`git diff --check`退出0：147 passed、3 deselected、8.93秒，109个Python文件格式通过；live API/camera标记用例不由离线测试替代。
- 浏览器实际检查新页面，独立`VAA_DATA_DIR=data/acceptance/ui-crop-20260908`，启动真实摄像头0、展示720p请求/880×420观察范围/真实杯框，然后点击停止成功。数据库Agent/tool/watch/notification记录均0，仅一个杯子出现事件；本地`ui-smoke-summary.json`保存实际观察计数与源码SHA。页面留在127.0.0.1:8501以便后续实物测试，摄像头已停止释放，不在后台继续检测。
- 尚待用户配合：固定候选设置的空背景、杯子移出/放回、完整瓶子与手机分别/同时入镜、每类十轮及真实Agent联合提醒；720p三十分钟与Windows实机也未完成。当前静止场景收益不关闭上述验收条件。

## 2026-09-08T20:05:00+08:00 — 远程Windows编码失败修复

- 取景里程碑`2e04668`已正常提交/推送，远程[检查34223770172](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34223770172)为Mac成功、Windows失败。实际读取GitHub失败注释定位到既有`test_object_marker_saves_recent_completed_input_with_distinct_times`：生产脚本写UTF-8中文JSON，测试Path.read_text未指定编码，在Windows cp1252解码失败；不是摄像头取景运行故障。
- 测试读取两处辅助台JSON均明确`encoding="utf-8"`，不改生产输出、不删除中文说明或验收断言。实际`.venv/bin/python scripts/ci_checks.py`全套147 passed、3 deselected、9.32秒，报告为被忽略的`data/ci-results.xml`。待重新推送后的Windows检查确认，不以本机通过代替。

## 2026-09-08T20:07:14+08:00 — 取景改进双平台检查完成

- 修复提交`89e2d20`已正常推送；[远程检查34224101212](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34224101212)已completed/success，Mac与Windows离线矩阵通过。它验证安装、静态检查和离线回归，不替代Windows实机摄像头验收。
- 新配置浏览器复验汇总为31条观察，其中22条有效新鲜观察全部检出杯子，1条appeared，最终stopped，Agent运行0。用户测试页面保留，摄像头已释放。未关闭剩余实物场景质量项。

## 2026-09-08T20:56:07+08:00 — 用户杯子移出/放回失败核对

- 用户报告按要求完成。只读查询实际页面测试库并目检missing证据及当前预览：20:49:57正确确认移出，5.026秒有效缺失且证据无杯；20:50:03放回附近只孤立检出一次，随后杯子在画面中持续漏检，无新的appeared。本轮闭环未通过，不能用之前静止119/120代表移出/放回可靠。
- 本轮232有效观察、故障0、最大有效间隔1.415秒，0 Agent运行；已在页面停止，最终stopped。明细保存到被忽略的`data/acceptance/ui-crop-20260908/operator-return-review.json`。实际动作缺少精确标记，不把5.026秒当物理动作端到端延迟，不将包含无杯时段的6/232当召回率。
- 为保存当前失败输入，在页面释放后执行`.venv/bin/python harness/artifacts/camera-return-20260908/probe.py`，短时读取同一摄像头0/AVFoundation，保存20:54:37原始裁后PNG并关闭。对这一新图四朝向本地诊断杯子最高分0.112/0.136/0.297/0.161，均低于正式0.35；不降低生产阈值、不引入旋转推理。详细事实与参数见[实机改进](../docs/camera-remediation-2026-09-08.md)。
- 本轮没有业务代码变更；文档更新、证据本地保留。下一步需要用户调整斜视角让杯身可见并完整入镜；三类质量仍开放。摄像头已释放，原测试页面保留。

## 2026-09-08T21:37:56+08:00 — 摄像头断连恢复及重连操作修复

- 用户报告页面摄像头断开。真实记录自21:27启动后无有效帧，反复Camera frame read failed；先停止/重开仍失败。系统权限下system_profiler及FFmpeg仅列设备确认实体OBSBOT仍为AVFoundation index0，未打开其他设备，也未结束OBSBOT扩展或用户程序。受限环境第一次设备清单为空，未据此认定硬件拔出。
- 页面释放摄像头后，独立CameraSource短测：640×480得到96帧；首次720p把打开耗时计入六秒而没有实际read，结果无效，明确重测。第二次720p从open完成后计时：open5.360秒、6.016秒内366有效帧、读失败0、正常关闭，见被忽略的`harness/artifacts/camera-disconnect-20260908/720-after-open.json`。不能由首次0采样宣称720p故障。
- 旧Streamlit进程即使重新创建连接仍失败，停止采集后仅正常终止已核验监听8501的项目进程，使用同一`VAA_DATA_DIR=data/acceptance/ui-crop-20260908`重启。实际浏览器恢复实时画面，21:36:21—21:37:07累计46有效观察、最大间隔1.065秒、首帧后故障0，880×420预览；21:00后视觉事件0、Agent运行0。恢复快照保存在本地`recovery.json`。可判断进程重启恢复有效，不能进一步证明唯一原生驱动根因，也不等于用户拔插USB验收。
- Sol仅修改app.py/tests/ui/test_app.py：断连/error时用户显式点击开始观察执行stop→start，stop失败不start，健康同配置保留幂等；无画面时显示明确重连提示。没有新增后台重试，没有更改摄像头线程、3次/5秒或模型阈值。它修复原按钮只返回already_running的缺陷，但不承诺能代替本次所需的整个进程重启。
- Sol全套149 passed、3 deselected及Ruff/格式通过；主任务审查差异并实际执行UI/runtime定向16 passed（7.71秒）、Ruff/格式和git diff --check通过。README记录重连仍失败时的进程重启方式。当前保持恢复后的实时观察供用户继续斜视角测试；杯子仍未检出，识别问题尚未解决。

## 2026-09-08T22:16:00+08:00 — 当前斜拍透明杯：固定缩放复查与正式页面恢复

- 用户确认杯子已放置，要求开始检测并解决漏检。恢复现场HEAD为71d50fe，工作树原干净；既有页面60新鲜采样0cup。暂停页面后摄像头0保存6张原始PNG，正常关闭；未访问其他摄像头。Sol分别执行只读资料/模型对照、困难样本检查和有界核心实现，另Sol只读审查，无高于Sol子Agent。
- 依据官方TTA、SAHI、OpenCV资料进行有界实测。28次旋转/上下文、18次光度/缩放开发尝试，仅原始全画面0.75缩小居中114填充恢复n杯子；冻结六图6/6。m/RF局部有改善但未采用，保留原n/0.35/一秒档与模型SHA。细节与来源见[报告](../docs/cup-scale-recheck-2026-09-08.md)。
- `.venv/bin/python harness/artifacts/cup-slant-20260908/live_scale.py`：120新鲜采样同帧原图0cup、缩放120cup，仅1appeared、0missing/0fault；worker正常停止。结果保存后误调不存在的MemoryStore.close导致退出1，辅助脚本已移除该调用，未伪报退出0。配对p95 134.43ms、CPU均值31.10%（含启动，单核100%）、峰值RSS421.23MiB；非隔离性能基准。
- 新增默认关闭的CupScaleRecheckDetector和VAA_CUP_SCALE_RECHECK/UI开关；最多同帧追加一次同模型杯子复查，原图证据/坐标、观察计数与三次/五秒条件保留。切换后stop再start并重置当前事件基线，原历史保留。doctor、benchmark、camera_acceptance与.env.example同步。未新增依赖、训练或云端视觉。
- 30个既有困难样本实际条件组合原/新TP/FP/FN均32/25/43；杯14/16/12。13张跳过，17张未补杯，新增正确杯/假杯均0；原锅桶四帧误报继承，不称修复。该刻意选样不能估计总体误报率。可分享数据与证据SHA：[摘要](evaluations/cup-scale-recheck-20260908.json)。
- 正式页面重启沿用原data/acceptance/ui-crop-20260908，私有.env仅改本机完整720p与增强开启，密钥不输出/提交。22:12:52—22:14:51首120fresh中110cup，最大连续漏检1，1appeared/0missing/0fault，最大采样间隔1.094秒，推理p95 177.76ms（并发开发时），Agent运行0。页面目检杯子框正确；保持摄像头观察，已请求用户移出10秒再放回保持15秒，尚待其确认，不写移动闭环通过。
- root回归首轮156pass/1fail：UI测试被本机新.env设置影响。离线fixture现在禁用dotenv并清空继承VAA变量、保留显式case设置和真实marker边界；复跑`.venv/bin/pytest`退出0：157 passed / 3 deselected。Ruff check/format（112文件）与diff --check通过。独立[审查](code_review/cup-scale-recheck-review.md)无阻断。下一步提交本里程碑并正常同步，用户移动与Windows实机不伪装成已通过。

## 2026-09-08T22:21:00+08:00 — 杯子复查里程碑同步与双平台离线CI

- 功能提交90cde26c50ef5792df566eac911d8a20fd877028已正常推送；推送前读取远程仍为71d50fe，未发现未知新历史，未强推。随后本地origin/main与HEAD一致、工作树干净。
- GitHub公开API核验运行34237436514，head_sha为上述功能提交；`offline (windows-latest)`和`offline (macos-latest)`均completed/success。此为离线CI，不代表Windows USB实机验收。系统未安装gh CLI，改用无凭据公开只读API查询，没有安装工具或读取额外凭据。
- 截至22:19:53页面仍为running/fresh，正确检测杯子（最新分数约0.39）；原数据库继续保留。从22:12启动至此只新增一次cup appeared，没有新missing。尚未收到用户移出/放回完成确认，继续保持页面采集，移动场景验收仍开放。本条仅补记已观察到的同步/CI结果，不再改业务代码。

## 2026-09-09T10:53:37+08:00 — 隔夜摄像头断开后的恢复

- 用户报告摄像头断开，要求尝试恢复。现场HEAD/远程均为9b39274，工作树干净；8501仍由上一轮Streamlit进程28786监听。SQLite及页面确认最后有效画面为09-08 23:05:15，之后持续Camera frame read failed；故障记录沿用旧observed_at，核对故障写入时间使用ingested_at，未把旧图当新帧。
- 页面显式开始触发stop→start后仍无新画面；随后点击停止，SQLite确认stopped。仅向已核验的项目进程28786发送SIGINT，以原`VAA_DATA_DIR=data/acceptance/ui-crop-20260908`、127.0.0.1:8501重启Streamlit，再点击开始。摄像头0、1280×720完整画面和杯子增强保持；未打开其他设备、未修改.env/业务代码/依赖、未清空历史。
- 恢复验证：09-09 10:52:22.922—10:53:36.171，共74个新鲜观察，首末跨度73.248秒，最大间隔1.059秒，首帧后故障0，最新running；页面时间持续更新，当前输出瓶子候选。Agent运行0次。私人统计与观察元数据保存在被忽略的`harness/artifacts/camera-recovery-20260909/recovery.json`。
- 已恢复实时采集并保持页面观察。本轮证明重启应用进程能恢复此次连接，不证明已定位隔夜断连根因或永久解决USB/睡眠恢复；未新增推理准确率验收，未机械重跑无改动的软件测试。

## 2026-09-09T12:00:00+08:00 — 杯子移动核对、新视角失败与有界候选排除

- 用户报告完成杯子移出10秒/放回15秒，随后回复无遮挡已就绪，再于诊断期间报告调整摄像头视角。本轮逐段核对，不把旧姿态与新姿态混算；起点c245c05、业务工作树干净。完整记录见[新视角复验](../docs/cup-viewpoint-validation-2026-09-09.md)，可分享计数/证据SHA见[摘要](evaluations/cup-viewpoint-20260909.json)。
- 先前10:52的bottle证据实际为蓝杯误分类。11:39:17生成bottle missing，截图杯已移出；11:40:08孤立cup未形成appeared。固定105fresh窗口含27bottle/1cup，但有真实缺席与身体遮挡，不能当召回率。cup移动闭环未通过；物理动作缺少精确标记，不计算端到端延迟。
- 原页面停止后摄像头0/720p保存六张PNG并关闭。root16种有界输入变换，仅0.75水平flip六图6/6；Sol24次n/s/m/RF对照显示上一无遮挡姿态RF整图/crop较强。所有输入/模型SHA已核验；未训练/下载/上传图像/API。第三遍flip原型定向46测试通过，但Sol30困难样本验证新增白桶cup FP1（无新增TP），原型归档、三个试改文件恢复原HEAD，不部署。
- 独立一分钟同帧2pass/3pass工作器60fresh，两者均60cup，只有1appeared/0missing/0fault，正常停止；用户期间报告视角变更，已另存标记并修正该运行scope，不能当固定视角准确率或flip收益。首末原图均目检到近镜头杯子。
- 正式页面重开时旧原生连接再次read failed；确认stopped后SIGINT核验进程32166，以原data/acceptance/ui-crop-20260908重启。新视角正式窗口11:50:35.968—11:52:34.560共119fresh/83cup（69.7%采样命中），最长8次未检出，故障0；11:52:09错误cup missing证据杯仍在，随后11:52:14重新appeared。静止连续性未通过，不能只报告前60/60。Agent运行0。
- RF对新角度三张原图0cup、3/3错误bottle（0.884/0.912/0.890）；不继续无收益的RF实机补cup测试。其30困难样本cup-only fallback另新增2TP/1FP，仍有桶误报，不部署RF也不把bottle强改cup。模型/阈值/正式代码及.env均保持。私人试验保存在harness/artifacts/cup-return-20260909，已标记未采用原型与失败结果。
- 当前页面running，等待用户保持镜头不动、将杯底/杯柄完整入镜且四周留白后继续排除边缘影响；该因素尚未证实。当前仅文档/脱敏摘要变更，未机械重跑无改动正式软件测试，未将未采用原型的46项计作新的生产验收。

## 2026-09-09T12:19:48+08:00 — 固定视角使用条件与误检口径分析

- 用户明确后续视角与困难杯姿态是正常使用条件；不再等待用户改变摆放作为推进前提。历史失败计数不改写，GOALS/PLANS记录新增场景覆盖。当前不训练约束保留。
- root复核已提交119fresh/83cup/最长连续漏8/错误missing1的结果；Sol只读审查指标口径。69.7%与30.3%为本段采样比例，没有足够独立标注与负例时长计算现场通用误检率；不把1次错误事件外推每小时频率，不把相邻帧当独立样本。
- 查阅TransCues透明物体论文、YOLO26官方微调及Frigate静止物体说明，形成[固定视角分析](../docs/recognition-fixed-view-strategy-2026-09-09.md)。建议先验证不确定状态/当前图像复核，长期场景微调仅列条件选项；没有承诺准确率或宣称方案已实现。
- 本轮未打开新摄像头、未调用视觉云服务、未训练、未改生产代码/.env；仅资料与文档分析，软件测试不重复运行。既有静止连续性与移动闭环仍失败，Windows实机仍未完成。

## 2026-09-09T13:40:47+08:00 — 用户授权场景训练、两轮微调与独立训练日志

- 用户明确“请开始训练”，随后要求单独记录训练过程与参数；更新AGENTS/GOALS/PLANS/架构，原计划基线与历史不覆盖。建立根目录[训练日志](../训练日志.md)，记录实际命令、数据、参数、调整原因、逐轮结果、失败与限制；详细数值见[脱敏摘要](evaluations/scene-finetune-20260909.json)。开发子Agent均为Sol。
- 复用.export-venv的Python3.11.16/torch2.14.0/ultralytics8.4.142，系统权限小探针确认MPS可用。下载120张固定选样COCO train图，复用本地官方标注；与已有90张公共回归图SHA无重合。现场人工9train+4test，root纠正手机标注并追加既有失败帧；共105train/24val/4test，同杯实例且相关，不伪称独立泛化。图片/标签/权重留本机，不上传视觉API。
- r01实际13:13:59开始，30轮、AdamW lr0=.001、freeze10、mosaic.5、batch4、640，最佳第26轮；训练/最终验证242.37秒（不含随后导出）。三类头612/708权重项迁移，现场杯3/4→4/4，但public60 TP/FP/FN 56/21/99→23/75/132，严重退化，未部署。
- r02从官方起点保留80类head及公共图全类别标签，私人其他77类未全面标注；业务仍仅三类。13:31:00开始，freeze23、lr0=.0001、mosaic.2，其余主要参数保持；708/708项迁移。实际13轮因patience10停止，最佳第3轮，训练149.43秒、含导出150.46秒。现场杯4/4；public60 58/18/97，public30 19/3/33，偏置hard30 30/17/45，仍有瓶子与部分集合退化。没有继续以测试集调阈值。
- `.export-venv/bin/python scripts/evaluate_scene_model.py`两轮各评估124个输入（集合之间有重叠），修正PyTorch默认one-to-many与导出one-to-one不一致的评估问题后，三业务输出全部通过IoU≥.999/置信度差≤.001/框差≤.1px对照。r02 CPU单遍中位约49—53ms、p95约52—55ms，非隔离性能基准。MPS非确定性警告、末尾最终验证回调不应多算epoch均已如实记录。
- r02既有九视频488采样回放完成，逐点与旧官方单遍n基线(t,frame_index)对齐；不是与当前生产两遍配置比较。桶仍在36/37s误杯，部分错误事件减少，但thermo漏杯、child漏杯和rice新增杯候选仍存在；没有完整真值，不计算视频准确率。详细视频结果在本地video-r02/comparison.json。
- 完整离线回归174 passed/3 deselected，最终定向17 passed，Ruff/格式与diff检查通过；[审查](code_review/scene-finetune.md)记录已修复问题。正式模型/.env/事件契约未替换，摄像头未重新打开，未调用项目云端Agent；新日期/新杯/真实连续事件与Windows模型实机仍待验证。两轮训练已完成，质量问题未宣称解决。

## 2026-09-09T14:55:35+08:00 — 两轮训练后的改进资料分析

- 用户要求查阅改进空间，root查阅Ultralytics微调/数据/增强/测试官方文档、缺标与透明物体论文、scikit-learn分组划分和Frigate静止物体说明；Sol只读审查训练器、数据及评价口径。形成[改进分析](../docs/finetune-improvement-2026-09-09.md)，同步训练日志与路线图入口。
- 复核现场9train/公共24val、80类头下私人缺标、默认best.pt选择与部署one-to-one评价目标差异及多因素同时改变。影响大小仍待消融，不将合理机制当成已证明退化原因。
- 读取本机ultralytics 8.4.142的trainer与metrics源码及r02 args/log，确认close_mosaic要到第26轮才触发，而实际第13轮提前停止；冻结BatchNorm已有eval处理，不列为缺陷。未追加训练，未打开摄像头或调用视觉API，正式模型不变。
- 本轮仅文档与只读分析；使用git diff --check及本地链接核验，不重复软件测试。新的独立会话、物体实例与最终事件验收仍未完成。

## 2026-09-09T15:44:45+08:00 — 下一轮现场数据交接说明

- 用户询问如何提供训练数据；复核现有数据构建脚本、忽略规则与微调缺口，并查阅官方数据采集/标注及分组验证资料。形成[数据交接清单](../docs/training-data-handoff.md)，明确首批试交、实际机位覆盖、会话/实例说明及最终测试隔离。
- 创建被Git忽略的`data/training-inbox/development`和`final-test`接收目录及纯文本填写模板；没有新增私人图片、摄像头采集或训练。用户无需先制作YOLO标签，助手负责抽帧、去重和标注核对，疑义再询问。
- 数量和时长是采样建议，不是已收到样本或准确率保证；本轮文档与路径检查，不重复软件测试。实际新数据仍待用户提供。

## 2026-09-09T17:06:56+08:00 — r02真实摄像头同帧三分钟对照

- 用户要求使用已训练模型测试。现场核对旧项目服务没有运行、OBSBOT实体摄像头仍连接；仅读取白名单相机配置字段，核验r02 ONNX SHA。Sol实现独立`finetuned_camera_test.py`，root审查修正基线JPEG证据、精简实验预览及停止请求来源校验后启动摄像头0；不修改生产模型、.env或数据库。
- 实际17:02:28.967—17:05:28.616共180fresh，同一帧r02单遍对照正式n+CupScale方案，实际1280×720/AVFOUNDATION/0.35。双方杯输出180/180、手机0/180，无瓶输出；各启动确认cup appeared一次，无missing。最大新鲜间隔1.026秒，故障0，停止正常，Agent/云端调用0。
- root目检7张稀疏原图合览及首/中段大图：杯子与下边缘部分截断手机可见，摆放无明显变化。当前姿态不同于上午失败原图，不能回填历史验收；本段只证明当前静止杯采样稳定，手机漏检仍在，没有证明新模型优于旧方案。数据与指标限制见[训练日志](../训练日志.md)和[脱敏汇总](evaluations/finetune-live-20260909-r02.json)。
- 真实测试完成后执行`.venv/bin/python -m pytest -q -m 'not live_api'`：176 passed/3 deselected；Ruff/130文件格式/diff检查通过。双方事件证据JPEG路径核实，私人原图/数据库/完整日志留忽略目录。临时测试到期关闭摄像头及8765服务，正式App原本未运行且本轮未启动。移动、无目标、瓶子、新实例与Windows实机验收仍待执行。

## 2026-09-09T17:16:03+08:00 — 手机居中后的r02复验仍漏检

- 用户已把手机移到中间，沿用独立同帧对照工具、两模型SHA、摄像头0/1280×720/0.35；仅更换新输出目录`finetune-live-20260909-r02-phone-center`和时长120秒。确认没有旧摄像头测试进程，未改代码、.env或正式模型，未训练。
- 17:12:47.757—17:14:47.198取得120fresh，故障0、最大间隔1.026秒、停止正常。双方手机输出0/120；r02杯29/120，正式方案杯87/120及瓶输出3次。现场杯子有移动/转向和手部经过，计数不是固定姿态召回率。
- root目检5张定时原图及两次missing证据：第30/60帧手机居中完整无遮挡仍漏；r02于17:13:20.880、正式方案于17:14:21.088各有一次杯子仍可见的错误missing。已确认手机问题不限于靠边截断，两模型都未通过当前场景可靠性验收。
- 实测记录追加[训练日志](../训练日志.md)，脱敏汇总为[手机居中复验](evaluations/finetune-live-20260909-phone-center.json)。私人素材与数据库保持忽略；无云端调用，临时测试页和摄像头到期关闭。文档/JSON/diff核验，不重复软件测试。

## 2026-09-09T17:24:12+08:00 — 手机/杯失败处理资料与局部诊断

- 用户要求查阅处理方法。root查阅Ultralytics、SAHI、ByteTrack、Frigate及透明物体研究；Sol只读复核管线不对称与missing语义。明确现有CupScale只补杯，r02单遍/原n两遍差值不全归因权重。
- 在既有手机居中第30/60无遮挡PNG上运行本地`harness/artifacts/phone-diagnostic-20260909/probe.py`：模型有贴近手机轮廓的框，全图手机分数约0.013—0.027；人工上下文裁剪后约0.096—0.168，仍低于0.35。同两图r02加现有杯复查补杯2/2，原n也2/2；只是开发样本诊断，不推断新准确率。
- 建立[处理分析](../docs/phone-cup-remediation-2026-09-09.md)与[脱敏诊断](evaluations/phone-diagnostic-20260909.json)，更新训练日志；后续优先对齐复查流程、补手机复查入口与实际数据、设计有预算的不确定状态。未改生产代码/阈值/模型，未开摄像头或训练，未调用云端视觉；只有文档、JSON与本地诊断输出核验，不重复软件测试。


## 2026-09-09T17:48:39+08:00 — development素材质量审查，按用户新指令暂缓训练

- 用户提交10段development视频，指定GPT-5.6子Agent；采用Sol分工质检/准备，随后用户要求先审查质量、不足先补充。未启动训练，准备阶段未完成代码草案保存在忽略目录patch并恢复，业务代码无变化。
- root完整OpenCV解码12,461帧（容器估计12,475），再FFmpeg全片10/10退出0且无错误输出；1920×1080/60fps、约208秒、37.11MiB。Sol稀疏抽帧30张及图像审查，root复核全体拼图和原图；语义不是逐帧穷尽检查。
- 现有杯/手机变化可用，但30代表帧没有瓶子或三目标全空场，独立采集验证不足。决定先补瓶、全空场、离手静置与另次采集验证；白色截断容器类别待用户确认。不宣称当前素材无价值或需要全部重拍。
- 建立[数据质量审查](../docs/development-data-quality-2026-09-09.md)、[脱敏计数](evaluations/development-quality-20260909.json)，更新训练日志；不读取final-test，不上传私人图像，不开摄像头/云端视觉。此轮仅资料和数据检查，未机械重跑软件测试。


## 2026-09-09T18:09:05+08:00 — 新增六段development质量复核

- 用户要求继续分析质量，使用GPT-5.6 Sol抽帧及审查，root独立SHA/元信息/FFmpeg全片解码与全部36代表画面复核。新增6段168.935秒，1080p/60fps，完整解码6/6无错误；原10段未改变。
- 新素材补全空场、杯/手机离手和同框。用户明确瓶尚未拍摄，保留瓶正例/杯瓶同框/瓶整段验证为当前补充项；不再要求重拍已补齐内容。现有后续片段可做有限同日验证，不虚称跨会话泛化。
- 建立[补充质量报告](../docs/development-supplement-quality-2026-09-09.md)、[脱敏计数](evaluations/development-supplement-quality-20260909.json)并更新训练日志。私人数据本机保存，未读取final-test，未训练/开摄像头/调用项目云端视觉，业务代码未变；仅核验文档与JSON，不重复软件测试。


## 2026-09-09T19:09:17+08:00 — 用户免除现场瓶子补拍，继续杯手机训练准备

- 用户明确“瓶子不需要”，不再将现场瓶素材视为本轮训练前提；保留公共瓶回归和产品接口，现场重点杯/手机。当前继续已授权训练，尚在人工标注/数据冻结/训练工具修正阶段。
- Sol并行核对39张新增待标帧与部署分支选优工具，root复核已有15张框预览，准备54张现场素材和公共120张。旧白色容器待定帧不纳入；整段划分，同日同实例限制保留。
- 隔离.export-venv MPS探针成功，dataset builder保留COCO80映射定向3测试通过；完整训练与评价尚未执行，实际后续记入训练日志。

## 2026-09-09T19:36:37+08:00 — r03训练、导出与固定回归完成

- 按用户免除瓶子补拍的范围，Sol人工标注/root复核54现场图，36train/18val整视频隔离，混入公共96train/24val；132train/42val共1589全类别标签，旧4test保留，final-test未读取。未知容器帧排除，源视频/新增抽帧SHA现场核验。
- 实际训练19:20:19—19:29:30，官方80类708/708迁移，freeze10、lr0=.0002、warmup_bias_lr=.01、mosaic.2、30轮/patience0、MPS，最后5轮关闭mosaic已执行。部署分支四组开发F1选第29轮，保留内部best；参数/SHA/命令详见[训练日志](../训练日志.md)。
- 现场开发18图杯9/9、手机6/6、三业务0FP；旧4失败及2居中图杯/手机均全部匹配。公共60图杯/手机TP33→45、FP12→23，收益与退化同时报告，不称整体达标。168输入PT/ONNX一致性全部通过；[完整结果](../docs/finetune-r03-results-2026-09-09.md)与[脱敏计数](evaluations/scene-finetune-20260909-r03.json)保留四管线/各集合数值和CPU时延。
- 三段同日development验证视频1Hz回放61次：18空场无业务输出；15次杯片新模型确认杯、旧模型未确认；28次同框新模型手机28次/杯24次，旧模型3/11。双方无missing，模拟停止后的过期观察无新增missing；root目检全部4张出现证据。不是实时摄像头或独立密集真值验收。
- 185离线测试通过/3 deselected，最终Ruff和134文件格式通过，本地文档链接/JSON/diff/敏感模式检查通过；Sol复审实际epoch/SHA/数据隔离无阻断问题，限制记入[审查](code_review/scene-finetune.md)。无摄像头/云端调用，生产模型与.env未修改；私人素材/权重/日志留忽略目录。公共误报、跨会话/新实例、真实事件与Windows验收仍待完成。

## 2026-09-09T21:08:15+08:00 — r03真实摄像头三分钟与移出/放回

- 按用户新请求启动OBSBOT摄像头0，独立实验入口双方加相同CupScale；新增可选候选复查并去除页面固定r02标签。实际21:04:06.096—21:07:05.906，180fresh/0fault、1280×720/AVFOUNDATION、最大新鲜间隔1.071515秒，正常结束释放设备。生产模型/.env/数据库不变，云端调用0。
- 当前画面杯中间、黑屏手机右侧；提出移动步骤后，从事件证据观察到两类各一次移出/放回。r03整段杯154/手机159输出，包含物品实际不在场阶段，非召回率；前50/后50固定窗口均两类50/50。基线整段杯33、手机0、瓶9输出。
- root核对7张定时原图、全部13条双方事件截图；r03六条事件与物品在场/不在场一致。基线错误瓶出现1条、杯仍可见的错误missing2条。r03有13帧同杯双重叠框，Sol确认不重复类别事件但会增加瞬时候选数量，作为待修正问题保留。
- 入口2项定向、完整离线185通过/3 deselected，Ruff/格式通过；完整结果与真实命令追加[训练日志](../训练日志.md)，[脱敏汇总](evaluations/finetune-live-20260909-r03.json)。性能测试后段与离线测试并行，时延非隔离基准。本次单循环改善明确，不关闭十次事件、新实例、长时空场及Windows验收，不自动部署。

## 2026-09-09T21:34:51+08:00 — 用户将当前阶段模型验收收敛到固定场景

- 用户明确“当前模型不需要泛化，当前阶段只会固定场景使用”。起点16e9699、工作树干净；核对现行目标、路线图、训练上下文与最新r03记录后，将跨场景/新实例泛化移出当前必做项，公共回归只作诊断，不再单独阻止固定场景接入。历史FP与失败事实不改写。
- GOALS定义当前质量边界，PLANS更新执行顺序，testing和微调context同步：先修同杯重叠框，再以同场景新片段/实际操作做多轮事件、空场与日常变化验证，达标后接入并做UI/USB/千问联合与稳定性验证。无需为新实例或公共指标单独追加训练；独立数据隔离与现场误报要求保留。
- 本轮仅同步用户范围澄清，没有训练、摄像头采集、API调用、生产模型替换或新测试结果；未将r03单循环成功扩写成质量验收通过。Windows、三类接口和瓶子补拍豁免不由本条擅自改变。文档差异与本地链接检查通过，纯文档变更不重跑185项软件测试。

## 2026-09-09T22:13:55+08:00 — 多条件情境提醒需求与可实施方案

- 用户希望加入钥匙/桌面、关闭电脑、18点之后组合提醒。只读核验当前三类模型/关注接口与r03 COCO80配置，确认无钥匙类别；查阅Home Assistant规则组织、Apple睡眠通知与Windows会话结束官方资料。
- 建立[情境提醒方案](../docs/contextual-reminders-proposal.md)，记录受限规则、三态条件、新鲜事实、触发顺序、去重、真实Agent复核和本机停止运行的限制。已询问关闭电脑是锁屏/结束工作、合盖、主机关机还是另一台电脑；尚未收到答案，不擅选系统钩子。
- 本轮仅方案与路线图入口，未新增模型训练、摄像头采集、系统监听、外部消息或实际提醒；未承诺现有YOLO可以识别钥匙、未将页面提醒视为关机后可达。固定场景原质量验收仍开放，下一步取决于设备动作定义与钥匙样本。

## 2026-09-09T22:17:50+08:00 — 合盖触发与常开推理主机澄清

- 用户回答关闭指合上笔记本盖子，运行模型的电脑长期开机。方案按视觉观察笔记本合盖、主机持续处理修订，不再等待设备动作澄清；通知是否可见与外部终端接入仍不能由常开主机推断。
- 只读检查development文件清单、既有质检及标注说明：已有笔记本上下文，但没有已核验的开/合盖状态数据集或钥匙标注，未重新解码全片，不声称视频内一定没有合盖。方案明确从确认打开到确认闭合才触发，黑屏/漏检/遮挡不等于合盖；启动已闭合不造事件，持续闭合不重复通知。
- 本轮仅修订情境提醒文档和路线图，没有训练、采集、新增规则运行或外部通知。尚需规则实现、固定机位视觉状态与钥匙数据验证及真实Agent提醒闭环，不能将澄清完成写成能力已实现。


## 2026-09-09T22:37:41+08:00 — 当前取景座位有人判断的局部可行性核对

- 用户要求基于当前画面分析是否可以用YOLO判断有人坐在座位上。8501页面显示CONNECTING/摄像头断开及历史预览，不能当实时事实；进程核对无Streamlit或既有摄像头验收进程后，仅读取已授权OBSBOT摄像头0。
- 临时本地探针于22:36:53—22:36:54取得3张1280×720相邻画面，官方yolo26n-e2e.onnx（SHA 9c60d351bb2865a8169d0590c07c905b372e81e4a846a8ff920d244955e2516c）直接读取COCO person输出，三张各1框，置信度0.8998/0.8981/0.8998，均超过既有0.35阈值。不是r03行为模型，也不是准确率或持续稳定性测试。
- 目检当前标注图：胸腹与手臂可见，头部、髋膝及椅面关键关系不可见。可尝试固定区域人体存在作为在位代理；单帧无法可靠区分坐着、站在桌前或俯身，当前没有证据宣称已实现坐姿/在座判定。建议先验证区域人体检测和三态连续判断，再视站立混淆结果选择局部分类或调整取景；空座、衣物、路过/伸手和同机位坐站转换尚未测试。
- 图片与JSON仅保存在忽略目录data/acceptance/seat-view-probe-ijosr04n。释放方法返回并保存结果后，临时进程退出码134，原生库recursive_mutex异常；明确不算停止稳定性通过，原因尚未定位。未修改生产模型、配置或业务代码，未启动正式页面，未调用项目云端Agent。


## 2026-09-09T22:48:55+08:00 — 用户更换视角后打开实验测试页

- 用户请求打开测试网页。检查无既有Streamlit/摄像头测试服务后，使用既有finetuned_camera_test.py启动摄像头0、1280×720、r03部署候选SHA f71abc2197aeb433fb6ba21f97113b5797c2bdbbaeded3fe37466e5ee1a4d004、保留80类头且双方开启杯复查，独立输出data/acceptance/r03-new-view-20260909-session1；网页127.0.0.1:8765，目标时长1800秒，可在页内停止。
- 刷新旧标签后页面已切换为实验模型摄像头对照，核对时27次新鲜采样、故障0。本次仅启动预览，尚未完成整段稳定性或新视角质量验收；不将无检测输出解释为物品不存在。未替换正式配置或增加行为检测，没有云端Agent调用。


## 2026-09-09T23:28:37+08:00 — 新视角在座/饮水两模型试训与离线事件回放

- 用户明确扩展授权限定行为训练，已同步AGENTS/GOALS/PLANS/架构；三个Sol分别数据标注、官方资料审查、训练/评估工具，root复核数据、代码并顺序执行MPS训练。新增三视频约66.18秒、完整解码3968帧，4fps抽样形成姿态244/饮水260个任务样本；原始及复核标签留本机，final-test未读取。
- 官方YOLO26n-cls分别微调20轮，姿态162.33秒、饮水165.77秒（含导出）。因正例集中单视频，全部train-only、固定last.pt，不宣称独立val；原始完整参数/SHA和实际命令见[训练日志](../训练日志.md)，模型仍隔离，不替换生产。
- 实际evaluate_behavior_models入口退出0；504标注任务样本PT/ORT同输入输出一致，maxabs1.1921e-7。训练素材匹配244/244、260/260仅为拟合检查。2fps回放生成2起身/2坐下/2疑似饮水/1离座，root目检全部7张事件图；另一次离座因unknown打断而漏事件，空座状态仍在4.5秒确认，不改低阈值掩盖失败。
- 脱敏结果见[evaluation](evaluations/behavior-20260909-r01.json)、[报告](../docs/behavior-training-r01-20260909.md)及[审查](code_review/behavior-training.md)。CPU预处理+ORT短测姿态p50/p95=9.35/9.76ms、饮水9.31/9.63ms，每模型20预热/100计时，不代替长期或Windows基准。
- 真实命令`.venv/bin/pytest -q -m 'not live_api and not camera'`最终206 passed/3 deselected，10.32秒；Ruff和145文件格式检查通过。图像、标签、权重及完整日志均忽略，云端调用0。未新增摄像头采集、正式UI或通知集成。需要同机位独立视频补站立/饮水与反例，再关闭质量验收；不要求跨人/场景泛化。
