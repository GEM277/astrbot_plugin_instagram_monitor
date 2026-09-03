"""Instagram 动态监控插件状态模块测试。"""

import json
import tempfile
from pathlib import Path

import pytest

from instagram_monitor.state import StateStore


@pytest.fixture
def temp_state_file():
    """临时状态文件。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir) / "test_state.json"


def test_state_store_init(temp_state_file):
    """测试状态存储初始化。"""
    store = StateStore(temp_state_file)
    assert store.data == {"subscriptions": {}}
    assert store.get_profiles() == []
    assert store.get_umos("test") == []


def test_subscribe(temp_state_file):
    """测试订阅功能。"""
    store = StateStore(temp_state_file)
    # 新增订阅
    assert store.subscribe("user1", "session1") is True
    assert store.subscribe("user1", "session2") is False  # 重复订阅
    assert store.subscribe("user2", "session1") is True
    # 验证数据
    assert store.get_profiles() == ["user1", "user2"]
    assert store.get_umos("user1") == ["session1", "session2"]
    assert store.get_umos("user2") == ["session1"]
    # 验证文件写入
    assert temp_state_file.exists()
    data = json.loads(temp_state_file.read_text())
    assert data == {
        "subscriptions": {
            "user1": {
                "umos": ["session1", "session2"],
                "last_seen": "",
                "last_seen_ts": "",
            },
            "user2": {"umos": ["session1"], "last_seen": "", "last_seen_ts": ""},
        }
    }


def test_unsubscribe(temp_state_file):
    """测试取消订阅功能。"""
    store = StateStore(temp_state_file)
    store.subscribe("user1", "session1")
    store.subscribe("user1", "session2")
    # 取消订阅
    assert store.unsubscribe("user1", "session1") is True
    assert store.unsubscribe("user1", "session1") is False  # 重复取消
    assert store.unsubscribe("user2", "session1") is False  # 不存在的订阅
    # 验证数据
    assert store.get_profiles() == ["user1"]
    assert store.get_umos("user1") == ["session2"]
    # 最后一个会话退订，删除账号
    assert store.unsubscribe("user1", "session2") is True
    assert store.get_profiles() == []


def test_cursor(temp_state_file):
    """测试游标功能。"""
    store = StateStore(temp_state_file)
    store.subscribe("user1", "session1")
    # 初始游标
    shortcode, ts = store.get_cursor("user1")
    assert shortcode == ""
    assert ts is None
    # 更新游标
    from datetime import datetime, timezone

    ts1 = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    store.update_cursor("user1", "abc123", ts1)
    shortcode, ts = store.get_cursor("user1")
    assert shortcode == "abc123"
    assert ts == ts1
    # 更新游标（naive datetime）
    ts2 = datetime(2024, 1, 1, 13, 0)
    store.update_cursor("user1", "def456", ts2)
    shortcode, ts = store.get_cursor("user1")
    assert shortcode == "def456"
    assert ts.tzinfo == timezone.utc


def test_corrupted_file(temp_state_file):
    """测试损坏的文件处理。"""
    # 写入无效 JSON
    temp_state_file.write_text("invalid json")
    store = StateStore(temp_state_file)
    assert store.data == {"subscriptions": {}}
    # 写入无效结构
    temp_state_file.write_text('{"subscriptions": "invalid"}')
    store = StateStore(temp_state_file)
    assert store.data == {"subscriptions": {}}


def test_missing_umos(temp_state_file):
    """测试缺少 umos 字段的兼容性。"""
    temp_state_file.write_text(
        '{"subscriptions": {"user1": {"last_seen": "abc", "last_seen_ts": "2024-01-01T12:00:00+00:00"}}}'
    )
    store = StateStore(temp_state_file)
    assert store.get_umos("user1") == []
    assert store.get_cursor("user1") == (
        "abc",
        datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
