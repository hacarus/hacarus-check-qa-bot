"""Slack のユーザーグループのメンバーを読み、利用者や管理者の判定に使う"""

from __future__ import annotations

import logging
from typing import Any, Iterable

log = logging.getLogger(__name__)

# メンバーを読み直す間隔
REFRESH_SECONDS = 10 * 60


class GroupMembers:
    def __init__(self) -> None:
        self._members: dict[str, frozenset[str]] = {}

    def users(self, groups: Iterable[str]) -> frozenset[str]:
        result: set[str] = set()
        for g in groups:
            result |= self._members.get(g, frozenset())
        return frozenset(result)

    def set(self, group: str, users: Iterable[str]) -> None:
        self._members[group] = frozenset(users)

    async def refresh(self, client: Any, groups: Iterable[str]) -> None:
        """S で始まる ID はそのまま、@ハンドル名は ID に引き直してから、メンバーを読む。
        読めなかったグループは前回のメンバーのまま残す(一時的な失敗で全員が使えなくなるのを防ぐ)"""
        groups = list(dict.fromkeys(groups))
        if not groups:
            return
        handles: dict[str, str] = {}
        if any(g.startswith("@") for g in groups):
            try:
                resp = await client.usergroups_list(include_disabled=False)
                handles = {"@" + ug["handle"]: ug["id"] for ug in resp.get("usergroups", [])}
            except Exception:
                log.warning("ユーザーグループの一覧を読めませんでした", exc_info=True)
        for g in groups:
            group_id = handles.get(g) if g.startswith("@") else g
            if group_id is None:
                log.warning("ユーザーグループ %s が見つかりません", g)
                continue
            try:
                resp = await client.usergroups_users_list(usergroup=group_id)
                self.set(g, resp.get("users", []))
            except Exception:
                log.warning("ユーザーグループ %s のメンバーを読めませんでした", g, exc_info=True)
