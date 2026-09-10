# 行为推理延迟改进与失败诊断

2026-09-10，Asia/Shanghai。用户要求修复第二次实测的坐下漏报。[原始失败](behavior-seat-live-session2-20260910.md)由过期推理导致状态上下文清空；本轮保持0.25秒新鲜门限、状态确认和故障重置，优先减少推理耗时并补齐失败诊断。

## 已实现

- `SceneObservation`新增可选分阶段耗时与阶段字段，旧调用兼容。采集read、推理前画面年龄、detect、annotate、JPEG和发布前年龄均绑定本次输入；失败路径也记录，异常标明实际阶段。上一次回调耗时单列，不混成本帧推理。
- 行为入口保存本次模型预处理、ONNX、人体候选解码、图片复制/哈希耗时。过期推理的结果只进入`inference_attempt`诊断，凭完成时间和序号拒绝复用旧尝试，不进入状态和事件计算。新鲜样本统计与包含失败的统计分开。
- 人体辅助ONNX可以显式设置线程数和空转等待；当前10核Mac采用4线程、关闭空转。OpenCV请求1线程在本机GCD构建上仍报告10，未实际限制为1；记录请求值与实际值，不将此项当有效优化。较小CPU默认2线程，Windows性能尚未验证。两个行为分类器仍各2线程，输入与模型权重不变。
- 人体和饮水推理各使用一个本地有界执行线程，与主线程的姿态分类并行。每次任务绑定当前帧，主线程共用从本轮开始计时的120ms等待预算；超时任务继续占槽，下一次提交直接busy，不积压队列，不拿迟到结果当下一帧证据。人体超时清空辅助连续性，饮水超时输出unknown；姿态仍须通过VisionWorker原0.25秒检查。停止时有界回收两条线程，未退出明确报错。
- 运行启动时记录实际代码SHA，避免结束时文件变化混淆版本。

依据[ONNX Runtime官方线程说明](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)，线程数和空转等待影响CPU资源使用，效果须在实际模型上测量。没有据文档直接宣称更快，也没有增加云端调用。

## 实际比较

使用上次会话7张不同姿态事件图，原模型，10fps节奏。原始数据位于忽略目录`harness/artifacts/behavior-latency-20260910/`。

1. OpenCV请求10/1线程交替，各70次，共280次：中位约71.6—72.8ms；后续核对本机GCD实际都报告10，所以不能当实际单/多线程对照。此项不作为修复有效性依据。
2. 人体ORT 2线程空转、1线程不空转、2线程不空转、4线程不空转，再重复2/4两配置；各42次，共252次。4线程两轮中位62.33/61.48ms、p95 66.32/63.41ms；原2线程空转两轮中位72.78/72.91ms、p95 84.78/74.79ms。1线程反而变慢（中位108.10ms），不采用。
3. 各配置人体候选数量/顺序相同，所有候选数值在`rtol=1e-4, atol=0.01`内一致，不声称逐位相同。初次严格相等断言因浮点差异失败，保留为试验过程，之后明确数值容差重跑。没有改变权重、输入或判断阈值。
4. 增加诊断后的旧配置60秒真实摄像头：580 fresh、0 stale、正常退出0；detect p50/p95=69.094/73.454ms，最大86.795ms。未复现原来的过期失败，不能据此确定历史突发延迟的内部来源。

仅改线程后的实机复现了两次过期，第一次人体ONNX约204.6ms、整个detect271.9ms；第二次饮水ONNX165.4ms、人体96.3ms、整个detect342.9ms。这说明不能只优化平均耗时；两个模型的串行等待都可能拖慢姿态链路。相机read仅约14/16ms，发布前JPEG约5/9ms，本轮证据中的主要延迟处于推理调用内；不能进一步断言具体CPU算子或操作系统调度原因。

因此继续实施上述有界并行。最终用同7张不同姿态图片各重复2次，串行/并行各14次：姿态与饮水标签一致，分类概率`rtol=1e-5, atol=1e-6`内一致，人体候选仍满足前述容差。全部任务ready；串行中位61.75ms、并行44.28ms，短测不能保证长期无尖峰。前一版仅并行人体的42次比较亦一致，中位60.68→45.10ms，之后又把饮水隔离，不混用版本成绩。

最终自动回归：`.venv/bin/pytest -q`为279 passed、1 skipped、3 deselected（11.04s）；Ruff和169文件格式检查通过。新增失败用例覆盖过期detect、过期JPEG、annotate异常、失败attempt与旧结果隔离，以及两路辅助卡住后主姿态继续更新、饮水unknown、旧帧不复用、任务不排队和停止超时可见。模拟验证证明诊断和故障边界，不能代替真实动作验收。

## 实机命令

```sh
.venv/bin/python -m scripts.behavior_camera_test \
  --posture-manifest harness/artifacts/behavior-20260910-r02/posture-run/training-manifest.json \
  --drinking-manifest harness/artifacts/behavior-20260910-r02/drinking-run/training-manifest.json \
  --output data/acceptance/behavior-latency-20260910-bounded \
  --duration 300 --camera 0 --width 1920 --height 1080 --port 8765 \
  --person-association seat --seat-roi 0.2 0.2 0.95 1.0 \
  --person-threads 4 --opencv-threads 1 \
  --auxiliary-mode bounded --person-wait-ms 120
```

旧配置基线使用输出目录`behavior-latency-20260910-baseline`、`--duration 60 --person-threads 2 --person-spinning --opencv-threads 10`，当时尚未实现并行，当前代码复现串行需加`--auxiliary-mode serial`。仅调线程的中间会话为`behavior-latency-20260910-optimized`，18:05:48—18:08:42，1581 fresh、2 stale、用户动作未确认，正常停止；不能将它当作最终修复版。实机均顺序运行，不能视为同帧受控性能比较。

最终有界并行会话18:15:55启动，截至18:18:33记录约148.53秒、1405 fresh、0 stale。人体ready1397次、timeout4次、busy4次；这8次仍为fresh，当前空座分类继续输出，未将辅助超时升级成整帧过期。饮水1405次均ready。包括失败路径的worker detect中位43.37ms、p95 48.31ms，最大203.28ms；操作系统调度会使实际返回时间超过等待预算，因此120ms不是硬实时保证。

最终18:21:03.907达到300秒上限并退出0，worker_stop_error为空。2838 fresh、0 stale，人体2824 ready、7 timeout、7 busy，这14次均未拖垮主帧；饮水2838次均ready。全部detect中位43.36ms、p95 48.91ms，最大203.28ms。原始summary与[脱敏结果](../harness/evaluations/behavior-latency-fix-20260910.json)已保存。没有用户确认动作，0事件不代表坐下召回通过；该会话已停止。

## 仍需核对

只减少平均耗时无法保证操作系统调度等所有长尾消失。摄像头停止、故障、过期和证据不足依旧输出不确定，不补写推测动作。用户动作复测、反例、长时运行和Windows尚须分别记录；不把处理更快等同于本次坐下漏报已经解决。

模型真正抛异常时，worker记录失败总耗时；模型内部阶段明细只对完整完成的detect可用，不能宣称覆盖全部异常内部细节。辅助任务超时时原生调用不会被强制取消，结果被丢弃；如果姿态自身或系统调度卡住，最终新鲜检查仍会拒绝该帧。人体低分重复框歧义本轮没有改动，后续须独立验证。
