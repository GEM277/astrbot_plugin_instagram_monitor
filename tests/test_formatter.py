"""Instagram 动态监控插件消息格式化模块测试。"""

from datetime import datetime, timezone

import pytest

from instagram_monitor.formatter import MessageFormatter
from instagram_monitor.models import MediaItem, Post, PostType


@pytest.fixture
def sample_post():
    """示例帖子数据。"""
    return Post(
        shortcode="abc123",
        username="testuser",
        caption="这是一条测试动态 📸",
        post_type=PostType.IMAGE,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[MediaItem(url="https://example.com/image.jpg", is_video=False)],
    )


def test_default_template(sample_post):
    """测试默认模板。"""
    formatter = MessageFormatter()
    message = formatter.format(sample_post)
    assert "📸 Instagram 新动态" in message
    assert "👤 testuser" in message
    assert "📝 这是一条测试动态 📸" in message
    assert "🔗 https://www.instagram.com/p/abc123/" in message
    assert "🕒 2024-01-01 12:00" in message


def test_custom_template(sample_post):
    """测试自定义模板。"""
    template = "来自 {name}：{caption}\n链接：{link}"
    formatter = MessageFormatter(template=template)
    message = formatter.format(sample_post)
    assert "来自 testuser：" in message
    assert "这是一条测试动态 📸" in message
    assert "链接：https://www.instagram.com/p/abc123/" in message


def test_caption_clipping(sample_post):
    """测试文案截断。"""
    long_caption = "a" * 500
    post = Post(
        shortcode="abc123",
        username="testuser",
        caption=long_caption,
        post_type=PostType.IMAGE,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[],
    )
    # 默认限制 300
    formatter = MessageFormatter()
    message = formatter.format(post)
    assert len(message) <= 300 + len("📸 Instagram 新动态\n👤 testuser\n📝 \n🔗 \n🕒 ")
    assert "…" in message
    # 自定义限制
    formatter = MessageFormatter(caption_limit=100)
    message = formatter.format(post)
    assert len(message) <= 100 + len("📸 Instagram 新动态\n👤 testuser\n📝 \n🔗 \n🕒 ")


def test_empty_caption(sample_post):
    """测试空文案。"""
    post = Post(
        shortcode="abc123",
        username="testuser",
        caption="",
        post_type=PostType.IMAGE,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[],
    )
    formatter = MessageFormatter()
    message = formatter.format(post)
    assert "（无文案）" in message


def test_timezone_conversion(sample_post):
    """测试时区转换。"""
    # 无时区（系统本地）
    formatter = MessageFormatter()
    message = formatter.format(sample_post)
    assert "2024-01-01 12:00" in message
    # 指定时区
    from datetime import timedelta

    tz = timezone(timedelta(hours=8))
    formatter = MessageFormatter(tz=tz)
    message = formatter.format(sample_post)
    assert "2024-01-01 20:00" in message


def test_post_type_display(sample_post):
    """测试动态类型显示。"""
    # 图片
    formatter = MessageFormatter()
    message = formatter.format(sample_post)
    assert "图片" in message
    # 视频
    post = Post(
        shortcode="abc123",
        username="testuser",
        caption="视频动态",
        post_type=PostType.VIDEO,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[MediaItem(url="https://example.com/video.mp4", is_video=True)],
    )
    message = formatter.format(post)
    assert "视频" in message
    # 多图
    post = Post(
        shortcode="abc123",
        username="testuser",
        caption="多图动态",
        post_type=PostType.CAROUSEL,
        created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        media=[
            MediaItem(url="https://example.com/image1.jpg", is_video=False),
            MediaItem(url="https://example.com/image2.jpg", is_video=False),
        ],
    )
    message = formatter.format(post)
    assert "多图" in message
