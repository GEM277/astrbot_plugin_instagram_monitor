# Instagram 动态监控插件

基于 [instaloader](https://instaloader.github.io/) 开发的 Instagram 动态监控插件，支持多账号订阅、代理、媒体推送与命令管理。

## 功能特性

- ✅ **多账号监控**：同时监控多个 Instagram 账号动态
- ✅ **订阅管理**：通过指令灵活订阅/取消订阅
- ✅ **代理支持**：支持 HTTP/HTTPS/SOCKS5 代理
- ✅ **媒体推送**：自动下载并推送图片、视频、多图
- ✅ **模板定制**：可自定义推送消息模板
- ✅ **智能去重**：游标机制避免重复推送
- ✅ **错误处理**：账号不可访问、限流等异常通知
- ✅ **免打扰**：支持自定义免打扰时间段
- ✅ **命令管理**：支持群聊/私聊指令，管理员权限控制

## 安装

### 1. 放置插件

将 `astrbot_plugin_instagram_monitor` 文件夹放入 AstrBot 的 `data/plugins` 目录中。

### 2. 安装依赖

```bash
cd data/plugins/astrbot_plugin_instagram_monitor
pip install -r requirements.txt
```

### 3. 启用插件

在 AstrBot 管理后台启用插件，或通过指令启用：

```bash
/ig enable
```

### 4. 配置代理（可选）

如果需要通过代理访问 Instagram，在插件配置中填写代理地址：

```
http://127.0.0.1:7890
或
socks5://127.0.0.1:1080
```

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| accounts | 监控的 Instagram 账号列表 | `[]` |
| schedule.poll_interval_minutes | 轮询间隔（分钟） | 15 |
| schedule.first_check_delay_seconds | 首次启动延迟检查（秒） | 30 |
| push.message_template | 推送消息模板 | 默认模板 |
| push.caption_limit | 文案最大长度 | 300 |
| push.push_video | 是否推送视频 | true |
| push.max_images_per_post | 单帖最多推送图片数 | 9 |
| push.max_video_size_mb | 视频大小上限（MB） | 100 |
| push.request_timeout | 媒体下载超时（秒） | 120 |
| push.retention_minutes | 媒体文件保留时间（分钟） | 60 |
| notify.error_message | 是否推送错误消息 | true |
| notify.quiet_hours | 免打扰时间段 | `[]` |
| instagram.username | Instagram 登录账号（可选） | 留空 |
| instagram.password | Instagram 登录密码（可选） | 留空 |
| instagram.proxy | 代理地址（可选） | 留空 |
| instagram.session_dir | Session 文件目录（可选） | 留空 |

## 指令说明

| 指令 | 权限 | 说明 |
|------|------|------|
| `/ig sub <username>` | 管理员 | 订阅指定账号动态 |
| `/ig unsub <username>` | 管理员 | 取消订阅指定账号动态 |
| `/ig list` | 管理员 | 查看当前订阅的账号列表 |
| `/ig check <username>` | 管理员 | 检查账号状态（可选指定用户名） |
| `/igumo` | 所有用户 | 查看自己订阅的账号列表 |

## 工作机制

### 订阅流程

1. 管理员使用 `/ig sub <username>` 指令订阅账号
2. 插件验证账号存在性（私密账号会报错）
3. 记录订阅关系与游标（last_seen）
4. 按配置的轮询间隔定时检查新动态

### 推送逻辑

- **游标机制**：记录每条账号最后推送的帖子 shortcode，避免重复推送
- **时间倒序**：新动态按发布时间倒序推送，最新在前
- **媒体处理**：图片/视频先下载到本地缓存，通过 `fromFileSystem` 推送
- **去重检查**：如果账号删除了帖子，游标会回退到时间戳兜底
- **订阅会话**：可指定推送目标群/用户，默认推送到所有订阅会话

### 首次检查

- 首次启动延迟 `first_check_delay_seconds` 秒，避免立即轮询
- 首次检查返回最新的 `limit` 条动态（不推送历史）
- 后续检查只推送比游标更新的内容

### 错误处理

- **账号不存在**：自动移除订阅，通知管理员
- **私密账号**：无法监控，需取消订阅
- **请求限流**：等待后重试，多次失败通知管理员
- **网络错误**：自动重试，超过次数跳过
- **媒体下载失败**：跳过该媒体，推送其他内容

### 媒体清理

- 推送完成后立即清理下载的媒体文件
- 保留时间 `retention_minutes` 为兜底，防止异常残留

## 注意事项

### 风险提示

- **非官方接口**：instaloader 使用逆向工程，存在账号被封风险
- **频率限制**：频繁请求可能触发 Instagram 限流
- **匿名访问**：未登录只能访问公开账号，私密账号无法监控
- **2FA 不支持**：开启双重认证的账号无法登录

### QQ 平台限制

- **视频支持**：QQ 官方客户端不支持视频推送，建议使用第三方客户端
- **路径要求**：`Video.fromFileSystem` 要求机器人端与用户端在同一文件系统
- **Docker 部署**：确保媒体文件挂载到容器内，否则无法发送视频

### 使用建议

1. 轮询间隔建议 15~30 分钟，避免过于频繁
2. 使用代理降低被封风险
3. 避免监控大量账号，建议不超过 10 个
4. 定期检查账号状态，及时移除无效账号

### 故障排查

1. **无法获取动态**：检查代理、网络连接、账号状态
2. **推送失败**：检查 QQ 连接、媒体文件权限
3. **重复推送**：检查游标文件是否正常
4. **限流错误**：延长轮询间隔、使用代理、减少监控账号

## 致谢

本插件设计参考了 AstrBot 社区中的 [微博监控插件](https://github.com/AstrBotDevs/astrbot_plugin_weibo_monitor) 和 [Pixiv 监控插件](https://github.com/AstrBotDevs/astrbot_plugin_pixiv_monitor) 的架构与实现。

- [instaloader](https://instaloader.github.io/) - Instagram 数据抓取库
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) - 开源 QQ 机器人框架