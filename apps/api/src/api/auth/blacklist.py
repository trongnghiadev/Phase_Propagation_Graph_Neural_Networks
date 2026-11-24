"""Blacklist token đăng xuất (đề cương 5.7, mục GĐ7.2).

Logout đưa `jti` của token vào Redis với TTL bằng thời hạn còn lại, nên token bị chặn
ngay (không cần chờ hết hạn) và tự biến mất khỏi Redis khi hết hạn.
"""
from __future__ import annotations

import time
from typing import Any

__all__ = ["TokenBlacklist", "blacklist_key"]

from api.auth.jwt import blacklist_key


class TokenBlacklist:
    """Danh sách đen `jti` trong Redis."""

    def __init__(self, redis_client: Any):
        self.redis = redis_client

    def revoke(self, jti: str, ttl_s: int) -> bool:
        """Chặn token `jti` trong `ttl_s` giây (nếu ttl <= 0 thì không chặn: token đã hết hạn)."""
        if not jti or ttl_s <= 0:
            return False
        self.redis.set(blacklist_key(jti), "1", ex=int(ttl_s))
        return True

    def is_revoked(self, jti: str) -> bool:
        """Token đã bị chặn hay chưa."""
        if not jti:
            return False
        return bool(self.redis.exists(blacklist_key(jti)))

    def ttl(self, jti: str) -> int:
        """Số giây còn lại của lệnh chặn (0 nếu không có)."""
        if not jti:
            return 0
        v = self.redis.ttl(blacklist_key(jti))
        return int(v) if v and v > 0 else 0

    def purge(self) -> int:
        """Xoá toàn bộ danh sách đen (dùng trong test)."""
        n = 0
        cursor = 0
        while True:
            cursor, batch = self.redis.scan(cursor=cursor, match="session:*", count=500)
            if batch:
                n += int(self.redis.delete(*batch) or 0)
            if cursor == 0:
                break
        return n


def now_ts() -> int:
    """Số giây Unix hiện tại (phục vụ tính TTL còn lại của token)."""
    return int(time.time())
