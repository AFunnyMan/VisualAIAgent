# 阶段 02 上下文：本地低频视觉

状态：业务实现与离线验证已完成；Mac OBSBOT三十分钟本地稳定性通过，固定场景发现误识别和漏检；Windows与三类质量验收仍开放。最新取景改进见[实机复验](../../docs/camera-remediation-2026-09-08.md)。

## 接口与运行决策

- `VisionWorker` 使用独立采集线程和推理线程，采集端只保留一个带序号的最新帧；推理默认每秒一次，可配置为每两秒一次。重复帧、过期帧、断连、暂停及错误一律产生 `fresh=false` 且空检测列表的观察，不构成物品缺失依据。
- 回调为 `on_observation(SceneObservation, jpeg bytes | None)`；JPEG 是使用 Supervision 标注后的本地预览/证据图。故障时可带最后帧帮助诊断，但观察仍明确标记无效。`snapshot()` 返回最近观察的深拷贝和不可变 JPEG。
- `CameraSource` 记录 OpenCV 实际取得的分辨率和后端；`ReplaySource` 支持文件结束与循环。输入和展示均不镜像，框中心按画面三等分得到左/中/右。
- ONNX Runtime 固定 CPU provider、顺序执行、两个 intra-op 线程、一个 inter-op 线程，并关闭 intra/inter-op 空闲自旋。运行环境没有 PyTorch；Ultralytics/PyTorch 只存在于被忽略的 `.export-venv`。
- `YoloOnnxDetector` 默认置信度为 0.35，只接受静态 `[1,3,640,640] -> [1,300,6]` 的 YOLO26 end-to-end 模型。模型元数据必须可解析且 COCO 39/41/67 分别为 bottle/cup/cell phone。多个同类框原样作为候选，不实施跟踪或身份推断。
- 模型默认从 `model_manifests/<ONNX 文件名>.json` 读取清单；清单 SHA、配置 SHA（若提供）和文件实际 SHA 必须三者一致。假模型测试必须显式传 `verify_manifest=False`。

## 官方模型与复现事实

- 官方说明确认 YOLO26 `nms=False` 使用 one-to-one 端到端头，输出 `(N,300,6)`；默认 one-to-many 输出为 `(N,nc+4,8400)`，本实现显式拒绝后者。[YOLO26 模型说明](https://docs.ultralytics.com/models/yolo26/)、[导出参数](https://docs.ultralytics.com/modes/export)
- 权重来源为 Ultralytics assets v8.4.0 的 `yolo26n.pt`；官方 Dockerfile 也使用同一 URL。代码和模型适用 AGPL-3.0 或 Ultralytics 企业许可。[官方权重来源](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt)、[官方许可证](https://github.com/ultralytics/ultralytics/blob/main/LICENSE)
- 导出环境：Python 3.11.16、ultralytics 8.4.142、torch 2.14.0、onnx 1.22.0、onnxslim 0.1.96、onnxruntime 1.29.0；平台为 macOS Apple M2 Pro。参数为 ONNX、640、batch 1、静态维度、FP32、simplify、`nms=False`、CPU。
- checkpoint SHA-256：`9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`。
- 最终 ONNX SHA-256：`9c60d351bb2865a8169d0590c07c905b372e81e4a846a8ff920d244955e2516c`。二进制位于被忽略的 `models/`，提交的是 `model_manifests/yolo26n-e2e.onnx.json`。
- 官方 bus 图片在 PT 与 ONNX 预测时都指定 `rect=False`，保证和业务 640×640 letterbox 相同。五个通用 COCO 框类别完全一致，最小 IoU 0.99999815，最大框差 0.000610 像素，最大置信度差 0.00000131；阈值分别为 0.9999、0.01 像素、0.0001。首次未统一 `rect` 的失败对比已用于修正方法，未通过放宽阈值掩盖。
- 业务 `YoloOnnxDetector` 又与 PyTorch 参考在三张公开图片上逐框比较：手机 3 个候选（0.889/0.872/0.798）、杯 1 个（0.964）、瓶 1 个（0.417），类别和框吻合。该结果验证导出、类别映射、逆 letterbox 和多候选处理；公开图片不是固定摄像头场景的 90% 质量验收。
- 本机纯运行环境确认找不到 `torch`；业务 detector 三次 bus 推理约为 64.0、52.9、50.2 ms。该短样本不代替三十分钟性能验收。

实际使用的公开样本均保存在被忽略的 `harness/artifacts/`，不进入 Git：

| 用途 | 下载 URL | 尺寸 | 文件 SHA-256 |
|---|---|---:|---|
| 通用 PT/ONNX 对比 | `https://ultralytics.com/images/bus.jpg` | 810×1080 | `c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63` |
| 多手机候选 | `https://upload.wikimedia.org/wikipedia/commons/7/79/Responsive_design_-_Commons_Android_app.jpg` | 2592×1936 | `a3b3234e6bd8837d4ffcd80bfd79e69331efee66cb021f366c58e646d90a8e4a` |
| 杯子 | `https://www.publicdomainpictures.net/pictures/610000/velka/coffee-mugs-1714587640DWA.jpg` | 1920×1440 | `5b28ce28f80b7b30a3eda26e546ac810c29da70086e04bffb8d8ca11c595e9ee` |
| 两个水瓶场景 | `https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Two_water_bottles_on_table_at_final_Wikimania_2016_dinner.jpg/1920px-Two_water_bottles_on_table_at_final_Wikimania_2016_dinner.jpg` | 1920×2975 | `63358f81a13b295461318faa4f249b8349acd974af2c302eab15277e47bdf54b` |

### 真实动态视频回放

另使用 Wikimedia Commons 的实拍视频
[《Сжимаем открытую бутылку》](https://commons.wikimedia.org/wiki/File:%D0%A1%D0%B6%D0%B8%D0%BC%D0%B0%D0%B5%D0%BC_%D0%BE%D1%82%D0%BA%D1%80%D1%8B%D1%82%D1%83%D1%8E_%D0%B1%D1%83%D1%82%D1%8B%D0%BB%D0%BA%D1%83.webm)
验证动态输入。视频由 Flexmanru 拍摄并以
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 发布；本次下载并原样回放，
没有剪辑或画面合成。原文件为 WebM、720×1280、30 FPS、625 帧、20.833 秒，下载后的
SHA-256 为 `1b032b7e29c95897dc1f47e8c8fb1e68dacc84057f184ef3c2e6442109bb6adc`，
本地路径为被 Git 忽略的 `harness/artifacts/wikimedia-squeezing-open-bottle.webm`。

新增的 `scripts/video_acceptance.py` 不读取 `.env`、不实例化 Agent、也不访问摄像头；它校验
视频哈希后，通过 `ReplaySource(realtime=True)`、真实 `YoloOnnxDetector` 和 `MemoryStore`
原速运行，并在 EOF 后继续观察至少六秒。默认间隔为产品标准的一秒，也可显式传入两秒验证
省电模式。2026-09-07 04:02—04:06（Asia/Shanghai）实际执行：

```text
.venv/bin/python scripts/video_acceptance.py \
  --video harness/artifacts/wikimedia-squeezing-open-bottle.webm \
  --expected-sha256 1b032b7e29c95897dc1f47e8c8fb1e68dacc84057f184ef3c2e6442109bb6adc \
  --category bottle
```

上面的默认一秒模式得到 22 个新鲜推理观察，其中 21 个检出瓶子、共 22 个瓶子候选，最高
置信度 0.9041，机器可读证据为 `harness/artifacts/video-acceptance-c0bd9876/summary.json`。
显式增加 `--interval 2` 的省电模式得到 12 个新鲜观察，全部检出瓶子、共 13 个候选，最高
置信度 0.8965，证据为 `harness/artifacts/video-acceptance-8848816b/summary.json`。两种产品模式
都恰好生成一次 `bottle:appeared`；视频期间和 EOF 后的 `missing` 分别计数且均为零，EOF 后
当前画面转为非当前，Agent 请求计数为零。此前 0.5 秒诊断运行的 43 个新鲜观察结果仍保存在
`harness/artifacts/video-acceptance-03c83790/summary.json`，没有被覆盖。

限时补充筛选了两段同样许可明确的真实视频：Queen Asali 的 CC BY-SA 4.0
[《A cup of Kenyan Coffee》](https://commons.wikimedia.org/wiki/File:A_cup_of_Kenyan_Coffee.webm)
和 Derek J Moore 的 CC BY 4.0
[《1 Me and my phone》](https://commons.wikimedia.org/wiki/File:1_Me_and_my_phone.webm)。前者原文件
SHA-256 为 `8ec9c0b030f6df28fcf48f615b3e89086fd490342f5f1b350f5d61c8e23d3b5c`，抽取九帧均
未检出杯子；后者 SHA-256 为 `51302ba9475e5660785d928f3e306fae64553ebb682a66c7bbaee2520020cd6a`，
30 秒视频逐秒抽帧均未检出手机。因此两者只作为被 Git 忽略的候选留在 `harness/artifacts/`，
没有把主题标签当作模型通过证据，也没有为凑齐类别而降低阈值。

这些结果说明公开视频输入能贯通真实 ONNX、低频采样、证据存储和出现事件，并验证 EOF 不会
被解释为消失；当前合格素材仍只有瓶子一类，不能替代三类物品、固定摄像头视角、真实 USB
断开恢复或提醒延迟验收。

实际执行命令摘要：

```text
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv venv --python .python/cpython-3.11.16-macos-aarch64-none/bin/python .export-venv
VIRTUAL_ENV=.export-venv UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv sync --active --locked --group export
YOLO_CONFIG_DIR=/private/tmp/vaa-ultralytics MPLCONFIGDIR=/private/tmp/vaa-matplotlib ORT_DISABLE_TELEMETRY=1 .export-venv/bin/python scripts/prepare_model.py
```

三张类别图片分别使用 `curl -L -sS <上表 URL> -o harness/artifacts/<文件名>` 下载。业务/参考比较用 `.export-venv/bin/python -c ...` 一次性命令加载 `YOLO("models/yolo26n.pt")` 和 `YoloOnnxDetector("models/yolo26n-e2e.onnx", expected_sha256=...)`，两端统一 `imgsz=640, conf=0.35, nms=False, rect=False, device="cpu"`，逐张打印类别、置信度和 xyxy 框。运行时隔离检查使用 `.venv/bin/python -c 'import importlib.util; print(importlib.util.find_spec("torch"))'`，结果为 `None`。

## 已知平台事项

- ONNX Runtime 1.29.0 macOS wheel 默认在模块 import 时尝试持久化 telemetry device ID，并生成 51 字节的 `:memory:.ses`。运行模块和导出脚本现均在导入原生扩展前设置 `ORT_DISABLE_TELEMETRY=1`，并在导入后调用 Python 禁用函数；独立子进程回归确认无警告、无旁路文件。
- 已在用户授权下访问真实OBSBOT摄像头；首次停止崩溃已修复，Mac三十分钟本地稳定性和短时重启有证据。三类各十轮、遮挡/物理拔插及联合Agent仍待用户场景配合，见[实机记录](../../docs/camera-validation-2026-09-08.md)。
- Windows 11 x64 尚无现场环境；当前代码路径避免平台专属导入，但不能据此声称 Windows 已验收。

## 2026-09-08 取景配置与事件基线

- 可选采集尺寸三档、归一化观察范围在CameraSource读帧时应用，返回裁剪副本；全画面None/全幅tuple统一，不改变默认输入行为。观察与证据尺寸相同，区域以预览为准。
- 分辨率/设备/范围改变时先完成旧worker停止，MemoryStore重建当前事件状态机；保留历史、last_seen与关注。reset_continuity仅清连续计时、不清已有present，所以不足以保护换视野场景。
- 数据显示只有720p/1080p与指定桌面范围的组合恢复当前杯子；640裁剪失败、更大模型与旋转没有证明可用。不推广私人场景参数，不修改三次/五秒契约；完整结果与来源只维护在[实机改进](../../docs/camera-remediation-2026-09-08.md)。
