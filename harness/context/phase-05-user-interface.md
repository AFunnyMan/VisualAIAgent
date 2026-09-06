# 阶段 05 上下文

2026-09-07，Asia/Shanghai。前置模块已固定接口并通过各自部分离线验证；root 开始集成 Streamlit 页面和运行时。

页面通过全局 `st.cache_resource` 持有一个 ApplicationRuntime，资源锁防止同一数据目录的第二个进程重复运行。摄像头的采集/推理线程独立；模型请求进入有界队列，由单个后台线程持有 asyncio 循环，避免 Streamlit 重运行关闭 SDK 客户端事件循环。

页面分区刷新使用官方 `st.fragment`，用户等待期间不阻塞预览；只监听 127.0.0.1。启动按钮才打开摄像头，无模型配置时聊天明确不可用，查询/关注仍使用同一业务服务。线程停止、错误与实际测试结果在集成审查中记录。

来源：[fragment](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)、[cache_resource](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource)。全局缓存仅用于线程安全服务，不能依赖缓存析构保证停机；额外使用显式 stop 和 atexit。
