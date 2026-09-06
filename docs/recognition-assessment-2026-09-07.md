# 模型调用与本地识别评估

2026-09-07，Asia/Shanghai。结论：当前业务的模型调用结构基本合理；扩大素材后，本地识别明显不够稳健，已经影响事件层，不能宣布三类识别质量达标。本轮没有新增百炼请求，没有打开 USB 摄像头，没有修改生产权重、阈值或验收条件。

## 调用次数是否合理

这里的“请求”是产品向百炼发出的模型 HTTP 请求，不是视觉推理次数，也不包括 Codex 开发子 Agent 的用量。一条用户消息可包含多个模型请求和业务工具调用。

| 实际观察 | Agent 运行 | 模型请求 | 判断 |
|---|---:|---:|---|
| 最终完整流程中的用户查询、关注管理 | 10 | 20 | 每次2轮：选工具，再读取工具结果作答，合理 |
| 同一流程中的自动事件提醒 | 2 | 6 | 每次3轮：复查场景、查询证据、调用通知工具，符合当前证据约束 |
| 已连接模型的30分钟等待 | 新增0 | 新增0 | 没有轮询大模型，符合设计 |
| 本轮9段视频识别测评 | 0 | 0 | 只使用本地 ONNX 和事件状态机 |

证据分别为 `harness/artifacts/live-video-5102f9b9/summary.json` 与 `live-video-0bdb843d/summary.json`、`idle-samples.json`、`completion-audit.json`。最终完整流程26次HTTP与数据库中12条Agent运行记录的26次请求一致；等待实际1800.013秒、360次检查，新增请求均为零。

先前累计 **115次** 是包含连接探测、失败排查、修复复验、UI和三个完整流程的开发验证总量，不能当成每半小时的正常调用量。累计输入163408、输出5799 Tokens；按 [百炼 qwen-flash 北京短上下文官方原价](https://help.aliyun.com/zh/model-studio/qwen-flash) 的输入0.15元/百万、输出1.5元/百万估算约0.03321元，非实际账单，也不包含开发 Agent 用量。

这套结构合理，但不是理论最少请求数。当前首轮强制真实工具执行是为了避免模型声称完成却未执行；因此无业务意图的闲聊也可能触发多余工具。后续可优化这类输入路由，保留业务执行校验。输入占总 Token 约96.6%，压缩重复上下文和工具返回值得关注；现有直接操作按钮已经可以避免自然语言调用。开发验证也应优先定向复验，只有跨模块改动才重跑完整付费流程。不能为了减少一次请求而取消证据复查、允许零工具假成功或把真实 Agent 替换为规则模拟。

本轮发现的持续视觉误报还可能唤醒匹配的关注任务，间接消耗模型请求；这一风险比当前正常业务的2—3轮更值得优先处理。本轮没有建立关注或运行云端 Agent，因此这里只确认视觉事件错误，没有把潜在请求说成实际收费。

## 新增视频测试

使用锁定的 YOLO26n ONNX、CPU、输入640、置信阈值0.35，模型 SHA 为 `9c60d351bb2865a8169d0590c07c905b372e81e4a846a8ff920d244955e2516c`。9段不同实拍视频共 **488个每秒采样帧**；按预先均匀选取的点逐图复核 **53帧**，确认目标是否可见、类别是否检出、框是否落在目标上。下文的人工复核指开发 Agent 直接查看图像作判断，未经用户独立标注，因此这些标签也有复核者判断误差。

下表“抽查正确命中”只用人工确认目标可见的帧作分母；标题卡、黑场和完全遮挡不算漏检。“检出帧/采样帧”包含目标还未进入或已经不可见的时间，**不能当成召回率或准确率**。素材是用于发现弱点的便利样本，数量小、相邻帧相关，不能外推为现场成功率或模型通用排名。

| 视频场景 | 目标 | 检出帧/采样帧 | 抽查正确命中/目标可见帧 | 观察 |
|---|---|---:|---:|---|
| 工业工位、多台小手机 | 手机 | 0/30 | 0/5 | 小目标与杂乱背景下漏检；另1个复核点是黑场 |
| 近景桌面装置、手放入手机 | 手机 | 12/54 | 1/2 | 无遮挡时可正确识别，手遮挡时漏检 |
| 桌面手机、拿取并放入米箱 | 手机 | 13/124 | 1/3 | 桌面可见时漏检，完全遮挡后不能推断被移走 |
| 变色陶瓷马克杯近景 | 杯子 | 4/35 | 0/6 | 大而完整也会漏检，少数其他采样点能检出 |
| 透明带柄茶杯、移动强光 | 杯子 | 6/13 | 3/6 | 检出不稳定，11秒才形成出现事件 |
| 手持小杯喝水 | 杯子 | 0/5 | 0/4 | 手脸遮挡与小目标漏检；另1个复核点是黑场 |
| 厨房装瓶、多瓶与容器混杂 | 瓶子 | 51/189 | 1/2 | 模糊瓶子漏检；多瓶只检出部分；另见大桶误分类 |
| 夜间街道、手持横放瓶子 | 瓶子 | 1/8 | 1/6 | 横放时多数漏检，最后竖起一帧检出；没有形成出现事件 |
| 室外行人负样本，前30秒 | 三类 | 0/30 | 不适用 | 6个复核点均无可辨认目标，也没有检测框 |

只对所述目标类别作人工判定，未对每一个实例逐框标注 IoU，也未把未复核的其他类别输出当作正确。厨房瓶子在188秒有两个正确框，但许多其他瓶子没有框；“该帧命中”不代表全部候选都被找到。产品只保留 COCO 的 cup/bottle/cell phone，高脚酒杯 wine glass 属于另一类，不将其被过滤算为普通杯子漏检。

## 两个影响业务的失败

**持续误分类可以通过状态机。** 厨房视频36、37、38秒的大白桶被识别成杯子，分数约0.89、0.60、0.51；连续三个采样点足以触发38秒的 `cup appeared`。这不是偶发一帧抖动。逐秒全片顺序回放同样复现该事件，局部复核图保存在 `harness/artifacts/video-survey/bucket-detail/review-sheet.jpg`。提高连续次数只能过滤短暂抖动，不能保证纠正持续错误分类。

**持续漏检会产生“未检测到”事件，但不证明物体离开。** 桌面米箱视频7—9秒检出手机并形成出现事件；15秒产生 `cell phone missing`，此时图中手机仍在桌面上且清晰可辨。12、15、18、21秒的复核图均可见手机而无框。证据为 `harness/artifacts/video-survey/phone-missing-sequential/`。该子窗口15个样本的检测与完整顺序回放对应样本逐项完全一致。当前用“持续未检测到”表达是必要的，但措辞正确不等于用户关注的现实事件准确。

事件重放使用视频相对秒数、每秒一个观测和生产状态机，不运行实时线程/SQLite/云端通知；这些是给定采样序列上的事件证据，不是 USB 实机提醒延迟。剪辑切换、视角改变和完全遮挡也不能解释为物品被拿走。

## 推理与测评方法复核

对一张完整马克杯失败帧，现有官方 PyTorch 权重与生产 ONNX 的最高 cup 得分分别为0.01264465和约0.012646，框坐标一致；在生产阈值0.35下均无结果。检查时仅在独立诊断实例将输出阈值降至0.001以观察原始分数，生产配置没有变化。这支持该帧的问题是模型低分，而不是已检查的 RGB、归一化、letterbox、类别和坐标适配错误；单帧对照不证明整个管线不存在其他问题。

初轮随机按时间 seek 的 OGV 解码出现“报告同一时间但随 seek 历史返回不同像素”的情况。测评脚本已改为从头顺序解码，再按标称恒定帧率取最近的目标帧，并重跑全部9段。旧探索报告保留，最终表格只采用 `*-sequential` 输出及对应新复核标签。修复后手机7—21秒子窗口与整片解码15/15检测一致。这个改动只影响测评脚本，生产回放本来按顺序读取。

仍有边界：VFR素材未验证；每秒采样可能错过短暂动作；均匀少量抽查不能提供完整准确率；本轮不是性能基准，不用并发测评中的推理耗时宣称实机性能。原始53帧之外的针对性复核用于分析失败，没有混入表格的分母。

## 素材与复现

来源、作者、许可、直链、SHA-256、每段采样参数、最终检测数量、53帧标签及对照摘要保存在 [可复核清单](../harness/evaluations/video-survey-20260907.json)。视频、原始帧和完整检测输出只保存在忽略目录 `harness/artifacts/video-survey/`，不随仓库分发。

| 素材 | 来源与许可 |
|---|---|
| 工业手机 | [Gigaset quality inspection](https://commons.wikimedia.org/wiki/File:Gigaset_Smartphone_Production_IV_Quality_Inspection.webm)，Kathinka Engels / Inke Pickhardt，CC BY 3.0 |
| 近景手机 | [Smartphone EMF Detection](https://commons.wikimedia.org/wiki/File:Smartphone_EMF_Detection.webm)，Epic Jefferson，CC BY 3.0 |
| 米箱手机 | [Rice-pattern demonstration](https://commons.wikimedia.org/wiki/File:Tamper-evident_unrepeatable-pattern_security_principle_demonstrated_by_securing_a_phone_with_rice_patterns.ogv)，MarkJFernandes，CC0 |
| 变色杯 | [Thermochromic mug](https://commons.wikimedia.org/wiki/File:Thermochromic_mug.webm)，The wub，CC BY-SA 4.0 |
| 透明杯 | [Glass tea cup setup](https://commons.wikimedia.org/wiki/File:Setup_with_two_flashlights_and_a_glass_tea_cup_before_smash_experiment.webm)，Pittigrilli，CC BY-SA 4.0 |
| 手持喝水 | [CDC milestone clip](https://commons.wikimedia.org/wiki/File:1_Year_Milestone-_Starts_to_use_things_correctly;_for_example,_drinks_from_a_cup,_brushes_hair.webm)，CDC，美国联邦政府公有领域标识 |
| 厨房装瓶 | [Beer-bottling](https://commons.wikimedia.org/wiki/File:Beer-bottling.webm)，Sage Ross，CC BY-SA 3.0 |
| 夜间瓶子 | [Sabrage with the glass 2](https://commons.wikimedia.org/wiki/File:Sabrage_with_the_glass_2.webm)，PeterLemenkov，CC BY-SA 4.0 |
| 行人负样本 | [OpenCV vtest sample](https://github.com/opencv/opencv/blob/4.x/samples/data/vtest.avi)，来自官方样例仓库；原始视频的单独许可证未确定，不以仓库许可证替代素材许可，不分发视频 |

实际在项目根目录使用已锁定环境执行：

```sh
.venv/bin/python scripts/recognition_survey.py --video harness/artifacts/video-survey/beer-bottling.webm --output harness/artifacts/video-survey/beer-sequential --duration 190
.venv/bin/python scripts/recognition_survey.py --video harness/artifacts/video-survey/sabrage.webm --output harness/artifacts/video-survey/sabrage-sequential --duration 190
.venv/bin/python scripts/recognition_survey.py --video harness/artifacts/video-survey/opencv-vtest.avi --output harness/artifacts/video-survey/negative-sequential --duration 30
```

另外6段对清单中的 `videos/<id>.<ext>` 各执行同一命令，`--duration 190`、输出 `<id>-sequential`，到素材尾部自动停止；实际窗口、总帧数和结果均在清单。复跑时换一个新输出目录，脚本拒绝覆盖已有 `samples.json`。

## 后续优先级

本轮完成调用审计、素材扩展和质量诊断；识别缺陷尚未修复。下一步应使用这些失败样本与另外保留的验证素材，比较本地识别改进对漏检、误报、事件延迟和CPU的共同影响。不要仅降低全局阈值：当前大桶误检已高达0.89，而完整杯子的得分仅约0.01265，单个阈值无法同时解决这两例。优先在当前首版范围内评估固定桌面取景与本地处理；更换模型规格需要单独记录架构取舍和性能证据。

保留当前真实 Agent 和证据核验；不得把云端文字推理当成能够纠正它未见过的图像。三类90%现场事件目标、真实 USB、Mac/Windows 11各30分钟验收仍保持未完成，不能因离线软件测试通过而关闭识别质量问题。
