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
