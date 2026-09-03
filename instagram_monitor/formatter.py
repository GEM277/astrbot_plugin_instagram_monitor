"""推送消息模板渲染。

模板变量（安全替换，未知变量原样保留）::

    {name}     发帖人用户名
    {caption}  帖子文案（超长截断；空文案显示占位符）
    {link}     帖子链接
    {date}     发布时间（本地时区，格式 YYYY-MM-DD HH:MM）
    {type}     动态类型（图片/视频/多图）
"""

from datetime import datetime
from datetime import timezone as _timezone

from .models import Post

DEFAULT_TEMPLATE = "📸 Instagram 新动态\n👤 {name}\n📝 {caption}\n🔗 {link}\n🕒 {date}"

EMPTY_CAPTION_TEXT = "（无文案）"


class MessageFormatter:
    def __init__(
        self,
        template: str = "",
        caption_limit: int = 300,
        tz: _timezone | None = None,
    ):
        """
        Args:
            template:      消息模板，留空使用默认模板
            caption_limit: 文案最大长度，超出截断
            tz:            时间显示时区，None 时使用系统本地时区
        """
        self.template = (template or "").strip() or DEFAULT_TEMPLATE
        self.caption_limit = max(0, int(caption_limit))
        self._tz = tz

    def format(self, post: Post) -> str:
        caption = self._clip_caption(post.caption)
        created: datetime = post.created_at
        if self._tz is not None:
            created = created.astimezone(self._tz)
        else:
            created = created.astimezone()
        variables = {
            "name": post.username,
            "caption": caption,
            "link": post.link,
            "date": created.strftime("%Y-%m-%d %H:%M"),
            "type": post.post_type.value,
        }
        rendered = self.template
        for key, value in variables.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered.strip()

    def _clip_caption(self, caption: str | None) -> str:
        caption = (caption or "").strip()
        if not caption:
            return EMPTY_CAPTION_TEXT
        if self.caption_limit and len(caption) > self.caption_limit:
            return caption[: self.caption_limit].rstrip() + "…"
        return caption
