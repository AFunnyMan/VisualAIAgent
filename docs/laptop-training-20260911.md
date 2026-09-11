# 笔记本开合实验训练 r01

2026-09-11，Asia/Shanghai。本轮建立固定场景笔记本开合的隔离分类基线。模型、私人抽帧和详细预测仅保存在本机 `harness/artifacts/laptop-20260911-r01`，没有替换正式模型。

## 语义与数据隔离

- `open`：被观察笔记本在画面中明确开盖，黑屏仍算开盖。
- `closed`：被观察笔记本明确在画面中且完全合盖。
- `unknown`：半合、开合转换、手臂遮挡、移出画面或视觉不清；这些帧不作为 `closed` 训练样本。二分类模型在最高置信度低于 0.75 或前两类差值低于 0.2 时也输出 `unknown`。

抽帧前调用 `scripts.training_source_policy.verify_development_sources` 对照冻结来源清单。本训练任务只读取 7 段 2026-09-11 development 视频；带“验收”前缀的 `2026-09-11 23-08-00.mkv` 未被本训练任务读取、训练、预测或调参。四个完整源视频归 train，三个完整源视频归 development val，没有把同一视频相邻帧拆到两侧。独立模型评价尚未进行。

人工按连续原尺寸画面复核区间，跳过所有边界。尤其将 `23-05-02` 的 58—70 秒标为稳定开盖、70—72 秒排除、72—76 秒标为稳定合盖；将 `23-02-14` 的 35—37 秒旧候选纠正为 33.5—35 秒合盖、35—37 秒转换、37—42 秒开盖、42—44.5 秒转换、44.5—46.5 秒合盖。最终 train 为 closed 40 帧、open 24 帧，development val 为 closed 37 帧、open 42 帧，采样率 2fps。

## 训练与结果

使用 `.export-venv`、YOLO26n-cls 预训练权重，固定 20 epochs、320 输入、batch 8、seed 20260911、冻结前 5 层、AdamW，训练记录选择独立 development val 最优 checkpoint。当前模型是全图分类器，`roi` 为 null；没有完成“笔记本局部分类”或笔记本检测定位，人物姿态和手的位置也不是开合判据。

正式 `OnnxClassifier` 的 0.75/0.2 拒识规则在 79 张 development val 上得到：69 正确、7 unknown、3 个 open 错判 closed；全样本准确率 87.34%，已接受样本准确率 95.83%。closed 为 36/37 正确、1 unknown、0 错判；open 为 33/42 正确、6 unknown、3 错判。143 张 train+val 图的 ONNX CPU 单次推理 p50 13.99ms、p95 22.21ms、最大 32.49ms，仅为本机短测。

同一预处理张量上逐张比较 best PT 与 ONNX，143/143 的 argmax 一致，最大类别概率差 `1.61e-6`。可复算的脱敏汇总见 [评估记录](../harness/evaluations/laptop-20260911-r01.json)，人工区间见 [标注清单](../harness/evaluations/laptop-annotations-20260911.json)。

随后仅对 development 的 `23-02-14` 单片做 10fps 时序回放，共 469 个样本：open 205、closed 209、unknown 55，合盖事件为 0。原因是二分类过渡帧被正确拒识为 unknown，而保守时序在 unknown 时清除了 open 基线。这个结果明确否定“r01 已完成合盖事件功能”的说法；其余 6 段尚未据此记录为已回放。

## 局限与下一步

open 训练样本只有一个源视频、24 张强相关帧，数据量很小；development val 的多轮开合也只来自一个源视频。训练成功和逐帧分类分数不能证明合盖事件可靠，更不能证明半合、遮挡或移出能在连续视频中始终拒识。当前 3 个 open→closed 错判会直接威胁事件语义。

模型保持实验状态。r02 必须先在全部 development 视频完成逐帧与事件回放，验证稳定 open→partial→closed、半合、遮挡、移出反例和额外事件；只有开发检查通过才考虑独立验收。验收失败后若用该片改进，必须将它降为 development 并重新补独立验收。若全图背景捷径或错误仍明显，再以开发片对照固定笔记本 ROI，但在校准可用位置证据前不宣称局部分类完成。

r02 最小探索将“笔记本明确可见且处于中间角度”定义为第三类 `partial`，只允许时序桥接 open→partial→closed；遮挡、移出和不可判断仍为 unknown 并清基线。冻结三类标注见 [r02 标注](../harness/evaluations/laptop-annotations-20260911-r02.json)。4fps 数据为 train closed/open/partial 80/48/12、development val 74/80/46；训练 partial 全来自一个开发源，相邻帧不算独立数据量。

r02 使用与 r01 相同的固定训练参数。正式拒识评估在 200 张 development val 上为 147 正确、13 unknown、40 错误，全样本准确率 73.5%，accepted 准确率 78.61%。partial 只有 21/46 正确，14 错成 open、1 错成 closed、10 unknown；更严重的是 21/74 的稳定 closed 被错成 partial。PT/ONNX 在 340 张图上 argmax 无差异，最大概率差 `2.50e-6`，说明失败来自模型而非导出。

7 段完整 development 回放共 3761 个 10fps 样本，原始输出 closed/open/partial/unknown 为 3164/375/175/47，最终合盖事件仍为 0。模型把稳定合盖帧过多识别成 partial，而且 partial 段持续过久，使保守时序按最大转换时长清除 open 基线。这个端到端结果失败，r02 不进入独立验收，也不接入产品。脱敏结果见 [r02 评估](../harness/evaluations/laptop-20260911-r02.json)。后续需要更多独立的可见转换素材或可靠的笔记本局部定位，再重新训练；不能通过放宽 unknown 或延长转换保护来把本轮结果写成通过。

## 固定 ROI 对照 r03

在追加素材前，使用既定 ROI `(0.17, 0.32, 0.75, 1.0)` 完成最后一次有界对照。人工复核 7 段 development 的代表性原帧后，合盖盖面和开盖键盘/屏幕角度均有足够可见部分；人物遮挡或笔记本离开 ROI 的画面仍应拒识。r02 数据缓存保持完整 1920×1080 原帧，训练变换按清单裁一次 ROI，评估的正式 `OnnxClassifier` 也从完整原帧按同一清单裁一次，没有二次裁剪。

r03 沿用 r02 的 140/200 张 train/development val 和相同 20 epochs、320、batch 8、seed、freeze 参数，改为 CPU 4 线程训练。development val 为 122/200 正确、56 unknown、22 错误，全样本准确率 61.0%，accepted 准确率 84.72%。partial 仅 4/46 正确，32 unknown、9 错成 open、1 错成 closed；closed 为 49/74 正确，open 为 69/80 正确。PT/ONNX 340 张图 argmax 无差异，最大概率差 `1.61e-6`。

保持 0.75/0.2 拒识与 `LaptopTimeline` 不变，对关键 development 视频完整 10fps 回放：`23-02-14` 469 样本、`23-05-02` 1160 样本，两者合盖事件仍均为 0。固定 ROI 没有打通端到端事件链，因此 r03 同样不进入独立验收或产品集成。结果见 [r03 评估](../harness/evaluations/laptop-20260911-r03.json)。由于原 development val 已包含尚未参加训练的多轮 partial，下一轮先冻结参数并把全部已有 development 源作为 train-only 候选，不直接要求补拍。

## 全 development 训练候选 r04

r03 后没有立即要求补拍，因为原 development val 中已有多轮可见 partial。冻结模型选择和参数后，将 r02 人工标注中的全部 7 个 development 源整体转为 train-only；没有改标签，也没有读取独立验收片。派生清单记录基础标注 SHA，最终 4fps train 为 closed/open/partial 154/128/58。模型沿用固定 ROI、20 epochs 和其他超参，使用 CPU 4 线程，按约定选择最后一轮；训练清单明确 `validation.mode=none_train_only`，不得引用训练过程中的占位字段作为验证指标。

同源训练帧拟合检查为 339/340 正确、1 unknown，只证明拟合。更严格的 7 段完整同源 development 回放共 3761 个 10fps 样本，只产生 1 个 `laptop_closed`：`23-02-14` 的 25.3 秒；这段含多轮合盖，而 `23-05-02` 的开合仍为 0 个事件。即使使用全部已有开发正例，端到端链路仍明显不足。因此 r04 在独立验收前停止，不读取验收视频、不接入产品。脱敏证据见 [r04 评估](../harness/evaluations/laptop-20260911-r04.json)。下一步确实需要新的开发证据或不同的定位/时序表征，且不能靠放宽 unknown 保护制造事件。

复现入口：

```bash
.export-venv/bin/python -m scripts.prepare_laptop_data --annotations harness/evaluations/laptop-annotations-20260911.json --registry harness/evaluations/recognition-20260911-sources.json --output harness/artifacts/laptop-20260911-data --sample-fps 2
.export-venv/bin/python -m scripts.train_laptop_model --data harness/artifacts/laptop-20260911-data/laptop --weights harness/artifacts/behavior-20260909-r01/yolo26n-cls.pt --output harness/artifacts/laptop-20260911-r01 --epochs 20 --imgsz 320 --device mps --batch 8 --seed 20260911 --freeze 5
.export-venv/bin/python -m scripts.evaluate_laptop_model --model harness/artifacts/laptop-20260911-r01/weights/best.onnx --training-manifest harness/artifacts/laptop-20260911-r01/training-manifest.json --dataset-manifest harness/artifacts/laptop-20260911-data/manifest.json --output harness/artifacts/laptop-20260911-r01/development-evaluation.json
.export-venv/bin/python -m scripts.check_laptop_export --training-manifest harness/artifacts/laptop-20260911-r01/training-manifest.json --dataset-manifest harness/artifacts/laptop-20260911-data/manifest.json --output harness/artifacts/laptop-20260911-r01/export-consistency.json
```

## 2026-09-12 用户简化状态定义（覆盖前述三类设计）

当前仅展示开着、合上、未知：可辨认且未完全合上（含半开和可辨的开合过程）为open；可辨且完全闭合为closed；遮挡、不在画面、模糊或无法判断为unknown。未知是拒识结果，不通过一类未知训练图片覆盖所有故障。

新增r05标注文件`harness/evaluations/laptop-annotations-20260912-r05-binary.json`，基于旧人工区间仅将明确partial映射为open；未标注和unknown区间不变。当前本机r05数据为open186、closed154，共340张，保留旧版本及SHA来源。用户完成定义复核后已据此训练；实际结果见下节，未宣称新定义检测质量通过。

LaptopTimeline取消partial状态与三秒过渡桥接，仅由连续有效且确认的open→closed/closed→open触发动作。未知、断流、场景变化仍清基线，启动看到合盖不补事件。模型加载仅接受open/closed分类头；旧partial模型拒绝加载，不能改清单冒充新模型。二类模型训练与开发回放结果见下节。

## 二分类固定 ROI r05 结果

2026-09-12 00:46—00:52 +08，使用已复核的 7 个 development 源、固定 ROI `(0.17, 0.32, 0.75, 1.0)` 和 340 张 train-only 图（open 186、closed 154）完成 20 epochs CPU 4 线程训练。继续沿用 320、batch 8、seed 20260911、freeze 5 和既定优化参数，按冻结约定选择最后一轮；训练耗时约 355 秒只作为作业记录，不是独立性能结果。训练清单只包含 `closed/open` 两类，ONNX SHA256 为 `c15dd7b5641a81794a4b1446bba4ea0714080d948951f85d60909672456971e3`，没有把旧三分类模型改名复用。

同源 340 帧拟合检查为 340/340，0 unknown；PT/ONNX 340/340 argmax 一致，最大概率差 `2.98e-7`。这些结果只证明拟合和导出一致。随后对注册的 7 段 development 视频完成 10fps 回放，共 3761 个采样。稀疏人工标注的 closed 为 379 正确、3 错成 open、3 unknown；open 为 462 正确、1 错成 closed、2 unknown。其余 2911 个采样没有逐帧真值，不能当作 unknown，也不能据此计算缺席或遮挡误报；r05 尚缺明确的可见性反例验证。

按新的二类语义复看动作，`23-02-14` 的 33.9 秒 closed 是半开/开盖后再次完全合盖，不应沿用旧三类语义称为重复误报；该片还需要按画面冻结完整动作真值后评分。`23-05-02` 的明确 open→closed 暂为 0 事件：71.8 秒画面已接近完全闭合，但模型输出低边际 unknown，清除了 open 基线，之后只能把 closed 当作重新初始化状态。不能靠降低拒识阈值规避；下一轮以同一人工区间提高开发采样密度，检查相邻帧鲁棒性。

r05 的新语义动作真值在 r06 预测前人工冻结：`23-02-14` 有 6 个动作，`23-05-02` 有 2 个，其余 5 段无动作；真值及修订原因见 [二类动作清单](../harness/evaluations/laptop-development-actions-20260912-binary.json)。r05 命中 4/8、漏 4、额外 0。

## 密集采样单变量对照 r06

r06 保持来源、人工区间、固定 ROI、模型、20 epochs 和全部训练参数不变，只将训练抽帧从 4fps 提高到 10fps，得到 open 465、closed 385。2026-09-12 00:59—01:13 +08 使用 CPU 4 线程完成 train-only 训练；同源拟合 850/850，PT/ONNX 850/850 argmax 一致，最大概率差 `1.19e-7`。ONNX SHA256 为 `eae21d419fab75f6adfa882b4895b69326934c13516578da4351adbd9a2b4481`。

冻结动作评分改善到 6/8、漏 2、额外 0；5 段无动作视频仍为 0 额外事件。`23-05-02` 的 opened 55.5 秒、closed 72.4 秒均命中，说明密集采样修复了本轮重点边界。`23-02-14` 命中 opened 4.6、opened 27.5、closed 34.0、opened 35.8，仍漏 closed 24.5—26.0 和 closed 44.0—45.5。增加密度使部分边界改善、另一些动作仍被 unknown 清基线，不能继续靠堆相邻帧或训练轮次追事件。

r06 因 6/8 未通过 development 门槛，独立验收视频始终未读取，r05/r06 均不接入产品。下一步应在单独、连续的笔记本可见性正证据成立时，验证是否允许短暂跨越分类边界 unknown；真实遮挡、缺席、模糊、断流和场景变化仍必须清基线。缺少这组显式 development 证据前不再训练，也不开启 holdout。脱敏汇总见 [r05/r06 评估](../harness/evaluations/laptop-20260912-r05-binary.json)，详细产物留在本机 `harness/artifacts/laptop-20260912-r06-dense`。
