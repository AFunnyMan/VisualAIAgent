# 第三方组件、版本与许可

2026-09-07，Asia/Shanghai。本表来自实际安装的包元数据；完整版本与哈希在 [uv.lock](../uv.lock)。下列许可证从安装分发原样保存，项目代码不复制第三方实现。表内摘要不替代原许可证。

| 运行组件 | 已验证版本 | 元数据许可 | 保存的许可证 |
|---|---|---|---|
| openai-agents | 0.22.0 | MIT | [licenses/LICENSE](../third_party/licenses/openai-agents/licenses/LICENSE) |
| openai | 3.8.0 | Apache-2.0 | [licenses/LICENSE](../third_party/licenses/openai/licenses/LICENSE) |
| opencv-python | 4.14.0.94 | Apache 2.0 | [LICENSE-3RD-PARTY.txt](../third_party/licenses/opencv-python/LICENSE-3RD-PARTY.txt), [LICENSE.txt](../third_party/licenses/opencv-python/LICENSE.txt) |
| onnxruntime | 1.29.0 | MIT License | [LICENSE](../third_party/licenses/onnxruntime/LICENSE) |
| supervision | 0.30.2 | MIT | [licenses/LICENSE.md](../third_party/licenses/supervision/licenses/LICENSE.md) |
| streamlit | 1.63.0 | Apache-2.0 | [官方版本 LICENSE](../third_party/licenses/streamlit/LICENSE) |
| pydantic | 2.13.5 | MIT | [licenses/LICENSE](../third_party/licenses/pydantic/licenses/LICENSE) |
| python-dotenv | 1.2.3 | BSD-3-Clause | [licenses/LICENSE](../third_party/licenses/python-dotenv/licenses/LICENSE) |
| psutil | 7.2.2 | BSD-3-Clause | [LICENSE](../third_party/licenses/psutil/LICENSE) |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [licenses/LICENSE.txt](../third_party/licenses/numpy/licenses/LICENSE.txt), [licenses/numpy/_core/include/numpy/libdivide/LICENSE.txt](../third_party/licenses/numpy/licenses/numpy/_core/include/numpy/libdivide/LICENSE.txt), [licenses/numpy/_core/src/common/pythoncapi-compat/COPYING](../third_party/licenses/numpy/licenses/numpy/_core/src/common/pythoncapi-compat/COPYING), [licenses/numpy/_core/src/highway/LICENSE](../third_party/licenses/numpy/licenses/numpy/_core/src/highway/LICENSE), [licenses/numpy/_core/src/multiarray/dragon4_LICENSE.txt](../third_party/licenses/numpy/licenses/numpy/_core/src/multiarray/dragon4_LICENSE.txt), [licenses/numpy/_core/src/npysort/x86-simd-sort/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/_core/src/npysort/x86-simd-sort/LICENSE.md), [licenses/numpy/_core/src/umath/svml/LICENSE](../third_party/licenses/numpy/licenses/numpy/_core/src/umath/svml/LICENSE), [licenses/numpy/fft/pocketfft/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/fft/pocketfft/LICENSE.md), [licenses/numpy/linalg/lapack_lite/LICENSE.txt](../third_party/licenses/numpy/licenses/numpy/linalg/lapack_lite/LICENSE.txt), [licenses/numpy/ma/LICENSE](../third_party/licenses/numpy/licenses/numpy/ma/LICENSE), [licenses/numpy/random/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/random/LICENSE.md), [licenses/numpy/random/src/distributions/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/random/src/distributions/LICENSE.md), [licenses/numpy/random/src/mt19937/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/random/src/mt19937/LICENSE.md), [licenses/numpy/random/src/pcg64/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/random/src/pcg64/LICENSE.md), [licenses/numpy/random/src/philox/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/random/src/philox/LICENSE.md), [licenses/numpy/random/src/sfc64/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/random/src/sfc64/LICENSE.md), [licenses/numpy/random/src/splitmix64/LICENSE.md](../third_party/licenses/numpy/licenses/numpy/random/src/splitmix64/LICENSE.md) |
| tzdata | 2026.3 | Apache-2.0 | [licenses/LICENSE](../third_party/licenses/tzdata/licenses/LICENSE), [licenses/licenses/LICENSE_APACHE](../third_party/licenses/tzdata/licenses/licenses/LICENSE_APACHE) |

SQLite 是 Python 3.11 标准库接口，SQLite 源码为公有领域；Python 自身遵守其发行许可证。uv 0.12.10 用作开发环境管理，源为 [astral-sh/uv](https://github.com/astral-sh/uv)。其余间接依赖和 dev/export 组精确版本均在锁文件，可用 `uv tree --locked` 查看。每个 wheel 的原许可随安装包保留。

## 官方来源

- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)、[OpenAI Python](https://github.com/openai/openai-python)
- [OpenCV](https://github.com/opencv/opencv)、[opencv-python wheels](https://github.com/opencv/opencv-python)
- [ONNX Runtime](https://github.com/microsoft/onnxruntime)、[Supervision](https://github.com/roboflow/supervision)
- [Streamlit](https://github.com/streamlit/streamlit)、[Pydantic](https://github.com/pydantic/pydantic)
- [NumPy](https://github.com/numpy/numpy)、[psutil](https://github.com/giampaolo/psutil)

## 仅用于模型导出的组件

Ultralytics 8.4.142、PyTorch 2.14.0、ONNX 1.22.0、onnxslim 0.1.96 在独立 `.export-venv` 使用，主运行环境不含训练链。Ultralytics/YOLO26n 适用 AGPL-3.0 或其企业许可，导出 ONNX 不改变许可。权重及导出产物不进入 Git；[模型清单](../model_manifests/yolo26n-e2e.onnx.json) 保存官方来源、实际版本、导出参数和 SHA-256。

项目自身发布许可证尚未选择，不能将依赖 MIT 许可解读为整个项目 MIT。商业闭源发行不在当前范围。公开验证图片来源仅用于本地比较，出处记于阶段 02 上下文，不分发图片。

导出链随安装包保存的许可证副本：[ultralytics/LICENSE](../third_party/licenses/ultralytics/LICENSE)、[torch/LICENSE](../third_party/licenses/torch/LICENSE)、[torch/LICENSE.txt](../third_party/licenses/torch/LICENSE.txt)、[onnx/LICENSE](../third_party/licenses/onnx/LICENSE)、[onnxslim/LICENSE](../third_party/licenses/onnxslim/LICENSE)。

Streamlit许可证来自官方1.63.0标签：https://raw.githubusercontent.com/streamlit/streamlit/1.63.0/LICENSE。
