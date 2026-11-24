"""Dependency của FastAPI: người dùng hiện tại, kiểm tra quyền, giới hạn tần suất.

- `current_user`: xác thực Bearer access token → `UserContext` (id, tên, `perm_ver`, quyền).
  Token bị chặn trong blacklist (đăng xuất) → 401.
- `require(flag, collection)`: kiểm tra bitmask quyền theo collection → 403 nếu thiếu.
- `optional_user`: không token → trả `None` (cho endpoint công khai).
- `rate_limit(...)`: giới hạn tần suất theo IP + người dùng → 429. IP lấy qua `client_ip`: chỉ tin
  `X-Forwarded-For` khi kết nối đến từ proxy trong `rate_limit.trusted_proxies` (mặc định: không tin).
  Redis lỗi: scope `login` **fail-closed** (503), scope khác cho qua kèm cảnh báo.

Mỗi endpoint trong `router/` dùng `dependencies=[Depends(require(P.READ, "<collection>"))]` hoặc
`user: UserContext = Depends(require(P.UPDATE, "tide_extremes"))` để lấy cả người dùng.
"""
from __future__ import annotations

import ipaddress
import logging
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


def _parse_ip(text: str):
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def _trusted_networks() -> list:
    try:
        items = state.config.rate_limit.trusted_proxies
    except Exception:  # noqa: BLE001 - cấu hình lỗi → không tin proxy nào
        return []
    return [ipaddress.ip_network(i, strict=False) for i in items]


def _is_trusted(ip, nets: list) -> bool:
    return ip is not None and any(ip in n for n in nets)


def client_ip(request: Request) -> str:
    """IP người gọi.

    `X-Forwarded-For` do client tự đặt được, nên chỉ dùng khi kết nối TCP đến từ một proxy tin cậy
    (`rate_limit.trusted_proxies`, mặc định rỗng). Khi đó duyệt XFF từ PHẢI sang TRÁI (phần tử cuối do
    proxy gần nhất thêm), bỏ qua các proxy tin cậy, lấy IP đầu tiên không tin cậy. Ngược lại dùng IP peer.
    """
    peer = request.client.host if request.client else "unknown"
    nets = _trusted_networks()
    if not nets or not _is_trusted(_parse_ip(peer), nets):
        return peer
    xff = request.headers.get("x-forwarded-for") or ""
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        ip = _parse_ip(hop)
        if ip is None:
            return peer              # XFF hỏng → không tin, dùng IP peer (proxy)
        if not _is_trusted(ip, nets):
            return str(ip)
    return str(_parse_ip(hops[0])) if hops else peer


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
    try:
        if state.blacklist.is_revoked(str(payload.get("jti", ""))):
            return None
    except HTTPException:  # Redis lỗi → không xác nhận được phiên → coi như khách (ít quyền hơn)
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

    Đếm theo cặp (scope, IP, user_id) trong Redis; cửa sổ cố định theo TTL: `INCR`, và chỉ đặt
    `EXPIRE` khi `INCR` trả 1 (request đầu của cửa sổ) — đặt lại TTL ở mọi request sẽ kéo dài cửa sổ vô
    hạn khi bị gọi liên tục. Key mất TTL (EXPIRE lỗi giữa chừng) được đặt lại TTL khi phát hiện.

    Với `scope="login"` dùng ngưỡng riêng `rate_limit.login_requests` /
    `rate_limit.login_window_s` (nới riêng cho endpoint đăng nhập công khai).

    Redis lỗi: `scope="login"` → 503 (fail-closed: không cho dò mật khẩu không giới hạn khi Redis
    chết); scope khác → cho qua kèm log cảnh báo.
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
        r = state.redis
        try:
            count = int(r.incr(key))
            if count == 1:
                r.expire(key, window)
            retry = window
            if count > limit:
                ttl = int(r.ttl(key) or -1)
                if ttl < 0:              # key không có TTL → đặt lại để không bị chặn vĩnh viễn
                    r.expire(key, window)
                    ttl = window
                retry = ttl
        except Exception as e:  # noqa: BLE001
            if scope == "login":
                log.warning("giới hạn tần suất (login): Redis lỗi → từ chối (fail-closed): %s", e)
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="tạm thời không đăng nhập được (không kiểm tra được giới hạn tần suất)",
                                    headers={"Retry-After": "5"}) from None
            log.warning("giới hạn tần suất: không kiểm tra được (%s)", e)
            return
        if count > limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail={"error": "vượt giới hạn tần suất", "limit": limit,
                                        "window_s": window, "retry_after_s": retry},
                                headers={"Retry-After": str(retry)})
    return dependency
