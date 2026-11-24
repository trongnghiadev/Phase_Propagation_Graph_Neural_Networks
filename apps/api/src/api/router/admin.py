"""Quản trị hệ thống (`/api/v1/admin/cache`, `/api/v1/admin/locks`).

- `GET /admin/cache` — thống kê cache: hit/miss theo loại key, số khoá đang có, TTL trung bình.
- `DELETE /admin/cache` — xoá cache (tuỳ chọn theo collection/prefix). Tăng `ver:{coll}` để
  mọi khoá cũ trở nên vô hiệu (cache-aside theo phiên bản).
- `GET /admin/locks` — các khoá phân tán đang giữ (quét Redis), kèm fencing token.

Mọi endpoint ở đây đều cần quyền `MANAGE_CACHE` / `MANAGE_USERS` và bị giới hạn tần suất.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from api.deps import require, rate_limit
from api.permissions import P
from api.state import state

log = logging.getLogger("api.admin")

router = APIRouter(tags=["admin"])

CACHE_PREFIX = "cache:"
LOCK_PREFIX = "lock:"


class CachePurgeRequest(BaseModel):
    """Xoá cache theo loại / prefix cụ thể."""
    collection: str | None = Field(default=None, description="Collection cần tăng phiên bản (ver:{coll})")
    prefix: str | None = Field(default=None, description="Tiền tố khoá Redis cần xoá")
    bump_version: bool = Field(default=True, description="Tăng ver:{coll} để vô hiệu cache cũ")


@router.get("/admin/cache", dependencies=[Depends(require(P.MANAGE_CACHE, "cache"))])
async def cache_info():
    """Thống kê cache (hit/miss theo loại khoá) và độ dài khoá đang có."""
    stats = await asyncio.to_thread(state.cache.stats)
    info = await asyncio.to_thread(state.cache.info)
    return {"stats": stats, "info": info}


@router.delete("/admin/cache", dependencies=[Depends(require(P.MANAGE_CACHE, "cache")),
                                            Depends(rate_limit(scope="write"))])
async def purge_cache(body: CachePurgeRequest | None = None):
    """Xoá cache.

    - `collection` → tăng `ver:{collection}` (mọi khoá cache-aside của collection đó mất hiệu lực).
    - `prefix` → xoá trực tiếp các khoá Redis có tiền tố đó (dùng `SCAN`, không dùng `KEYS`).
    """
    body = body or CachePurgeRequest()
    deleted = 0
    bumped = None
    if body.prefix:
        deleted = await asyncio.to_thread(state.cache.delete_pattern, body.prefix)
    if body.collection and body.bump_version:
        bumped = await asyncio.to_thread(state.cache.bump_version, body.collection)
    elif body.bump_version and not body.collection:
        bumped = await asyncio.to_thread(state.cache.bump_version, "*")
    return {"deleted_keys": deleted, "bumped_version": bumped,
            "collection": body.collection, "prefix": body.prefix}


@router.get("/admin/locks", dependencies=[Depends(require(P.MANAGE_CACHE, "locks"))])
async def list_locks(limit: int = Query(200, ge=1, le=2000)):
    """Các khoá phân tán đang được giữ (quét Redis bằng `SCAN`)."""
    locks = await asyncio.to_thread(state.lock.list_locks, limit)
    return {"count": len(locks), "backend": state.lock.backend, "items": locks}


@router.get("/admin/health", dependencies=[Depends(require(P.READ, "system"))])
async def admin_health():
    """Tình trạng MongoDB / Redis / khoá (chẩn đoán nhanh)."""
    out: dict[str, Any] = {}
    try:
        out["mongodb"] = {"ok": state.db.command("ping").get("ok") == 1,
                          "collections": len(state.db.list_collection_names())}
    except Exception as e:  # noqa: BLE001
        out["mongodb"] = {"ok": False, "error": str(e)}
    try:
        out["redis"] = {"ok": bool(state.cache.ping())}
    except Exception as e:  # noqa: BLE001
        out["redis"] = {"ok": False, "error": str(e)}
    out["lock_backend"] = state.lock.backend
    out["instance"] = state.instance
    return out
