# 第二轮识别改进：资料与实验约定

2026-09-07，Asia/Shanghai。用户要求继续查资料并实际尝试。承接 [上一轮结果](../../docs/recognition-ab-results-2026-09-07.md)，不训练、不调用云端视觉/百炼、不修改生产模型、事件契约或全局依赖。

## 预先冻结

- 两个Sol：RF-DETR Nano官方预训练/CPU/ONNX；n端到端与s传统头的CLAHE、gamma、原图+水平翻转NMS。
- 一个Luna：使用现有COCO原标注，seed20260908，新增手机/杯/瓶各16张加12负例共60图，排除旧30图，推理前冻结名单，不看模型结果选图。COCO不是模型未见数据，只保证本项目选择过程隔离。
- 主Agent：旧30图现在明确转为开发/校准集。对n端到端与s传统头，各类别在[0.10,0.15,…,0.90]中选阈值：该类FP不超过旧n端到端0.35的FP预算；先最大化TP，同分取更高阈值。新60图只验证不再调参。低分原始候选重新完整获取，不沿用每类前10的截断缓存。
- 主Agent：s传统头做全图+固定2×2重叠切片，每块ceil(原边长/1.8)，约20%切片重叠，输出反变换到原图，按类NMS IoU0.5。固定0.35，不按验证集调裁剪或合并参数；这是有界切片实验，不冒称完整SAHI默认GreedyNMM。

亮度/TTA冻结：CLAHE仅LAB的L通道clipLimit2/grid8；gamma指数0.7；左右翻转后x1=W-x2、x2=W-x1，按类NMS0.5。前两者各一次推理，flip两次，切片五次。不同改法分别计分，不一次叠加。

比较旧30图、53视频诊断时点，再在60新图验证。只有有希望的改法扩大到全488视频及独立串行计时；所有视频从头顺序解码，不随机seek。正式三类计分仍采用原GT、IoU≥0.5匹配；报告TP/FP/FN与事件，不把事件数或框数量当准确率。

## 官方资料入口

- [置信度与Precision/Recall](https://docs.ultralytics.com/guides/yolo-performance-metrics)：阈值改变取舍，需看分类别结果。本项目使用FP约束校准是工程设计，不是文档保证有效。
- [切片机制](https://github.com/obss/sahi/blob/main/docs/guides/sliced-inference.md)：保留全图、重叠切片、还原坐标、合并重复框，小目标收益需与开销一起验证。
- [TTA](https://docs.ultralytics.com/yolov5/tutorials/test-time-augmentation)：属于YOLOv5说明。本轮在ONNX外显式实现有限变换，不声称YOLO26有相同原生接口。
- [OpenCV CLAHE](https://docs.opencv.org/4.8.0/d6/dc7/group__imgproc__hist.html)：限制局部对比度；本项目检测收益待实测。
- [RF-DETR](https://github.com/roboflow/rf-detr)、[导出](https://rfdetr.roboflow.com/latest/learn/export/)：Nano使用不同检测结构，官方代码及该规格权重许可单独核验，不能拿GPU宣传速度当本机CPU表现。

全部实验完整产物在harness/artifacts/recognition-v2-20260907；实际结果追加build-log，不将本约定当通过记录。

本轮中途追加YOLO26m传统头640/.35及同一旧集FP预算校准，单独标记为探索路线。理由是当前s约138ms、产品1Hz，尚有CPU时间余量，检验增大容量能否带来实质召回改善。m的分类阈值只由旧30图决定，m-development.json在m自身新60图推理前写出；不能仅凭文件时间证明早于读取其他模型新集结果，因此不声称与首批预定路线有同样强的预注册证据。未根据m新集结果重调阈值。
