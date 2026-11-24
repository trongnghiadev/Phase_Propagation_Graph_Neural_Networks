"""Blacklist token đăng xuất (đề cương 5.7, mục GĐ7.2).

Logout đưa `jti` của token vào Redis với TTL bằng thời hạn còn lại, nên token bị chặn
ngay (không cần chờ hết hạn) và tự biến mất khỏi Redis khi hết hạn.

Refresh token dùng một lần: `consume_refresh` đặt `refresh_used:{jti}` bằng **SET NX EX** (nguyên tử) —
chỉ đúng một request thắng; request khác cùng `jti` nhận `False` (= token bị dùng lại).

Redis lỗi khi kiểm tra/tiêu thụ → `BlacklistUnavailable` (HTTP 503 có kiểm soát, không 500, và không
"cho qua" token có thể đã đăng xuất).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, status

__all__ = ["TokenBlacklist", "blacklist_key", "refresh_used_key", "BlacklistUnavailable"]

from api.auth.jwt import blacklist_key

log = logging.getLogger("api.auth.blacklist")


def refresh_used_key(jti: str) -> str:
    """Khoá đánh dấu refresh token đã dùng: ``refresh_used:{jti}``."""
    return f"refresh_used:{jti}"


class BlacklistUnavailable(HTTPException):
    """Không truy cập được Redis của blacklist → 503."""

    def __init__(self, exc: BaseException):
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE,
                         detail="không kiểm tra được trạng thái phiên đăng nhập (Redis không sẵn sàng), thử lại sau",
                         headers={"Retry-After": "5"})
        log.warning("blacklist: Redis lỗi: %s: %s", type(exc).__name__, exc)


class TokenBlacklist:
    """Danh sách đen `jti` trong Redis."""

    def __init__(self, redis_client: Any):
        self.redis = redis_client

    def revoke(self, jti: str, ttl_s: int) -> bool:
        """Chặn token `jti` trong `ttl_s` giây (nếu ttl <= 0 thì không chặn: token đã hết hạn)."""
        if not jti or ttl_s <= 0:
            return False
        try:
            self.redis.set(blacklist_key(jti), "1", ex=int(ttl_s))
        except Exception as e:  # noqa: BLE001
            raise BlacklistUnavailable(e) from None
        return True

    def is_revoked(self, jti: str) -> bool:
        """Token đã bị chặn hay chưa."""
        if not jti:
            return False
        try:
            return bool(self.redis.exists(blacklist_key(jti)))
        except Exception as e:  # noqa: BLE001 - Redis lỗi → 503 có kiểm soát
            raise BlacklistUnavailable(e) from None

    def consume_refresh(self, jti: str, ttl_s: int) -> bool:
        """Tiêu thụ refresh token `jti` (một lần). True = lần dùng đầu; False = đã dùng trước đó.

        `SET refresh_used:{jti} 1 NX EX ttl` là một lệnh nguyên tử: hai request đồng thời không thể
        cùng nhận True.
        """
        if not jti:
            return False
        try:
            return bool(self.redis.set(refresh_used_key(jti), "1", nx=True, ex=max(1, int(ttl_s))))
        except Exception as e:  # noqa: BLE001
            raise BlacklistUnavailable(e) from None

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
