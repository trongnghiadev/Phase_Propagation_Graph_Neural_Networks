"""Dự báo và mô hình (`/api/v1/forecasts/...`, `/api/v1/models/...`).

`POST /forecasts/run` giao việc ``forecast_run`` (``queued``) cho worker; worker chạy PhaseGNN từ
checkpoint và ghi `model_forecasts` (`GET /forecasts/latest` đọc collection này khi `forecasts` rỗng).

**Quy tắc (GĐ7.8):** khi chưa có dự báo của mô hình, các endpoint đọc sẽ:

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
from api.router.common import actor_labels, clean, to_vn_date, to_vn_datetime
from api.state import state

log = logging.getLogger("api.forecasts")

router = APIRouter(tags=["forecasts"])

FORECASTS = "forecasts"
#: Dự báo PhaseGNN do worker ghi (`apps/worker/src/worker/forecast.py`).
MODEL_FORECASTS = "model_forecasts"
MODELS = "models"
ALERTS = "alerts"

#: Thư mục kết quả nghiên cứu (chứa dự báo mẫu). Có thể ghi đè bằng biến môi trường.
RESULTS_DIR = Path(os.environ.get("PGNN_RESULTS_DIR", "results")).resolve()


#: Tệp dự báo mẫu, theo thứ tự ưu tiên (tương đối `RESULTS_DIR`): dự báo tập test theo
#: `schemas/prediction/v1.schema.json` — PhaseGNN (E2) trước, rồi mô hình cơ sở. `results/E5/*.csv` là bảng
#: chỉ số tổng quát hóa, KHÔNG phải dự báo ⇒ không dùng. `predictions*.csv` bị gitignore: chỉ có trên máy đã
#: chạy thực nghiệm; không có ⇒ không trả mẫu (503).
SAMPLE_FORECAST_CANDIDATES = (
    "E2/phasegnn_long/test_full/predictions_test.csv",
    "E2/*/test_full/predictions_test.csv",
    "baselines/B0/predictions_test.csv",
    "baselines/*/predictions_test.csv",
)
#: Cột bắt buộc của một tệp dự báo mẫu (tập con của schema dự báo v1).
SAMPLE_REQUIRED_COLUMNS = frozenset({"model", "station", "issued_date", "target_date", "lead_day", "peak_idx",
                                     "h_q10", "h_q50", "h_q90"})


def _sample_header(path: Path) -> list[str] | None:
    import csv

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return next(csv.reader(fh), None)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None


def _sample_forecast_files() -> list[Path]:
    """Các tệp dự báo mẫu hợp lệ trong `results/` (đúng thứ tự ưu tiên, đủ cột, nằm trong `RESULTS_DIR`)."""
    if not RESULTS_DIR.exists():
        return []
    out: list[Path] = []
    for pat in SAMPLE_FORECAST_CANDIDATES:
        for f in sorted(RESULTS_DIR.glob(pat)):
            f = f.resolve()
            if f in out or not f.is_file():
                continue
            try:
                f.relative_to(RESULTS_DIR)
            except ValueError:      # symlink trỏ ra ngoài results/
                continue
            header = _sample_header(f)
            if header is None or not SAMPLE_REQUIRED_COLUMNS <= set(header):
                log.info("bỏ qua tệp mẫu %s: thiếu cột dự báo", f.name)
                continue
            out.append(f)
    return out


def _load_sample_rows(path: Path, limit: int, station: str | None = None) -> list[dict]:
    """Đọc tệp CSV mẫu (dùng `csv` chuẩn, không phụ thuộc pandas); lọc trạm khi đọc."""
    import csv

    want = station.upper() if station else None
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if want and str(row.get("station", "")).upper() != want:
                    continue
                rows.append(dict(row))
                if len(rows) >= limit:
                    break
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        log.warning("không đọc được tệp mẫu %s: %s", path.name, e)
        return []
    return rows


def _sample_payload(station: str | None, limit: int) -> dict | None:
    """Dựng payload dự báo mẫu từ `results/` (trả None nếu không có tệp hợp lệ / không có trạm yêu cầu)."""
    for path in _sample_forecast_files():
        rows = _load_sample_rows(path, limit, station)
        if not rows:
            continue
        rel = str(path.relative_to(RESULTS_DIR)).replace("\\", "/")
        return {"source": "sample", "file": rel, "rows": rows,
                "note": "dự báo mẫu (tập test của thực nghiệm) từ results/; KHÔNG phải dự báo vận hành"}
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
    mf = _model_forecasts_latest(db, station, limit)
    if mf:
        return mf
    sample = _sample_payload(station, limit)
    if sample:
        return {"source": "sample", "count": len(sample["rows"]), "items": sample["rows"],
                "file": sample["file"], "note": sample["note"]}
    raise _not_ready("chưa có dự báo nào trong MongoDB và không tìm thấy dự báo mẫu trong results/")


def _model_forecasts_latest(db, station: str | None, limit: int) -> dict | None:
    """Lượt dự báo PhaseGNN mới nhất trong `model_forecasts` (worker ghi; mỗi (run_date, trạm, lead) một document)."""
    if MODEL_FORECASTS not in db.list_collection_names():
        return None
    flt: dict[str, Any] = {"station": station} if station else {}
    last = db[MODEL_FORECASTS].find_one(flt, {"run_date": 1}, sort=[("run_date", -1)])
    if not last:
        return None
    flt["run_date"] = last["run_date"]
    docs = list(db[MODEL_FORECASTS].find(flt).sort([("station", 1), ("lead_day", 1)]).limit(limit))
    items = [{"station": d.get("station"), "run_date": d.get("run_date"), "lead_day": d.get("lead_day"),
              "target_date": d.get("target_date"), "model": d.get("model"),
              "model_version": d.get("model_version"), "h_max_q50": d.get("h_max_q50"),
              "peaks": [{"peak_idx": p.get("peak_idx"), "h_q10": p.get("h_q10"), "h_q50": p.get("h_q50"),
                         "h_q90": p.get("h_q90"), "t_peak": to_vn_datetime(p.get("t_peak"))}
                        for p in d.get("peaks") or []],
              "source": "phasegnn"} for d in docs]
    return {"source": "model", "collection": MODEL_FORECASTS, "run_date": last["run_date"],
            "count": len(items), "items": items}


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


def _sample_enabled() -> bool:
    """Bật nhánh dự báo mẫu cho `POST /forecasts/run` (``PGNN_FORECAST_SAMPLE=1``; mặc định tắt)."""
    return os.environ.get("PGNN_FORECAST_SAMPLE", "").strip().lower() in ("1", "true", "yes", "on")


@router.post("/forecasts/run", dependencies=[Depends(require(P.RUN_FORECAST, FORECASTS)),
                                            Depends(rate_limit(scope="job"))])
async def run_forecast(body: ForecastRunRequest,
                       user: UserContext = Depends(require(P.RUN_FORECAST, FORECASTS))):
    """Chạy dự báo: giao việc ``forecast_run`` cho `apps/worker` (``state="queued"``).

    `apps/api` không import `phasegnn` (luật phụ thuộc): worker nhận việc, chạy PhaseGNN từ checkpoint và ghi
    `model_forecasts`; thiếu checkpoint / thiếu `phasegnn` → worker đánh việc ``skipped`` kèm lý do.

    Nhánh dự báo mẫu (`results/`) chỉ dùng khi ``force_sample=true`` hoặc cấu hình ``PGNN_FORECAST_SAMPLE=1``:
    có bản mẫu → bản ghi `jobs` chèn thẳng trạng thái cuối ``sample`` (không qua ``queued`` ⇒ không tranh với
    worker); ``force_sample`` mà không có bản mẫu → ``failed`` + 503. Cấu hình bật nhưng không có bản mẫu →
    giao worker như bình thường.
    """
    from vngis.job_params import JobParamsError, validate_job_params

    db = state.db
    now = datetime.now(timezone.utc)
    job_id = f"forecast:{body.station or body.basin or 'all'}:{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    actor = user.actor
    base = {"_id": job_id, "type": "forecast_run", "requested_by": actor, "worker": None, "fence": None,
            "heartbeat": None, "created_at": now, "updated_at": now}
    if body.force_sample or _sample_enabled():
        sample = _sample_payload(body.station, 200)
        if sample:
            db["jobs"].insert_one({**base, "params": body.model_dump(), "state": "sample",
                                   "finished_at": now, "note": sample["note"]})
            return {"job_id": job_id, "state": "sample", "source": "sample",
                    "file": sample["file"], "note": sample["note"]}
        if body.force_sample:
            db["jobs"].insert_one({**base, "params": body.model_dump(), "state": "failed", "finished_at": now,
                                   "error": "sample_not_found", "message": "không có dự báo mẫu trong results/"})
            raise _not_ready("force_sample=true nhưng không có dự báo mẫu trong results/")
    try:
        params = validate_job_params("forecast_run", body.model_dump(exclude_unset=True, exclude={"force_sample"}))
    except JobParamsError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"message": str(e), "errors": e.errors}) from None
    db["jobs"].insert_one({**base, "params": params, "state": "queued", "priority": 0, "finished_at": None})
    return {"job_id": job_id, "state": "queued", "source": "model",
            "note": "đã giao worker: chạy PhaseGNN từ checkpoint, ghi `model_forecasts`; "
                    "thiếu checkpoint/phasegnn → việc `skipped` kèm lý do (xem GET /jobs/{job_id})"}


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
                           "requested_by": user.actor, "worker": None, "fence": None,
                           "heartbeat": None, "created_at": datetime.now(timezone.utc)})
    return {"job_id": job_id, "model_version": ver, "state": "queued",
            "note": "đã giao cho worker; apps/api không import phasegnn theo luật phụ thuộc"}


@router.post("/models/{version}/promote", dependencies=[Depends(require(P.MANAGE_MODEL, MODELS)),
                                                        Depends(rate_limit(scope="job"))])
async def promote_model(version: str,
                        user: UserContext = Depends(require(P.MANAGE_MODEL, MODELS))):
    """Chuyển một phiên bản mô hình thành bản đang phục vụ (promote).

    Theo registry `phasegnn/registry.py` (`ModelRecord`): ``version`` là số nguyên (tham số đường dẫn
    ``3``/``v3`` được ép kiểu; không phải số → 422), trạng thái ở trường ``status``
    (``staging|production|archived``). Bản được promote → ``status="production"``; bản production khác →
    ``archived``. Worker (`forecast_run`) chỉ chọn bản ``production``. Tăng `ver:models` để cache dự báo cũ
    bị vô hiệu.
    """
    from vngis.job_params import parse_model_version

    try:
        ver = parse_model_version(version)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
    if ver is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="thiếu phiên bản mô hình")
    db = state.db
    doc = db[MODELS].find_one({"version": ver})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có mô hình phiên bản {ver!r}")
    now = datetime.now(timezone.utc)
    actor = user.actor
    db[MODELS].update_one({"_id": doc["_id"]},
                          {"$set": {"status": "production", "promoted_at": now, "promoted_by": actor}})
    db[MODELS].update_many({"_id": {"$ne": doc["_id"]}, "status": "production"},
                           {"$set": {"status": "archived"}})
    try:
        state.cache.bump_version("models")
        state.cache.bump_version("forecasts")
    except Exception as e:  # noqa: BLE001
        log.warning("không bump được ver: %s", e)
    return {"model_version": ver, "status": "production", "state": "production",
            "promoted_at": to_vn_datetime(now), "promoted_by": actor,
            "promoted_by_label": actor_labels([actor]).get(actor)}
