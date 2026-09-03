"""Instagram 动态监控核心包。

模块划分（借鉴 astrbot_plugin_pixiv_monitor 的模块化设计）：

- client:    instaloader 封装（登录/Session、代理、拉取最近动态）
- models:    数据模型（Post / MediaItem / PostType）
- state:     订阅关系与游标持久化
- formatter: 推送消息模板渲染
- media:     图片/视频下载与清理
"""

__version__ = "1.0.0"
