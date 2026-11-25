"""Đăng nhập / làm mới token / đăng xuất (`POST /api/v1/auth/login|refresh|logout`).

- `login`: kiểm tra mật khẩu (argon2), phát hành access (15 phút) + refresh (7 ngày).
- `refresh`: đổi refresh token lấy cặp token mới. Refresh token dùng MỘT lần, tiêu thụ bằng SET NX EX
  (nguyên tử). Dùng lại một refresh token đã tiêu thụ (dấu hiệu token bị đánh cắp) thì thu hồi toàn bộ
  phiên của người dùng (tăng `perm_ver`) và trả 401.
- `logout`: chặn `jti` của access token trong Redis (blacklist) → token không dùng được nữa; chặn luôn
  refresh token (body hoặc cookie) và xoá cookie.

Trình duyệt (gửi header `X-Requested-With`, xem `api.auth.cookies`): refresh token nằm trong cookie
httpOnly `SameSite=Strict`, `Path` = endpoint refresh/logout, không trả trong JSON. Client khác vẫn nhận
và gửi `refresh_token` trong JSON body như trước. Refresh/logout bằng cookie phải qua kiểm tra CSRF.

Mật khẩu sai nhiều lần → khoá tài khoản tạm (`auth.max_failed_logins`, `auth.lockout_minutes`).

Đăng nhập không tiết lộ tài khoản nào tồn tại: luôn chạy `verify_password` (hash giả khi không có
tài khoản); tài khoản không tồn tại, mật khẩu sai, kể cả tài khoản đang bị khoá/vô hiệu mà mật khẩu sai
đều trả CÙNG 401. Trạng thái khoá (423) / vô hiệu (403) chỉ báo khi mật khẩu đúng.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from api.auth.password import dummy_hash, needs_rehash, verify_password
from api.auth.users import UserRepo
from api.auth.cookies import (check_csrf, clear_refresh_cookie, is_browser, refresh_from_cookie,
                              set_refresh_cookie)
from api.deps import UserContext, current_user, optional_user, rate_limit
from api.state import state

log = logging.getLogger("api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    # Bỏ trống khi dùng cookie (trình duyệt); client khác gửi trong body.
    refresh_token: str | None = Field(default=None, min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    # `None` ở chế độ cookie (trình duyệt): refresh token chỉ nằm trong cookie httpOnly.
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    username: str
    role: str = ""
    perm_ver: int = 1


def _repo() -> UserRepo:
    return state.repo


def _pair_response(user: dict, repo: UserRepo, *, cookie: Response | None = None) -> TokenResponse:
    """Phát hành cặp token cho một bản ghi người dùng.

    `cookie` (response của trình duyệt): refresh token đặt vào cookie httpOnly, không trả trong JSON.
    """
    perm_ver = int(user.get("perm_ver", 1))
    pair = state.jwt.issue(user_id=user["_id"], username=user.get("username", ""), perm_ver=perm_ver,
                           role=user.get("role", ""))
    refresh_token: str | None = pair.refresh_token
    if cookie is not None:
        set_refresh_cookie(cookie, pair.refresh_token, int(state.config.auth.refresh_ttl_days * 86400))
        refresh_token = None
    return TokenResponse(access_token=pair.access_token, refresh_token=refresh_token,
                         expires_in=pair.expires_in, user_id=user["_id"],
                         username=user.get("username", ""), role=user.get("role", ""), perm_ver=perm_ver)


@router.post("/login", response_model=TokenResponse,
             dependencies=[Depends(rate_limit(scope="login"))])
def login(body: LoginRequest, request: Request, response: Response):
    """Đăng nhập bằng tên đăng nhập và mật khẩu (trình duyệt: refresh token vào cookie httpOnly)."""
    repo = _repo()
    cfg = state.config.auth
    user = repo.get_user_by_username(body.username)
    stored = (user or {}).get("password_hash") or ""
    # Luôn chạy argon2 (hash giả khi không có tài khoản hoặc không có hash) → thời gian như nhau.
    ok = verify_password(stored or dummy_hash(), body.password) and bool(stored)
    if user is None or not ok:
        if user is not None:
            repo.register_failure(user["_id"], max_failures=cfg.max_failed_logins,
                                  lock_minutes=cfg.lockout_minutes)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="tên đăng nhập hoặc mật khẩu không đúng")
    # Từ đây mật khẩu ĐÚNG: mới được báo trạng thái tài khoản.
    if not user.get("active", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="tài khoản đã bị vô hiệu")
    if repo.is_locked(user):
        raise HTTPException(status.HTTP_423_LOCKED, detail="tài khoản đang bị khoá tạm do đăng nhập sai nhiều lần")
    repo.reset_failures(user["_id"])
    if needs_rehash(user.get("password_hash", "")):
        log.info("băm mật khẩu của %s không còn khớp tham số hiện tại", user["_id"])
    return _pair_response(user, repo, cookie=response if is_browser(request) else None)


@router.post("/refresh", response_model=TokenResponse,
             dependencies=[Depends(rate_limit(scope="refresh"))])
def refresh(request: Request, response: Response, body: RefreshRequest | None = None):
    """Làm mới token bằng refresh token (7 ngày). Mỗi refresh token chỉ dùng được một lần.

    Nguồn token: `refresh_token` trong body (client không phải trình duyệt) hoặc cookie httpOnly (trình
    duyệt; phải qua kiểm tra CSRF → 403 nếu thiếu header tuỳ biến / Origin lạ).

    - 401: token sai/hết hạn, đã đăng xuất, quyền đã đổi, hoặc token bị DÙNG LẠI (khi đó mọi phiên của
      người dùng bị thu hồi);
    - 503: Redis không sẵn sàng (không tiêu thụ được token một cách nguyên tử).
    """
    repo = _repo()
    token = body.refresh_token if body is not None and body.refresh_token else None
    from_cookie = False
    if token is None:
        token = refresh_from_cookie(request)
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="thiếu refresh token")
        check_csrf(request)
        from_cookie = True
    browser = from_cookie or is_browser(request)
    try:
        payload = state.jwt.decode_refresh(token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"refresh token không hợp lệ: {e}") from None
    jti = str(payload.get("jti", ""))
    if state.blacklist.is_revoked(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token đã đăng xuất")
    user_id = str(payload.get("user_id") or payload.get("sub") or "")
    user = repo.get_user(user_id)
    if user is None or not user.get("active", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="tài khoản không tồn tại hoặc đã bị vô hiệu")
    if int(user.get("perm_ver", 1)) != int(payload.get("perm_ver", 1)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="quyền đã thay đổi, hãy đăng nhập lại")
    # Tiêu thụ nguyên tử (SET NX EX): chỉ một request với cùng jti được cấp token mới.
    ttl = max(1, int(payload.get("exp", 0)) - int(time.time())) + 60
    if not state.blacklist.consume_refresh(jti, ttl):
        new_ver = repo.revoke_all_sessions(user_id)
        log.warning("refresh token của %s bị dùng lại (jti=%s): thu hồi toàn bộ phiên (perm_ver=%s)",
                    user_id, jti, new_ver)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="refresh token đã được dùng; mọi phiên của tài khoản đã bị thu hồi, "
                                   "hãy đăng nhập lại")
    return _pair_response(user, repo, cookie=response if browser else None)


@router.post("/logout", dependencies=[Depends(rate_limit(scope="logout"))])
def logout(request: Request, response: Response, body: LogoutRequest | None = None,
           user: UserContext | None = Depends(optional_user)):
    """Đăng xuất: chặn access token hiện tại và refresh token (body hoặc cookie) trong blacklist.

    Không bắt buộc access token còn hạn (trình duyệt có thể chỉ còn cookie refresh); khi đó chỉ thu hồi
    được refresh token. Không có token nào → 401. Cookie luôn bị xoá. Dùng cookie → kiểm tra CSRF (403).
    """
    cfg = state.config.auth
    refresh_tok = body.refresh_token if body is not None and body.refresh_token else None
    if refresh_tok is None:
        refresh_tok = refresh_from_cookie(request)
        if refresh_tok is not None:
            check_csrf(request)
    if user is None and refresh_tok is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="thiếu token đăng nhập")
    revoked = 0
    if user is not None and user.jti:
        if state.blacklist.revoke(user.jti, int(cfg.access_ttl_min * 60)):
            revoked += 1
    user_id = user.user_id if user is not None else None
    if refresh_tok:
        try:
            payload = state.jwt.decode_refresh(refresh_tok)
            owner = str(payload.get("user_id") or payload.get("sub") or "")
            jti = str(payload.get("jti", ""))
            if user_id is not None and owner != user_id:
                jti = ""     # refresh token của tài khoản khác: không thu hồi hộ
            if jti and state.blacklist.revoke(jti, int(cfg.refresh_ttl_days * 86400)):
                revoked += 1
                user_id = user_id or owner
        except Exception:  # noqa: BLE001 - refresh token sai thì bỏ qua, vẫn đăng xuất được
            pass
    clear_refresh_cookie(response)
    return {"ok": True, "revoked": revoked, "user_id": user_id,
            "at": datetime.now(timezone.utc).isoformat()}


@router.get("/me")
def me(user: UserContext = Depends(current_user)):
    """Thông tin người dùng hiện tại và quyền theo collection (frontend dùng để ẩn/hiện nút)."""
    doc = state.repo.get_user(user.user_id) or {}
    public = state.repo.public_user(doc)
    public["collection_perm"] = {k: int(v) for k, v in (doc.get("perm") or {}).items()}
    return public


@router.get("/roles")
def roles():
    """Bảng vai trò mặc định (8 vai trò, bitmask 12 bit)."""
    from api.permissions import ROLES
    return [{"name": name, "mask": info["mask"], "perm": info["perm"], "description": info["description"]}
            for name, info in ROLES.items()]
