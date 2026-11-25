"""Refresh token trong cookie httpOnly cho trình duyệt (đề cương 5.7) và chống CSRF.

Hai kiểu client:

- **Trình duyệt** (web `apps/web`): gửi header tuỳ biến `auth.csrf_header` (mặc định
  `X-Requested-With: XMLHttpRequest`). Login/refresh đặt refresh token vào cookie
  `httpOnly; SameSite=Strict; Secure (cấu hình); Path=<endpoint refresh/logout>` và KHÔNG trả
  `refresh_token` trong JSON (JavaScript không đọc được → XSS không lấy được refresh token).
  Access token chỉ giữ trong bộ nhớ của trang.
- **Client không phải trình duyệt** (script, test): không gửi header đó → nhận `refresh_token` trong
  JSON như trước và gửi lại trong body; không có cookie.

Chống CSRF khi refresh/logout dùng cookie:

1. `SameSite=Strict`: trình duyệt không gửi cookie trong request khởi phát từ site khác;
2. header tuỳ biến bắt buộc: request chéo origin có header này phải qua preflight CORS, mà CORS chỉ
   cho các origin trong `auth.cors_origins`;
3. nếu có `Origin` thì phải là origin được phép (`auth.cors_origins`) hoặc trùng host của chính
   request (cùng origin qua Nginx / proxy dev); `Sec-Fetch-Site: cross-site` bị từ chối.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status

__all__ = ["is_browser", "refresh_from_cookie", "set_refresh_cookie", "clear_refresh_cookie",
           "check_csrf"]


def _auth_cfg():
    from api.state import state
    return state.config.auth


def is_browser(request: Request) -> bool:
    """Request đến từ web (có header tuỳ biến chống CSRF) → dùng chế độ cookie."""
    return bool(request.headers.get(_auth_cfg().csrf_header))


def refresh_from_cookie(request: Request) -> str | None:
    """Refresh token trong cookie (nếu có)."""
    tok = request.cookies.get(_auth_cfg().cookie_name)
    return tok.strip() or None if tok else None


def set_refresh_cookie(response: Response, token: str, max_age_s: int) -> None:
    """Đặt cookie refresh token cho từng đường dẫn trong `auth.cookie_paths`."""
    cfg = _auth_cfg()
    for path in cfg.cookie_paths:
        response.set_cookie(cfg.cookie_name, token, max_age=int(max_age_s), path=path, httponly=True,
                            secure=bool(cfg.cookie_secure), samesite=cfg.cookie_samesite)


def clear_refresh_cookie(response: Response) -> None:
    """Xoá cookie refresh token (cùng thuộc tính khi đặt để trình duyệt chấp nhận)."""
    cfg = _auth_cfg()
    for path in cfg.cookie_paths:
        response.delete_cookie(cfg.cookie_name, path=path, httponly=True, secure=bool(cfg.cookie_secure),
                               samesite=cfg.cookie_samesite)


def _origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else ""


def _from_trusted_proxy(request: Request) -> bool:
    """Kết nối trực tiếp đến từ proxy trong `rate_limit.trusted_proxies` (mặc định rỗng → không tin)."""
    from api.deps import _is_trusted, _parse_ip, _trusted_networks

    nets = _trusted_networks()
    peer = request.client.host if request.client else ""
    return bool(nets) and _is_trusted(_parse_ip(peer or ""), nets)


def _same_host(request: Request, origin: str) -> bool:
    """`Origin` trùng host mà request thực sự tới.

    Luôn so với `Host`; `X-Forwarded-Host` chỉ được tin khi request đến từ proxy trong
    `rate_limit.trusted_proxies` (giống `client_ip` với `X-Forwarded-For`) — client trực tiếp tự đặt header này
    thì bị bỏ qua.
    """
    netloc = urlsplit(origin).netloc.lower()
    hosts = {(request.headers.get("host") or "").lower()}
    fwd = request.headers.get("x-forwarded-host")
    if fwd and _from_trusted_proxy(request):
        hosts.update(h.strip().lower() for h in fwd.split(","))
    hosts.discard("")
    return netloc in hosts


def check_csrf(request: Request) -> None:
    """Kiểm tra request dùng cookie refresh: header tuỳ biến + Origin hợp lệ. Sai → 403."""
    cfg = _auth_cfg()
    if not request.headers.get(cfg.csrf_header):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail=f"thiếu header {cfg.csrf_header} (chống CSRF khi dùng cookie)")
    if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="request chéo site bị từ chối (CSRF)")
    origin = request.headers.get("origin")
    if origin is None:
        return
    o = _origin_of(origin)
    allowed = {x.lower() for x in cfg.cors_origins}
    if not o or (o not in allowed and not _same_host(request, o)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="origin không được phép (CSRF)")
