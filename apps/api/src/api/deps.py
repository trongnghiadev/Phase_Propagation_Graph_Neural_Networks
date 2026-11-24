"""Dependency của FastAPI: người dùng hiện tại, kiểm tra quyền, giới hạn tần suất.

- `current_user`: xác thực Bearer access token → `UserContext` (id, tên, `perm_ver`, quyền).
  Token bị chặn trong blacklist (đăng xuất) → 401.
- `require(flag, collection)`: kiểm tra bitmask quyền theo collection → 403 nếu thiếu.
- `optional_user`: không token → trả `None` (cho endpoint công khai).
- `rate_limit(...)`: giới hạn tần suất theo IP + người dùng → 429.

Mỗi endpoint trong `router/` dùng `dependencies=[Depends(require(P.READ, "<collection>"))]` hoặc
`user: UserContext = Depends(require(P.UPDATE, "tide_extremes"))` để lấy cả người dùng.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request, status

from api.permissions import P, has, mask_of
from api.state import state

log = logging.getLogger("api.deps")

__all__ = ["UserContext", "current_user", "optional_user", "require", "require_any", "rate_limit",
           "client_ip"]


@dataclass
class UserContext:
    """Người dùng đã xác thực."""
    user_id: str
    username: str = ""
    role: str = ""
    perm_ver: int = 1
    jti: str = ""
    perm: dict[str, int] = field(default_factory=dict)

    def mask_for(self, collection: str) -> int:
        return mask_of(self.perm, collection)

    def can(self, collection: str, flag: int) -> bool:
        return has(self.mask_for(collection), flag)


def client_ip(request: Request) -> str:
    """IP người gọi (ưu tiên `X-Forwarded-For` do Nginx đặt)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _bearer_token(request: Request) -> str | None:
    """Lấy access token từ header `Authorization: Bearer ...`."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        return tok or None
    return None


def _load_user(payload: dict[str, Any]) -> UserContext:
    """Đọc quyền của người dùng và kiểm tra `perm_ver` còn hợp lệ."""
    user_id = str(payload.get("user_id") or payload.get("sub") or "")
    perm_ver = int(payload.get("perm_ver", 1))
    repo = state.repo
    doc = repo.get_user(user_id)
    if doc is None or not doc.get("active", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="tài khoản không tồn tại hoặc đã bị vô hiệu")
    current_ver = int(doc.get("perm_ver", 1))
    if current_ver != perm_ver:
        # Quyền đã đổi sau khi token được phát → token cũ không dùng được nữa (phải đăng nhập lại).
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="quyền đã thay đổi, hãy đăng nhập lại")
    perm = {k: int(v) for k, v in (doc.get("perm") or {}).items()}
    return UserContext(user_id=user_id, username=doc.get("username", ""), role=doc.get("role", ""),
                       perm_ver=current_ver, jti=str(payload.get("jti", "")), perm=perm)


async def optional_user(request: Request) -> UserContext | None:
    """Người dùng nếu có token hợp lệ, ngược lại ``None`` (không ném 401)."""
    token = _bearer_token(request)
    if not token:
        return None
    try:
        payload = state.jwt.decode_access(token)
    except Exception:  # noqa: BLE001 - token hỏng/hết hạn → coi như khách
        return None
    if state.blacklist.is_revoked(str(payload.get("jti", ""))):
        return None
    try:
        return _load_user(payload)
    except HTTPException:
        return None


async def current_user(request: Request) -> UserContext:
    """Người dùng đã xác thực; 401 nếu thiếu token, token sai, hết hạn hoặc đã đăng xuất."""
    token = _bearer_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="thiếu token đăng nhập")
    try:
        payload = state.jwt.decode_access(token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"token không hợp lệ: {e}") from None
    jti = str(payload.get("jti", ""))
    if state.blacklist.is_revoked(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="token đã đăng xuất")
    return _load_user(payload)


def require(flag: int, collection: str) -> Callable[..., Any]:
    """Dependency kiểm tra quyền: `flag` trên `collection`. Thiếu quyền → 403.

    Dùng được cả hai kiểu:
        dependencies=[Depends(require(P.READ, "hydromet_bulletins"))]
        user: UserContext = Depends(require(P.UPDATE, "tide_extremes"))
    """
    async def dependency(request: Request, user: UserContext = Depends(current_user)) -> UserContext:
        mask = user.mask_for(collection)
        if not has(mask, flag):
            names = ", ".join(f.name for f in P if flag & int(f))
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"error": "thiếu quyền", "required": names, "collection": collection,
                        "your_mask": mask})
        return user
    return dependency


def require_any(*flags: int, collection: str) -> Callable[..., Any]:
    """Dependency: cần có ÍT NHẤT một quyền trong `flags` (ví dụ READ hoặc EXPORT)."""
    required_any = 0
    for f in flags:
        required_any |= int(f)

    async def dependency(request: Request, user: UserContext = Depends(current_user)) -> UserContext:
        mask = user.mask_for(collection)
        if (mask & required_any) == 0:
            names = ", ".join(f.name for f in P if required_any & int(f))
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail={"error": "thiếu quyền", "required_any": names,
                                        "collection": collection, "your_mask": mask})
        return user
    return dependency


def rate_limit(requests: int | None = None, window_s: int | None = None, *, scope: str = "api"):
    """Dependency giới hạn tần suất → 429 khi vượt.

    Đếm theo cặp (scope, IP, user_id) trong Redis; cửa sổ trượt đơn giản theo TTL.

    Với `scope="login"` dùng ngưỡng riêng `rate_limit.login_requests` /
    `rate_limit.login_window_s` (nới riêng cho endpoint đăng nhập công khai).
    """
    async def dependency(request: Request) -> None:
        cfg = state.config.rate_limit
        if not cfg.enabled:
            return
        if scope == "login" and requests is None:
            limit = cfg.login_requests
            window = cfg.login_window_s if window_s is None else window_s
        else:
            limit = requests if requests is not None else cfg.requests
            window = window_s if window_s is not None else cfg.window_s
        uid = getattr(request.state, "user_id", None)
        ident = f"{scope}:{client_ip(request)}:{uid or '-'}"
        key = f"rl:{ident}"
        try:
            pipe = state.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window)
            count, _ = pipe.execute()
        except Exception as e:  # noqa: BLE001 - Redis lỗi thì không chặn request
            log.warning("giới hạn tần suất: không kiểm tra được (%s)", e)
            return
        if int(count) > limit:
            retry = int(state.redis.ttl(key) or window)
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail={"error": "vượt giới hạn tần suất", "limit": limit,
                                        "window_s": window, "retry_after_s": retry},
                                headers={"Retry-After": str(retry)})
    return dependency
