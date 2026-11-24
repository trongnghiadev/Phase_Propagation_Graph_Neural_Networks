"""Số liệu hồ chứa (`GET /api/v1/reservoirs/{name}/daily`).

Đọc từ `reservoir_daily` (G0). Mỗi bản ghi có `province` = `{era, code, note, provisional}` —
địa giới LUÔN kèm `era` (ADR 0006). Có cache-aside theo phiên bản `ver:reservoir_daily`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import require
from api.permissions import P
from api.router.common import clean, parse_day, to_vn_date
from api.state import state

log = logging.getLogger("api.reservoirs")

router = APIRouter(tags=["reservoirs"])

RESERVOIR_DAILY = "reservoir_daily"


@router.get("/reservoirs", dependencies=[Depends(require(P.READ, RESERVOIR_DAILY))])
async def list_reservoirs():
    """Danh sách các hồ có số liệu (mã trạm trong `stations`)."""
    db = state.db
    names = sorted(db[RESERVOIR_DAILY].distinct("reservoir"))
    return {"count": len(names), "items": names}


@router.get("/reservoirs/{name}/daily", dependencies=[Depends(require(P.READ, RESERVOIR_DAILY))])
async def reservoir_daily(name: str, from_: str | None = Query(None, alias="from"),
                          to: str | None = None, limit: int = Query(1000, ge=1, le=20000)):
    """Số liệu hồ chứa theo ngày (mực nước, lưu lượng vào, chạy máy, xả tràn)."""
    db = state.db
    d_to = parse_day(to, field="to") or datetime.now(timezone.utc)
    d_from = parse_day(from_, field="from") or (d_to - timedelta(days=365))
    flt = {"reservoir": name, "date": {"$gte": d_from, "$lte": d_to}}
    cache = state.cache
    key = cache.versioned_key("res", RESERVOIR_DAILY, name, to_vn_date(d_from), to_vn_date(d_to), limit)

    def compute():
        docs = list(db[RESERVOIR_DAILY].find(flt).sort("date", 1).limit(limit))
        if not docs:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail=f"không có số liệu hồ {name!r} trong khoảng yêu cầu")
        return clean(docs)

    items = await asyncio.to_thread(state.cache.get_or_compute, key, "stats", compute)
    return {"reservoir": name, "count": len(items),
            "from": to_vn_date(d_from), "to": to_vn_date(d_to), "items": items}
