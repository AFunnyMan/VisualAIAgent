# 阶段 02 上下文：本地低频视觉

状态：业务实现及离线/公开图片验证已完成；真实摄像头、Windows 与固定场景质量验收待执行。

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

实际执行命令摘要：

```text
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv venv --python .python/cpython-3.11.16-macos-aarch64-none/bin/python .export-venv
VIRTUAL_ENV=.export-venv UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python .tools/bin/uv sync --active --locked --group export
YOLO_CONFIG_DIR=/private/tmp/vaa-ultralytics MPLCONFIGDIR=/private/tmp/vaa-matplotlib ORT_DISABLE_TELEMETRY=1 .export-venv/bin/python scripts/prepare_model.py
```

三张类别图片分别使用 `curl -L -sS <上表 URL> -o harness/artifacts/<文件名>` 下载。业务/参考比较用 `.export-venv/bin/python -c ...` 一次性命令加载 `YOLO("models/yolo26n.pt")` 和 `YoloOnnxDetector("models/yolo26n-e2e.onnx", expected_sha256=...)`，两端统一 `imgsz=640, conf=0.35, nms=False, rect=False, device="cpu"`，逐张打印类别、置信度和 xyxy 框。运行时隔离检查使用 `.venv/bin/python -c 'import importlib.util; print(importlib.util.find_spec("torch"))'`，结果为 `None`。

## 已知平台事项

- ONNX Runtime 1.29.0 macOS wheel 默认在模块 import 时尝试持久化 telemetry device ID，并生成 51 字节的 `:memory:.ses`。运行模块和导出脚本现均在导入原生扩展前设置 `ORT_DISABLE_TELEMETRY=1`，并在导入后调用 Python 禁用函数；独立子进程回归确认无警告、无旁路文件。
- 未访问真实摄像头，避免在没有用户布置手机、杯子、瓶子和授权场景时产生伪验收。USB 后端、实际分辨率、断开恢复、三类各十次放入/移出、两秒遮挡和三十分钟运行均待用户辅助实测。
- Windows 11 x64 尚无现场环境；当前代码路径避免平台专属导入，但不能据此声称 Windows 已验收。
