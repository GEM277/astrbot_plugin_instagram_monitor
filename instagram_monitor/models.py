"""Instagram 动态数据模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
"""访问 Instagram 使用的 User-Agent，client 与 media 共用。"""


class PostType(str, Enum):
    """动态类型。"""

    IMAGE = "图片"
    VIDEO = "视频"
    CAROUSEL = "多图"


@dataclass
class MediaItem:
    """帖子中的单个媒体资源。"""

    url: str
    is_video: bool


@dataclass
class Post:
    """一条 Instagram 动态。

    Attributes:
        shortcode:   帖子短码（唯一标识，用于拼接链接与游标去重）
        username:    发帖人用户名
        caption:     文案（可能为空）
        post_type:   动态类型
        created_at:  发布时间（tz-aware，UTC）
        media:       媒体资源列表
    """

    shortcode: str
    username: str
    caption: str
    post_type: PostType
    created_at: datetime
    media: list[MediaItem] = field(default_factory=list)

    @property
    def link(self) -> str:
        return f"https://www.instagram.com/p/{self.shortcode}/"
