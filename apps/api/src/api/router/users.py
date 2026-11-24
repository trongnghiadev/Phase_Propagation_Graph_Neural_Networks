"""Quản lý người dùng và quyền (`/api/v1/users/...`).

- `GET /users` — MANAGE_USERS
- `GET /users/{id}` — MANAGE_USERS
- `POST /users` — MANAGE_USERS (tạo tài khoản, mật khẩu băm argon2)
- `PATCH /users/{id}/active` — MANAGE_USERS (khoá/mở khoá tài khoản)
- `PUT /users/{id}/perm` — MANAGE_USERS (cập nhật quyền **nguyên tử** bằng `$bit`)

Cập nhật quyền được bảo vệ bởi khoá `lock:user:{user_id}` (423 nếu đang bị khoá) và
điều kiện `version` (409 nếu xung đột). Sau khi đổi quyền: xoá cache `perm:{user_id}`
và tăng `perm_ver` (để token cũ mất hiệu lực).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from api.auth.password import hash_password
from api.auth.users import UserRepo
from api.deps import UserContext, require, rate_limit
from api.permissions import P, flag_names, mask_from_names, validate_mask
from api.router.common import clean, to_vn_datetime
from api.state import state

log = logging.getLogger("api.users")

router = APIRouter(tags=["users"])

USERS = "users"


class CreateUserRequest(BaseModel):
    """Tạo tài khoản mới."""
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(default="", max_length=128)
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, min_length=10, max_length=256,
                                 description="Bỏ trống để hệ thống sinh mật khẩu ngẫu nhiên")
    role: str | None = Field(default=None, description="Tên vai trò mặc định (xem /auth/roles)")
    roles: list[str] | None = None
    station: str | None = None


class PermRequest(BaseModel):
    """Cập nhật quyền của người dùng."""
    grant: list[str] = Field(default_factory=list, description="Danh sách quyền cần BẬT")
    revoke: list[str] = Field(default_factory=list, description="Danh sách quyền cần TẮT")
    collections: dict[str, list[str]] | None = Field(
        default=None, description="Quyền riêng theo collection: {coll: [flags]}")
    version: int | None = Field(default=None, description="Phiên bản mong đợi (409 nếu lệch)")
    reason: str = Field(default="", max_length=512)


class ActiveRequest(BaseModel):
    """Khoá / mở khoá tài khoản."""
    active: bool
    reason: str = Field(default="", max_length=512)


@router.get("/users", dependencies=[Depends(require(P.MANAGE_USERS, USERS))])
async def list_users(q: str | None = None, limit: int = Query(100, ge=1, le=1000)):
    """Danh sách tài khoản (không kèm mật khẩu hash)."""
    repo = UserRepo(state.db)
    flt: dict[str, Any] = {}
    if q:
        flt["$or"] = [{"username": {"$regex": q, "$options": "i"}},
                      {"display_name": {"$regex": q, "$options": "i"}}]
    docs = list(state.db[USERS].find(flt).sort("username", 1).limit(limit))
    items = []
    for d in docs:
        out = clean(d)
        out.pop("password_hash", None)
        # `perm` là dict {collection: mask} → trả về tên quyền cho từng collection.
        perm = out.get("perm") or {}
        out["perm"] = {coll: flag_names(int(mask)) for coll, mask in perm.items()}
        out["perm_default"] = flag_names(int(perm.get("*", 0)))
        out["roles"] = out.get("roles") or []
        out["created_at"] = to_vn_datetime(out.get("created_at"))
        out["last_login"] = to_vn_datetime(out.get("last_login"))
        items.append(out)
    return {"count": len(items), "items": items}


@router.get("/users/{user_id}", dependencies=[Depends(require(P.MANAGE_USERS, USERS))])
async def get_user(user_id: str):
    """Chi tiết tài khoản (quyền hiện tại, `perm_ver`, trạng thái)."""
    repo = UserRepo(state.db)
    doc = repo.get_user(user_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có người dùng {user_id!r}")
    out = clean(doc)
    out.pop("password_hash", None)
    perm = out.get("perm") or {}
    out["perm"] = {coll: flag_names(int(mask)) for coll, mask in perm.items()}
    out["perm_default"] = flag_names(int(perm.get("*", 0)))
    out["roles"] = out.get("roles") or []
    out["created_at"] = to_vn_datetime(out.get("created_at"))
    out["last_login"] = to_vn_datetime(out.get("last_login"))
    return out


@router.post("/users", dependencies=[Depends(require(P.MANAGE_USERS, USERS)),
                                     Depends(rate_limit(scope="write"))])
async def create_user(body: CreateUserRequest,
                      user: UserContext = Depends(require(P.MANAGE_USERS, USERS))):
    """Tạo tài khoản mới.

    Nếu không truyền `password`, hệ thống sinh mật khẩu ngẫu nhiên và chỉ trả về **một lần**
    (không lưu bản rõ trong CSDL; chỉ lưu hash argon2). Mật khẩu sinh ra KHÔNG được ghi vào
    báo cáo hay log.
    """
    repo = UserRepo(state.db)
    if repo.get_user_by_username(body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"tài khoản {body.username!r} đã tồn tại")
    roles = body.roles or ([body.role] if body.role else [])
    generated = None
    pwd = body.password
    if not pwd:
        from api.auth.password import generate_password
        generated = generate_password(20)
        pwd = generated
    try:
        doc = repo.create_user(username=body.username, password_hash=hash_password(pwd),
                               roles=roles, display_name=body.display_name, email=body.email,
                               station=body.station)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from None
    out = clean(doc)
    out.pop("password_hash", None)
    perm = out.get("perm") or {}
    out["perm"] = {coll: flag_names(int(mask)) for coll, mask in perm.items()}
    out["perm_default"] = flag_names(int(perm.get("*", 0)))
    if generated:
        # Trả về một lần cho quản trị viên; KHÔNG ghi log/ghi ra tệp.
        out["generated_password"] = generated
        out["warning"] = "mật khẩu này chỉ hiển thị một lần; hãy đổi ngay sau lần đăng nhập đầu"
    return out


@router.patch("/users/{user_id}/active", dependencies=[Depends(require(P.MANAGE_USERS, USERS)),
                                                        Depends(rate_limit(scope="write"))])
async def set_active(user_id: str, body: ActiveRequest,
                     user: UserContext = Depends(require(P.MANAGE_USERS, USERS))):
    """Khoá / mở khoá tài khoản (khoá = không đăng nhập được nữa)."""
    repo = UserRepo(state.db)
    if repo.get_user(user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có người dùng {user_id!r}")
    doc = repo.set_active(user_id, body.active)
    try:
        state.cache.delete(f"perm:{user_id}")
    except Exception as e:  # noqa: BLE001
        log.warning("không xoá được cache quyền: %s", e)
    out = clean(doc)
    out.pop("password_hash", None)
    perm = out.get("perm") or {}
    out["perm"] = {coll: flag_names(int(mask)) for coll, mask in perm.items()}
    out["perm_default"] = flag_names(int(perm.get("*", 0)))
    return out


@router.put("/users/{user_id}/perm", dependencies=[Depends(require(P.MANAGE_USERS, USERS)),
                                                    Depends(rate_limit(scope="write"))])
async def update_perm(user_id: str, body: PermRequest,
                      user: UserContext = Depends(require(P.MANAGE_USERS, USERS))):
    """Cập nhật quyền của người dùng (nguyên tử bằng `$bit`).

    - 404 nếu không có người dùng;
    - 423 nếu đang có tiến trình khác sửa quyền của tài khoản này;
    - 409 nếu `version` mong đợi không khớp.
    """
    from vngis.locks import LockBusy, LockGuard

    repo = UserRepo(state.db)
    doc = repo.get_user(user_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có người dùng {user_id!r}")
    grant = _mask_from_names(body.grant)
    revoke = _mask_from_names(body.revoke)
    if grant & revoke:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="một quyền không thể vừa bật vừa tắt")
    expected = body.version if body.version is not None else int(doc.get("version", 1))
    guard = LockGuard(state.lock, db=db_holder(state), resource=f"user:{user_id}", ttl_ms=300_000)
    try:
        guard.acquire()
    except LockBusy as e:
        raise HTTPException(status.HTTP_423_LOCKED,
                            detail=f"đang có tiến trình khác cập nhật quyền cho {user_id!r}: {e}") from None
    try:
        now = datetime.now(timezone.utc)
        if grant or revoke:
            # `$bit` — cập nhật nguyên tử, không đọc–ghi–ghi (tránh race).
            upd = repo.apply_bits(user_id, grant=grant, revoke=revoke, expected_version=expected)
            if upd is None:
                actual = repo.get_user(user_id)
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    detail={"error": "xung đột phiên bản", "expected": expected,
                                            "current": (actual or {}).get("version")})
        else:
            upd = repo.get_user(user_id)
        if body.collections:
            for coll, flags in body.collections.items():
                repo.set_collection_perm(user_id, coll, _mask_from_names(flags))
        # Vô hiệu cache quyền + tăng perm_ver (token cũ không còn hiệu lực)
        try:
            state.cache.delete(f"perm:{user_id}")
        except Exception as e:  # noqa: BLE001
            log.warning("không xoá được cache quyền: %s", e)
        out = clean(upd or {})
        out.pop("password_hash", None)
        perm = out.get("perm") or {}
        out["perm"] = {coll: flag_names(int(mask)) for coll, mask in perm.items()}
        out["perm_default"] = flag_names(int(perm.get("*", 0)))
        out["perm_ver"] = (upd or {}).get("perm_ver")
        return out
    finally:
        guard.release()


def db_holder(state_obj):
    """Lấy handle MongoDB từ state (tiện cho LockGuard)."""
    return state_obj.db


def _mask_from_names(names: list[str] | None) -> int:
    """Danh sách tên quyền → mask; tên sai → 422 (không phải 500)."""
    try:
        return mask_from_names(names or [])
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
