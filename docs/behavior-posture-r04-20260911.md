# 姿态 r04 隔离训练记录（2026-09-11）

## 范围与隔离

本轮只训练 `empty`、`seated`、`standing` 三类姿态。七段 2026-09-11 development 视频均先经 `harness/evaluations/recognition-20260911-sources.json` 来源核验；验收视频 `2026-09-11 23-08-00.mkv` 没有用于抽帧、训练、调参或本文开发评估。本文候选没有替换正式权重。

人工复核使用连续联系图，而非以头部是否出框判断站立。`23-05-02` 的 4.4–8.4 秒是坐姿抬臂伸展；84–96 秒抱臂后靠按用户确认标为坐姿。102 秒起身、103–111 秒空座、111.5 秒站立返回、113 秒坐下构成第二次完整离座；原事件清单漏记了这一组。49.5 秒站立入画、50.0 秒弯身、50.5 秒坐下，因此首次返回事件修订为 `sat_down`。修订依据保存在 `data/behavior-review-20260911/posture-r04-event-expectations.json`，只修正事件预期，没有改动已绑定训练清单的逐帧标签。

新来源抽取 735 张训练图：`seated=661`、`standing=51`、`empty=23`。与 r02 的 496 张旧姿态训练图合并后共有 1,231 张：`seated=1053`、`standing=74`、`empty=104`，覆盖 12 个来源。合并工具逐样本校验 `source_sha256` 必须在输入清单中登记为 `train`，拒绝路径逃逸、内容变化和重复图像。

## 训练与导出

训练命令：

```text
.export-venv/bin/python -m scripts.train_behavior_model --data data/behavior-20260911-r04-train/posture --weights harness/artifacts/behavior-20260909-r01/yolo26n-cls.pt --output harness/artifacts/behavior-20260911-r04/posture/posture-run --epochs 20 --imgsz 320 --device mps --batch 8 --seed 20260911 --freeze 5 --train-only
```

20 轮训练于 2026-09-11 23:26:18–23:46:17（Asia/Shanghai）完成，耗时 1198.63 秒。训练是 `train-only`，没有验证指标；训练来源成绩不能解释为独立验收。

- PyTorch checkpoint SHA-256：`dd8c118367b90cb656599c97567e2e39d819e27cb9dd11b7884087b0bf29317a`
- ONNX SHA-256：`b46aeb38f1dc7dcb90d162223796732b4666f192e68130e328583dab1873b1bb`
- 预训练源权重 SHA-256：`0dd6f8dbc448870ac98a3cbb7156f923f7ce21fed3755d4019169ffffd279e81`

## 开发对照

七段新视频按 2 fps 做分类回代，共 735 个已标注训练来源帧。r02 为 483/735，r04 为 735/735；r04 固定门限（confidence 0.75、margin 0.20）下 735 张全部接受且正确。r04 的 PT/ONNX 最大绝对差为 `1.1920928955078125e-07`。关键 84–96 秒共 24 帧，r02 全部接受为 `standing`，r04 全部接受为 `seated`，r04 unknown 为 0。

事件评估使用 10 fps、正式 `max_gap=0.25s`、确认时长 0.3 秒、unknown bridge 1 秒和 `SeatPersonGate`，逐采样结果保存在本机 JSONL：

- `22-59-29`：r04 为 10 TP / 0 FP / 0 FN；r02 为 5 / 0 / 5。
- `23-05-02`：按连续画面修订的事件预期，r04 为 8 / 0 / 0；r02 为 2 / 2 / 6。关键后靠段的 120 个 10 fps 样本中，r04 全为 `seated`、unknown 为 0，r02 全为 `standing`。
- 其余五段纯坐姿视频共 2,043 个 10 fps 样本，r04 没有产生事件（0 FP），r02 产生 6 个误事件。

旧机位兼容性方面，r02 的 496 张旧训练图上两模型均为 496/496，固定门限均全部接受正确；这是回代检查。既有独立测试的 241 张姿态图上两模型均为 240/241：r02 错 1 张坐姿为站姿，r04 错 1 张站姿为空座；r04 的 seated unknown 从 3 降为 1，但 standing 出现 1 个 accepted wrong。该变化需要结合新的独立验收判断是否可接受。

## 证据与命令

主要证据位于 `harness/artifacts/behavior-20260911-r04/posture/`：

- `posture-run/training-manifest.json` 与 `training.log`
- `development-all-classification/summary.json`
- `development-key-events/summary.json`、`predictions.jsonl` 和 `rescored-manual-events.json`
- `development-negative-events/summary.json` 与 `predictions.jsonl`（JSONL SHA-256 `98f776097972e46ddd48d5bb018a977d557a08d414745336fcb0e92bc2caaa05`）
- `legacy-496-classification.json`
- `legacy-final-test/summary.json`

开发评估命令分别为 `python -m scripts.evaluate_posture_models`（2 fps 分类）和 `python -m scripts.evaluate_posture_events`（10 fps 事件）。代码质量及数据合并测试：

```text
.export-venv/bin/python -m ruff check scripts/create_behavior_review_sheets.py scripts/prepare_behavior_data.py scripts/merge_behavior_training_data.py scripts/evaluate_posture_models.py scripts/evaluate_posture_events.py tests/test_prepare_behavior_data.py tests/test_merge_behavior_training_data.py
.export-venv/bin/python -m pytest -q tests/test_prepare_behavior_data.py tests/test_merge_behavior_training_data.py
```

结果为 Ruff 通过、9 项测试通过。

## 限制

七段新视频及其中 735 张分类样本参与了训练，只能说明开发回代行为。84–96 秒覆盖抱臂后靠；仍缺少不抱臂后靠素材。独立验收由未接触验收视频预测的流程另行执行，结果不在本文中预设。


## 2026-09-12 独立视频结果

root在候选冻结后于00:00:38—00:04:25（Asia/Shanghai）执行 `scripts.evaluate_posture_holdout`。视频源SHA、人工真值SHA、候选SHA先检查，未修改训练参数、拒识门槛或事件预期；结果见[脱敏汇总](../harness/evaluations/posture-r04-independent-20260912.json)。完整命令参数补记在本机`root-independent-20260912/invocation.json`，预测前冻结校验保存在`frozen.json`，逐样本证据为同目录`observations.jsonl`，SHA `c81580f2f437fd0c2852a74fb9a55851a5e6855cc3cca72fc50d035df8856b82`。

176.7秒验收片以10fps解码得到1767采样，其中1699有明确稳定状态真值，68个过渡/未标注采样不纳入分类准确率。r04固定拒识门下1699/1699正确：在座1531、站立148、空座20；56—58秒伸展20/20和114—117秒抱臂后靠30/30均为在座。r02在相同稳定样本上786正确、292错误、621未知；两个困难区间共50帧均误判站立。不能把相关连续帧视为1699次独立试验或长期100%准确率。

动作评分使用事前人工窗口且不加额外容差：r04检出4/5、0额外事件，r02检出2/5、2额外事件。r04起身63.9秒、坐下79.4秒、起身118.4秒、返回坐下123.2秒；漏一次离座。118.8秒出现unknown且人体连续支持为false，旧站姿上下文按既有保护清除；119.4秒已确认空座，但未补造left_seat事件。这是明确的动作层缺口，不能因稳定分类分数高将其改为通过。

候选继续标为实验，未替换正式默认权重。后续需解决离开边缘时的连续动作证据，并补真实摄像头动作验证与不抱臂后靠。若用本验收片进行新模型/状态机选优，它以后只能作为开发回归，必须另留新独立片段。本次没有进行这些后续调参。
