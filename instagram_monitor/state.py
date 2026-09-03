"""订阅关系与游标状态的本地持久化（JSON 文件，原子写入）。

数据结构::

    {"subscriptions": {"<profile>": {"umos": [...], "last_seen": "<shortcode>", "last_seen_ts": "<iso8606>"}}}

- umos:         订阅了该账号的会话 ID 列表（unified_msg_origin）
- last_seen:    已处理到的最新帖子 shortcode（游标）
- last_seen_ts: 该帖子的发布时间（ISO 8601，UTC），shortcode 被删帖时兜底
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("astrbot_plugin_instagram_monitor.state")


def _new_entry() -> dict:
    return {"umos": [], "last_seen": "", "last_seen_ts": ""}


class StateStore:
    """管理 profile -> 订阅会话 + 游标 的映射，自动持久化到 JSON 文件。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, dict] = {"subscriptions": {}}
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("状态文件读取失败，将重建：%s", e)
            try:
                os.replace(self.path, self.path.with_suffix(".corrupt"))
                logger.warning(
                    "原状态文件已备份为 %s", self.path.with_suffix(".corrupt")
                )
            except OSError:
                pass
            return
        if not isinstance(raw, dict):
            return
        subs = raw.get("subscriptions")
        if isinstance(subs, dict):
            self.data["subscriptions"] = subs
        self._normalize()

    def _normalize(self) -> None:
        subs = self.data["subscriptions"]
        for profile, entry in list(subs.items()):
            if not isinstance(entry, dict) or not isinstance(entry.get("umos"), list):
                subs[profile] = _new_entry()
                continue
            entry.setdefault("last_seen", "")
            entry.setdefault("last_seen_ts", "")

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.path)
        except OSError as e:
            logger.error("状态文件写入失败：%s", e)

    # ---- 订阅管理 ----

    def subscribe(self, profile: str, umo: str) -> bool:
        """添加订阅。返回是否为新订阅（False 表示本会话已订阅过）。"""
        entry = self.data["subscriptions"].setdefault(profile, _new_entry())
        if umo in entry["umos"]:
            return False
        entry["umos"].append(umo)
        self._save()
        return True

    def unsubscribe(self, profile: str, umo: str) -> bool:
        """移除订阅。最后一个会话退订后连同游标一起删除该账号。"""
        entry = self.data["subscriptions"].get(profile)
        if not entry or umo not in entry["umos"]:
            return False
        entry["umos"].remove(umo)
        if not entry["umos"]:
            del self.data["subscriptions"][profile]
        self._save()
        return True

    def get_profiles(self) -> list[str]:
        return list(self.data["subscriptions"].keys())

    def get_umos(self, profile: str) -> list[str]:
        entry = self.data["subscriptions"].get(profile)
        return list(entry["umos"]) if entry else []

    # ---- 游标 ----

    def get_cursor(self, profile: str) -> tuple[str, datetime | None]:
        """返回 (last_seen_shortcode, last_seen_datetime)。未订阅时返回 ("", None)。"""
        entry = self.data["subscriptions"].get(profile)
        if not entry:
            return "", None
        shortcode = entry.get("last_seen") or ""
        ts_raw = entry.get("last_seen_ts") or ""
        ts: datetime | None = None
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                ts = None
        return shortcode, ts

    def update_cursor(self, profile: str, shortcode: str, ts: datetime) -> None:
        entry = self.data["subscriptions"].get(profile)
        if not entry:
            return
        entry["last_seen"] = shortcode
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        entry["last_seen_ts"] = ts.astimezone(timezone.utc).isoformat()
        self._save()
