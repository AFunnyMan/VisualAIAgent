# 座位人体关联与坐下事件改进

2026-09-10，Asia/Shanghai。本轮保留r02姿态/饮水权重和v2时序参数，改进输入证据关联；没有新增训练或云端模型调用，正式行为集成仍未验收。

## 程序改动

新增`SeatPersonGate`：人体中心须在固定活动区域`[0.2,0.2,0.95,1.0]`内；唯一置信度≥0.5的真实人体框建立目标，下一帧匹配后才提供连续性支持。已有目标可以使用≥0.25的真实低分框短暂延续，最多距上次强框0.4秒，且每次都须满足IoU≥0.3、中心距离≤画面对角线0.15。低分框不能新建目标；没有真实候选、多匹配、时间异常、采集故障和超过0.25秒的间隔均重置。

这让短暂分类不确定时，有条件保留此前站立上下文；当前输出仍为不确定。人体框关联只是几何辅助，不能证明动作完整可见，也不是身份识别。实际效果须通过新动作和遮挡反例验证。

实时与离线入口均增加新分支，旧严格分支保留。实时对照复用同一帧分类概率和人体候选，不重复执行模型推理。日志保存全部人体候选、选择原因与旧逻辑事件；不确定/连续性中断/事件触发前后最多保存8组、每组9张静态证据，图片与该次观测及SHA一一对应。故障清空图片缓冲，私人图片留本机。

## 已完成的离线验证

- `.venv/bin/pytest -q`：摄像头结束后最终复验269 passed、1 skipped、3 deselected，10.71秒。新增测试覆盖低分框不能创建目标、有界延续、多人歧义、断帧/过期/倒序、诊断证据边界，以及unknown期间保持拒识但后续恢复坐下事件、真实故障不得补事件。
- `.venv/bin/ruff check .`及格式检查通过，165个文件格式符合要求。
- 同帧旧缓存对照：原严格人体分支和新增座位分支均匹配17/17事件，未匹配额外事件0，沿用±1秒匹配窗口。四段视频已经用于开发，只能作为退化检查，不能据此计算独立准确率。
- 原始结果在忽略目录`harness/artifacts/behavior-seat-20260910/`；参数在评分和实测前写入`experiment-plan.json`。上次真实测试的坐下漏报仍保留于[原报告](behavior-live-test-20260910.md)，不能用本轮开发分数覆盖。

实际离线命令：

```sh
.venv/bin/python -m scripts.behavior_experiment score \
  --cache harness/artifacts/behavior-20260910-r03/baseline-cache \
  --output harness/artifacts/behavior-seat-20260910/offline-comparison.json \
  --annotations data/behavior-review-20260910/annotations-r03-development-rev2.json \
  --annotation-revision-note 'Existing independently reviewed correction; same development sources; compare association only.' \
  --person-cache harness/artifacts/behavior-20260910-r03/person-cache/person-predictions.jsonl
```

## 实机验证

实际启动命令（每次需要新的输出目录）：

```sh
.venv/bin/python -m scripts.behavior_camera_test \
  --posture-manifest harness/artifacts/behavior-20260910-r02/posture-run/training-manifest.json \
  --drinking-manifest harness/artifacts/behavior-20260910-r02/drinking-run/training-manifest.json \
  --output data/acceptance/behavior-seat-live-20260910-session1 \
  --duration 300 --camera 0 --width 1920 --height 1080 --port 8765 \
  --person-association seat --seat-roi 0.2 0.2 0.95 1.0
```

16:57:25启动，首末新鲜画面为16:57:34.681—17:02:34.634，17:02:35结束，进程退出0，worker_stop_error为空。AVFOUNDATION实际1920×1080，新鲜2809次，约9.36fps；启动等待80次，过期4次安全丢弃，正常停止不计故障。新鲜推理耗时p50/p95为73.81/76.497ms。

新旧逻辑均输出0事件，新鲜观测中的确认状态为空座2794次、不确定15次，座位区域内强人体候选均为0。期间请求用户演示两次坐站及离座返回，但截至会话结束未收到完成回复；人工抽查预览为空座，未保存或人工标注全程录像。因此只能报告本段未触发事件，不能把它当动作召回测试或严格的零误报率验收，更不能宣称上次坐下漏报已修复。

0个事件图、0组诊断图符合本段无相关触发的记录。逐帧候选、时间和旧分支结果仍保存于本机忽略目录；脱敏时间、模型/代码SHA和指标见[汇总](../harness/evaluations/behavior-seat-20260910.json)。服务已随限时测试结束关闭。

Sol只读复审未发现当前实验的阻断问题。限制包括：故障帧不进入静态证据组；汇总旧事件须关联逐帧日志才能审查；离线分支固定默认ROI。本轮没有遮挡/弯腰/拿杯反例、USB重插、长时或Windows实机验收。下一步需要新的真实坐站动作来判断本轮改动是否补回坐下事件，并检查是否新增误事件。
