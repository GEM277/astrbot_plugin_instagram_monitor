"""Instagram 动态监控插件客户端模块测试。"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from instagram_monitor.client import (
    InstagramClient,
    PrivateProfileError,
)


@pytest.fixture
def temp_session_dir():
    """临时 Session 目录。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def client(temp_session_dir):
    """Instagram 客户端。"""
    return InstagramClient(
        username="testuser",
        password="testpass",
        proxy="http://127.0.0.1:7890",
        session_dir=temp_session_dir,
        request_timeout=30.0,
    )


def test_client_init():
    """测试客户端初始化。"""
    client = InstagramClient(
        username="testuser",
        password="testpass",
        proxy="http://127.0.0.1:7890",
        session_dir=Path("/tmp"),
    )
    assert client._ig_username == "testuser"
    assert client._ig_password == "testpass"
    assert client._proxy == "http://127.0.0.1:7890"
    assert client._logged_in is False


def test_profile_exists_public(client):
    """测试公开账号存在性检查。"""
    mock_profile = MagicMock()
    mock_profile.is_private = False

    with patch("instaloader.Profile.from_username") as mock_from_username:
        mock_from_username.return_value = mock_profile
        assert client.profile_exists("publicuser") is True


def test_profile_exists_private(client):
    """测试私密账号存在性检查。"""
    mock_profile = MagicMock()
    mock_profile.is_private = True

    with patch("instaloader.Profile.from_username") as mock_from_username:
        mock_from_username.return_value = mock_profile
        with pytest.raises(PrivateProfileError):
            client.profile_exists("privateuser")


def test_profile_not_exists(client):
    """测试不存在的账号。"""
    with patch("instaloader.Profile.from_username") as mock_from_username:
        mock_from_username.side_effect = Exception("Profile not found")
        assert client.profile_exists("notexists") is False


@pytest.mark.asyncio
async def test_get_recent_posts(client):
    """测试获取最新动态。"""
    mock_post = MagicMock()
    mock_post.shortcode = "abc123"
    mock_post.owner_username = "testuser"
    mock_post.caption = "测试动态"
    mock_post.typename = "GraphImage"
    mock_post.url = "https://example.com/image.jpg"
    mock_post.date_utc = datetime(2024, 1, 1, 12, 0)

    mock_profile = MagicMock()
    mock_profile.is_private = False
    mock_profile.get_posts.return_value = iter([mock_post])

    with patch("instaloader.Profile.from_username") as mock_from_username:
        mock_from_username.return_value = mock_profile
        posts = client.get_recent_posts("testuser", limit=1)
        assert len(posts) == 1
        assert posts[0].shortcode == "abc123"
        assert posts[0].username == "testuser"
        assert posts[0].caption == "测试动态"
        assert posts[0].post_type.value == "图片"
        assert posts[0].link == "https://www.instagram.com/p/abc123/"


def test_to_utc():
    """测试时间转换。"""
    from instagram_monitor.client import to_utc

    naive = datetime(2024, 1, 1, 12, 0)
    aware = to_utc(naive)
    assert aware.tzinfo == timezone.utc
    assert aware.year == 2024
    assert aware.month == 1
    assert aware.day == 1
    assert aware.hour == 12


def test_to_utc_aware():
    """测试已有时区的时间转换。"""
    from instagram_monitor.client import to_utc

    aware = datetime(2024, 1, 1, 12, 0, tzinfo=timezone(tzhours=8))
    utc = to_utc(aware)
    assert utc.tzinfo == timezone.utc
    assert utc.hour == 4  # UTC+8 -> UTC


def test_new_loader(client):
    """测试新加载器创建。"""
    loader = client._new_loader()
    assert loader is not None
    assert loader.context._session.proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


def test_new_loader_no_proxy():
    """测试无代理的加载器创建。"""
    client = InstagramClient(username="test", password="test")
    loader = client._new_loader()
    assert loader is not None
    assert loader.context._session.proxies == {}


def test_session_file(client):
    """测试 Session 文件路径。"""
    session_file = client._session_file()
    assert session_file.name == "session-testuser"


def test_session_file_anonymous():
    """测试匿名模式的 Session 文件路径。"""
    client = InstagramClient()
    session_file = client._session_file()
    assert session_file.name == "session-anonymous"


def test_restore_login_with_file(client):
    """测试从文件恢复登录。"""
    session_file = client._session_file()
    session_file.write_text("fake session data")
    with patch("instaloader.Instaloader.load_session_from_file") as mock_load:
        mock_load.return_value = None
        client._restore_login()
        assert client._logged_in is True


def test_restore_login_no_file(client):
    """测试无文件时不恢复登录。"""
    client._restore_login()
    assert client._logged_in is False


def test_do_login_success(client):
    """测试成功登录。"""
    with patch("instaloader.Instaloader.login") as mock_login:
        mock_login.return_value = None
        result = client._do_login()
        assert result is True
        assert client._logged_in is True


def test_do_login_no_credentials(client):
    """测试无凭据时不登录。"""
    client = InstagramClient()
    result = client._do_login()
    assert result is False
    assert client._logged_in is False


def test_do_login_exception(client):
    """测试登录异常。"""
    with patch("instaloader.Instaloader.login") as mock_login:
        mock_login.side_effect = Exception("Login failed")
        result = client._do_login()
        assert result is False
        assert client._logged_in is False


def test_relogin_success(client):
    """测试重新登录成功。"""
    with patch.object(client, "_do_login", return_value=True) as mock_do_login:
        result = client._relogin()
        assert result is True
        mock_do_login.assert_called_once()


def test_relogin_no_credentials(client):
    """测试无凭据时重新登录失败。"""
    client = InstagramClient()
    result = client._relogin()
    assert result is False


def test_relogin_cooldown(client):
    """测试冷却期内不重新登录。"""
    import time

    client._relogin_after = time.time() + 1000
    result = client._relogin()
    assert result is False


def test_call_with_relogin_success(client):
    """测试成功执行函数。"""

    def dummy_func():
        return "success"

    with patch.object(client, "_relogin", return_value=False):
        result = client._call_with_relogin(dummy_func)
        assert result == "success"


def test_call_with_relogin_login_required(client):
    """测试需要登录时的重试。"""
    from instaloader.exceptions import LoginRequiredException

    def dummy_func():
        raise LoginRequiredException()

    with patch.object(client, "_relogin", return_value=True), patch.object(
        client, "_call_with_relogin", side_effect=["success"]
    ) as mock_call:
        result = client._call_with_relogin(dummy_func)
        assert result == "success"


def test_call_with_relogin_login_required_no_relogin(client):
    """测试需要登录但无法重新登录。"""
    from instaloader.exceptions import LoginRequiredException

    def dummy_func():
        raise LoginRequiredException()

    with patch.object(client, "_relogin", return_value=False):
        with pytest.raises(Exception):
            client._call_with_relogin(dummy_func)


def test_to_post_image():
    """测试图片帖子转换。"""
    mock_post = MagicMock()
    mock_post.shortcode = "abc123"
    mock_post.owner_username = "testuser"
    mock_post.caption = "测试动态"
    mock_post.typename = "GraphImage"
    mock_post.url = "https://example.com/image.jpg"
    mock_post.date_utc = datetime(2024, 1, 1, 12, 0)

    post = InstagramClient._to_post(mock_post)
    assert post.shortcode == "abc123"
    assert post.username == "testuser"
    assert post.caption == "测试动态"
    assert post.post_type.value == "图片"
    assert len(post.media) == 1
    assert post.media[0].url == "https://example.com/image.jpg"
    assert post.media[0].is_video is False


def test_to_post_video():
    """测试视频帖子转换。"""
    mock_post = MagicMock()
    mock_post.shortcode = "abc123"
    mock_post.owner_username = "testuser"
    mock_post.caption = "测试动态"
    mock_post.typename = "GraphVideo"
    mock_post.video_url = "https://example.com/video.mp4"
    mock_post.date_utc = datetime(2024, 1, 1, 12, 0)

    post = InstagramClient._to_post(mock_post)
    assert post.post_type.value == "视频"
    assert len(post.media) == 1
    assert post.media[0].url == "https://example.com/video.mp4"
    assert post.media[0].is_video is True


def test_to_post_carousel():
    """测试多图帖子转换。"""
    mock_node1 = MagicMock()
    mock_node1.is_video = False
    mock_node1.display_url = "https://example.com/image1.jpg"
    mock_node1.video_url = None

    mock_node2 = MagicMock()
    mock_node2.is_video = True
    mock_node2.display_url = None
    mock_node2.video_url = "https://example.com/video1.mp4"

    mock_post = MagicMock()
    mock_post.shortcode = "abc123"
    mock_post.owner_username = "testuser"
    mock_post.caption = "测试动态"
    mock_post.typename = "GraphSidecar"
    mock_post.get_sidecar_nodes.return_value = [mock_node1, mock_node2]
    mock_post.date_utc = datetime(2024, 1, 1, 12, 0)

    post = InstagramClient._to_post(mock_post)
    assert post.post_type.value == "多图"
    assert len(post.media) == 2
    assert post.media[0].url == "https://example.com/image1.jpg"
    assert post.media[0].is_video is False
    assert post.media[1].url == "https://example.com/video1.mp4"
    assert post.media[1].is_video is True
