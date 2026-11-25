"""Dữ liệu không gian và truy vết dòng sông (`/api/v1/features/{coll}`, `/api/v1/trace/{comid}`).

- `GET /features/{coll}` — trả về GeoJSON FeatureCollection của một collection không gian
  (lọc theo bbox và mức zoom `z`). `era` là tham số BẮT BUỘC cho các collection hành chính
  (`admin_units`, `province_timeline`, `admin_unit_mappings`) — ADR 0003.
- `GET /trace/{comid}` — truy vết dòng sông theo COMID (mạng thủy văn), trả về các đoạn
  từ trên xuống dưới cùng các trạm ven sông.

Cache-aside với khoá theo phiên bản `ver:{coll}`; GeoJSON được nén (zstd+msgpack) nếu lớn.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import require
from api.permissions import P
from api.router.common import check_era, clean, to_lonlat
from api.state import state

log = logging.getLogger("api.features")

router = APIRouter(tags=["features"])

#: Các collection không gian được phép truy vấn (chống truy cập tùy tiện).
ALLOWED = {"admin_units", "province_timeline", "admin_unit_mappings", "rivers", "watersheds",
           "reservoirs_geo", "subbasins", "flood_plains", "catchments"}

#: Các collection BẮT BUỘC có `era` trong mọi truy vấn.
ERA_REQUIRED = {"admin_units", "province_timeline", "admin_unit_mappings"}


@router.get("/features/{coll}", dependencies=[Depends(require(P.READ, "admin_units"))])
async def features(coll: str, bbox: str | None = Query(None, description="minx,miny,maxx,maxy (EPSG:4326)"),
                   z: int | None = Query(None, ge=0, le=24, description="mức zoom (lọc theo min_zoom)"),
                   era: str | None = Query(None, description="BẮT BUỘC với collection hành chính"),
                   limit: int = Query(5000, ge=1, le=50000)):
    """Trả về GeoJSON FeatureCollection của một collection không gian."""
    if coll not in ALLOWED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"collection {coll!r} không được phép; chỉ cho phép: {sorted(ALLOWED)}")
    if coll in ERA_REQUIRED:
        if not era:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail=f"collection {coll!r} bắt buộc có tham số `era` "
                                       "(before_2025_07 | from_2025_07)")
        check_era(era)
    db = state.db
    flt: dict[str, Any] = {}
    if era:
        flt["era"] = era
    if bbox:
        try:
            minx, miny, maxx, maxy = [float(v) for v in bbox.split(",")]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="bbox phải có dạng minx,miny,maxx,maxy") from None
        geom = flt.setdefault("geometry", {})
        geom["$geoWithin"] = {"$geometry": {"type": "Polygon", "coordinates": [[
            [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]]}}
    if z is not None:
        flt.setdefault("min_zoom", {"$lte": z})
    cache = state.cache
    key = cache.versioned_key("feat", coll, era or "all", bbox or "", z if z is not None else "", limit)

    def compute():
        docs = list(db[coll].find(flt, {"_id": 0}).limit(limit))
        features_out = []
        for d in docs:
            geom = d.get("geometry") or d.get("geom")
            if not geom:
                # `location` có thể là GeoJSON Point hoặc mảng [lon, lat] → luôn trả về geometry GeoJSON.
                loc = d.get("location")
                if isinstance(loc, dict) and loc.get("type"):
                    geom = loc
                else:
                    coords = to_lonlat(loc)
                    geom = {"type": "Point", "coordinates": coords} if coords else None
            props = d.get("properties") or {k: v for k, v in d.items()
                                           if k not in ("_id", "geometry", "geom", "location")}
            features_out.append({"type": "Feature", "geometry": geom, "properties": clean(props)})
        return {"type": "FeatureCollection", "collection": coll, "era": era,
                "count": len(features_out), "features": features_out}

    return await asyncio.to_thread(state.cache.get_or_compute, key, "features", compute)


@router.get("/trace/{comid}", dependencies=[Depends(require(P.READ, "rivers"))])
async def trace(comid: int, limit: int = Query(500, ge=1, le=5000)):
    """Truy vết dòng sông theo COMID: các đoạn từ trên xuống dưới + các trạm ven sông."""
    db = state.db
    colls = set(db.list_collection_names())
    if "rivers" not in colls:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"error": "graph_not_ready",
                                    "reason": "collection `rivers` (mạng thủy văn) chưa được nạp"})
    flt = {"comid": int(comid)}
    upstream = list(db["rivers"].find(flt).limit(limit))
    # Nếu `rivers` lưu cạnh (from/to), dựng đường đi xuôi dòng
    sample = db["rivers"].find_one({}, {"downstream_comid": 1}) or {}
    edges = list(db["rivers"].find({"downstream_comid": int(comid)}, {"_id": 0}).limit(limit)) \
        if "downstream_comid" in sample else []
    stations = []
    if "stations" in colls:
        stations = list(db["stations"].find({"merit_comid": int(comid)},
                                           {"_id": 0, "code": 1, "name": 1, "location": 1}).limit(limit))
        for s in stations:
            s["location"] = to_lonlat(s.get("location"))
    return {"comid": int(comid), "segments": clean(upstream), "upstream_edges": clean(edges),
            "stations": clean(stations), "count": len(upstream)}
