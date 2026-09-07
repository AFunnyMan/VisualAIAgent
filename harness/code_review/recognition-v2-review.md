# 第二轮识别实验独立审查

审查日期：2026-09-07，Asia/Shanghai。

范围：`harness/context/recognition-v2.md`、`scripts/recognition_input_trial.py`、
`tests/test_recognition_input_trial.py`、`scripts/detector_comparison.py` 的当前差异，以及
`harness/artifacts/recognition-v2-20260907` 中已有结果。未读取 `.env`，未运行任何模型推理，
未审查仍在串行执行的计时结果，也未把尚未运行的 `review` 分支当作实验通过证据。

## 发现

### [P1，文档已修正] YOLO26m 的预注册证据强度原先表述过强

原文声称在读取任何新 60 图模型输出前追加 YOLO26m，但文件时间只能证明
`m-development.json` 形成于 m 自身新集推理之前，不能证明型号决定发生在其他模型新集输出
之前或之后；型号决定时间与阈值文件完成时间也不是同一事实。context 现已如实改为“本轮中途
追加探索路线”，说明 m 阈值只由 old30 决定并在 m 自身 new60 推理前保存，同时不再声称与
首批路线具有同等强度的预注册证据。该修订与现有证据一致。

### [P2，已修复] 新旧集合排除断言原先恒真

`harness/artifacts/recognition-v2-20260907/validation/build_validation.py:55-56` 先把全部旧 ID
放入 `selected_ids`，随后 `:88` 检查
`selected_ids.intersection(excluded) != excluded`。无论新选择是否错误地包含旧图，这个条件都
不会成立，因而不能保护“new60 排除 old30”约束。审查中已改为直接比较
`{row["image_id"] for row in selected}.intersection(excluded)` 并在非空时失败。修改后完成语法
解析和当前两个 manifest 的集合复核；未重新下载数据或运行模型。

这个缺陷没有污染当前产物：独立读取两个 manifest 后，new60 有 60 个唯一 ID，与 old30
交集为空；selection 四层分别为手机、杯、瓶各 16 图和负例 12 图。

## 已核实且未发现阻断问题

- 阈值候选确为 0.10 至 0.90、步长 0.05；每类以旧 n-e2e 0.35 的 FP 作为预算，先取最大
  TP、同 TP 取更高阈值。冻结文件绑定的旧 30 图 manifest SHA-256 与现场文件一致。
- 新集计分包含 155 个三类非 crowd GT；另有 1 个目标类 crowd 标注，计分器将其仅用于
  忽略覆盖预测，不计入 TP/FN，因此“155 个实例”口径成立。12 张负例均没有三类目标标注。
- 切片是一次全图加四个固定切片，切片边界未越界，框坐标平移回原图后重新计算 region，
  最后按类、置信度降序做 IoU 0.5 NMS。测试覆盖右下切片坐标还原、边缘覆盖、同类重复框
  抑制和异类/分离目标保留。
- `s-calibrated-videos` 有 9 个逐视频 JSON、合计 488 个样本；summary 的逐类检测帧数可由
  原始样本重新计算得到。`videos` 路径调用 `run_video`，该函数在推理前校验视频 SHA-256、
  从头顺序 `grab/retrieve`，并把状态机事件与逐帧检测共同保存，因此事件具有可追溯证据。
- `scripts/detector_comparison.py` 的差异只把结果元数据中的 `proposal_floor` 修正为模型 profile
  的实际下限与 0.01 的较大值；探测器调用仍传 0.01，但导出模型自身的更高 floor 不可由
  运行时恢复，这个修改使报告口径更准确。该说明也适用于本轮传统头 m 导出，不把它误称为
  端到端模型。
- RF-DETR 的全 488 视频结果来自 PT 适配器；独立 ORT 证据仅覆盖 old30 一致性检查。归档时
  不应把前者表述为 ORT 全视频实测。

## 归档复核

- `harness/evaluations/recognition-v2-20260907.json` 含 14 组模型/改法图片结果。对每组归档
  `images` 使用相同 validation manifest 和 `scoring_floor=0.05` 重新调用 `score_dataset`，
  逐类 TP/FP/FN/ignored/precision/recall 以及汇总 totals 全部与归档一致。
- 14 个 `source_artifact` 均存在，现场 SHA-256 全部等于归档 `source_sha256`；归档引用的
  validation manifest SHA-256 也与现场文件一致。
- 归档 validation manifest 含 60 个样本；所有 `image_path` 均存在且图片 SHA-256 匹配。
  selection 含 60 个唯一 image ID，manifest 正确引用同前缀归档 selection；三类非 crowd GT
  重新计数为 155。

## 未检查或未执行

- 没有运行识别模型、视频重放或串行 benchmark，避免干扰正在进行的计时。
- 没有人工逐帧标注 488 个视频样本；视频框数和事件只作为诊断证据，不能解释为精确率或召回率。
- 没有执行真实摄像头、Windows 或生产配置验证。
- `review` 分支本轮未执行；对该分支的结论仅限静态代码检查。
