"""Hàng đợi duyệt QC (`GET /api/v1/review`, `POST /api/v1/review/{id}/approve`).

Các mục cần duyệt là cờ chất lượng trong `qc_flags` (do job QC hoặc người kiểm tra tạo), theo mô hình
của `vngis.qc_rules`: field **`status`** ∈ `suspect` (chưa quyết định) | `confirmed_ok` | `rejected` |
`corrected`, kèm `version`. Hàng đợi mặc định là các cờ `suspect` chưa `retired`.

Quyết định được ghi qua **`vngis.qc.decide`**: `find_one_and_update` có điều kiện `version`
(khóa lạc quan, 409 nếu lệch) và bản ghi `audit_logs` (`qc_decision`) trong cùng transaction (replica set)
hoặc pending → applied (một node). API KHÔNG ghi vào `tide_extremes`: G0 áp quyết định ở lần chạy sau.
Collection đích luôn là `qc_flags` (không nhận tên collection từ client).

Khoá `lock:review:{id}` chỉ để báo 423 sớm khi người khác đang xử lý cùng cờ; KHÔNG dùng fencing
token để bảo vệ lệnh ghi — tính đúng đắn dựa trên điều kiện `version` của `decide`. Trước khi ghi vẫn
gọi `guard.check()` (mất khoá → 503).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.deps import UserContext, require, rate_limit
from api.permissions import P
from api.router.common import (acquire_guard, attach_actor_labels, clean, guard_check, qc_http_error,
                               to_vn_datetime)
from api.state import state

log = logging.getLogger("api.review")

router = APIRouter(tags=["review"])

QC_FLAGS = "qc_flags"


class ReviewDecision(BaseModel):
    """Quyết định của người rà soát cho một cờ QC."""
    status: Literal["confirmed_ok", "rejected", "corrected", "suspect"] = Field(
        description="confirmed_ok: giá trị đúng; rejected: bỏ giá trị; corrected: thay bằng corrected_value; "
                    "suspect: bỏ quyết định cũ")
    reason: str = Field(min_length=1, max_length=1000, description="Lý do (bắt buộc, ghi vào audit_logs)")
    corrected_value: float | None = Field(default=None, description="Giá trị sửa (chỉ khi status=corrected)")
    version: int = Field(description="`version` của cờ mà người duyệt đã xem (409 nếu lệch)")


def _item(d: dict) -> dict:
    return {"id": d.get("_id"), "collection": d.get("collection"), "station": d.get("station"),
            "kind": d.get("kind"), "ts": to_vn_datetime(d.get("ts")), "value": d.get("value"),
            "status": d.get("status"), "corrected_value": d.get("corrected_value"),
            "rules": [r.get("rule") for r in (d.get("rules") or []) if isinstance(r, dict)],
            "origin": d.get("origin"), "reason": d.get("reason"), "version": d.get("version", 1),
            "value_mismatch": bool(d.get("value_mismatch")), "decided_by": d.get("decided_by"),
            "decided_at": to_vn_datetime(d.get("decided_at")),
            "created_at": to_vn_datetime(d.get("created_at"))}


@router.get("/review", dependencies=[Depends(require(P.READ, QC_FLAGS))])
async def review_queue(review_status: str | None = Query(None, alias="status"),
                       station: str | None = None, limit: int = Query(200, ge=1, le=2000)):
    """Hàng đợi duyệt: cờ QC theo `status` (mặc định `suspect`), bỏ cờ `retired`."""
    flt: dict[str, Any] = {"status": review_status or "suspect", "retired": {"$ne": True}}
    if station:
        flt["station"] = station
    docs = list(state.db[QC_FLAGS].find(flt).sort([("station", 1), ("ts", 1)]).limit(limit))
    items = attach_actor_labels([_item(d) for d in docs], "decided_by")
    return {"count": len(items), "items": items}


@router.post("/review/{review_id}/approve", dependencies=[Depends(require(P.APPROVE, QC_FLAGS)),
                                                          Depends(rate_limit(scope="write"))])
async def review_approve(review_id: str, body: ReviewDecision,
                         user: UserContext = Depends(require(P.APPROVE, QC_FLAGS))):
    """Ghi quyết định cho một cờ QC qua `vngis.qc.decide`.

    - 404 nếu không có cờ;
    - 409 nếu `version` không khớp (người khác vừa quyết định);
    - 422 nếu quyết định sai (ví dụ `corrected` thiếu `corrected_value`);
    - 423 nếu cờ đang được người khác xử lý; 503 nếu mất khoá trước khi ghi.
    """
    from vngis import qc
    from vngis.locks import LockGuard

    db = state.db
    if qc.get_flag(db, review_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có cờ QC {review_id!r}")
    guard = LockGuard(state.lock, db=db, resource=f"review:{review_id}", ttl_ms=60_000, use_fence=False)
    acquire_guard(guard, busy_detail="cờ QC đang được người khác xử lý")
    try:
        guard_check(guard)
        try:
            res = await asyncio.to_thread(
                qc.decide, db, review_id, body.status, reason=body.reason, decided_by=user.actor,
                expected_version=int(body.version), corrected_value=body.corrected_value, source="api")
        except (qc.QcError, ValueError) as e:
            raise qc_http_error(e) from None
    finally:
        guard.release()
    try:
        state.cache.bump_version(QC_FLAGS)
    except Exception as e:  # noqa: BLE001
        log.warning("không bump ver:%s: %s", QC_FLAGS, e)
    out = attach_actor_labels([_item(res)], "decided_by")[0]
    out["flag"] = clean(res)
    return out
