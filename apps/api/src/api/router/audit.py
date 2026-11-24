"""Nhật ký thay đổi và hoàn tác (`GET /api/v1/audit`, `POST /api/v1/audit/{id}/revert`).

`audit_logs` lưu lại mọi thay đổi (nguồn `api` hoặc `pipeline`) với trạng thái
`pending` / `applied` / `reverted`. Hoàn tác (`revert`) dựng lại giá trị `before` của bản ghi
gốc, dưới khoá sửa + điều kiện `version`, và đánh dấu chính bản ghi nhật ký là `reverted`.

Yêu cầu quyền `VIEW_AUDIT` để xem và `UPDATE` trên collection gốc để hoàn tác.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from bson import ObjectId

from api.deps import UserContext, require, rate_limit
from api.permissions import P
from api.router.common import clean, to_vn_datetime
from api.state import state

log = logging.getLogger("api.audit")

router = APIRouter(tags=["audit"])

AUDIT_LOGS = "audit_logs"


def _is_objectid(s: str) -> bool:
    """Chuỗi có phải ObjectId hợp lệ của MongoDB không."""
    try:
        ObjectId(s)
        return True
    except Exception:  # noqa: BLE001
        return False


class RevertRequest(BaseModel):
    """Yêu cầu hoàn tác một thay đổi đã ghi."""
    reason: str = Field(min_length=1, description="Lý do hoàn tác (bắt buộc, ghi vào audit_logs)")
    version: int | None = Field(default=None, description="Phiên bản mong đợi (409 nếu lệch)")


@router.get("/audit", dependencies=[Depends(require(P.VIEW_AUDIT, AUDIT_LOGS))])
async def list_audit(collection: str | None = None, actor: str | None = None,
                     audit_state: str | None = Query(None, alias="state"),
                     from_: str | None = Query(None, alias="from"), to: str | None = None,
                     limit: int = Query(200, ge=1, le=2000)):
    """Danh sách bản ghi nhật ký thay đổi."""
    from api.router.common import parse_day

    db = state.db
    flt: dict[str, Any] = {}
    if collection:
        flt["collection"] = collection
    if actor:
        flt["actor"] = actor
    if audit_state:
        flt["state"] = audit_state
    if from_ or to:
        d_to = parse_day(to, field="to") or datetime.now(timezone.utc)
        d_from = parse_day(from_, field="from") or d_to
        flt["at"] = {"$gte": d_from, "$lte": d_to}
    docs = list(db[AUDIT_LOGS].find(flt).sort([("at", -1)]).limit(limit))
    items = []
    for d in docs:
        out = clean(d)
        # `clean` bỏ `_id` nếu là ObjectId → phải lấy trước khi làm sạch.
        out["id"] = str(d.get("_id")) if d.get("_id") is not None else None
        out.pop("_id", None)
        out["at"] = to_vn_datetime(out.get("at"))
        out["reverted_at"] = to_vn_datetime(out.get("reverted_at"))
        items.append(out)
    return {"count": len(items), "items": items}


@router.get("/audit/{audit_id}", dependencies=[Depends(require(P.VIEW_AUDIT, AUDIT_LOGS))])
async def get_audit(audit_id: str):
    """Chi tiết một bản ghi nhật ký."""
    doc = state.db[AUDIT_LOGS].find_one({"_id": ObjectId(audit_id)}) if _is_objectid(audit_id) \
        else state.db[AUDIT_LOGS].find_one({"_id": audit_id})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có bản ghi nhật ký {audit_id!r}")
    out = clean(doc)
    out["id"] = str(doc.get("_id"))
    out.pop("_id", None)
    out["at"] = to_vn_datetime(out.get("at"))
    return out


@router.post("/audit/{audit_id}/revert", dependencies=[Depends(require(P.VIEW_AUDIT, AUDIT_LOGS)),
                                                       Depends(rate_limit(scope="write"))])
async def revert_audit(audit_id: str, body: RevertRequest,
                       user: UserContext = Depends(require(P.VIEW_AUDIT, AUDIT_LOGS))):
    """Hoàn tác một thay đổi đã ghi (khôi phục giá trị `before`).

    - 404 nếu không có bản ghi nhật ký;
    - 409 nếu bản ghi đã được hoàn tác hoặc `version` không khớp;
    - 423 nếu bản ghi gốc đang bị khoá sửa.
    """
    from vngis.locks import LockBusy, LockGuard

    db = state.db
    doc = db[AUDIT_LOGS].find_one({"_id": ObjectId(audit_id)}) if _is_objectid(audit_id) \
        else db[AUDIT_LOGS].find_one({"_id": audit_id})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có bản ghi nhật ký {audit_id!r}")
    if doc.get("state") == "reverted":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="bản ghi này đã được hoàn tác trước đó")
    collection = doc.get("collection")
    before = doc.get("before")
    if not collection:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="bản ghi nhật ký không có `collection`")
    if before is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="bản ghi nhật ký không lưu giá trị `before` nên không thể hoàn tác")
    target = doc.get("target") or {}
    flt = target.get("filter") or {"_id": doc.get("doc_id")}
    if isinstance(flt, str):
        flt = {"_id": flt}
    expected = body.version if body.version is not None else int(doc.get("version", 1))
    guard = LockGuard(state.lock, db=db, resource=f"revert:{collection}", ttl_ms=300_000)
    try:
        guard.acquire()
    except LockBusy as e:
        raise HTTPException(status.HTTP_423_LOCKED, detail=f"bản ghi đang được người khác sửa: {e}") from None
    try:
        now = datetime.now(timezone.utc)
        res = db[collection].find_one_and_update(
            flt, {"$set": {**before, "updated_at": now}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER)
        if res is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail="không tìm thấy bản ghi gốc để hoàn tác")
        db[AUDIT_LOGS].update_one({"_id": doc["_id"]},
                                  {"$set": {"state": "reverted", "reverted_at": now,
                                            "reverted_by": user.username,
                                            "revert_reason": body.reason},
                                   "$inc": {"version": 1}})
        try:
            state.cache.bump_version(collection)
        except Exception as e:  # noqa: BLE001
            log.warning("không bump ver:%s: %s", collection, e)
        out = clean(res)
        out["reverted"] = True
        out["reverted_at"] = to_vn_datetime(now)
        return out
    finally:
        guard.release()
