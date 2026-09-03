"""Instagram 动态监控插件媒体下载模块测试。"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from instagram_monitor.media import MediaDownloader


@pytest.fixture
def temp_cache_dir():
    """临时缓存目录。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def downloader(temp_cache_dir):
    """媒体下载器。"""
    return MediaDownloader(
        cache_dir=temp_cache_dir,
        proxy="http://127.0.0.1:7890",
        push_video=True,
        max_images_per_post=9,
        max_video_size_mb=10,
        request_timeout=30.0,
        retention_minutes=60,
    )


@pytest.fixture
def sample_post():
    """示例帖子数据。"""
    from datetime import datetime, timezone

    from instagram_monitor.models import MediaItem, Post, PostType

    return Post(
        shortcode="abc123",
        username="testuser",
        caption="测试动态",
        post_type=PostType.IMAGE,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[
            MediaItem(url="https://example.com/image1.jpg", is_video=False),
            MediaItem(url="https://example.com/image2.jpg", is_video=False),
        ],
    )


def test_select_items_images(sample_post):
    """测试图片选择。"""
    downloader = MediaDownloader(
        cache_dir=Path("/tmp"),
        push_video=True,
        max_images_per_post=2,
    )
    items = downloader._select_items(sample_post)
    assert len(items) == 2


def test_select_items_video():
    """测试视频选择。"""
    from datetime import datetime, timezone

    from instagram_monitor.models import MediaItem, Post, PostType

    post = Post(
        shortcode="abc123",
        username="testuser",
        caption="视频动态",
        post_type=PostType.VIDEO,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[MediaItem(url="https://example.com/video.mp4", is_video=True)],
    )
    downloader = MediaDownloader(
        cache_dir=Path("/tmp"),
        push_video=False,
        max_images_per_post=9,
    )
    items = downloader._select_items(post)
    assert len(items) == 0
    downloader = MediaDownloader(
        cache_dir=Path("/tmp"),
        push_video=True,
        max_images_per_post=9,
    )
    items = downloader._select_items(post)
    assert len(items) == 1


def test_select_items_carousel():
    """测试多图选择。"""
    from datetime import datetime, timezone

    from instagram_monitor.models import MediaItem, Post, PostType

    post = Post(
        shortcode="abc123",
        username="testuser",
        caption="多图动态",
        post_type=PostType.CAROUSEL,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[
            MediaItem(url="https://example.com/image1.jpg", is_video=False),
            MediaItem(url="https://example.com/video1.mp4", is_video=True),
            MediaItem(url="https://example.com/image2.jpg", is_video=False),
            MediaItem(url="https://example.com/image3.jpg", is_video=False),
        ],
    )
    downloader = MediaDownloader(
        cache_dir=Path("/tmp"),
        push_video=False,
        max_images_per_post=2,
    )
    items = downloader._select_items(post)
    assert len(items) == 2  # 只选图片
    downloader = MediaDownloader(
        cache_dir=Path("/tmp"),
        push_video=True,
        max_images_per_post=3,
    )
    items = downloader._select_items(post)
    assert len(items) == 3  # 2图片 + 1视频


@pytest.mark.asyncio
async def test_download_success(downloader, sample_post):
    """测试成功下载。"""
    mock_response = AsyncMock()
    mock_response.headers = {"content-length": "1000"}
    mock_response.aiter_bytes.return_value = [b"fake image data"]
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.stream.return_value.__aenter__.return_value = (
            mock_response
        )
        mock_client.return_value.stream.return_value.__aexit__.return_value = None

        downloaded = await downloader.download_for_post(sample_post)
        assert len(downloaded) == 2
        assert all(item.path.exists() for item in downloaded)
        assert downloader.cache_dir / "abc123" / "01.jpg"
        assert downloader.cache_dir / "abc123" / "02.jpg"


@pytest.mark.asyncio
async def test_download_video_too_large(downloader):
    """测试视频过大。"""
    from datetime import datetime, timezone

    from instagram_monitor.models import MediaItem, Post, PostType

    post = Post(
        shortcode="abc123",
        username="testuser",
        caption="大视频",
        post_type=PostType.VIDEO,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[MediaItem(url="https://example.com/bigvideo.mp4", is_video=True)],
    )

    mock_response = AsyncMock()
    mock_response.headers = {"content-length": "20000000"}  # 20MB
    mock_response.aiter_bytes.return_value = [b"fake video data"]
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.stream.return_value.__aenter__.return_value = (
            mock_response
        )
        mock_client.return_value.stream.return_value.__aexit__.return_value = None

        downloaded = await downloader.download_for_post(post)
        assert len(downloaded) == 0  # 跳过


@pytest.mark.asyncio
async def test_download_error(downloader, sample_post):
    """测试下载失败。"""
    mock_response = AsyncMock()
    mock_response.raise_for_status.side_effect = Exception("网络错误")

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.stream.return_value.__aenter__.return_value = (
            mock_response
        )
        mock_client.return_value.stream.return_value.__aexit__.return_value = None

        downloaded = await downloader.download_for_post(sample_post)
        assert len(downloaded) == 0


def test_cleanup_post(downloader):
    """测试清理帖子。"""
    post_dir = downloader.cache_dir / "abc123"
    post_dir.mkdir(parents=True)
    (post_dir / "01.jpg").write_text("fake")
    downloader.cleanup_post("abc123")
    assert not post_dir.exists()


def test_sweep_expired(downloader):
    """测试清理过期文件。"""
    # 创建过期的帖子目录
    old_post = downloader.cache_dir / "old_post"
    old_post.mkdir(parents=True)
    (old_post / "01.jpg").write_text("fake")
    # 设置过期时间
    import time

    old_time = time.time() - 7200  # 2小时前
    old_post.lstat().st_mtime = old_time
    # 创建新的帖子目录
    new_post = downloader.cache_dir / "new_post"
    new_post.mkdir(parents=True)
    (new_post / "01.jpg").write_text("fake")
    new_time = time.time() - 1800  # 30分钟前
    new_post.lstat().st_mtime = new_time
    # 清理
    downloader.sweep_expired()
    assert not old_post.exists()
    assert new_post.exists()


@pytest.mark.asyncio
async def test_client_close(downloader):
    """测试客户端关闭。"""
    mock_client = MagicMock()
    downloader._client = mock_client
    await downloader.aclose()
    mock_client.aclose.assert_called_once()
