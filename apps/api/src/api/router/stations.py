"""Danh mục trạm và số liệu triều (`/api/v1/stations`, `/api/v1/tides`).

- `GET /stations` — READ, cache-aside theo phiên bản `ver:stations`.
- `GET /stations/{code}` — READ.
- `PATCH /stations/{code}` — UPDATE: sửa thuộc tính trạm; khoá sửa `lock:edit:stations:{code}`
  (423 nếu đang bị khoá) + điều kiện `version` (409 nếu xung đột).
- `GET /stations/{code}/tides` — READ: chuỗi đỉnh/chân của trạm trong khoảng ngày.
- `PATCH /tides/{station}/{ts}` — UPDATE: quyết định QC cho một đỉnh/chân triều qua `vngis.qc`
  (tạo cờ thủ công nếu chưa có, rồi `decide` với `expected_version`). API KHÔNG ghi `tide_extremes`
  và không ghi đè `value` của cờ: G0 áp quyết định (rejected/corrected/confirmed_ok) ở lần chạy sau.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from api.deps import UserContext, rate_limit, require
from api.permissions import P
from api.router.common import (acquire_guard, clean, guard_check, parse_day, parse_dt, qc_http_error,
                               to_vn_date, to_vn_datetime)
from api.state import state

log = logging.getLogger("api.stations")

router = APIRouter(tags=["stations"])

STATIONS = "stations"
TIDE_EXTREMES = "tide_extremes"
QC_FLAGS = "qc_flags"


async def _cache_get_or_compute(key: str, kind: str, compute):
    return await asyncio.to_thread(state.cache.get_or_compute, key, kind, compute)


class StationPatch(BaseModel):
    """Các thuộc tính trạm được phép sửa qua API."""
    name: str | None = None
    location: list[float] | None = Field(default=None, description="GeoJSON Point [lon, lat]")
    location_meta: dict[str, Any] | None = None
    river: str | None = None
    aliases: list[str] | None = None
    datum: str | None = None
    merit_comid: int | None = None
    admin: dict[str, Any] | None = None
    note: str | None = None
    reason: str | None = Field(default=None, description="Lý do sửa (ghi vào audit_logs)")
    version: int | None = Field(default=None, description="Phiên bản mong đợi (409 nếu lệch)")


@router.get("/stations", dependencies=[Depends(require(P.READ, STATIONS))])
async def list_stations(kind: str | None = None, q: str | None = None,
                        limit: int = Query(200, ge=1, le=1000)):
    """Danh sách trạm (lọc theo loại hoặc tên)."""
    db = state.db
    flt: dict[str, Any] = {}
    if kind:
        flt["kind"] = kind
    if q:
        flt["$or"] = [{"name": {"$regex": q, "$options": "i"}}, {"code": {"$regex": q, "$options": "i"}}]
    cache = state.cache
    key = cache.versioned_key("st", STATIONS, kind or "all", q or "", limit)

    def compute():
        return clean(list(db[STATIONS].find(flt).sort("code", 1).limit(limit)))

    items = await _cache_get_or_compute(key, "stations", compute)
    return {"total": db[STATIONS].count_documents(flt), "count": len(items), "items": items}


@router.get("/stations/{code}", dependencies=[Depends(require(P.READ, STATIONS))])
async def get_station(code: str):
    """Chi tiết một trạm."""
    cache = state.cache
    key = cache.versioned_key("st", STATIONS, code)

    def compute():
        doc = state.db[STATIONS].find_one({"code": code})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có trạm {code!r}")
        return clean(doc)

    return await _cache_get_or_compute(key, "stations", compute)


@router.patch("/stations/{code}", dependencies=[Depends(require(P.UPDATE, STATIONS)),
                                                Depends(rate_limit(scope="write"))])
async def patch_station(code: str, body: StationPatch,
                        user: UserContext = Depends(require(P.UPDATE, STATIONS))):
    """Sửa thuộc tính trạm. 423 nếu đang bị khoá sửa, 409 nếu xung đột `version`."""
    from vngis.locks import LockGuard

    changes = body.model_dump(exclude_none=True, exclude={"reason", "version"})
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="không có trường nào để sửa")
    db = state.db
    cur = db[STATIONS].find_one({"code": code})
    if cur is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có trạm {code!r}")
    expected = body.version if body.version is not None else int(cur.get("version", 1))
    guard = LockGuard(state.lock, db=db, resource=f"edit:stations:{code}", ttl_ms=300_000)
    acquire_guard(guard, busy_detail="bản ghi đang được người khác sửa")
    try:
        guard_check(guard)
        updates = {**changes, "updated_at": datetime.now(timezone.utc)}
        res = db[STATIONS].find_one_and_update({"code": code, "version": expected},
                                               {"$set": updates, "$inc": {"version": 1}},
                                               return_document=ReturnDocument.AFTER)
        if res is None:
            actual = db[STATIONS].find_one({"code": code}, {"version": 1})
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail={"error": "xung đột phiên bản", "expected": expected,
                                        "current": (actual or {}).get("version")})
        _invalidate("stations")
        _audit("stations", code, code, changes, body.reason, user.username)
        return clean(res)
    finally:
        guard.release()


@router.get("/stations/{code}/tides", dependencies=[Depends(require(P.READ, TIDE_EXTREMES))])
async def station_tides(code: str, from_: str | None = Query(None, alias="from"),
                        to: str | None = None, kind: str | None = None,
                        limit: int = Query(2000, ge=1, le=20000)):
    """Chuỗi đỉnh/chân triều của một trạm trong khoảng ngày (mặc định 90 ngày gần nhất)."""
    from datetime import timedelta

    from vngis.admin import VN_TZ
    d_to = parse_day(to, field="to") or __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc)
    d_from = parse_day(from_, field="from") or (d_to - timedelta(days=90))
    flt: dict[str, Any] = {"meta.station": code, "ts": {"$gte": d_from, "$lte": d_to}}
    if kind:
        flt["meta.kind"] = kind
    db = state.db
    docs = list(db[TIDE_EXTREMES].find(flt).sort([("ts", 1)]).limit(limit))
    return {"station": code, "count": len(docs),
            "from": to_vn_date(d_from), "to": to_vn_date(d_to),
            "items": [{"ts": to_vn_datetime(d.get("ts")), "kind": (d.get("meta") or {}).get("kind"),
                       "value": d.get("value"), "qc": d.get("qc"), "source_file": d.get("source_file")}
                      for d in docs]}


class TidePatch(BaseModel):
    """Quyết định QC cho một đỉnh/chân triều (áp vào `tide_extremes` bởi G0, không ghi trực tiếp)."""
    status: Literal["rejected", "corrected", "confirmed_ok"] = Field(
        description="rejected: bỏ giá trị; corrected: thay bằng corrected_value; confirmed_ok: giá trị đúng")
    corrected_value: float | None = Field(default=None, description="Giá trị sửa (chỉ khi status=corrected)")
    kind: Literal["high", "low"] | None = Field(default=None,
                                                description="Đỉnh/chân; bắt buộc nếu cùng ts có cả hai")
    reason: str = Field(min_length=1, max_length=1000, description="Lý do (ghi vào audit_logs)")
    version: int | None = Field(default=None,
                                description="`version` của cờ hiện có (bắt buộc khi cờ đã tồn tại; 409 nếu lệch)")


@router.patch("/tides/{station}/{ts}", dependencies=[Depends(require(P.UPDATE, TIDE_EXTREMES)),
                                                    Depends(rate_limit(scope="write"))])
async def patch_tide(station: str, ts: str,
                     body: TidePatch,
                     user: UserContext = Depends(require(P.UPDATE, TIDE_EXTREMES))):
    """Quyết định QC cho một đỉnh hoặc chân triều qua `vngis.qc`.

    Chưa có cờ → `create_manual_flag` (cờ thủ công) rồi `decide(expected_version=1)`; đã có cờ →
    `decide(expected_version=body.version)`. Mỗi bước ghi `audit_logs`. `tide_extremes` không bị sửa.

    - 404 nếu không có phần tử triều; 422 nếu dữ liệu sai hoặc cần chỉ rõ `kind`;
    - 428 nếu cờ đã tồn tại mà không gửi `version`; 409 nếu `version` lệch;
    - 423 nếu đang bị khoá sửa bởi người khác; 503 nếu mất khoá trước khi ghi.
    """
    from vngis import qc, qc_rules
    from vngis.locks import LockGuard

    ts_dt = parse_dt(ts, field="ts")
    db = state.db
    q: dict[str, Any] = {"meta.station": station, "ts": ts_dt}
    if body.kind:
        q["meta.kind"] = body.kind
    kinds = sorted({(d.get("meta") or {}).get("kind") for d in db[TIDE_EXTREMES].find(q, {"meta.kind": 1})})
    if not kinds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có phần tử triều ({station}, {ts})")
    if len(kinds) > 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"cùng thời điểm có {kinds}: phải gửi `kind`")
    kind = kinds[0]
    try:
        fid = qc.flag_id(station, kind, ts_dt)
        # Kiểm tra quyết định TRƯỚC khi tạo cờ thủ công, để quyết định sai không để lại cờ mồ côi.
        qc_rules.validate_decision(body.status, body.corrected_value, body.reason, user.username)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None

    guard = LockGuard(state.lock, db=db, resource=f"edit:tide_extremes:{station}", ttl_ms=60_000,
                      use_fence=False)
    acquire_guard(guard, busy_detail="bản ghi đang được người khác sửa")
    created = False
    try:
        flag = qc.get_flag(db, fid)
        if flag is None:
            guard_check(guard)
            try:
                flag = await asyncio.to_thread(qc.create_manual_flag, db, station, kind, ts_dt,
                                               reason=body.reason, decided_by=user.username, source="api")
            except (qc.QcError, ValueError) as e:
                raise qc_http_error(e) from None
            created = True
            expected = int(flag["version"])
        else:
            if body.version is None:
                raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED,
                                    detail={"error": "cờ đã tồn tại: gửi `version` của cờ",
                                            "flag_id": fid, "current": flag.get("version")})
            expected = int(body.version)
        guard_check(guard)
        try:
            res = await asyncio.to_thread(qc.decide, db, fid, body.status, reason=body.reason,
                                          decided_by=user.username, expected_version=expected,
                                          corrected_value=body.corrected_value, source="api")
        except (qc.QcError, ValueError) as e:
            raise qc_http_error(e) from None
    finally:
        guard.release()
    _invalidate(QC_FLAGS)
    out = clean(res)
    out["flag_id"] = fid
    out["flag_created"] = created
    out["note"] = "quyết định được ghi vào qc_flags; G0 áp vào tide_extremes ở lần chạy sau"
    return out


def _invalidate(coll: str) -> None:
    """Tăng phiên bản cache của collection (vô hiệu cache cũ)."""
    try:
        state.cache.bump_version(coll)
    except Exception as e:  # noqa: BLE001
        log.warning("không tăng được ver:%s: %s", coll, e)


def _audit(collection: str, doc_id: str, target: str, changes: dict, reason: str | None, actor: str) -> None:
    """Ghi `audit_logs` (nhật ký thay đổi) — không làm hỏng request nếu lỗi."""
    from datetime import datetime, timezone
    try:
        state.db["audit_logs"].insert_one({"at": datetime.now(timezone.utc), "actor": actor,
                                           "action": "api_update", "source": "api",
                                           "collection": collection, "doc_id": doc_id,
                                           "target": {"id": target}, "before": None,
                                           "after": changes, "reason": reason or "", "state": "applied"})
    except Exception as e:  # noqa: BLE001
        log.warning("không ghi được audit_logs: %s", e)
