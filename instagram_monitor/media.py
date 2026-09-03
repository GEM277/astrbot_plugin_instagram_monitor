"""媒体（图片/视频）下载与清理。

Instagram CDN 直链有时效且带防盗链，因此先下载到本地缓存目录，
再通过消息组件的 fromFileSystem 发送，发送完成后清理。
"""

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .models import DEFAULT_USER_AGENT, MediaItem, Post, PostType

logger = logging.getLogger("astrbot_plugin_instagram_monitor.media")

DOWNLOAD_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Referer": "https://www.instagram.com/",
}
CHUNK_SIZE = 64 * 1024


class _MediaTooLargeError(Exception):
    """媒体超过配置的大小限制。"""


@dataclass
class DownloadedMedia:
    """已下载到本地的媒体文件。"""

    path: Path
    is_video: bool


class MediaDownloader:
    def __init__(
        self,
        cache_dir: Path,
        proxy: str = "",
        push_video: bool = True,
        max_images_per_post: int = 9,
        max_video_size_mb: int = 100,
        request_timeout: float = 120.0,
        retention_minutes: int = 60,
    ):
        self.cache_dir = Path(cache_dir)
        self.proxy = (proxy or "").strip()
        self.push_video = push_video
        self.max_images_per_post = max(1, int(max_images_per_post))
        self.max_video_bytes = max(0, int(max_video_size_mb)) * 1024 * 1024
        self.request_timeout = request_timeout
        self.retention_seconds = max(0, int(retention_minutes)) * 60
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict = {
                "timeout": self.request_timeout,
                "follow_redirects": True,
                "headers": DOWNLOAD_HEADERS,
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- 媒体挑选 ----

    def _select_items(self, post: Post) -> list[MediaItem]:
        """按配置挑选要下载的媒体（多图帖受图片数限制，视频受开关限制）。"""
        if post.post_type == PostType.VIDEO:
            return post.media if self.push_video else []
        if post.post_type == PostType.CAROUSEL:
            selected: list[MediaItem] = []
            image_count = 0
            for item in post.media:
                if item.is_video:
                    if self.push_video:
                        selected.append(item)
                elif image_count < self.max_images_per_post:
                    selected.append(item)
                    image_count += 1
            return selected
        return post.media[: self.max_images_per_post]

    # ---- 下载 ----

    async def download_for_post(self, post: Post) -> list[DownloadedMedia]:
        """下载一条帖子的媒体到缓存目录，返回成功下载的文件列表。"""
        items = self._select_items(post)
        results: list[DownloadedMedia] = []
        if not items:
            return results
        post_dir = self.cache_dir / post.shortcode
        post_dir.mkdir(parents=True, exist_ok=True)
        client = self._get_client()
        for index, item in enumerate(items, start=1):
            suffix = ".mp4" if item.is_video else ".jpg"
            dest = post_dir / f"{index:02d}{suffix}"
            # 视频受大小限制约束，图片不限制
            max_bytes = self.max_video_bytes if item.is_video else None
            try:
                ok = await self._download(client, item.url, dest, max_bytes)
            except Exception as e:
                logger.warning(
                    "媒体下载失败（%s 第 %d 个）：%s", post.shortcode, index, e
                )
                ok = False
            if ok:
                results.append(DownloadedMedia(path=dest, is_video=item.is_video))
            elif dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
        return results

    async def _download(
        self,
        client: httpx.AsyncClient,
        url: str,
        dest: Path,
        max_bytes: int | None,
    ) -> bool:
        """流式下载到 dest。返回 False 表示跳过（超限等），异常向上抛出。"""
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            if max_bytes is not None:
                content_length = resp.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    logger.info(
                        "跳过超过大小限制（%d MB）的媒体",
                        max_bytes // (1024 * 1024),
                    )
                    return False
            received = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                    received += len(chunk)
                    if max_bytes is not None and received > max_bytes:
                        raise _MediaTooLargeError()
                    f.write(chunk)
        return True

    # ---- 清理 ----

    def cleanup_post(self, shortcode: str) -> None:
        """删除某条帖子的媒体缓存目录。"""
        shutil.rmtree(self.cache_dir / shortcode, ignore_errors=True)

    def sweep_expired(self) -> None:
        """清理超过保留时长的残留媒体目录（正常推送后会即时清理，此处兜底）。"""
        if not self.cache_dir.exists():
            return
        now = time.time()
        try:
            children = list(self.cache_dir.iterdir())
        except OSError:
            return
        for child in children:
            try:
                if (
                    child.is_dir()
                    and now - child.stat().st_mtime > self.retention_seconds
                ):
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue
