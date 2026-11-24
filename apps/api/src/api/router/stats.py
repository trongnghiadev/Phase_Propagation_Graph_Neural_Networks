"""Thống kê đỉnh triều và vượt báo động (`GET /api/v1/stats/tide`).

Tính từ `tide_extremes` (G0) và `stations.alarm_history` (ngưỡng báo động theo thời gian,
K9 của đề cương — không dùng ngưỡng cố định).

Cache-aside với khoá theo phiên bản `ver:tide_extremes`; giá trị nặng được dựng lại dưới
khoá `lock:rebuild:{key}` (chống stampede, xem `vngis.cache`).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from api.deps import require
from api.permissions import P
from api.router.common import to_vn_date
from api.state import state

log = logging.getLogger("api.stats")

router = APIRouter(tags=["stats"])

TIDE_EXTREMES = "tide_extremes"
STATIONS = "stations"


def _alarm_levels(station_doc: dict | None, when: datetime) -> dict:
    """Ngưỡng báo động của trạm tại thời điểm `when` (theo `alarm_history`)."""
    if not station_doc:
        return {"level_1_m": None, "level_2_m": None, "level_3_m": None}
    for seg in station_doc.get("alarm_history") or []:
        frm = seg.get("from")
        to = seg.get("to")
        if frm and when < frm:
            continue
        if to and when > to:
            continue
        return {"level_1_m": seg.get("level_1_m"), "level_2_m": seg.get("level_2_m"),
                "level_3_m": seg.get("level_3_m")}
    return {"level_1_m": None, "level_2_m": None, "level_3_m": None}


@router.get("/stats/tide", dependencies=[Depends(require(P.READ, TIDE_EXTREMES))])
async def tide_stats(station: str | None = None, year: int | None = Query(None, ge=2016, le=2100),
                     kind: str = Query("high", pattern="^(high|low)$")):
    """Thống kê đỉnh (hoặc chân) triều: số lần, trung vị, max, số ngày vượt từng cấp báo động."""
    db = state.db
    cache = state.cache
    key = cache.versioned_key("stats", TIDE_EXTREMES, station or "all", year or "all", kind)

    def compute():
        flt: dict = {"meta.kind": kind}
        if station:
            flt["meta.station"] = station
        if year:
            flt["ts"] = {"$gte": datetime(year, 1, 1, tzinfo=timezone.utc),
                         "$lt": datetime(year + 1, 1, 1, tzinfo=timezone.utc)}
        docs = list(db[TIDE_EXTREMES].find(flt, {"_id": 0, "meta": 1, "ts": 1, "value": 1, "qc": 1}))
        vals = [d["value"] for d in docs if isinstance(d.get("value"), (int, float))]
        stations = {s["code"]: s for s in db[STATIONS].find({}, {"_id": 0, "code": 1, "alarm_history": 1})}
        by_station: dict[str, dict] = {}
        for d in docs:
            code = (d.get("meta") or {}).get("station")
            slot = by_station.setdefault(code, {"station": code, "n": 0, "n_value": 0, "values": [],
                                                 "exceed": {"level_1": 0, "level_2": 0, "level_3": 0},
                                                 "max": None, "max_ts": None})
            slot["n"] += 1
            v = d.get("value")
            if not isinstance(v, (int, float)):
                continue
            slot["n_value"] += 1
            slot["values"].append(float(v))
            if slot["max"] is None or float(v) > slot["max"]:
                slot["max"] = float(v)
                slot["max_ts"] = to_vn_date(d.get("ts"))
            lv = _alarm_levels(stations.get(code), d.get("ts") or datetime.now(timezone.utc))
            if lv["level_1_m"] is not None and float(v) >= float(lv["level_1_m"]):
                slot["exceed"]["level_1"] += 1
            if lv["level_2_m"] is not None and float(v) >= float(lv["level_2_m"]):
                slot["exceed"]["level_2"] += 1
            if lv["level_3_m"] is not None and float(v) >= float(lv["level_3_m"]):
                slot["exceed"]["level_3"] += 1
        out = []
        for slot in by_station.values():
            vs = sorted(slot.pop("values"))
            n = len(vs)
            slot["median"] = (vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2) if n else None
            slot["mean"] = sum(vs) / n if n else None
            slot["min"] = vs[0] if n else None
            out.append(slot)
        out.sort(key=lambda s: s["station"] or "")
        return {"kind": kind, "year": year, "n_documents": len(docs), "stations": out}

    return await asyncio.to_thread(state.cache.get_or_compute, key, "stats", compute)


@router.get("/stats/cache", dependencies=[Depends(require(P.MANAGE_CACHE, TIDE_EXTREMES))])
async def cache_stats():
    """Bộ đếm hit/miss của cache theo loại key (quản trị)."""
    stats = await asyncio.to_thread(state.cache.stats)
    return stats
