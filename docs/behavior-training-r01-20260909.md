# 新视角在座与饮水模型：首轮试训结果

2026-09-09，Asia/Shanghai。已完成两套YOLO26n-cls本地微调、ONNX导出、CPU一致性检查及源视频回放。当前是训练素材上的原型复测，**尚不能报告独立准确率或正式可用**。物品模型、正式配置及Agent业务未替换。

## 输入与模型划分

| 素材 | 实际内容 | 监督用途 |
|---|---|---|
| 23-00-14，约17.48秒 | 持续在座、操作电脑、摸头 | 坐姿及非饮水 |
| 23-00-33，约19.95秒 | 起身、离开、空座、返回坐下 | 坐姿、站立、空座及非饮水 |
| 23-00-54，约28.75秒 | 两次杯口靠嘴动作、离座返回、持杯不喝、摸脸 | 姿态与疑似饮水 |

三段均为1080p/60fps，完整解码分别1049、1195、1724帧；容器估计帧数可能略有差异，未用容器数量冒充实际解码。人工审阅1fps拼图并以0.25秒间隔核对转场与饮水边界，root复核并收窄部分模糊区间。完整图像、时间标签和SHA留在本机。

两种监督任务分开，坐着与饮水可以同时成立：

- 姿态模型：empty、seated、standing。4fps采样排除未知区间后244图，分别40、189、15。
- 饮水模型：drinking、not_drinking。260图，分别23、237。仅杯口接近嘴部的可见动作作为正例，不标注实际吞咽或饮水量。
- 起身/坐下：由连续姿态状态转换判定；一张站立图片本身不能证明起身。

饮水正例仅在一个源视频里，无法将三段整视频划成均覆盖动作的train/val/test。本轮全部train-only，不随机拆相邻帧，也不以训练集选优。后续需新独立视频。

## 研究选择与实际参数

首轮选择两个轻量分类模型，复用YOLO部署技术栈。[官方分类说明](https://docs.ultralytics.com/tasks/classify)支持YOLO26n-cls微调及ONNX，并明确默认分类裁剪可能丢失关键画面。因此实现保留全画幅的等比填充：320×320、RGB114、FP32/NCHW/0—1；训练仅轻微颜色扰动，禁用翻转、擦除和随机裁剪。未把[姿态模型](https://docs.ultralytics.com/tasks/pose)或[AVA动作类别](https://research.google.com/ava/download/ava_action_list_v2.2.pbtxt)的能力当作本项目已实现结果。

共同参数：官方ImageNet预训练YOLO26n-cls起点，20轮，batch8、freeze5、AdamW lr0=.0002/lrf=.1、weight_decay=.0005、warmup2/warmup_bias_lr0、dropout.1、seed20260909、MPS、amp=False、workers0。环境Python3.11.16、PyTorch2.14.0、Ultralytics8.4.142，与正式ORT环境隔离。

姿态训练含导出162.33秒，饮水165.77秒，均实际完成20轮并选last.pt。框架初始化使用train占位val loader，实际validate/final_eval被禁用；日志中的“val images”不得当成执行验证。完整实际参数、训练曲线及权重SHA见本机各training-manifest.json；脱敏记录见[汇总](../harness/evaluations/behavior-20260909-r01.json)。

## 已执行检查与发现

1. **导出一致性**：504个任务标注样本用相同输入比较PT与ONNX，全部top1一致，概率最大绝对差1.1921e-7（验收容差1e-4）。另核验类别元数据、FP32静态输入输出和概率归一化。
2. **训练素材复测**：姿态244/244、饮水260/260与标签匹配。这只是拟合检查，不能解释成100%实际准确率；过渡帧没有被算入这些标注样本。
3. **原视频2fps时序回放**：产生2次起身、2次坐下、2次疑似饮水、1次离座事件；root复核全部7张事件图，动作含义相符。两次起身和两次饮水在本素材中都被捕捉，没有重复饮水事件。
4. **保留失败**：第二段有一次离座未形成事件。3.5秒姿态最高分.7417低于固定.75门限，状态变unknown；4.5秒空座状态已确认，但前序状态被清除，未生成left_seat。空座状态识别与离座事件不能混为同一指标。未降低门限来抹去失败，后续独立素材重点覆盖这一过程。
5. **故障与实现检查**：过期输入不产生新事件；空座/未知人体证据阻止饮水确认；完整离线206 passed、3项真实API排除，Ruff与格式检查通过。无云端Agent调用。
6. **短时CPU测量**：每模型2线程、20预热/100计时，包含全图预处理与ORT；姿态p50/p95=9.35/9.76ms，饮水9.31/9.63ms。不包含摄像头、界面、JPEG或模型加载，不代表持续运行/Windows性能。

## 复现入口与产物

准备数据：

```sh
.venv/bin/python scripts/prepare_behavior_data.py --annotations data/behavior-inbox-review-r01/annotations-root.json --output data/behavior-20260909-r01 --sample-fps 4
```

训练（两次分别替换TASK为posture、drinking；输出目录必须新建）：

```sh
.export-venv/bin/python -m scripts.train_behavior_model --data data/behavior-20260909-r01/TASK --weights harness/artifacts/behavior-20260909-r01/yolo26n-cls.pt --output harness/artifacts/behavior-20260909-r01/TASK-run --train-only --seed 20260909
```

评估（已存在evaluation目录时使用新目录）：

```sh
.export-venv/bin/python -m scripts.evaluate_behavior_models --posture-manifest harness/artifacts/behavior-20260909-r01/posture-run/training-manifest.json --drinking-manifest harness/artifacts/behavior-20260909-r01/drinking-run/training-manifest.json --data-manifest data/behavior-20260909-r01/manifest.json --annotations data/behavior-inbox-review-r01/annotations-root.json --output harness/artifacts/behavior-20260909-r01/evaluation
```

权重位于各TASK-run/weights/last.onnx。私人视频、图片、标签、模型和完整日志均不提交仓库。官方权重来源为[Ultralytics v8.4.0发布](https://github.com/ultralytics/assets/releases/tag/v8.4.0)，沿用Ultralytics许可证。

## 需要补充与未完成内容

不需要用户提供预训练模型。建议固定本机位再录3段独立完整流程，每段1—2分钟：一段补训练，另外两段分别留作验证和最终测试。每段包括空座10秒、稳定坐着、起身后站稳3—5秒、离开、返回坐下，以及3—5次自然杯口靠嘴动作；穿插拿杯不喝、移动杯子、摸脸/头、弯腰取物、短暂路过。无需换人、换衣服、换机位或等待另一天，不要求实际反复吞咽。

原3段可继续使用，不必重拍。最终测试素材不用于挑轮次或调阈值。当前尚未完成独立动作准确率、离座事件缺口修复、真实摄像头行为/UI/Agent联合及Windows实机验收；新行为模型没有自动接入正式页面。
