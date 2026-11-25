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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from api.deps import UserContext, require, rate_limit
from api.permissions import P
from api.router.common import (acquire_guard, actor_labels, clean, guard_check, to_vn_date,
                               to_vn_datetime)
from api.state import state
from vngis.job_params import MAX_TRAIN_EPOCHS, TRAIN_SAMPLE_PARAMS

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
    # cùng mô hình với checkpoint mặc định của worker (`spread` 9 trạm, đồ thị con v2, seed 0)
    "E2/phasegnn_spread_v2/test_full_seed0/predictions_test.csv",
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
    """Yêu cầu huấn luyện mô hình (bỏ trống `model_version` → hệ thống cấp số nguyên mới).

    Khoá lạ → 422 (``extra="forbid"``). Bỏ trống ``max_train_samples``/``max_val_samples`` → toàn bộ dữ liệu
    (~19,6 nghìn mẫu train, ~25 phút/epoch trên CPU); cấu hình chắc chắn vượt ``TRAIN_TIMEOUT_S`` → 422.
    """
    model_config = ConfigDict(extra="forbid")

    model_version: int | str | None = Field(default=None, description="chỉ để hỏi lại phiên bản đã có")
    epochs: int = Field(default=50, ge=1, le=MAX_TRAIN_EPOCHS)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    station: str | None = None
    basin: str | None = None
    max_train_samples: int | None = Field(default=None, ge=100, le=50_000,
                                          description="giới hạn số mẫu train (bỏ trống = toàn bộ)")
    max_val_samples: int | None = Field(default=None, ge=50, le=50_000,
                                        description="giới hạn số mẫu val (bỏ trống = toàn bộ)")
    subsample: Literal["spread", "head"] = Field(default="spread",
                                                 description="cách chọn mẫu khi giới hạn: spread (đều) | head")
    device: Literal["auto", "cpu", "cuda"] | None = Field(
        default=None, description="thiết bị huấn luyện; bỏ trống → PGNN_TRAIN_DEVICE (không có → auto). "
                                  "Không lưu vào hyperparams: giao lại dùng mặc định trừ khi gửi kèm")


#: Khoá tham số huấn luyện lưu trong `models.hyperparams` (giao lại dùng lại đúng cấu hình).
_HYPERPARAM_KEYS = ("epochs", "learning_rate", *TRAIN_SAMPLE_PARAMS)


def _check_train_budget(params: dict) -> dict:
    """Ước lượng thời gian (`vngis.job_params.estimate_train_seconds`); chắc chắn vượt timeout → 422."""
    from vngis.job_params import estimate_train_seconds

    est = estimate_train_seconds(params)
    if est["verdict"] == "exceeds":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "error": "train_exceeds_timeout", "estimate": est,
            "reason": "ước lượng ngay cả khi dừng sớm (patience) vẫn vượt TRAIN_TIMEOUT_S; giảm epochs hoặc đặt "
                      "max_train_samples/max_val_samples"})
    return est


def _budget_note(est: dict) -> dict:
    out: dict[str, Any] = {"estimate": est}
    if est["verdict"] == "may_exceed":
        out["warning"] = (f"ước lượng {est['min_s']}–{est['max_s']} s có thể vượt TRAIN_TIMEOUT_S="
                          f"{est['timeout_s']} s nếu không dừng sớm; cân nhắc giới hạn số mẫu")
    return out


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


#: Trường `models` trả cho giao diện (không trả `checkpoint_path`, `hyperparams` đầy đủ, `train_config`).
_MODEL_FIELDS = ("version", "name", "variant", "status", "train_state", "train_reason", "train_error", "job_id",
                 "metrics", "requested_by", "created_at", "updated_at", "train_started_at", "train_finished_at",
                 "train_heartbeat", "promoted_at", "promoted_by", "promote_seq", "archived_at")
_MODEL_TIME_FIELDS = ("created_at", "updated_at", "train_started_at", "train_finished_at", "train_heartbeat",
                      "promoted_at", "archived_at")


@router.get("/models", dependencies=[Depends(require(P.READ, MODELS))])
async def list_models(limit: int = Query(100, ge=1, le=500)):
    """Danh sách phiên bản mô hình (registry `models`, ``version`` int), mới nhất trước.

    Kèm ``train_state``/``train_reason``/``train_error`` (worker `train`) và ``status``; ``promotable`` = đã
    huấn luyện xong (``train_state`` thiếu — bản ghi cũ — hoặc ``succeeded``) và chưa phục vụ.
    """
    from vngis.model_registry import PRODUCTION, find_production

    db = state.db

    def _load() -> tuple[list[dict], Any]:
        docs = list(db[MODELS].find({"version": {"$type": "number"}}, {k: 1 for k in _MODEL_FIELDS},
                                    sort=[("version", -1)], limit=limit))
        top = find_production(db, projection={"_id": 1})
        return docs, (top or {}).get("_id")

    docs, serving = await asyncio.to_thread(_load)
    actors = actor_labels([d.get("requested_by") for d in docs] + [d.get("promoted_by") for d in docs])
    items = []
    for d in docs:
        it = {k: d.get(k) for k in _MODEL_FIELDS if k in d}
        it["id"] = str(d["_id"])
        for k in _MODEL_TIME_FIELDS:
            if it.get(k) is not None:
                it[k] = to_vn_datetime(it[k])
        it["serving"] = d["_id"] == serving
        it["promotable"] = d.get("train_state") in (None, "succeeded") and not (
            d.get("status") == PRODUCTION and it["serving"])
        it["requested_by_label"] = actors.get(d.get("requested_by"))
        it["promoted_by_label"] = actors.get(d.get("promoted_by"))
        items.append(clean(it))
    return {"items": items, "count": len(items)}


#: Trạng thái huấn luyện được giao lại qua `POST /models/train` với ``model_version=n``.
_RETRYABLE_TRAIN_STATES = ("failed", "skipped")
#: Việc ``train`` còn hiệu lực (chưa kết thúc) — có thì không giao thêm.
_ACTIVE_JOB_STATES = ("queued", "claimed", "running")


def _train_stale_s() -> float:
    """``train_state=running`` không có heartbeat quá ngưỡng này (và không còn việc hiệu lực) → coi là kẹt."""
    try:
        return max(float(os.environ.get("PGNN_TRAIN_STALE_S", "2700")), 60.0)
    except ValueError:
        return 2700.0


def _train_job_doc(job_id: str, ver: int, params: dict, actor: str, now: datetime, attempt: int) -> dict:
    return {"_id": job_id, "type": "train", "state": "queued", "priority": 0, "params": params,
            "model_version": ver, "train_attempt": attempt, "requested_by": actor, "worker": None, "fence": None,
            "heartbeat": None, "created_at": now, "updated_at": now, "finished_at": None}


def _insert_train_job(db, job: dict, model_id: str, now: datetime) -> None:
    """Chèn việc ``train``; lỗi → đánh bản ghi `models` ``failed`` kèm lý do (không để ``queued`` mồ côi)."""
    try:
        db["jobs"].insert_one(job)
    except Exception as e:  # noqa: BLE001 - mọi lỗi ghi: dọn trạng thái rồi báo 503
        db[MODELS].update_one({"_id": model_id, "job_id": job["_id"], "train_state": "queued"},
                              {"$set": {"train_state": "failed", "updated_at": now,
                                        "train_error": f"không giao được việc huấn luyện: {e}"[:500]}})
        log.warning("không chèn được việc %s: %s", job["_id"], e)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"error": "train_enqueue_failed", "model_id": model_id,
                                    "reason": "không giao được việc huấn luyện; bản ghi models đánh failed"}) from None


@router.post("/models/train", dependencies=[Depends(require(P.MANAGE_MODEL, MODELS)),
                                            Depends(rate_limit(scope="job"))])
async def train_model(body: TrainRequest,
                      user: UserContext = Depends(require(P.MANAGE_MODEL, MODELS))):
    """Yêu cầu huấn luyện mô hình — giao cho `apps/worker` (luật: `apps/api` không import `phasegnn`).

    **BREAKING (so với ``v<timestamp>``):** ``version`` là **số nguyên** do hệ thống cấp nguyên tử (bộ đếm
    `counters._id="models.version"`, ``$inc``); phản hồi ``model_version`` kiểu int. API ghi trước bản ghi
    `models` ``{_id: "phasegnn-<n>", version: n, status: "staging", train_state: "queued", job_id,
    train_attempt: 1}`` rồi giao việc ``train`` (``_id: "train:<n>"``); chèn việc lỗi → bản ghi `models` đánh
    ``failed`` (``train_error``) và trả 503.

    ``model_version=n`` (``3``/``"v3"``) không tự đặt số mới (chưa có → 404, không phải số → 422):

    - bản ghi ``train_state`` ∈ ``failed|skipped``, hoặc ``running`` kẹt (không còn việc ``train`` hiệu lực và
      ``train_heartbeat`` cũ hơn ``PGNN_TRAIN_STALE_S``, mặc định 2700 s) → **giao lại**: ``train_attempt += 1``
      (nguyên tử, có điều kiện trạng thái), việc mới ``train:<n>:<attempt>`` (không trùng ``_id``), tham số =
      ``hyperparams`` cũ + trường gửi kèm;
    - còn lại (``queued``/``running`` đang chạy/``succeeded``, bản không phải staging) → trả bản ghi hiện có
      (``created: false``).
    """
    from vngis.job_params import JobParamsError, parse_model_version, validate_job_params
    from vngis.model_registry import STAGING, allocate_model_version, model_doc_id

    db = state.db
    actor = user.actor
    overrides = body.model_dump(exclude_unset=True, exclude={"model_version"})
    if body.model_version not in (None, ""):
        try:
            asked = parse_model_version(body.model_version)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
        existing = db[MODELS].find_one({"version": asked})
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail={"error": "model_version_not_found", "model_version": asked,
                                        "reason": "phiên bản do hệ thống cấp; bỏ trống model_version để "
                                                  "huấn luyện phiên bản mới"})
        return await asyncio.to_thread(_retry_or_report, db, existing, asked, overrides, actor)
    try:
        params = validate_job_params("train", overrides)
    except JobParamsError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"message": str(e), "errors": e.errors}) from None
    est = _check_train_budget(params)
    ver = allocate_model_version(db)
    params["model_version"] = ver
    now = datetime.now(timezone.utc)
    job_id = f"train:{ver}"
    model_id = model_doc_id("phasegnn", ver)
    db[MODELS].insert_one({"_id": model_id, "model_id": model_id,
                           "name": "phasegnn", "variant": "phasegnn", "version": ver, "status": STAGING,
                           "train_state": "queued", "job_id": job_id, "train_attempt": 1, "requested_by": actor,
                           "hyperparams": {k: params[k] for k in _HYPERPARAM_KEYS if k in params},
                           "created_at": now, "updated_at": now})
    _insert_train_job(db, _train_job_doc(job_id, ver, params, actor, now, 1), model_id, now)
    return {"job_id": job_id, "model_version": ver, "status": STAGING, "state": "queued", "created": True,
            "note": "đã giao cho worker; apps/api không import phasegnn theo luật phụ thuộc", **_budget_note(est)}


def _existing_reply(doc: dict, ver: int, note: str) -> dict:
    return {"model_version": ver, "status": doc.get("status"), "created": False,
            "state": doc.get("train_state") or doc.get("status") or "unknown",
            "job_id": doc.get("job_id"), "note": note}


def _aware(ts: Any) -> Any:
    if ts is not None and getattr(ts, "tzinfo", None) is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _retry_or_report(db, existing: dict, ver: int, overrides: dict, actor: str) -> dict:
    """`POST /models/train` với ``model_version``: giao lại khi được phép, không thì trả bản ghi hiện có."""
    from vngis.job_params import JobParamsError, validate_job_params
    from vngis.model_registry import STAGING

    ts = existing.get("train_state")
    active = db["jobs"].find_one({"type": "train", "model_version": ver,
                                  "state": {"$in": list(_ACTIVE_JOB_STATES)}}, {"_id": 1, "state": 1})
    if active is not None:
        return _existing_reply(existing, ver, f"đang có việc huấn luyện {active['_id']}")
    if existing.get("status") != STAGING or not (ts in _RETRYABLE_TRAIN_STATES or ts == "running"):
        return _existing_reply(existing, ver, "phiên bản đã tồn tại")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=_train_stale_s())
    cond: dict[str, Any] = {"_id": existing["_id"], "status": STAGING, "train_state": ts}
    if ts == "running":
        hb = _aware(existing.get("train_heartbeat") or existing.get("train_started_at")
                    or existing.get("updated_at"))
        if hb is not None and hb >= cutoff:
            return _existing_reply(existing, ver, "đang huấn luyện (heartbeat còn mới)")
        cond["$or"] = [{"train_heartbeat": None}, {"train_heartbeat": {"$lt": cutoff}}]
    base = {k: v for k, v in (existing.get("hyperparams") or {}).items() if k in _HYPERPARAM_KEYS}
    try:
        params = validate_job_params("train", {**base, **overrides})
    except JobParamsError as e:
        # Lỗi nằm ở `hyperparams` cũ (không gửi kèm), ví dụ bản ghi từ trước khi siết ``epochs`` ≤ 500: không
        # tự kẹp (đổi cấu hình của phiên bản mà người dùng không biết) — 422 nêu rõ trường cần gửi giá trị mới.
        stale = sorted({str(x["loc"][1]) for x in e.errors
                        if len(x.get("loc") or ()) > 1 and str(x["loc"][1]) in base
                        and str(x["loc"][1]) not in overrides})
        if stale:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "error": "legacy_hyperparams_invalid", "model_version": ver, "fields": stale,
                "legacy_values": {k: base[k] for k in stale}, "limits": {"epochs": {"max": MAX_TRAIN_EPOCHS}},
                "message": str(e),
                "reason": f"hyperparams đã lưu của phiên bản {ver} không còn hợp lệ ({', '.join(stale)}); gửi kèm "
                          f"giá trị mới khi giao lại, ví dụ {{\"model_version\": {ver}, \"epochs\": "
                          f"{MAX_TRAIN_EPOCHS}}}"}) from None
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"message": str(e), "errors": e.errors}) from None
    est = _check_train_budget(params)
    params["model_version"] = ver
    # Bản ghi cũ chưa có `train_attempt` (lượt đầu = 1) → lượt giao lại đầu tiên là 2.
    if existing.get("train_attempt") is None:
        db[MODELS].update_one({"_id": existing["_id"], "train_attempt": {"$exists": False}},
                              {"$set": {"train_attempt": 1}})
    # `train_attempt` cấp nguyên tử + điều kiện trạng thái: hai request đồng thời không cùng giao lại.
    doc = db[MODELS].find_one_and_update(
        cond, {"$inc": {"train_attempt": 1},
               "$set": {"train_state": "queued", "updated_at": now, "train_error": None, "train_reason": None,
                        "retried_by": actor,
                        "hyperparams": {k: params[k] for k in _HYPERPARAM_KEYS if k in params}}},
        return_document=True)
    if doc is None:
        cur = db[MODELS].find_one({"_id": existing["_id"]}) or existing
        return _existing_reply(cur, ver, "trạng thái vừa đổi; không giao lại")
    attempt = int(doc.get("train_attempt") or 2)
    job_id = f"train:{ver}:{attempt}"
    db[MODELS].update_one({"_id": existing["_id"], "train_attempt": attempt}, {"$set": {"job_id": job_id}})
    _insert_train_job(db, _train_job_doc(job_id, ver, params, actor, now, attempt), existing["_id"], now)
    return {"job_id": job_id, "model_version": ver, "status": STAGING, "state": "queued", "created": True,
            "retry": True, "train_attempt": attempt, "previous_train_state": ts,
            "note": f"giao lại huấn luyện (lượt {attempt}, trước đó train_state={ts})", **_budget_note(est)}


@router.post("/models/{version}/promote", dependencies=[Depends(require(P.MANAGE_MODEL, MODELS)),
                                                        Depends(rate_limit(scope="job"))])
async def promote_model(version: str,
                        user: UserContext = Depends(require(P.MANAGE_MODEL, MODELS))):
    """Chuyển một phiên bản mô hình thành bản đang phục vụ (promote).

    Theo registry `phasegnn/registry.py` (`ModelRecord`): ``version`` là số nguyên (tham số đường dẫn
    ``3``/``v3`` được ép kiểu; không phải số → 422), trạng thái ở trường ``status``
    (``staging|production|archived``). Bản được promote → ``status="production"``; bản production khác →
    ``archived``. Worker (`forecast_run`) chỉ chọn bản ``production`` (lượt promote mới nhất,
    `vngis.model_registry.PRODUCTION_SORT`). Tăng `ver:models` để cache dự báo cũ bị vô hiệu.

    An toàn đồng thời: giữ khoá ``models:promote`` (bận → 423) và ghi qua `vngis.model_registry.promote_model`
    — transaction khi có replica set, không thì thứ tự ghi có điều kiện ``promote_seq`` (tự sửa, không để hai
    bản production tồn tại lâu). Bản đang huấn luyện (``train_state`` queued/running/failed/skipped) → 409.
    """
    from vngis.job_params import parse_model_version
    from vngis.locks import LockGuard
    from vngis.model_registry import PROMOTE_LOCK_RESOURCE, promote_model as _promote

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
    ts = doc.get("train_state")
    if ts is not None and ts != "succeeded":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "model_not_trained", "model_version": ver, "train_state": ts,
                                    "reason": "chỉ promote được bản đã huấn luyện xong (train_state=succeeded)"})
    actor = user.actor
    guard = LockGuard(state.lock, db=db, resource=PROMOTE_LOCK_RESOURCE, ttl_ms=30_000)
    acquire_guard(guard, busy_detail="đang có lượt promote khác chạy")
    try:
        guard_check(guard)
        now = datetime.now(timezone.utc)
        out = await asyncio.to_thread(_promote, db, doc["_id"], actor=actor, now=now)
    finally:
        guard.release()
    try:
        state.cache.bump_version("models")
        state.cache.bump_version("forecasts")
    except Exception as e:  # noqa: BLE001
        log.warning("không bump được ver: %s", e)
    if out.get("not_trained"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "model_not_trained", "model_version": ver,
                                    "train_state": out.get("train_state"),
                                    "reason": "chỉ promote được bản đã huấn luyện xong (train_state=succeeded)"})
    if out.get("superseded"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"error": "promote_superseded", "model_version": ver,
                                    "reason": "một lượt promote mới hơn đã thắng; bản này không phục vụ"})
    return {"model_version": ver, "status": "production", "state": "production",
            "promoted_at": to_vn_datetime(now), "promoted_by": actor,
            "promoted_by_label": actor_labels([actor]).get(actor),
            "archived": [str(x) for x in (out.get("archived") or []) + (out.get("repaired") or [])],
            "transaction": bool(out.get("transaction"))}
