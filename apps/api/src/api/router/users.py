"""Quản lý người dùng và quyền (`/api/v1/users/...`).

- `GET /users` — MANAGE_USERS
- `GET /users/{id}` — MANAGE_USERS
- `POST /users` — MANAGE_USERS (tạo tài khoản, mật khẩu băm argon2)
- `PATCH /users/{id}/active` — MANAGE_USERS (khoá/mở khoá tài khoản)
- `PUT /users/{id}/perm` — MANAGE_USERS (cập nhật quyền **nguyên tử**)

Cập nhật quyền: toàn bộ thay đổi (`grant`/`revoke` trên `"*"` và `collections`) được tính từ bản ghi
vừa đọc rồi ghi bằng MỘT lệnh `find_one_and_update` có điều kiện `perm_ver` (compare-and-set): 409 nếu
`perm_ver` đã đổi. Khoá `lock:user:{user_id}` chỉ để báo 423 sớm; tính đúng đắn dựa trên điều kiện
`perm_ver`. Tên collection được kiểm tra (không `.`/`$`, phải nằm trong danh sách đã biết → 422).
Mỗi lần đổi quyền ghi `audit_logs` (action `user_perm`, before/after, `reason` bắt buộc). Sau khi đổi:
xoá cache `perm:{user_id}` và tăng `perm_ver` (token cũ mất hiệu lực).
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
from api.router.common import acquire_guard, clean, guard_check, to_vn_datetime
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
    grant: list[str] = Field(default_factory=list, description="Danh sách quyền cần BẬT (trên mask `*`)")
    revoke: list[str] = Field(default_factory=list, description="Danh sách quyền cần TẮT (trên mask `*`)")
    collections: dict[str, list[str]] | None = Field(
        default=None, description="Quyền riêng theo collection (ghi đè): {coll: [flags]}")
    perm_ver: int | None = Field(default=None, description="`perm_ver` mong đợi (409 nếu lệch)")
    version: int | None = Field(default=None, description="Bí danh cũ của `perm_ver` (không dùng nữa)")
    reason: str = Field(min_length=1, max_length=512, description="Lý do đổi quyền (ghi vào audit_logs)")


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
    """Cập nhật quyền của người dùng (một lệnh ghi có điều kiện `perm_ver`).

    - 404 nếu không có người dùng;
    - 422 nếu tên quyền/collection sai hoặc một quyền vừa bật vừa tắt;
    - 423 nếu đang có tiến trình khác sửa quyền của tài khoản này;
    - 409 nếu `perm_ver` mong đợi không khớp.
    """
    from vngis.locks import LockGuard

    from api.auth.users import PermissionChanged, UserNotFound
    from api.permissions import PERM_ALL, validate_perm_collection

    repo = state.repo
    doc = repo.get_user(user_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có người dùng {user_id!r}")
    grant = _mask_from_names(body.grant)
    revoke = _mask_from_names(body.revoke)
    if grant & revoke:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="một quyền không thể vừa bật vừa tắt")
    coll_masks: dict[str, int] = {}
    for coll, flags in (body.collections or {}).items():
        try:
            validate_perm_collection(coll)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
        coll_masks[coll] = _mask_from_names(flags)
    if "*" in coll_masks and (grant or revoke):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="không dùng đồng thời collections['*'] với grant/revoke")
    expected = body.perm_ver if body.perm_ver is not None else body.version
    if expected is None:
        expected = int(doc.get("perm_ver", 1))
    if int(doc.get("perm_ver", 1)) != int(expected):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "xung đột perm_ver", "expected": expected,
                                    "current": int(doc.get("perm_ver", 1))})
    masks = dict(coll_masks)
    if grant or revoke:
        # Mask `*` hiện tại (chưa khai báo thì suy từ vai trò, như `effective_perm`) → áp grant/revoke.
        star = int(repo.effective_perm(user_id).get("*", 0))
        masks["*"] = ((star | grant) & (PERM_ALL ^ revoke)) & PERM_ALL
    before = {"perm": {k: int(v) for k, v in (doc.get("perm") or {}).items()},
              "perm_ver": int(doc.get("perm_ver", 1))}

    guard = LockGuard(state.lock, db=state.db, resource=f"user:{user_id}", ttl_ms=300_000)
    acquire_guard(guard, busy_detail=f"đang có tiến trình khác cập nhật quyền cho {user_id!r}")
    try:
        if not masks:
            upd = doc
        else:
            guard_check(guard)
            now = datetime.now(timezone.utc)
            logs = state.db["audit_logs"]
            audit = {"at": now, "actor": user.username, "action": "user_perm", "source": "api",
                     "collection": USERS, "doc_id": user_id, "target": {"user_id": user_id},
                     "before": before, "after": None, "reason": body.reason.strip(), "state": "pending"}
            aid = logs.insert_one(audit).inserted_id
            try:
                upd = repo.update_perms(user_id, masks, expected_perm_ver=int(expected))
            except PermissionChanged as e:
                logs.delete_one({"_id": aid, "state": "pending"})
                cur = repo.get_user(user_id) or {}
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    detail={"error": "xung đột perm_ver", "expected": expected,
                                            "current": cur.get("perm_ver"), "message": str(e)}) from None
            except UserNotFound:
                logs.delete_one({"_id": aid, "state": "pending"})
                raise HTTPException(status.HTTP_404_NOT_FOUND,
                                    detail=f"không có người dùng {user_id!r}") from None
            except ValueError as e:
                logs.delete_one({"_id": aid, "state": "pending"})
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
            after = {"perm": {k: int(v) for k, v in (upd.get("perm") or {}).items()},
                     "perm_ver": int(upd.get("perm_ver", 1))}
            logs.update_one({"_id": aid, "state": "pending"}, {"$set": {"state": "applied", "after": after}})
        out = clean(upd or {})
        out.pop("password_hash", None)
        perm = out.get("perm") or {}
        out["perm"] = {coll: flag_names(int(mask)) for coll, mask in perm.items()}
        out["perm_default"] = flag_names(int(perm.get("*", 0)))
        out["perm_ver"] = (upd or {}).get("perm_ver")
        return out
    finally:
        guard.release()


def _mask_from_names(names: list[str] | None) -> int:
    """Danh sách tên quyền → mask; tên sai → 422 (không phải 500)."""
    try:
        return mask_from_names(names or [])
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
