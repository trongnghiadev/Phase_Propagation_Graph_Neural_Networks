"""Cảnh báo (`GET /api/v1/alerts`, `POST /api/v1/alerts/{id}/ack`).

Cảnh báo được sinh bởi scheduler/worker (GĐ7.7) và lưu trong `alerts`:
``{alert_id, station, basin, type, severity, issued_at, valid_to, message, status, ack_by, acked_at}``.
Khi đọc, API gắn ``ack_by_label`` (tra `users`, không lưu).

**Định danh cảnh báo.** `alert_id` là **chuỗi**. Bản ghi cũ (hoặc bản ghi ghi tay) có
thể chỉ có `_id` kiểu `ObjectId` — `ObjectId` không serialize được bởi JSON nên API luôn
ép `alert_id` về chuỗi (`_alert_id_of`) và tra cứu được theo cả `alert_id` lẫn `_id`
(`_alert_filter`).

Mỗi cảnh báo có ``era`` của địa giới liên quan (luôn kèm `era` — ADR 0006).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from api.deps import UserContext, require
from api.permissions import P
from api.router.common import attach_actor_labels, clean, to_vn_datetime
from api.state import state

log = logging.getLogger("api.alerts")

router = APIRouter(tags=["alerts"])

ALERTS = "alerts"


def _is_objectid(s: str) -> bool:
    """Chuỗi có phải ObjectId hợp lệ của MongoDB không."""
    try:
        ObjectId(s)
        return True
    except Exception:  # noqa: BLE001
        return False


def _alert_id_of(doc: dict) -> str | None:
    """Định danh cảnh báo dạng **chuỗi**.

    Cảnh báo do job `phasegnn` sinh có thể chỉ có `_id` kiểu `ObjectId` (bản ghi cũ,
    hoặc bản ghi ghi tay) — `ObjectId` **không** serialize được bởi JSON nên trả về
    client sẽ gây 500. Vì vậy luôn ép về chuỗi.
    """
    raw = doc.get("alert_id")
    if raw is None or raw == "":
        oid = doc.get("_id")
        return str(oid) if oid is not None else None
    return str(raw)


def _alert_filter(alert_id: str) -> dict[str, Any]:
    """Điều kiện tìm cảnh báo theo `alert_id` (chuỗi) — hỗ trợ cả `alert_id` lẫn `_id`.

    `alert_id` có thể là chuỗi do job sinh (``model:station:date:lead:level``) hoặc là
    biểu diễn chuỗi của `_id` (`ObjectId` 24 ký tự hex). Với trường hợp sau, `_id` là
    `ObjectId` nên phải truyền đúng kiểu `ObjectId` — nếu không, không tìm thấy bản ghi.

    Điều kiện là **OR** (`$or`) vì bản ghi cũ có `_id` là `ObjectId` mà **không** có
    trường `alert_id` (ngược lại, điều kiện AND sẽ không khớp bản ghi nào).
    """
    ors: list[dict[str, Any]] = [{"alert_id": alert_id}]
    if _is_objectid(alert_id):
        ors.append({"_id": ObjectId(alert_id)})
    else:
        # `_id` có thể là chuỗi (bản ghi cũ đặt `_id` = "A1") → thử cả hai kiểu.
        ors.append({"_id": alert_id})
    return {"$or": ors}


class AckRequest(BaseModel):
    """Xác nhận (ack) một cảnh báo."""
    note: str = Field(default="", description="Ghi chú khi xác nhận")
    status: str = Field(default="acknowledged", pattern="^(acknowledged|resolved)$")


@router.get("/alerts", dependencies=[Depends(require(P.READ, ALERTS))])
async def list_alerts(station: str | None = None, basin: str | None = None,
                      severity: str | None = None, alert_status: str | None = Query(None, alias="status"),
                      from_: str | None = Query(None, alias="from"), to: str | None = None,
                      limit: int = Query(200, ge=1, le=2000)):
    """Danh sách cảnh báo (lọc theo trạm, lưu vực, mức độ, trạng thái, thời gian phát hành)."""
    from api.router.common import parse_day

    db = state.db
    flt: dict[str, Any] = {}
    if station:
        flt["station"] = station
    if basin:
        flt["basin"] = basin
    if severity:
        flt["severity"] = severity
    if alert_status:
        flt["status"] = alert_status
    if from_ or to:
        d_to = parse_day(to, field="to") or datetime.now(timezone.utc)
        d_from = parse_day(from_, field="from") or d_to
        flt["issued_at"] = {"$gte": d_from, "$lte": d_to}
    docs = list(db[ALERTS].find(flt).sort([("issued_at", -1)]).limit(limit))
    items = []
    for d in docs:
        items.append({"alert_id": _alert_id_of(d), "station": d.get("station"),
                      "basin": d.get("basin"), "type": d.get("type"), "severity": d.get("severity"),
                      "issued_at": to_vn_datetime(d.get("issued_at")),
                      "valid_to": to_vn_datetime(d.get("valid_to")),
                      "message": d.get("message"), "status": d.get("status"),
                      "ack_by": d.get("ack_by"), "acked_at": to_vn_datetime(d.get("acked_at")),
                      "era": d.get("era") or (d.get("province") or {}).get("era")})
    attach_actor_labels(items, "ack_by")
    return {"count": len(items), "items": items}


@router.get("/alerts/{alert_id}", dependencies=[Depends(require(P.READ, ALERTS))])
async def get_alert(alert_id: str):
    """Chi tiết một cảnh báo."""
    doc = state.db[ALERTS].find_one(_alert_filter(alert_id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có cảnh báo {alert_id!r}")
    # `alert_id` phải lấy từ document **gốc**: `clean()` bỏ `_id` (ObjectId).
    aid = _alert_id_of(doc)
    doc = clean(doc)
    doc["alert_id"] = aid
    doc["issued_at"] = to_vn_datetime(doc.get("issued_at"))
    doc["valid_to"] = to_vn_datetime(doc.get("valid_to"))
    doc["acked_at"] = to_vn_datetime(doc.get("acked_at"))
    attach_actor_labels([doc], "ack_by")
    return doc


@router.post("/alerts/{alert_id}/ack", dependencies=[Depends(require(P.UPDATE, ALERTS))])
async def ack_alert(alert_id: str, body: AckRequest,
                    user: UserContext = Depends(require(P.UPDATE, ALERTS))):
    """Xác nhận cảnh báo (đóng yêu cầu xử lý)."""
    db = state.db
    now = datetime.now(timezone.utc)
    flt = _alert_filter(alert_id)
    res = db[ALERTS].find_one_and_update(
        flt,
        {"$set": {"status": body.status, "ack_by": user.actor, "acked_at": now,
                  "ack_note": body.note, "updated_at": now}},
        return_document=ReturnDocument.AFTER)
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có cảnh báo {alert_id!r}")
    try:
        state.cache.bump_version("alerts")
    except Exception as e:  # noqa: BLE001
        log.warning("không bump ver:alerts: %s", e)
    # `alert_id` phải lấy từ document **gốc**: `clean()` bỏ `_id` (ObjectId).
    aid = _alert_id_of(res)
    out = clean(res)
    out["alert_id"] = aid
    out["issued_at"] = to_vn_datetime(out.get("issued_at"))
    out["acked_at"] = to_vn_datetime(out.get("acked_at"))
    attach_actor_labels([out], "ack_by")
    return out
