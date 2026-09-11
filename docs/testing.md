# 验证与验收

识别策略离线对照及可复算命令见 [2026-09-07实测报告](recognition-ab-results-2026-09-07.md)。实验模型不会修改生产模型清单；图片实例计分、视频事件诊断、短时性能与USB实机验收分别报告。

测试入口已经建立；实际结果见 [build-log](../harness/build-log.md)。离线、真实模型、摄像头和平台结果分别记录，未执行项不能写为通过。

当前阶段模型质量范围按2026-09-09用户指令限定为现有固定场景，见[GOALS](../GOALS.md)。不要求跨场景/新物品泛化，公共图回归退化只作诊断，不单独作为该场景模型的部署否决条件；未参与训练或选优的同场景新片段/真实操作仍须独立评分。日常姿态、正常光照变化、短遮挡、空场、实际易混物和多轮事件仍要验证，不能用训练图命中替代验收。双平台与真实Agent闭环要求不因模型范围收敛而取消。

## 自动检查

仓库根目录执行：

```sh
uv sync --locked --group dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked python scripts/doctor.py
git diff --check
```

本开发机的 uv 位于 `.tools/bin/uv`，如未在 PATH，使用前缀 `UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv`。也可在已锁定环境使用 `.venv/bin/python`、`.venv/bin/pytest`、`.venv/bin/ruff`。

pytest 默认排除 `live_api` 和 `camera`。离线 fixture 拒绝 socket 连接和 OpenCV 的真实 VideoCapture，测试显式注入假模型/假输入并只写临时目录。Streamlit AppTest 和本机浏览器验证互相补充。

| 目录 | 验证内容 |
|---|---|
| tests/foundation | 显式配置、无 Key、非法范围、时区、原生进程互斥 |
| tests/agent | 真实 SDK Runner + 假 Model 工具循环、七工具、幂等、三轮上限、超时、失败用量、复查通知、真实 API 显式用例 |
| tests/vision | letterbox/逆映射、区域、候选、ONNX 元数据/输出/校验、输入异常、最新帧与停止 |
| tests/events | 三次出现、五秒有效缺失、两秒遮挡、采样缺口和单调钟 |
| tests/memory | SQLite 回滚、证据失败/保留/清理、重启与新鲜度、版本迁移前备份、未知版本拒绝、usage/chat 隔离 |
| tests/watches | 建立后事件、事务认领、并发提醒去重、取消/到期、重启、每日额度并发 |
| tests/ui | AppTest 创建/取消、重运行、缺模型、后台模型不阻塞记忆、资源释放与恢复队列 |

GitHub Actions 的 [offline.yml](../.github/workflows/offline.yml) 使用 macOS/Windows 托管 runner 安装锁定环境并运行上述离线检查。提交 c58a169 的双平台工作流已实际通过（[run 34058017128](https://github.com/AFunnyMan/VisualAIAgent/actions/runs/34058017128)）；托管 Windows 不等同 Windows 11 USB 实机。

## 模型和持续运行

在独立 `.export-venv` 执行 `scripts/prepare_model.py`，实际下载/导出/比较成功才更新模型 manifest。统一 PyTorch/ONNX 的 `rect=False` 预处理；不能用扩大公差掩盖坐标错误。模型 SHA、类别和验证图片来源见 [阶段 02 context](../harness/context/phase-02-local-vision.md)。

公开图片短测（需自行提供存在的图片路径）：

```sh
uv run --locked python scripts/benchmark.py --image path/to/image.jpg --samples 30
```

十分钟实际墙钟离线检查（仍不是真实摄像头）：

```sh
uv run --locked python scripts/offline_soak.py --image path/to/cup.jpg --duration 600 --watch-category 'cell phone'
```

该脚本使用真实 ONNX、本地状态机和 SQLite，并让一个与图片不相关的关注持续等待；一旦调用 Agent 哨兵就失败。不能用加速假时钟测试代替十分钟实际时间，不能用静态图片回放代替实机录像链路或识别率。

## 视频与真实模型

`scripts/video_acceptance.py` 校验下载视频哈希，使用真实ONNX原速回放并区分视频期间和EOF后的事件；一秒、两秒两档已实跑。`scripts/live_acceptance.py` 需要显式付费开关，独立数据目录内运行真实SDK、运行时队列、公开视频、故障和合成空白注入、重启及已连接模型待机。它捕获实际工具返回核对答案事实锚点，并将HTTP计数与本地运行记录对账；完整自然语言质量仍需结合人工审阅。视频素材与快照数据不提交。

## 真实验收

扩大到手机、不同杯型、瓶子和负样本的9段视频后，发现持续误分类和漏检；见 [识别与调用评估](recognition-assessment-2026-09-07.md)。这些结果没有满足三类现场90%的验收条件。可复用离线入口为：

```sh
uv run --locked python scripts/recognition_survey.py \
  --video path/to/video.webm --output harness/artifacts/new-survey --duration 190
```

该入口不读.env、不创建Agent、不打开USB；从头顺序解码，按标称恒定帧率每秒取最近帧，输出逐帧检测、复核图和加速时间线事件。VFR未验证，短暂目标可能落在采样间隙。原始视频来源、哈希、最终采样和人工复核标签见评估页链接的清单；检出占比不能当准确率，事件时间不能当真实提醒延迟。

真实千问已完成独立 SDK 和视频集成验证，提供商配置、公开视频命令及局限见 [千问说明](qwen.md)。USB 联合实测步骤放在 [用户辅助验收](user-acceptance.md)，包括：显式启用 `VAA_RUN_LIVE_API=1` 的真实 SDK 测试、三类各十次放入/移出、两秒遮挡、候选、断连/停止/重启、正常/降级通知和两平台各三十分钟运行。

```sh
uv run --locked pytest tests/agent -m live_api -v
uv run --locked python scripts/camera_acceptance.py --camera 0 --duration 1800 --output data/acceptance/new-camera-session
```

第一条没有开关/配置时只跳过，不算真实通过；第二条会打开真实摄像头，需用户准备设备及场景。模型凭据不写入测试输出。真实模型测试使用明确 `source=test` 的临时事实，摄像头质量单独验收。

## 需求映射与平台矩阵

| 目标 | 已建立的软件验证 | 外部验收 |
|---|---|---|
| G01 | ONNX 适配、区域、候选、有效输入、故障状态机 | USB 三类固定场景与两秒遮挡 |
| G02 | 历史、事件、证据、重启、清理、磁盘/事务失败 | 真实物品移出后查证 |
| G03 | SDK 离线边界、真实千问查询/创建/取消、视频查询事实锚点 | USB现场查询与不同模型需另测 |
| G04 | 任务、队列、去重、恢复；真实千问+视频出现/合成空白提醒 | 真实 USB 物品触发一次提醒 |
| G05 | 请求上限、用量完整性、无匹配零唤醒 | 真实等待与提醒延迟/费用记录 |
| G06 | 本机锁定安装与回归、原生 CI 配置 | Mac/Windows 11 各三十分钟与摄像头 |
| G07 | AppTest、后台队列、浏览器真实千问创建/取消 | 全闭环实机演示 |
| G08 | 锁文件、准备脚本、启动脚本、诊断与文档 | 新机器按步骤复现 |

| 平台 | 锁定安装 | 离线测试 | 实际摄像头 | 三十分钟运行 |
|---|---|---|---|---|
| 当前 Apple M2 Pro Mac | 已执行 | 最新数量与执行范围见日志 | OBSBOT短时采集、三轮底层及两轮应用启停通过；场景评分待操作 | 视频回放1800秒通过；USB本地视觉1800.005秒/1791有效观察通过；识别与Agent联合项仍开放 |
| GitHub macOS / Windows 托管 runner | 均通过 | 均通过，run 34058017128 | 不适用 | 未运行 |
| Windows 11 x64、8GB 无独显基线 | 未实机执行 | 不以托管CI代替实机 | 待用户辅助 | 待用户辅助 |

识别事件正确率定义、90% 目标和额外误报单列要求保持不变，详见 [GOALS](../GOALS.md) 与 [验收步骤](user-acceptance.md)。性能记录需包括 OS/CPU/内存、摄像头、实际分辨率、版本、档位、推理、CPU/RSS和提醒延迟。

## 证据与恢复

日志记录时间/时区、实际命令、退出状态、关键结果与局限；完整日志/图片放被忽略的 artifacts 或 data，仅脱敏摘要进入 Git。未知数据库版本拒绝写入，版本 1→2 在 SQLite 在线备份成功后迁移，不通过删库恢复。失败修复后定向复验，不删改验收条件。

## 第二轮识别实验复算

[第二轮报告](recognition-v2-results-2026-09-07.md)提供60图14配置的归档预测复算命令；归档已按各类阈值接受的框需使用`--confidence 0.05`，避免再次套用统一0.35丢掉合法候选。`scripts/recognition_input_trial.py`提供calibrate/images/review/videos入口，使用显式模型profiles与SHA，不读应用凭据；需要本地实验权重和素材。旧30图现在属于校准集，新60图只验证。完整命令与已运行范围见build-log，review分支本轮未运行。


## 实验行为摄像头入口

`scripts/behavior_camera_test.py`为隔离手动测试，真实执行命令、用户流程、结果及失败见[2026-09-10实测](behavior-live-test-20260910.md)。仅ONNX CPU，端口8765，需新输出目录；不加载.env或写正式业务库。正常停止后服务关闭，页面不再刷新。行为模型仍未获正式启用验收；不能把界面显示某状态视为事件已正确生成。

新增座位关联对照使用`--person-association seat --seat-roi 0.2 0.2 0.95 1.0`，默认仍为旧严格关联。具体参数、同帧旧逻辑比较、有限图片证据及测试结果见[座位关联改进](behavior-seat-improvement-20260910.md)。离线`v2-10fps-seat-association`固定采用上述默认ROI；改变实机ROI后不能直接声称与该离线配置一致。故障帧不保存为动作证据；诊断图片有限，不能替代完整连续动作的独立人工标注。

实时入口现默认有界并行人体/饮水推理，复现原串行需`--auxiliary-mode serial`。线程设置、等待预算、完整命令和失败耗时口径见[延迟修复](behavior-latency-fix-20260910.md)。辅助timeout/busy表示对应信息不可用，不等于相机画面过期；最终fresh与事件的安全边界保持。旧配置的“新旧同帧比较”仍只比较人体关联规则，不是两套模型运行性能对照。


## 行为产品集成（2026-09-11）

新增行为存储、共享采集、规则条件与并发领取、Agent受控扩展和UI测试。命令及实测边界见[集成说明](behavior-product-integration.md)，实际结果见build-log。`tests/agent/test_behavior_live.py`需显式live_api授权，测试隔离事实；`scripts.check_behavior_integration`访问真实相机但不创建规则或调用模型。二者不能互相替代用户动作/真实物品条件验收。
