"""Hàng đợi duyệt QC (`POST /api/v1/review/{id}/approve`).

Các bản ghi cần duyệt nằm trong `qc_flags` (cờ chất lượng do G0 sinh) với trạng thái
`pending`. Người duyệt phải có quyền `APPROVE` trên collection tương ứng.

Thao tác duyệt/từ chối được bảo vệ bằng khoá sửa `lock:review:{id}` và kiểm tra `version`
(409 nếu xung đột), ngoài ra ghi vào `audit_logs`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from api.deps import UserContext, require, rate_limit
from api.permissions import P
from api.router.common import clean, to_vn_datetime
from api.state import state

log = logging.getLogger("api.review")

router = APIRouter(tags=["review"])

QC_FLAGS = "qc_flags"
REVIEW_QUEUE = "review_queue"


class ReviewDecision(BaseModel):
    """Quyết định duyệt của người rà soát."""
    decision: str = Field(default="approve", pattern="^(approve|reject)$")
    reason: str = Field(default="", description="Lý do (bắt buộc khi từ chối)")
    collection: str | None = Field(default=None, description="Collection cần sửa (mặc định suy ra từ cờ QC)")
    new_value: float | None = Field(default=None, description="Giá trị đề xuất sửa")
    version: int | None = Field(default=None, description="Phiên bản mong đợi (409 nếu lệch)")


@router.get("/review", dependencies=[Depends(require(P.READ, QC_FLAGS))])
async def review_queue(review_status: str | None = Query(None, alias="status"),
                       limit: int = Query(200, ge=1, le=2000)):
    """Hàng đợi duyệt: các cờ QC đang chờ xử lý."""
    flt: dict[str, Any] = {}
    if review_status:
        flt["state"] = review_status
    else:
        flt["state"] = "pending"
    docs = list(state.db[QC_FLAGS].find(flt).sort([("created_at", 1)]).limit(limit))
    items = []
    for d in docs:
        items.append({"id": d.get("_id"), "collection": d.get("collection"), "station": d.get("station"),
                      "reason": d.get("reason"), "severity": d.get("severity"),
                      "state": d.get("state"), "version": d.get("version", 1),
                      "created_at": to_vn_datetime(d.get("created_at")),
                      "reviewed_by": d.get("reviewed_by"),
                      "reviewed_at": to_vn_datetime(d.get("reviewed_at"))})
    return {"count": len(items), "items": items}


@router.post("/review/{review_id}/approve", dependencies=[Depends(require(P.APPROVE, QC_FLAGS)),
                                                          Depends(rate_limit(scope="write"))])
async def review_approve(review_id: str, body: ReviewDecision,
                         user: UserContext = Depends(require(P.APPROVE, QC_FLAGS))):
    """Duyệt (hoặc từ chối) một cờ QC.

    - 404 nếu không có cờ;
    - 423 nếu đang bị người khác duyệt;
    - 409 nếu `version` mong đợi không khớp.
    """
    from vngis.locks import LockBusy, LockGuard

    db = state.db
    doc = db[QC_FLAGS].find_one({"_id": review_id})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có mục duyệt {review_id!r}")
    if body.decision == "reject" and not body.reason.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="phải nêu lý do khi từ chối")
    expected = body.version if body.version is not None else int(doc.get("version", 1))
    guard = LockGuard(state.lock, db=db, resource=f"review:{review_id}", ttl_ms=300_000)
    try:
        guard.acquire()
    except LockBusy as e:
        raise HTTPException(status.HTTP_423_LOCKED, detail=f"mục duyệt đang được người khác xử lý: {e}") from None
    try:
        now = datetime.now(timezone.utc)
        new_state = "approved" if body.decision == "approve" else "rejected"
        res = db[QC_FLAGS].find_one_and_update(
            {"_id": review_id, "version": expected},
            {"$set": {"state": new_state, "reviewed_by": user.username, "reviewed_at": now,
                      "review_reason": body.reason, "updated_at": now},
             "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER)
        if res is None:
            actual = db[QC_FLAGS].find_one({"_id": review_id}, {"version": 1})
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail={"error": "xung đột phiên bản", "expected": expected,
                                        "current": (actual or {}).get("version")})
        # Nếu duyệt kèm giá trị sửa → áp dụng vào collection gốc (có fencing token)
        coll = body.collection or doc.get("collection")
        applied = None
        if body.decision == "approve" and body.new_value is not None and coll:
            applied = _apply_value(db, coll, doc, float(body.new_value), user.username, now)
        try:
            state.cache.bump_version(coll or QC_FLAGS)
        except Exception as e:  # noqa: BLE001
            log.warning("không bump ver:%s: %s", coll, e)
        _audit(db, coll or QC_FLAGS, review_id, {"state": new_state, "new_value": body.new_value},
               body.reason, user.username, now)
        out = clean(res)
        out["created_at"] = to_vn_datetime(out.get("created_at"))
        out["reviewed_at"] = to_vn_datetime(out.get("reviewed_at"))
        out["applied"] = applied
        return out
    finally:
        guard.release()


def _apply_value(db, coll: str, flag_doc: dict, value: float, actor: str, now: datetime):
    """Ghi giá trị đề xuất vào bản ghi gốc (dùng fencing token của khoá đang giữ)."""
    try:
        target = flag_doc.get("target") or {}
        flt = target.get("filter") or {}
        if not flt:
            return {"applied": False, "reason": "cờ QC không có điều kiện lọc bản ghi gốc"}
        res = db[coll].update_one(flt, {"$set": {target.get("field", "value"): value,
                                                 "reviewed_by": actor, "updated_at": now}})
        return {"applied": res.matched_count > 0, "matched": res.matched_count}
    except Exception as e:  # noqa: BLE001
        log.warning("không áp dụng được giá trị vào %s: %s", coll, e)
        return {"applied": False, "reason": str(e)}


def _audit(db, collection: str, doc_id: str, changes: dict, reason: str, actor: str, now: datetime) -> None:
    try:
        db["audit_logs"].insert_one({"at": now, "actor": actor, "action": "review_approve",
                                     "source": "api", "collection": collection, "doc_id": doc_id,
                                     "before": None, "after": changes, "reason": reason or "",
                                     "state": "applied"})
    except Exception as e:  # noqa: BLE001
        log.warning("không ghi được audit_logs: %s", e)
