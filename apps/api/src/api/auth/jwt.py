"""JWT access (15 phút) + refresh (7 ngày) (đề cương 5.7, mục GĐ7.2).

Token chứa `user_id` và `perm_ver` (phiên bản quyền của người dùng). Khi đổi quyền,
`perm_ver` tăng → token cũ không còn hợp lệ về mặt quyền.

Ký bằng HS256 với khoá bí mật lấy từ biến môi trường `AUTH_SECRET` (tên lấy từ `auth.secret_env`).

- `APP_ENV` là `dev`/`test`: thiếu biến → khoá tạm sinh theo tiến trình (`secrets.token_urlsafe`) kèm
  cảnh báo (token không sống sót qua lần khởi động lại); khoá ngắn → cảnh báo.
- Môi trường khác (ví dụ `cluster`): thiếu khoá hoặc khoá < 32 byte → `JwtConfigError`, API **dừng
  khởi động** với thông báo rõ (không chạy production với khoá tạm/khoá yếu).

Logout = đưa `jti` của token vào blacklist trong Redis (TTL bằng thời hạn còn lại của token),
nên token bị chặn ngay cả khi chưa hết hạn.

Dùng `PyJWT` nếu có; nếu không có, module ném `JwtUnavailable` — không tự viết lại thuật toán ký.
"""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

__all__ = ["JwtUnavailable", "JwtConfigError", "TokenPair", "JwtManager", "decode_token", "blacklist_key",
           "resolve_secret", "MIN_SECRET_BYTES", "LAX_ENVS"]

#: Độ dài tối thiểu của khoá HS256 (RFC 7518 §3.2: khoá ≥ kích thước hash = 256 bit).
MIN_SECRET_BYTES = 32
#: Môi trường được phép chạy với khoá tạm / khoá ngắn.
LAX_ENVS = ("dev", "test")

log = logging.getLogger("api.auth.jwt")

try:
    import jwt
    _HAS_JWT = True
except ImportError:  # pragma: no cover - phụ thuộc bắt buộc
    jwt = None  # type: ignore[assignment]
    _HAS_JWT = False


class JwtUnavailable(RuntimeError):
    """Thiếu `PyJWT` — không được phát hành token không ký."""


class JwtConfigError(RuntimeError):
    """Khoá ký JWT thiếu hoặc yếu ở môi trường không phải dev/test → không được khởi động."""


def resolve_secret(secret_env: str = "AUTH_SECRET", app_env: str = "dev",
                   environ: dict[str, str] | None = None) -> str:
    """Đọc và kiểm tra khoá ký JWT từ biến môi trường `secret_env` (xem docstring module)."""
    env = os.environ if environ is None else environ
    value = (env.get(secret_env) or "").strip()
    lax = app_env in LAX_ENVS
    if not value:
        if not lax:
            raise JwtConfigError(
                f"APP_ENV={app_env}: thiếu biến môi trường {secret_env} (khoá ký JWT). Đặt {secret_env} là "
                f"chuỗi ngẫu nhiên ≥ {MIN_SECRET_BYTES} byte, ví dụ: "
                f"python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
        log.warning("thiếu biến môi trường %s: dùng khoá tạm theo tiến trình; "
                    "token sẽ mất hiệu lực khi khởi động lại tiến trình (chỉ chấp nhận ở dev/test)", secret_env)
        return secrets.token_urlsafe(48)
    n = len(value.encode("utf-8"))
    if n < MIN_SECRET_BYTES:
        if not lax:
            raise JwtConfigError(f"APP_ENV={app_env}: {secret_env} chỉ có {n} byte, cần ≥ {MIN_SECRET_BYTES} byte")
        log.warning("%s chỉ có %d byte (< %d): chấp nhận vì APP_ENV=%s", secret_env, n, MIN_SECRET_BYTES, app_env)
    return value


def blacklist_key(jti: str) -> str:
    """Khoá blacklist trong Redis: ``session:{jti}``."""
    return f"session:{jti}"


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 0
    jti: str = ""
    refresh_jti: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JwtManager:
    """Phát hành và kiểm tra token."""

    def __init__(self, secret: str | None = None, *, issuer: str = "gis-dist",
                 access_ttl_min: int = 15, refresh_ttl_days: int = 7,
                 secret_env: str = "AUTH_SECRET", app_env: str = "dev"):
        if not _HAS_JWT:
            raise JwtUnavailable("cài 'PyJWT' để phát hành token (bắt buộc theo đề cương)")
        self.secret = secret or resolve_secret(secret_env, app_env)
        self.issuer = issuer
        self.access_ttl = timedelta(minutes=access_ttl_min)
        self.refresh_ttl = timedelta(days=refresh_ttl_days)

    # ------------------------------------------------------------ phát hành -----
    def _encode(self, payload: dict[str, Any], ttl: timedelta, kind: str) -> tuple[str, str]:
        now = _now()
        jti = uuid.uuid4().hex
        body = {**payload, "jti": jti, "typ": kind, "iss": self.issuer,
                "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
        token = jwt.encode(body, self.secret, algorithm="HS256")
        return token, jti

    def issue(self, *, user_id: str, username: str, perm_ver: int, role: str = "") -> TokenPair:
        """Phát hành cặp access + refresh. Cả hai chứa `user_id` và `perm_ver`."""
        base = {"sub": user_id, "user_id": user_id, "username": username, "perm_ver": int(perm_ver),
                "role": role}
        access, jti = self._encode({**base, "typ": "access"}, self.access_ttl, "access")
        refresh, rjti = self._encode({**base, "typ": "refresh"}, self.refresh_ttl, "refresh")
        return TokenPair(access_token=access, refresh_token=refresh,
                         expires_in=int(self.access_ttl.total_seconds()), jti=jti, refresh_jti=rjti)

    # ------------------------------------------------------------ kiểm tra ------
    def decode(self, token: str, *, expected_type: str = "access") -> dict[str, Any]:
        """Giải mã và kiểm tra token; ném `jwt.InvalidTokenError` khi sai/hết hạn."""
        payload = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=self.issuer,
                             options={"require": ["exp", "iat", "jti", "user_id", "perm_ver"]})
        if payload.get("typ") != expected_type:
            raise jwt.InvalidTokenError(f"token sai loại (cần {expected_type}, nhận {payload.get('typ')})")
        return payload

    def decode_refresh(self, token: str) -> dict[str, Any]:
        return self.decode(token, expected_type="refresh")

    def decode_access(self, token: str) -> dict[str, Any]:
        return self.decode(token, expected_type="access")


def decode_token(token: str, secret: str, issuer: str = "gis-dist") -> dict[str, Any]:
    """Giải mã token với khoá cho sẵn (dùng trong test và script)."""
    if not _HAS_JWT:
        raise JwtUnavailable("cài 'PyJWT' để giải mã token")
    return jwt.decode(token, secret, algorithms=["HS256"], issuer=issuer)
