"""Địa giới hành chính theo hai thời kỳ (`/api/v1/admin-units/...`).

**Quy tắc bắt buộc của dự án (ADR 0003):** mọi truy vấn địa giới phải kèm ``era``
(``before_2025_07`` hoặc ``from_2025_07``), vì cùng một mã có thể là hai đơn vị khác nhau
ở hai thời kỳ (mã 80: Long An → Tây Ninh).

- `GET /admin-units/provinces?at=YYYY-MM-DD` — các tỉnh hiệu lực tại ngày (suy ra `era` từ ngày).
- `GET /admin-units/provinces/{era}/{code}` — chi tiết tỉnh kèm tỉnh kế thừa / hợp thành.
- `GET /admin-units/wards/{era}/{code}/mapping` — ánh xạ xã cũ ↔ xã mới.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import require
from api.permissions import P
from api.router.common import check_era, clean, parse_day, to_vn_date
from api.state import state
from vngis.admin import BEFORE, ERAS, FROM, era_of, province_at

log = logging.getLogger("api.admin_units")

router = APIRouter(prefix="/admin-units", tags=["admin-units"])

ADMIN_UNITS = "admin_units"
PROVINCE_TIMELINE = "province_timeline"
ADMIN_UNIT_MAPPINGS = "admin_unit_mappings"


@router.get("/provinces", dependencies=[Depends(require(P.READ, ADMIN_UNITS))])
async def list_provinces(at: str | None = None):
    """Các tỉnh có hiệu lực tại thời điểm `at` (mặc định: hiện tại).

    `at` là ngày `YYYY-MM-DD` theo giờ Việt Nam; thời kỳ địa giới suy ra từ ngày đó.
    """
    db = state.db
    dt = parse_day(at, field="at") or datetime.now(timezone.utc)
    era = era_of(dt)
    cache = state.cache
    key = cache.versioned_key("prov", ADMIN_UNITS, era, to_vn_date(dt))

    def compute():
        return clean(province_at(db, dt))

    items = await asyncio.to_thread(state.cache.get_or_compute, key, "admin_units", compute)
    return {"at": to_vn_date(dt), "era": era, "count": len(items), "items": items}


@router.get("/provinces/{era}/{code}", dependencies=[Depends(require(P.READ, PROVINCE_TIMELINE))])
async def get_province(era: str, code: int):
    """Chi tiết một tỉnh theo thời kỳ, kèm tỉnh kế thừa (cũ) hoặc tỉnh hợp thành (mới)."""
    check_era(era)
    db = state.db
    cache = state.cache
    key = cache.versioned_key("prov", PROVINCE_TIMELINE, era, code)

    def compute():
        doc = db[PROVINCE_TIMELINE].find_one({"era": era, "code": int(code)})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail=f"không có tỉnh mã {code} ở thời kỳ {era}")
        return clean(doc)

    return await asyncio.to_thread(state.cache.get_or_compute, key, "admin_units", compute)


@router.get("/wards/{era}/{code}/mapping", dependencies=[Depends(require(P.READ, ADMIN_UNIT_MAPPINGS))])
async def ward_mapping(era: str, code: int, limit: int = Query(500, ge=1, le=5000)):
    """Ánh xạ xã cũ ↔ xã mới cho một tỉnh ở một thời kỳ.

    - Thời kỳ cũ: các xã của tỉnh đó và xã mới tương ứng.
    - Thời kỳ mới: xã mới của tỉnh đó và các xã cũ nguồn.
    """
    check_era(era)
    db = state.db
    cache = state.cache
    key = cache.versioned_key("wardmap", ADMIN_UNIT_MAPPINGS, era, code, limit)

    def compute():
        if era == BEFORE:
            wards = list(db[ADMIN_UNITS].find({"era": BEFORE, "level": "ward", "province_code": int(code)},
                                               {"_id": 0, "code": 1, "name": 1,
                                                "successor_ward_codes": 1}).limit(limit))
            codes = [w["code"] for w in wards]
            mappings = list(db[ADMIN_UNIT_MAPPINGS].find({"old.ward_code": {"$in": codes}},
                                                         {"_id": 0}))
        else:
            wards = list(db[ADMIN_UNITS].find({"era": FROM, "level": "ward", "province_code": int(code)},
                                               {"_id": 0, "code": 1, "name": 1,
                                                "predecessor_ward_codes": 1}).limit(limit))
            codes = [w["code"] for w in wards]
            mappings = list(db[ADMIN_UNIT_MAPPINGS].find({"new.ward_code": {"$in": codes}},
                                                         {"_id": 0}))
        return {"era": era, "province_code": int(code), "wards": clean(wards),
                "mappings": clean(mappings)}

    return await asyncio.to_thread(state.cache.get_or_compute, key, "admin_units", compute)


@router.get("/eras", dependencies=[Depends(require(P.READ, ADMIN_UNITS))])
async def list_eras():
    """Hai thời kỳ địa giới và mốc hiệu lực."""
    return {"eras": list(ERAS), "before": BEFORE, "from": FROM,
            "effective_utc": "2025-06-30T17:00:00Z"}
