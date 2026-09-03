"""Instagram 动态监控插件主模块。

基于 Star 基类，实现多账号轮询、订阅管理、消息推送。
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import MessageChain, filter
from astrbot.api.star import Context, Star

from .client import InstagramClient, PrivateProfileError
from .formatter import MessageFormatter
from .media import MediaDownloader
from .models import DEFAULT_USER_AGENT, Post
from .state import StateStore


class InstagramMonitor(Star):
    """Instagram 动态监控插件。"""

    def __init__(self, context: Context):
        super().__init__(context)
        # 数据目录
        self.data_root = Path(context.get_data_path()) / "astrbot_plugin_instagram_monitor"
        self.data_root.mkdir(parents=True, exist_ok=True)
        # 初始化组件
        self.state = StateStore(self.data_root / "state.json")
        self.client = InstagramClient(
            username=self._get_config("instagram.username", ""),
            password=self._get_config("instagram.password", ""),
            proxy=self._get_config("instagram.proxy", ""),
            session_dir=self.data_root / "sessions",
            user_agent=DEFAULT_USER_AGENT,
            request_timeout=self._get_config("push.request_timeout", 120.0),
        )
        self.formatter = MessageFormatter(
            template=self._get_config("push.message_template", ""),
            caption_limit=self._get_config("push.caption_limit", 300),
            tz=context.get_timezone(),
        )
        self.media = MediaDownloader(
            cache_dir=self.data_root / "media",
            proxy=self._get_config("instagram.proxy", ""),
            push_video=self._get_config("push.push_video", True),
            max_images_per_post=self._get_config("push.max_images_per_post", 9),
            max_video_size_mb=self._get_config("push.max_video_size_mb", 100),
            request_timeout=self._get_config("push.request_timeout", 120.0),
            retention_minutes=self._get_config("push.retention_minutes", 60),
        )
        # 轮询任务
        self._poll_task: asyncio.Task | None = None
        self._running = False

    def _get_config(self, path: str, default=None):
        """嵌套获取配置值，支持点号路径。"""
        keys = path.split(".")
        config = self.context.get_config() or {}
        value = config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    async def initialize(self):
        """插件初始化。"""
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Instagram 监控插件已启动")

    async def terminate(self):
        """插件终止。"""
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self.media.aclose()
        logger.info("Instagram 监控插件已停止")

    # ---- 轮询任务 ----

    async def _poll_loop(self):
        """轮询循环。"""
        first_delay = self._get_config("schedule.first_check_delay_seconds", 30)
        interval = self._get_config("schedule.poll_interval_minutes", 15) * 60
        await asyncio.sleep(first_delay)
        while self._running:
            try:
                await self._check_all_profiles()
            except Exception as e:
                logger.error("轮询检查失败：%s", e, exc_info=True)
            await asyncio.sleep(interval)

    async def _check_all_profiles(self):
        """检查所有订阅账号是否有新动态。"""
        now = datetime.now(self.context.get_timezone())
        quiet_hours = self._get_config("notify.quiet_hours", [])
        if self._is_quiet_hours(now, quiet_hours):
            return
        for profile in self.state.get_profiles():
            try:
                await self._check_profile(profile)
            except Exception as e:
                logger.error("检查账号 %s 失败：%s", profile, e, exc_info=True)
                if self._get_config("notify.error_message", True):
                    await self._send_error_message(profile, str(e))

    def _is_quiet_hours(self, now: datetime, quiet_hours: list) -> bool:
        """判断当前是否在免打扰时间段。"""
        current_time = now.time()
        for period in quiet_hours:
            start = datetime.strptime(period["start"], "%H:%M").time()
            end = datetime.strptime(period["end"], "%H:%M").time()
            if start <= end:
                if start <= current_time <= end:
                    return True
            else:  # 跨天
                if current_time >= start or current_time <= end:
                    return True
        return False

    async def _check_profile(self, profile: str):
        """检查单个账号的新动态。"""
        last_seen, last_seen_ts = self.state.get_cursor(profile)
        posts = self.client.get_recent_posts(
            profile,
            limit=3,
            since_shortcode=last_seen,
            since_ts=last_seen_ts,
        )
        if not posts:
            return
        for post in posts:
            await self._process_post(profile, post)
            self.state.update_cursor(profile, post.shortcode, post.created_at)

    async def _process_post(self, profile: str, post: Post):
        """处理一条帖子：下载媒体并发送消息。"""
        try:
            downloaded = await self.media.download_for_post(post)
            if not downloaded:
                logger.warning(
                    "账号 %s 的帖子 %s 无可推送媒体", profile, post.shortcode
                )
                return
            message = self.formatter.format(post)
            # 构建消息链
            chain = MessageChain().message(message)
            for media in downloaded:
                if media.is_video:
                    chain.video(media.path)
                else:
                    chain.image(media.path)
            # 推送
            self._send_message(profile, chain)
            # 清理
            self.media.cleanup_post(post.shortcode)
        except Exception as e:
            logger.error("处理帖子 %s 失败：%s", post.shortcode, e, exc_info=True)
            if self._get_config("notify.error_message", True):
                self._send_error_message(profile, f"帖子处理失败：{e}")

    def _send_message(self, profile: str, chain: MessageChain):
        """发送消息到订阅会话。"""
        umos = self.state.get_umos(profile)
        for umo in umos:
            try:
                self.context.send_message(umo, chain)
            except Exception as e:
                logger.error("发送消息到会话 %s 失败：%s", umo, e, exc_info=True)

    def _send_error_message(self, profile: str, error: str):
        """发送错误消息到订阅会话。"""
        message = f"⚠️ Instagram 监控错误（账号：{profile}）\n{error}"
        chain = MessageChain().message(message)
        self._send_message(profile, chain)

    # ---- 指令处理 ----

    @filter.command_group("ig")
    def ig(self):
        """Instagram 动态监控指令组。"""

    @ig.command("sub")
    async def ig_sub(self, event, username: str = ""):
        """订阅指定账号动态。"""
        username = username.strip().lstrip("@")
        if not username:
            yield event.plain_result("请提供 Instagram 用户名，例如：/ig sub instagram")
            return
        try:
            if not self.client.profile_exists(username):
                yield event.plain_result(f"账号 @{username} 不存在或不可访问")
                return
        except PrivateProfileError:
            yield event.plain_result(f"账号 @{username} 是私密账号，无法监控")
            return
        if self.state.subscribe(username, event.unified_msg_origin):
            yield event.plain_result(f"✅ 已订阅 @{username} 的新动态")
        else:
            yield event.plain_result(f"ℹ️ 你已订阅 @{username}，无需重复订阅")

    @ig.command("unsub")
    async def ig_unsub(self, event, username: str = ""):
        """取消订阅指定账号动态。"""
        username = username.strip().lstrip("@")
        if not username:
            yield event.plain_result("请提供 Instagram 用户名，例如：/ig unsub instagram")
            return
        if self.state.unsubscribe(username, event.unified_msg_origin):
            yield event.plain_result(f"✅ 已取消订阅 @{username}")
        else:
            yield event.plain_result(f"ℹ️ 你未订阅 @{username}")

    @ig.command("list")
    async def ig_list(self, event):
        """查看当前订阅的账号列表。"""
        profiles = self.state.get_profiles()
        if not profiles:
            yield event.plain_result("📋 当前没有订阅任何 Instagram 账号")
            return
        message = "📋 订阅的 Instagram 账号：\n"
        for profile in profiles:
            umos = self.state.get_umos(profile)
            message += f"- @{profile}（{len(umos)} 个会话）\n"
        yield event.plain_result(message.strip())

    @ig.command("check")
    async def ig_check(self, event, username: str = ""):
        """检查账号状态。"""
        username = username.strip().lstrip("@")
        if not username:
            yield event.plain_result("请提供 Instagram 用户名，例如：/ig check instagram")
            return
        try:
            exists = self.client.profile_exists(username)
            last_seen, last_seen_ts = self.state.get_cursor(username)
            if last_seen_ts:
                time_str = last_seen_ts.strftime("%Y-%m-%d %H:%M")
            else:
                time_str = "未推送过"
            message = f"📊 账号 @{username} 状态：\n"
            message += f"- 状态：{'✅ 正常' if exists else '❌ 不可访问'}\n"
            message += f"- 最后推送：{time_str}"
            yield event.plain_result(message)
        except Exception as e:
            yield event.plain_result(f"❌ 检查失败：{e}")

    @ig.command("umo")
    async def ig_umo(self, event):
        """查看自己订阅的账号列表。"""
        profiles = []
        for profile in self.state.get_profiles():
            if event.unified_msg_origin in self.state.get_umos(profile):
                profiles.append(profile)
        if not profiles:
            yield event.plain_result("📋 你没有订阅任何 Instagram 账号")
            return
        message = "📋 你订阅的 Instagram 账号：\n"
        for profile in profiles:
            message += f"- @{profile}\n"
        yield event.plain_result(message.strip())
