# behavior-r03：时序改进与局部模型对照

2026-09-10，Asia/Shanghai。已完成实验代码、一次饮水ROI训练、同帧离线消融和事件画面复核。当前优先候选是原r02分类模型＋10fps＋分离确认时间＋人体框辅助；ROI模型没有额外收益，不采用。正式行为功能尚未接入，质量验收仍开放。

## 先修正此前的标签错误

原r02测试饮水视频的2.375—3.625秒被误标为饮水。root复查原始连续画面，并由未看旧标签及预测的Sol独立复核，确认该阶段只是拿杯，杯口未到嘴；整段应有两次饮水动作，原基线匹配应为**1/2，而非1/3**。此前将第一次归因为模型分类错误的结论撤回。

旧标签和r02结果完整保留。新修订只去掉这一错误正例区间及事件，其余历史边界不改；原标注SHA、修订SHA及盲审SHA保存在[脱敏记录](../harness/evaluations/behavior-20260910-r03.json)。历史0.25秒只是较窄的标注核心，不能据此断言实际整个饮水动作只有0.25秒。

本轮4段视频已用于诊断和选参数，全部按development处理。其中两段还参与了r02训练；另外两段虽未进入权重训练，也已不再是独立最终测试。约133秒素材不能证明长期误报率。

## 已实现的改动

- `behavior_timeline_v2.py`独立于旧状态机，保留可重放基线。当前状态、历史上下文和待确认状态分开；unknown当前仍输出unknown。只有外部明确提供连续可见证据，才允许在有限时间内保留历史上下文；恢复时重新确认。断流、陈旧帧、逆序和超时清除上下文，不跨故障补写动作。
- 姿态、饮水开始、饮水结束采用独立计时；饮水最少两个新鲜采样点，同一持续动作不重复报。最终实验参数：10fps、姿态确认0.3秒、饮水确认0.5秒、结束确认0.3秒、最长帧间隔0.25秒、unknown历史保留上限1秒。
- `behavior_visibility.py`以现有本地YOLO26n的person框提供辅助连续信号：仅一个置信度至少0.5的有效框、相邻IoU至少0.5、间隔不超过0.25秒；缺失、跳框、多强候选和尺寸改变会重置。
- `behavior_experiment.py`缓存10fps的同一批原始帧，再抽取2/5/10fps作比较。模型、源视频、标注和缓存均记录SHA；人体缓存验证metadata哈希、全量帧键与有限时间戳，禁止缺帧错位或重复记录。修订标注须说明理由并保持相同源清单，但人工标注正确性仍靠独立复核。
- 训练与PT/ONNX推理共用ROI裁剪及等比填充。ROI模型绑定1920×1080源尺寸，收到不同尺寸时拒绝推理，要求重新校准；尺寸不变的机位移动仍需人工识别和重新校准。

**人体框仅为实验代理，不等于手、嘴、杯口或完整起身过程无遮挡。** person-proxy配置显式把这一较弱信号代入连续性假设，用于观察漏报是否来自状态重置；不能据此认为遮挡安全已经解决。人体仍出现在画面其他区域，也不等于仍在座位，当前未实现座位ROI与人体框的冲突裁决。

## 相同素材、相同标注的比较

每类一对一匹配，沿用事件窗口前后各1秒容差；分母为4次起身、4次坐下、3次离座、6次疑似饮水。数字是窗口匹配数，不是精确时点或独立准确率。

| 原r02分类器＋时序方案 | 起身 | 坐下 | 离座 | 疑似饮水 | 未匹配额外事件 |
|---|---:|---:|---:|---:|---:|
| 原时序，2fps | 3/4 | 3/4 | 3/3 | 5/6 | 0 |
| 原时序，10fps | 1/4 | 1/4 | 3/3 | 5/6 | 0 |
| v2无人体辅助，5fps | 3/4 | 2/4 | 3/3 | 6/6 | 0 |
| v2人体辅助，5fps | 3/4 | 4/4 | 3/3 | 6/6 | 0 |
| v2人体辅助，10fps | 3/4 | 4/4 | 3/3 | 6/6 | 0 |
| 上一行＋姿态确认0.3秒 | 4/4 | 4/4 | 3/3 | 6/6 | 0 |
| 上一行＋饮水确认0.5秒（优先实验候选） | 4/4 | 4/4 | 3/3 | 6/6 | 0 |

直接增加采样率会暴露更多低置信过渡帧，原状态机反而更容易清掉前态。人体辅助可以保留部分上下文；剩下一次起身在0.5秒确认完成前遇到人体检测暂时低分，0.3秒确认在连续证据仍成立时完成。这是看过失败后的开发参数选择，不是预先冻结的独立验证。

最初0.1秒饮水确认容易在举杯阶段触发。额外对照0.1/0.3/0.5秒：原分类器均匹配6次；0.5秒使开发饮水片的提示由4.2/10.9/21.3/29.5秒变为4.6/11.3/21.7/29.9秒，另一片由9.9/27.9秒变为10.3/28.3秒，因此采用较长确认作为下一轮候选。

root与Sol复核全部17个事件的前后画面，并扩大检查两次饮水的原尺寸连续序列。小尺寸三帧拼图曾把4.2秒误判成未到嘴，扩大后确认动作存在；29.5秒属于提前提示，不是整段虚构动作。新29.9秒事件仍约早于清晰接触0.1秒，只能称疑似杯口到嘴。不能从画面证明实际吞咽、饮水量或健康效果。

为显露容差影响，评分同时输出零容差结果：最终候选仅10/17事件位于旧标签窗口内（其余7个在窗口外、但在1秒容差内）。其中姿态5/11、饮水5/6；历史过渡标签较粗、饮水末次核心区间较窄，零容差也不是重新标注后的真值。两种口径同时保留，不把17/17写成延迟验收或100%准确率。

## ROI训练结果

沿用r02的5个训练视频、508张原尺寸饮水图（55正/453负），只改变输入为归一化ROI `[0.25,0,0.75,0.85]`，从训练素材选取。官方YOLO26n-cls初始化；320输入、MPS FP32、AdamW、lr0=0.0002、freeze5、batch8、seed20260909、固定20轮、train-only、取last，不使用最终片段训练。

实际13:33:21—13:38:24，303.10秒，训练与ONNX导出成功。每种分类器组合均回放1332个10fps原始采样，逐帧检查PT/ONNX：原组合最大概率差6.23e-6，ROI组合5.75e-6，均小于1e-4且类别一致。

ROI在5fps无辅助分支只匹配4/6饮水，原模型6/6；最终0.5秒确认候选下ROI为5/6，原模型6/6。没有理由替换原模型。类别加权和更大模型本轮未运行：当前标签问题、采样及时序已经解释主要差异，先验证候选，不为完成实验清单继续增加训练花费。

## 验证、复现与剩余工作

离线回归246 passed、1 skipped、3 deselected（10.67秒）；主环境跳过的Torch变换检查已在训练环境执行，训练/预处理相关15项通过。评分新增配置后相关26项复验通过；Ruff、156个文件格式和diff检查通过。未进行真实摄像头行为、长时负例、完整应用10fps、Windows或云端Agent联合验收。云端模型调用0，生产配置、物品模型、数据库及.env未修改。

实际命令使用现有隔离环境（输出目录必须另取新名字才能重跑）：

```sh
.export-venv/bin/python -m scripts.train_behavior_model --data data/behavior-20260910-r02-train/drinking --weights harness/artifacts/behavior-20260909-r01/yolo26n-cls.pt --output harness/artifacts/behavior-20260910-r03/drinking-roi-run --epochs 20 --imgsz 320 --device mps --batch 8 --seed 20260909 --freeze 5 --train-only --roi 0.25 0 0.75 0.85 --source-size 1920 1080
.export-venv/bin/python -m scripts.behavior_experiment collect --posture-manifest harness/artifacts/behavior-20260910-r02/posture-run/training-manifest.json --drinking-manifest harness/artifacts/behavior-20260910-r02/drinking-run/training-manifest.json --annotations data/behavior-review-20260910/annotations-r03-development.json --output harness/artifacts/behavior-20260910-r03/baseline-cache --fps 10
.venv/bin/python -m scripts.behavior_experiment score --cache harness/artifacts/behavior-20260910-r03/baseline-cache --output harness/artifacts/behavior-20260910-r03/baseline-ablation-complete.json --annotations data/behavior-review-20260910/annotations-r03-development-rev2.json --annotation-revision-note 'Blind source review corrected only the first false drinking interval; source videos are development-used.' --person-cache harness/artifacts/behavior-20260910-r03/person-cache/person-predictions.jsonl
.venv/bin/pytest -q
.export-venv/bin/python -m pytest tests/test_train_behavior_model.py -q
```

ROI缓存collect只替换饮水manifest为本轮drinking-roi-run，并写到roi-cache；相同score写roi-ablation-complete.json。完整日志、源标签、图片、模型及逐帧结果留本机忽略目录；仓库仅保存脚本、测试和脱敏汇总。原始资料及设计依据沿用[改进分析](behavior-improvement-r02.md)，本轮新增结论来自实际本地实验。

下一步需要同一固定机位的新素材验证：自然坐着/短暂站立/离座返回，快慢饮水，拿杯靠脸但不喝、摸脸、擦嘴、遮住脸或杯口等反例，以及较长无动作片段。无需换人或换场景。冻结参数后作独立计分，再决定是否增加嘴杯关系特征或修订标签后重训。人体框与真实遮挡、座位区域关系仍要验证，不能仅凭本轮事件匹配启用正式提醒或累计行为时长。
