"""instaloader 封装：登录/Session 管理、代理注入、拉取最近动态。

instaloader 是同步库，调用方需通过 asyncio.to_thread 等方式在线程中执行。
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import instaloader

from .models import DEFAULT_USER_AGENT, MediaItem, Post, PostType

logger = logging.getLogger("astrbot_plugin_instagram_monitor.client")

# 触发重新登录前的冷却时间（秒），避免反复登录加剧风控
RELOGIN_COOLDOWN = 1800
# 查找游标（last_seen）帖子时最多额外向后扫描的帖子数
CURSOR_SCAN_WINDOW = 30


class InstagramError(Exception):
    """Instagram 抓取相关错误基类。"""


class LoginRequiredError(InstagramError):
    """需要登录（未配置账号、登录失效或被风控）。"""


class RateLimitedError(InstagramError):
    """请求被限流（429）。"""


class ProfileNotFoundError(InstagramError):
    """账号不存在或不可访问。"""


class PrivateProfileError(InstagramError):
    """私密账号，无法获取动态。"""


def to_utc(dt: datetime) -> datetime:
    """instaloader 返回 naive datetime，统一转为 UTC-aware。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class InstagramClient:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        proxy: str = "",
        session_dir: Path = Path("."),
        user_agent: str = DEFAULT_USER_AGENT,
        request_timeout: float = 30.0,
        max_connection_attempts: int = 3,
    ):
        self._ig_username = (username or "").strip()
        self._ig_password = (password or "").strip()
        self._proxy = (proxy or "").strip()
        self._session_dir = Path(session_dir)
        self._user_agent = user_agent
        self._request_timeout = request_timeout
        self._max_connection_attempts = max_connection_attempts
        self._loader: instaloader.Instaloader | None = None
        self._logged_in = False
        self._relogin_after = 0.0  # 冷却期内不再尝试登录

    @property
    def login_ok(self) -> bool:
        """当前是否处于登录态。"""
        return self._logged_in

    # ---- 对外接口 ----

    def profile_exists(self, username: str) -> bool:
        """校验账号是否存在且可监控（私密账号会抛 PrivateProfileError）。"""
        self._ensure_ready()
        try:
            profile = self._call_with_relogin(lambda: self._get_profile(username))
        except ProfileNotFoundError:
            return False
        if profile.is_private:
            raise PrivateProfileError(f"@{username} 是私密账号，无法监控")
        return True

    def get_recent_posts(
        self,
        profile_name: str,
        limit: int = 3,
        since_shortcode: str = "",
        since_ts: datetime | None = None,
    ) -> list[Post]:
        """获取比游标更新的帖子（时间倒序，最多 limit 条）。

        Args:
            profile_name:  Instagram 用户名
            limit:         返回的最大帖子数
            since_shortcode: 已处理到的帖子 shortcode（游标）
            since_ts:      已处理到的帖子发布时间（游标，比 shortcode 优先级高）

        Returns:
            按发布时间倒序排列的帖子列表（最新在前）

        Note:
            since_shortcode/since_ts 均为空时，返回最新的 limit 条（首查）。
            否则返回比游标更新的帖子（最多 limit 条）。
        """
        self._ensure_ready()
        return self._call_with_relogin(
            lambda: self._fetch(profile_name, limit, since_shortcode, since_ts)
        )

    # ---- 内部实现 ----

    def _new_loader(self) -> instaloader.Instaloader:
        loader = instaloader.Instaloader(
            quiet=True,
            user_agent=self._user_agent,
            sleep=True,
            download_comments=False,
            save_metadata=False,
            max_connection_attempts=self._max_connection_attempts,
            request_timeout=self._request_timeout,
        )
        if self._proxy:
            # instaloader 底层是 requests.Session，直接注入代理
            proxies = {"http": self._proxy, "https": self._proxy}
            try:
                loader.context._session.proxies.update(proxies)
            except AttributeError:  # 兼容未来版本结构变化
                logger.warning("无法向 instaloader session 注入代理，将退回匿名直连")
        return loader

    def _ensure_ready(self) -> None:
        if self._loader is None:
            self._loader = self._new_loader()
            self._restore_login()

    def _session_file(self) -> Path:
        safe = self._ig_username.replace("/", "_") or "anonymous"
        return self._session_dir / f"session-{safe}"

    def _restore_login(self) -> None:
        """按需恢复/建立登录态（失败时回退匿名模式）。"""
        if self._logged_in or not self._ig_username:
            return
        session_file = self._session_file()
        if session_file.exists():
            try:
                self._loader.load_session_from_file(
                    self._ig_username, str(session_file)
                )
                self._logged_in = True
                logger.info("已从 %s 恢复 Instagram 登录态", session_file.name)
                return
            except (OSError, instaloader.exceptions.InstaloaderException) as e:
                logger.warning("恢复 Instagram 登录态失败：%s", e)
        self._do_login()

    def _do_login(self) -> bool:
        if not (self._ig_username and self._ig_password):
            logger.info("未配置 Instagram 账号密码，使用匿名模式访问")
            return False
        if time.time() < self._relogin_after:
            return False
        try:
            self._loader.login(self._ig_username, self._ig_password)
        except instaloader.exceptions.TwoFactorAuthRequiredException:
            logger.error("Instagram 账号开启了双重验证(2FA)，本插件暂不支持 2FA 登录")
            self._relogin_after = time.time() + RELOGIN_COOLDOWN
            return False
        except instaloader.exceptions.LoginException as e:
            logger.error("Instagram 登录失败：%s", e)
            self._relogin_after = time.time() + RELOGIN_COOLDOWN
            return False
        except instaloader.exceptions.InstaloaderException as e:
            logger.error("Instagram 登录异常：%s", e)
            self._relogin_after = time.time() + RELOGIN_COOLDOWN
            return False
        self._logged_in = True
        self._relogin_after = 0.0
        try:
            self._session_dir.mkdir(parents=True, exist_ok=True)
            self._loader.save_session_to_file(str(self._session_file()))
            logger.info("Instagram 登录成功，Session 已保存")
        except OSError as e:
            logger.warning("Session 文件保存失败：%s", e)
        return True

    def _relogin(self) -> bool:
        """Session 失效后重建登录态。"""
        self._logged_in = False
        if not (self._ig_username and self._ig_password):
            return False
        if time.time() < self._relogin_after:
            return False
        logger.info("尝试重新登录 Instagram ...")
        # 重建 loader，避免复用失效 session
        self._loader = self._new_loader()
        return self._do_login()

    def _call_with_relogin(self, func):
        """执行 func()，遇到 LoginRequiredException 时重登录后重试一次。"""
        try:
            return func()
        except instaloader.exceptions.LoginRequiredException:
            if self._relogin():
                try:
                    return func()
                except instaloader.exceptions.LoginRequiredException:
                    pass
            raise LoginRequiredError(
                "Instagram 需要登录才能访问（未配置账号密码，或登录已失效）"
            ) from None

    def _get_profile(self, username: str) -> "instaloader.Profile":
        try:
            return instaloader.Profile.from_username(self._loader.context, username)
        except instaloader.exceptions.ProfileNotExistsException as e:
            raise ProfileNotFoundError(
                f"Instagram 用户 @{username} 不存在或不可访问"
            ) from e
        except instaloader.exceptions.LoginRequiredException:
            raise  # 由上层处理重登录
        except instaloader.exceptions.TooManyRequestsException as e:
            raise RateLimitedError(f"Instagram 请求被限流：{e}") from e
        except instaloader.exceptions.InstaloaderException as e:
            raise InstagramError(f"获取 Instagram 用户 @{username} 失败：{e}") from e

    def _fetch(self, profile_name, limit, since_shortcode, since_ts) -> list[Post]:
        profile = self._get_profile(profile_name)
        if profile.is_private:
            raise PrivateProfileError(f"@{profile_name} 是私密账号，无法监控")
        posts: list[Post] = []
        has_cursor = bool(since_shortcode) or since_ts is not None
        scan_window = limit + CURSOR_SCAN_WINDOW if has_cursor else limit
        try:
            iterator = profile.get_posts()
            for _ in range(scan_window):
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                if since_shortcode and item.shortcode == since_shortcode:
                    break
                if since_ts is not None and to_utc(item.date_utc) <= since_ts:
                    break
                posts.append(self._to_post(item))
                if len(posts) >= limit:
                    break
        except instaloader.exceptions.LoginRequiredException:
            raise
        except instaloader.exceptions.TooManyRequestsException as e:
            raise RateLimitedError(f"Instagram 请求被限流：{e}") from e
        except instaloader.exceptions.PrivateProfileNotFollowedException as e:
            raise PrivateProfileError(f"@{profile_name} 是私密账号，无法监控") from e
        except instaloader.exceptions.InstaloaderException as e:
            raise InstagramError(f"获取 @{profile_name} 动态失败：{e}") from e
        return posts

    @staticmethod
    def _to_post(item) -> Post:
        typename = item.typename
        media: list[MediaItem] = []
        if typename == "GraphSidecar":
            post_type = PostType.CAROUSEL
            for node in item.get_sidecar_nodes():
                if node.is_video and node.video_url:
                    media.append(MediaItem(url=node.video_url, is_video=True))
                else:
                    media.append(MediaItem(url=node.display_url, is_video=False))
        elif typename == "GraphVideo":
            post_type = PostType.VIDEO
            if item.video_url:
                media.append(MediaItem(url=item.video_url, is_video=True))
        else:
            post_type = PostType.IMAGE
            if item.url:
                media.append(MediaItem(url=item.url, is_video=False))
        return Post(
            shortcode=item.shortcode,
            username=item.owner_username,
            caption=item.caption or "",
            post_type=post_type,
            created_at=to_utc(item.date_utc),
            media=media,
        )
