"""Dự báo và mô hình (`/api/v1/forecasts/...`, `/api/v1/models/...`).

**Quy tắc quan trọng (theo yêu cầu GĐ7.8):** các endpoint phụ thuộc mô hình PhaseGNN chưa
huấn luyện sẽ:

1. ưu tiên đọc dự báo mẫu đã có trong `results/` (bản dự báo tham chiếu, không phải mô hình
   chạy thật) → trả kèm cờ ``"source": "sample"``; hoặc
2. nếu không có bản dự báo mẫu → trả **503** kèm lý do rõ ràng, KHÔNG bịa số liệu.

Cơ chế cache: ``write-through`` cho dự báo (ghi thẳng vào Redis kèm con trỏ
``fc:{station}:latest``), cache-aside cho các endpoint tổng hợp.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.deps import UserContext, require, rate_limit
from api.permissions import P
from api.router.common import clean, to_vn_date, to_vn_datetime
from api.state import state

log = logging.getLogger("api.forecasts")

router = APIRouter(tags=["forecasts"])

FORECASTS = "forecasts"
MODELS = "models"
ALERTS = "alerts"

#: Thư mục kết quả nghiên cứu (chứa dự báo mẫu). Có thể ghi đè bằng biến môi trường.
RESULTS_DIR = Path(os.environ.get("PGNN_RESULTS_DIR", "results")).resolve()


def _sample_forecast_files() -> list[Path]:
    """Các tệp CSV dự báo mẫu trong `results/` (ưu tiên các tệp của E5/E7)."""
    if not RESULTS_DIR.exists():
        return []
    pats = ["E5/*.csv", "E5/**/*.csv", "E7/*.csv", "E7/**/*.csv",
            "forecast*.csv", "**/forecast*.csv", "**/*prediction*.csv"]
    out: list[Path] = []
    for p in pats:
        out.extend(RESULTS_DIR.glob(p))
    return sorted({f.resolve() for f in out if f.is_file()})


def _load_sample_rows(path: Path, limit: int) -> list[dict]:
    """Đọc tệp CSV mẫu (dùng `csv` chuẩn, không phụ thuộc pandas)."""
    import csv

    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append(dict(row))
                if len(rows) >= limit:
                    break
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        log.warning("không đọc được tệp mẫu %s: %s", path, e)
        return []
    return rows


def _sample_payload(station: str | None, limit: int) -> dict | None:
    """Dựng payload dự báo mẫu từ `results/` (trả None nếu không có)."""
    files = _sample_forecast_files()
    if not files:
        return None
    for path in files:
        rows = _load_sample_rows(path, limit)
        if not rows:
            continue
        # lọc theo trạm nếu cột trạm có sẵn và người dùng yêu cầu
        if station:
            st_col = next((c for c in rows[0] if c.lower() in ("station", "station_code", "code")), None)
            if st_col:
                sel = [r for r in rows if str(r.get(st_col, "")).upper() == station.upper()]
                if sel:
                    rows = sel
        return {"source": "sample", "file": path.name, "rows": rows,
                "note": "dự báo mẫu từ kết quả nghiên cứu; KHÔNG phải kết quả mô hình PhaseGNN đã huấn luyện"}
    return None


def _not_ready(detail: str) -> HTTPException:
    """503 có giải thích rõ ràng (không có mô hình đã huấn luyện)."""
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={"error": "model_not_ready", "reason": detail,
                                "hint": "chạy POST /models/train sau khi cài PhaseGNN, hoặc dùng dự báo mẫu trong results/"})


class ForecastRunRequest(BaseModel):
    """Yêu cầu chạy dự báo."""
    station: str | None = None
    basin: str | None = None
    horizon_h: int = Field(default=72, ge=1, le=720)
    issued_at: str | None = Field(default=None, description="Thời điểm phát hành (ISO)")
    model_version: str | None = None
    force_sample: bool = Field(default=False, description="Ép dùng dự báo mẫu trong results/")


class TrainRequest(BaseModel):
    """Yêu cầu huấn luyện mô hình."""
    model_version: str | None = None
    epochs: int = Field(default=50, ge=1, le=2000)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    station: str | None = None
    basin: str | None = None


@router.get("/forecasts/latest", dependencies=[Depends(require(P.READ, FORECASTS))])
async def forecasts_latest(station: str | None = None, basin: str | None = None,
                           limit: int = Query(500, ge=1, le=5000)):
    """Dự báo mới nhất.

    Ưu tiên: (1) dự báo trong MongoDB; (2) dự báo mẫu trong `results/`; (3) 503.
    """
    db = state.db
    flt: dict[str, Any] = {}
    if station:
        flt["station"] = station
    if basin:
        flt["basin"] = basin
    docs = list(db[FORECASTS].find(flt).sort([("issued_at", -1)]).limit(limit))
    if docs:
        items = [{"station": d.get("station"), "basin": d.get("basin"),
                  "issued_at": to_vn_datetime(d.get("issued_at")),
                  "valid_from": to_vn_datetime(d.get("valid_from")),
                  "valid_to": to_vn_datetime(d.get("valid_to")),
                  "peak_ts": to_vn_datetime(d.get("peak_ts")),
                  "peak_value": d.get("peak_value"), "model_version": d.get("model_version"),
                  "source": d.get("source", "model")} for d in docs]
        return {"source": "model", "count": len(items), "items": items}
    sample = _sample_payload(station, limit)
    if sample:
        return {"source": "sample", "count": len(sample["rows"]), "items": sample["rows"],
                "file": sample["file"], "note": sample["note"]}
    raise _not_ready("chưa có dự báo nào trong MongoDB và không tìm thấy dự báo mẫu trong results/")


@router.get("/forecasts/compare", dependencies=[Depends(require(P.READ, FORECASTS))])
async def forecasts_compare(station: str | None = None, basin: str | None = None,
                            model_version: str | None = None,
                            limit: int = Query(500, ge=1, le=5000)):
    """So sánh các lần phát hành dự báo (hoặc nhiều phiên bản mô hình) cho một trạm."""
    db = state.db
    flt: dict[str, Any] = {}
    if station:
        flt["station"] = station
    if basin:
        flt["basin"] = basin
    if model_version:
        flt["model_version"] = model_version
    docs = list(db[FORECASTS].find(flt).sort([("station", 1), ("issued_at", 1)]).limit(limit))
    grouped: dict[str, list[dict]] = {}
    for d in docs:
        key = d.get("station") or d.get("basin") or "?"
        grouped.setdefault(key, []).append(d)
    if not grouped:
        sample = _sample_payload(station, limit)
        if sample:
            return {"source": "sample", "file": sample["file"], "groups": {station or "all": sample["rows"]},
                    "note": sample["note"]}
        raise _not_ready("chưa có dữ liệu dự báo để so sánh")
    out = {}
    for k, v in grouped.items():
        out[k] = [{"issued_at": to_vn_datetime(d.get("issued_at")),
                   "peak_ts": to_vn_datetime(d.get("peak_ts")),
                   "peak_value": d.get("peak_value"),
                   "model_version": d.get("model_version"),
                   "source": d.get("source", "model")} for d in v]
    return {"source": "model", "groups": out}


@router.get("/forecasts/map", dependencies=[Depends(require(P.READ, FORECASTS))])
async def forecasts_map(basin: str | None = None, issued_at: str | None = None):
    """Bản đồ dự báo: mỗi trạm một điểm GeoJSON kèm đỉnh dự báo."""
    db = state.db
    flt: dict[str, Any] = {}
    if basin:
        flt["basin"] = basin
    if issued_at:
        from api.router.common import parse_dt
        flt["issued_at"] = parse_dt(issued_at, field="issued_at")
    docs = list(db[FORECASTS].find(flt).sort([("issued_at", -1)]).limit(2000))
    coords = {s["code"]: s.get("location") for s in db["stations"].find(
        {}, {"_id": 0, "code": 1, "location": 1})}
    features = []
    for d in docs:
        loc = coords.get(d.get("station"))
        if not loc:
            continue
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": loc},
                         "properties": {"station": d.get("station"), "basin": d.get("basin"),
                                        "issued_at": to_vn_datetime(d.get("issued_at")),
                                        "peak_ts": to_vn_datetime(d.get("peak_ts")),
                                        "peak_value": d.get("peak_value"),
                                        "model_version": d.get("model_version"),
                                        "source": d.get("source", "model")}})
    if not features:
        sample = _sample_payload(None, 1000)
        if sample:
            return {"source": "sample", "file": sample["file"],
                    "type": "FeatureCollection", "features": [], "note": sample["note"]}
        raise _not_ready("chưa có dữ liệu dự báo để dựng bản đồ")
    return {"source": "model", "type": "FeatureCollection", "count": len(features), "features": features}


@router.get("/forecasts/{station}/propagation", dependencies=[Depends(require(P.READ, FORECASTS))])
async def forecast_propagation(station: str, limit: int = Query(500, ge=1, le=5000)):
    """Chuỗi lan truyền đỉnh cho một trạm (dùng để trực quan hoá độ trễ pha).

    Dữ liệu lấy từ `phase_lag`/`forecasts` nếu có; nếu không có thì trả 503.
    """
    db = state.db
    docs = list(db[FORECASTS].find({"station": station}).sort([("issued_at", -1)]).limit(limit))
    if docs:
        items = [{"issued_at": to_vn_datetime(d.get("issued_at")),
                  "peak_ts": to_vn_datetime(d.get("peak_ts")),
                  "peak_value": d.get("peak_value"),
                  "lag_hours": d.get("lag_hours"),
                  "upstream": d.get("upstream"), "model_version": d.get("model_version")} for d in docs]
        return {"station": station, "source": "model", "count": len(items), "items": items}
    lags = list(db["phase_lag"].find({"station": station}).sort([("ts", -1)]).limit(limit)) \
        if "phase_lag" in db.list_collection_names() else []
    if lags:
        items = [{"ts": to_vn_datetime(d.get("ts")), "lag_hours": d.get("lag_hours"),
                  "distance_km": d.get("distance_km"), "upstream": d.get("upstream"),
                  "method": d.get("method")} for d in lags]
        return {"station": station, "source": "phase_lag", "count": len(items), "items": items}
    raise _not_ready(f"không có dữ liệu lan truyền pha cho trạm {station!r}")


@router.post("/forecasts/run", dependencies=[Depends(require(P.RUN_FORECAST, FORECASTS)),
                                            Depends(rate_limit(scope="job"))])
async def run_forecast(body: ForecastRunRequest,
                       user: UserContext = Depends(require(P.RUN_FORECAST, FORECASTS))):
    """Chạy dự báo.

    Vì `apps/api` không được import `phasegnn` (luật phụ thuộc), endpoint này **không** chạy
    mô hình trực tiếp mà ghi một `jobs` và giao cho `apps/worker` xử lý. Nếu chưa có worker
    hoặc không có mô hình, trả 503 kèm lý do; nếu có dự báo mẫu thì trả bản mẫu.
    """
    db = state.db
    issued = body.issued_at or to_vn_datetime(datetime.now(timezone.utc))
    job_id = f"forecast:{body.station or body.basin or 'all'}:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    doc = {"_id": job_id, "type": "forecast_run", "state": "queued",
           "params": body.model_dump(), "requested_by": user.username,
           "worker": None, "fence": None, "heartbeat": None,
           "created_at": datetime.now(timezone.utc)}
    db["jobs"].insert_one(doc)
    if body.force_sample:
        sample = _sample_payload(body.station, 200)
        if sample:
            return {"job_id": job_id, "state": "sample", "source": "sample",
                    "file": sample["file"], "note": sample["note"]}
    if not body.force_sample:
        # Thử dùng dự báo mẫu sẵn có khi chưa có mô hình huấn luyện.
        sample = _sample_payload(body.station, 200)
        if sample:
            db["jobs"].update_one({"_id": job_id},
                                  {"$set": {"state": "sample", "note": sample["note"]}})
            return {"job_id": job_id, "state": "sample", "source": "sample",
                    "file": sample["file"], "note": sample["note"]}
    db["jobs"].update_one({"_id": job_id},
                          {"$set": {"state": "failed", "error": "model_not_ready",
                                    "message": "chưa có mô hình PhaseGNN đã huấn luyện"}})
    raise _not_ready("chưa có mô hình PhaseGNN đã huấn luyện; lệnh đã ghi vào `jobs` để worker xử lý")


@router.post("/models/train", dependencies=[Depends(require(P.MANAGE_MODEL, MODELS)),
                                            Depends(rate_limit(scope="job"))])
async def train_model(body: TrainRequest,
                      user: UserContext = Depends(require(P.MANAGE_MODEL, MODELS))):
    """Yêu cầu huấn luyện mô hình — giao cho `apps/worker` (luật: `apps/api` không import `phasegnn`)."""
    db = state.db
    ver = body.model_version or f"v{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    job_id = f"train:{ver}"
    existing = db[MODELS].find_one({"version": ver})
    if existing:
        return {"model_version": ver, "state": existing.get("state", "unknown"),
                "note": "phiên bản đã tồn tại"}
    db["jobs"].insert_one({"_id": job_id, "type": "train", "state": "queued",
                           "params": body.model_dump(), "model_version": ver,
                           "requested_by": user.username, "worker": None, "fence": None,
                           "heartbeat": None, "created_at": datetime.now(timezone.utc)})
    return {"job_id": job_id, "model_version": ver, "state": "queued",
            "note": "đã giao cho worker; apps/api không import phasegnn theo luật phụ thuộc"}


@router.post("/models/{version}/promote", dependencies=[Depends(require(P.MANAGE_MODEL, MODELS)),
                                                        Depends(rate_limit(scope="job"))])
async def promote_model(version: str,
                        user: UserContext = Depends(require(P.MANAGE_MODEL, MODELS))):
    """Chuyển một phiên bản mô hình thành bản đang phục vụ (promote).

    Ghi vào `models` và tăng `ver:models` để cache dự báo cũ bị vô hiệu.
    """
    db = state.db
    doc = db[MODELS].find_one({"version": version})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có mô hình phiên bản {version!r}")
    now = datetime.now(timezone.utc)
    db[MODELS].update_one({"version": version},
                          {"$set": {"state": "promoted", "promoted_at": now,
                                    "promoted_by": user.username}})
    db[MODELS].update_many({"version": {"$ne": version}, "state": "promoted"},
                           {"$set": {"state": "archived"}})
    try:
        state.cache.bump_version("models")
        state.cache.bump_version("forecasts")
    except Exception as e:  # noqa: BLE001
        log.warning("không bump được ver: %s", e)
    return {"model_version": version, "state": "promoted", "promoted_at": to_vn_datetime(now)}
