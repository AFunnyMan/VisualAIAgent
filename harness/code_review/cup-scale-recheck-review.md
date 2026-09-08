# 杯子缩放复查审查

2026-09-08，Asia/Shanghai。root实施集成，Sol实现核心、Sol独立只读审查；无阻断项。范围为可选同帧n复查、配置/页面/验收工具，详见[实测](../../docs/cup-scale-recheck-2026-09-08.md)。

- 同一帧先原图，已有cup跳过，否则额外一次；只补cup，不追加缩放图的phone/bottle。复用同一严格校验的基础n实例，不额外模型、API或训练。
- 坐标审查发现草稿曾按名义0.75反算；提交前改用实际取整后的x/y比例，奇数尺寸边界测试通过。纯padding、退化框丢弃；跨边界框按内容区裁剪。
- 包装器不缓存跨帧结果。VisionWorker每帧仍只接收一个合并结果、一次观察；既有慢推理/过期/故障约束与三次/五秒条件保留。
- 设置含增强开关，变更先stop，stop失败不start；开关变化重建当前事件基线，历史保留；相同设置幂等、再次关闭增强回到基础detector。UI断连恢复逻辑保留。
- 新模式实际页120个新鲜采样110次检出、一次appeared无missing；私有配对诊断120/120和30困难样本不新增预测均单列，不夸大成整体准确率。辅助诊断退出1的清理错误已记录，不能写为命令通过。
- root复验发现本机`.env`开启增强后，一项UI测试读入私人设置而失败（156 passed / 1 failed）。离线fixture现显式禁用dotenv并清除继承VAA环境，各测试仅用显式配置，避免依赖用户现场。真实API/摄像头marker不受此离线fixture修改影响。
- 最终 `.venv/bin/pytest`：157 passed，3 deselected；`.venv/bin/ruff check .`、`ruff format --check .`（112文件）和`git diff --check`通过。Windows当前改动的CI与实机情况单列，不能用Mac结果替代。
- 剩余边界：新的姿态/实物移出放回、桶锅持续误识、Windows11 USB实机仍须验证；默认关闭该可选功能。私人Key、摄像头图片、SQLite和权重不入Git。
