# 笔记本开合可选产品接入

更新：2026-09-12，Asia/Shanghai。

## 当前结论

程序已经具备笔记本开合的可选产品链路，但当前训练候选**不可用且默认关闭**。现有 open/closed
分类训练候选不能证明电脑仍在画面中，也没有合格的独立在场/遮挡模型及完整能力
验收。因此不能把单独的训练清单填入产品设置，也不能创建可生效的合盖规则。分类训练及回放的最新
进度以[训练报告](laptop-training-20260911.md)为准；完成训练或导出本身不会改变产品可用状态。

这里的 open 包含清晰可辨的半开与尚未完全闭合，closed 只表示电脑清晰可见且完全闭合。低置信、
模糊、遮挡、移出画面、过期、断流与场景变化都输出 unknown。unknown 不等于 closed，也不会触发
合盖或打开事件。

## 启用条件

功能只在以下两个环境变量均显式配置后尝试加载：

```text
VAA_LAPTOP_ENABLED=true
VAA_LAPTOP_CAPABILITY_MANIFEST=/absolute/path/to/laptop-capability.json
```

能力清单不是二分类训练清单。它必须引用两个各自带 SHA-256 的完成态 ONNX 清单：

- 状态分类器：类别严格为 `open`、`closed`；
- 在场分类器：类别严格为 `present`、`absent`、`occluded`。

能力清单还必须明确记录独立的状态转场验收，以及 absent 与 occluded 负例数量；任一负例被误报为
present 都会拒绝加载。当前加载契约如下：

```json
{
  "schema_version": 1,
  "capability": "laptop_lid_state_with_presence",
  "status": "accepted",
  "experimental": true,
  "model_version": "laptop-example",
  "state_manifest": "state-training-manifest.json",
  "state_manifest_sha256": "<64 lowercase hex>",
  "presence_manifest": "presence-training-manifest.json",
  "presence_manifest_sha256": "<64 lowercase hex>",
  "acceptance_report": "laptop-acceptance.json",
  "acceptance_report_sha256": "<64 lowercase hex>"
}
```

验收报告必须再次绑定两份 manifest SHA、两份 ONNX SHA、模型版本、场景、输入尺寸、两个 ROI、来源
注册表 SHA 和人工真值 SHA，并固定运行阈值 `presence_confidence=0.75`、
`state_confidence=0.75`、`state_margin=0.2`，以及时序参数 `confirm_seconds=0.5`、
`max_gap=0.25`、`visible_transition_bridge_seconds=0.15`。报告中的
absent/occluded 样本数均须大于零，两个 false-present 计数均须为零。模型、阈值、来源或真值任一变化
都会使加载失败。这份结构只定义可审计的加载边界，不代表示例计数已经完成；正式清单必须来自冻结
模型的实际独立验证，不能手工把开发成绩改写为 accepted。

## 运行与安全边界

物品、行为和笔记本共用一个物理摄像头读取线程。笔记本 worker 有自己的 latest-only subscription，
目标 10fps；慢推理只跳过旧帧，不形成待处理视频队列，也不阻塞物品消费者。画面在推理前、推理后和
发布前均检查 250ms 新鲜度。停止、断连、错误、重复时间戳或场景变化会清除转场基础。

每帧先运行独立在场门。只有 `present` 达到接受阈值且未遮挡时才运行状态分类器；状态结果也必须满足
confidence 与 margin 门限。ROI 和状态分类置信度本身从不作为在场证据。只有已验收的独立模型仍确认
电脑清晰可见时，时序才允许跨过最多一个不超过 150ms 的状态 unknown 样本保留前一稳定基线；该帧
仍输出 unknown、不触发事件，也不累计状态确认时长。连续两个 unknown、超过 150ms、采样间隔超过
250ms、遮挡、移出画面或画面过期都会清除基线。当前候选没有合格的独立可见性能力，因此不能使用
这条桥接，也不能据此把现有回放结果称为产品验收通过。

能力加载失败只关闭笔记本 worker，物品观察和已选择的行为 worker 继续运行。UI 会显示具体能力为
“未验收/不可用”，不会把已配置开关显示成模型已可用。

## 事实、规则与 Agent

SQLite schema v4 新增 `laptop_observations` 与 `laptop_events`。它们与
`behavior_intervals` 完全分开，所以开盖、合盖或 unknown 都不会计入在座、站立、空座、饮水或未知行为
时长。确认事件保存事件时间、截图证据编号、状态模型版本、在场模型版本、场景与来源；当前观察只保留
单条，不保存连续帧。

情境规则增加 `laptop_closed` 与 `laptop_opened` 两种触发。能力不可用时不能新建或重新启用这两类
规则。规则修改继续产生新版本，旧排队任务被 supersede；取消和 event/rule 去重沿用原有原子边界。
触发任务的 fact snapshot 保存真实笔记本事件及证据，自动 Agent 仍需先查询当前场景、再读取该规则
事件，最后才能调用受限通知工具。

产品仍只有七个 function tools，没有新增第八个工具：

- `get_current_scene(scope="laptop")` 查询能力和当前状态；
- `search_events(scope="laptop")` 查询历史开合事件；
- `create_watch(target="rule", trigger="laptop_closed"|"laptop_opened")` 管理规则；
- `list_watches`、`cancel_watch` 和受限 `notify_user` 沿用既有接口。

工具只返回文字事实与证据编号，不返回截图路径；Agent 不接收图片。无当前在场证据时必须回答无法确认。

## 已自动验证与待验收

离线测试覆盖能力清单拒绝、absent、遮挡、低置信、过期画面、共享订阅、转场确认、截图持久化、重启
后当前状态失效、规则创建/修改/取消/去重、schema v3 到 v4 备份迁移，以及七工具数量不变。

用户授权受控文字后，真实 qwen-flash 的查询、规则创建、事件复查及页面通知已验证通过（2026-09-12 01:46 +08），只使用隔离测试数据库和构造事实。通知实际持久化后触及原三轮上限，运行记录保留该上限诊断；未增加云调用上限。详见[真实工具链记录](../harness/evaluations/laptop-agent-live-20260912.json)。这不验证摄像头或训练模型。

以下内容尚未完成，因而当前模型保持关闭：

- 合格的笔记本在场/遮挡模型及独立负例验收；
- 冻结状态模型在未参与训练或选优的完整开合片段上的事件验收；
- 真实 USB 场景中的打开、半开、完全合上、遮挡、移出与恢复；
- 与物品及行为同时运行的长时性能和 Windows 11 实机验证。
