# 独立复现目录交付说明

2026-10-20，Asia/Shanghai。用户批准整理后，在项目根建立本地私人目录 `reproducibility_bundle/`。
没有移动、删除原始数据或权重，没有修改正式业务源码，没有重新训练。构建工具和说明进入Git；私人数据、模型、环境和本地完整验证报告不进入Git。

## 从哪里开始

打开交付目录的 `README.md`，依次阅读 `reports/directory-guide.md`、`reports/differences.md`、`reports/validation-summary.md`。
每个文件的旧路径、新路径、大小和SHA256见 `provenance/file-map.csv`；所有数据集、配方、依赖与模型指纹见 `provenance/catalog.json`。

| 新目录 | 作用 |
|---|---|
| data/raw | 输入视频、原始图片与训练记录引用的早期证据原图 |
| data/annotations | 人工标注、真值与修订记录 |
| data/datasets | 实际训练过的冻结图片、YOLO标签/分类目录、划分及manifest |
| models/pretrained、models/reference | 官方初始化、各轮PT/ONNX与部署参考产物 |
| recipes、profiles | 每轮完整配方与三种明确的启动组合 |
| source、tools | 源码、锁文件和独立运行/训练/验证入口 |
| provenance、history | 原始记录、来源许可、复核展示、被拒及无效历史 |
| reports | 目录差异、排除清单与实际验证证据 |
| work | 可重建的运行源码、路径清单、环境、运行数据库和新训练输出 |

保留23个冻结数据集条目和23份训练配方，21份材料就绪、2份无效钥匙实验隔离。姿态与饮水可能共享原素材，不将相邻帧或重复历史副本算作独立样本。`ready`只表示材料与历史指纹就绪，不代表模型质量验收或重新训练已经完成。

## 安装、运行与复训

在交付目录中首次联网安装：

```sh
uv sync --project source --locked --group dev --python 3.11.16
uv run --project source --locked python tools/bundle.py prepare
uv run --project source --locked python tools/verify.py --bundle .
```

Mac运行 `sh start-mac.sh`；Windows使用 `start-windows.ps1`，但Windows实机尚未验证。服务使用本机8511端口。
默认official为官方物品模型、行为关闭；摄像头需使用者点击开始。无API Key时明确显示Agent未连接。

```sh
uv run --project source --locked python tools/bundle.py launch --profile desktop-reference
uv run --project source --locked python tools/bundle.py launch --profile candidates
uv run --project source --locked python tools/bundle.py train --recipe 配方ID --check-only
```

desktop-reference为R05物品＋R02姿态/饮水；candidates为R04物品＋R04姿态＋20260912 R03全帧饮水，均保留实验身份。
笔记本和钥匙不自动启用。默认剥离原环境配置；真实Agent需使用者在包根 `.env` 中提供自己的必要API项并显式使用 `launch --allow-api`。

所有配方先做只读预检；去掉 `--check-only` 才会真正训练并安装export组隔离依赖。需要其他设备时显式传 `--device cpu` 等覆盖，不能宣称更换设备仍逐位一致。
R05依赖R03参考PT，并非直接从官方初始化；配方记录全部依赖，可按历史链重新训练。新结果写入work，不覆盖冻结参考模型。

搬动目录后重新安装source环境、再次prepare；虚拟环境的绝对链接不能直接搬运。移交时排除 `work/`、`source/.venv/`、自行创建的根 `.env`。
不要把本机生成的运行数据库或API配置发给其他人。冻结目录约12.087 GiB，环境和缓存另计；复制会增加占用，本轮未节省原目录空间。

## 实际验证与限制

- 首轮18,813个映射文件全部逐字节SHA校验；所有23份训练记录的数据指纹均按历史算法重算吻合。最终交付文件数以catalog及最终完整性报告为准，后续补充说明也计入。
- 29个ONNX通过CPU加载。受支持的生产模型在3张既有开发图上比较原权重与副本，检测/分类输出一致；旧三类或钥匙专用头仅加载，不冒充现有运行器支持。
- R03/R04/R05在三段既有development视频的61个1Hz采样点上，生产杯复查检测器及事件状态机迁移前后结果一致；流末stale、paused、disconnected、stopped不产生虚假移出。
- 三组配置通过AppTest：单Runtime、摄像头未运行、Agent运行次数0、未连接标识可见。实际8511 HTTP健康检查200，测试进程正常结束，端口释放。
- 移到独立的中文/空格/含images临时路径，禁止读取原项目后，重新安装、prepare、21份训练预检、三组页面及实际物品模型推理通过；随后放回并重建本机环境。
- 最后一次完整回归511 passed、1 skipped、5 deselected；格式与静态检查通过。文档生成调整后做定向复核，实际记录见build-log。

本次证明已保存材料及现有产物可独立运行，没有从头训练，不能保证新训练的精度或跨设备逐位相同。
没有进行新的独立质量验收、真实USB、真实云API或Windows实机测试。原有测试集已使用，不能改名当新留出集。
保留124条警告：R01旧label_path 120条、公共来源manifest历史SHA差异1条、视频声明帧数与实际解码各差1帧共3条。
实际成对标签和训练指纹验证通过；公共manifest旧快照无法从现存内容恢复，不篡改历史SHA来消除警告。

## 新旧差异与可移除内容

原先按时间/实验分散在data和harness/artifacts，现按原图、标注、冻结训练集、模型、配方及证据分类，内部保留原层级方便追溯。
不合并轮次，不去重改名，不重做train/val划分。旧JSON原样保留，运行时在work生成适合当前位置的路径。
work中的适配仅涉及模型预设路径、关闭隐式旧行为模型查找、YOLO路径中最后一级images映射；记录在prepared.json。

交付包省略旧虚拟环境、下载缓存、真实数据库、完整执行日志、凭据，以及非YOLO主链的RF-DETR/MobileCLIP/YOLOE二进制。
这些是交付可省略项，不意味着全部可以从原项目删除：运行数据库有真实历史，日志可能仍有诊断价值；本轮均原地保留。
复核展示图虽然可生成，仍保留修订证据；错误标注与无效钥匙模型保留在历史区，禁止混入有效训练。

构建与核查工具：`scripts/build_reproducibility_bundle.py`、`scripts/repro_bundle_catalog.py`、`scripts/repro_bundle_runtime.py`、`scripts/repro_bundle_verify.py`。
工具拒绝覆盖已有交付目录；今后重建需指定新输出路径并重新验证。详细决策见[上下文](../harness/context/reproducibility-bundle-20261020.md)，审查见[记录](../harness/code_review/reproducibility-bundle-20261020.md)。
