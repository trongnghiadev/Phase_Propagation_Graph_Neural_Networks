"""Đăng nhập / làm mới token / đăng xuất (`POST /api/v1/auth/login|refresh|logout`).

- `login`: kiểm tra mật khẩu (argon2), phát hành access (15 phút) + refresh (7 ngày).
- `refresh`: đổi refresh token lấy cặp token mới; refresh cũ bị chặn (blacklist) sau khi dùng.
- `logout`: chặn `jti` của access token trong Redis (blacklist) → token không dùng được nữa.

Mật khẩu sai nhiều lần → khoá tài khoản tạm (`auth.max_failed_logins`, `auth.lockout_minutes`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth.password import needs_rehash, verify_password
from api.auth.users import UserRepo
from api.deps import UserContext, current_user, rate_limit
from api.state import state

log = logging.getLogger("api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    username: str
    role: str = ""
    perm_ver: int = 1


def _repo() -> UserRepo:
    return state.repo


def _pair_response(user: dict, repo: UserRepo) -> TokenResponse:
    """Phát hành cặp token cho một bản ghi người dùng."""
    perm_ver = int(user.get("perm_ver", 1))
    pair = state.jwt.issue(user_id=user["_id"], username=user.get("username", ""), perm_ver=perm_ver,
                           role=user.get("role", ""))
    return TokenResponse(access_token=pair.access_token, refresh_token=pair.refresh_token,
                         expires_in=pair.expires_in, user_id=user["_id"],
                         username=user.get("username", ""), role=user.get("role", ""), perm_ver=perm_ver)


@router.post("/login", response_model=TokenResponse,
             dependencies=[Depends(rate_limit(scope="login"))])
def login(body: LoginRequest):
    """Đăng nhập bằng tên đăng nhập và mật khẩu."""
    repo = _repo()
    cfg = state.config.auth
    user = repo.get_user_by_username(body.username)
    if user is None:
        # Không tiết lộ có/không tồn tại tài khoản; cũng không ghi nhận số lần sai.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="tên đăng nhập hoặc mật khẩu không đúng")
    if not user.get("active", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="tài khoản đã bị vô hiệu")
    if repo.is_locked(user):
        raise HTTPException(status.HTTP_423_LOCKED, detail="tài khoản đang bị khoá tạm do đăng nhập sai nhiều lần")
    if not verify_password(user.get("password_hash", ""), body.password):
        repo.register_failure(user["_id"], max_failures=cfg.max_failed_logins,
                              lock_minutes=cfg.lockout_minutes)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="tên đăng nhập hoặc mật khẩu không đúng")
    repo.reset_failures(user["_id"])
    if needs_rehash(user.get("password_hash", "")):
        log.info("băm mật khẩu của %s không còn khớp tham số hiện tại", user["_id"])
    return _pair_response(user, repo)


@router.post("/refresh", response_model=TokenResponse,
             dependencies=[Depends(rate_limit(scope="refresh"))])
def refresh(body: RefreshRequest):
    """Làm mới token bằng refresh token (7 ngày). Refresh cũ bị chặn sau khi dùng."""
    repo = _repo()
    try:
        payload = state.jwt.decode_refresh(body.refresh_token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"refresh token không hợp lệ: {e}") from None
    jti = str(payload.get("jti", ""))
    if state.blacklist.is_revoked(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token đã dùng hoặc đã đăng xuất")
    user_id = str(payload.get("user_id") or payload.get("sub") or "")
    user = repo.get_user(user_id)
    if user is None or not user.get("active", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="tài khoản không tồn tại hoặc đã bị vô hiệu")
    if int(user.get("perm_ver", 1)) != int(payload.get("perm_ver", 1)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="quyền đã thay đổi, hãy đăng nhập lại")
    # chặn refresh cũ (dùng một lần)
    ttl = int(state.config.auth.refresh_ttl_days * 86400)
    state.blacklist.revoke(jti, ttl)
    return _pair_response(user, repo)


@router.post("/logout", dependencies=[Depends(rate_limit(scope="logout"))])
def logout(body: LogoutRequest | None = None, user: UserContext = Depends(current_user)):
    """Đăng xuất: chặn access token hiện tại (và refresh token nếu gửi kèm) trong blacklist."""
    cfg = state.config.auth
    revoked = 0
    if user.jti:
        if state.blacklist.revoke(user.jti, int(cfg.access_ttl_min * 60)):
            revoked += 1
    if body is not None and body.refresh_token:
        try:
            payload = state.jwt.decode_refresh(body.refresh_token)
            jti = str(payload.get("jti", ""))
            if jti and state.blacklist.revoke(jti, int(cfg.refresh_ttl_days * 86400)):
                revoked += 1
        except Exception:  # noqa: BLE001 - refresh token sai thì bỏ qua, vẫn đăng xuất được
            pass
    return {"ok": True, "revoked": revoked, "user_id": user.user_id,
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
