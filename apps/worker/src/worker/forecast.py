"""Chạy dự báo PhaseGNN từ checkpoint và ghi `model_forecasts` (job `forecast_run`).

**Luồng.**

1. Tìm checkpoint (`resolve_checkpoint`): biến môi trường ``FORECAST_CHECKPOINT`` → mô hình
   ``status="production"`` trong `models` (đúng registry `phasegnn/registry.py`; ``model_version`` của job ép
   về số nguyên và chỉ chọn được bản đang production) có ``checkpoint_path``/``artifact_path`` → mặc định
   ``results/E2/phasegnn_long/checkpoint_seed0.pt`` (đường dẫn tương đối tính từ gốc repo). Mọi đường dẫn
   được ``resolve()`` và phải nằm dưới ``<repo>/results`` (hoặc ``FORECAST_CHECKPOINT_DIR``), đuôi ``.pt``;
   checkpoint nạp bằng ``torch.load(weights_only=True)``. File ``*.pt`` bị gitignore: chỉ có trên máy đã
   huấn luyện. Không có → job ``skipped`` (lý do rõ).
2. Dự báo (`phasegnn_predictor`): **import `phasegnn`/`torch` lười** (image worker có thể không có torch;
   không có → ``skipped``). Nạp G5 + đồ thị, dựng mẫu như `phasegnn.forecast` /
   `packages/phasegnn/scripts/evaluate_checkpoint.py`, dự báo cho **ngày phát bản tin mới nhất có mẫu
   ≤ ngày yêu cầu** (dữ liệu G5 có ngày cuối cố định ⇒ ``run_date`` là ngày của dữ liệu, không phải ngày chạy).
3. Gom bản ghi theo `schemas/prediction/v1.schema.json` (mỗi (trạm, ngày, lead) 2 đỉnh) thành một document
   mỗi (``run_date``, ``station``, ``lead_day``) (`group_records`).
4. Ghi `model_forecasts` (`write_model_forecasts`): ``guard.check()`` trước khi ghi; upsert theo ``_id``
   tất định ``{run_date}:{station}:{lead_day}`` với điều kiện fence ``fence của bản ghi ≤ fence của lượt``
   (bản ghi của lượt mới hơn → upsert trùng ``_id`` → bị từ chối, đếm ``fence_rejected``); writeConcern
   majority. Chạy lại cùng ngày → ghi đè cùng document (idempotent).

Validator/index của `model_forecasts`: `MODEL_FORECASTS_VALIDATOR` (bản trong
`schemas/mongo/model_forecasts/v1.json`, test kiểm tra hai bản khớp) và migration
`migrations/mongo/0007_model_forecasts.py`; worker tự tạo nếu chưa có (`ensure_model_forecasts`).
"""
from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vngis.job_params import parse_model_version

log = logging.getLogger("worker.forecast")

MODEL_FORECASTS = "model_forecasts"
SCHEMA_VERSION = 1
#: Checkpoint mặc định (tương đối gốc repo).
DEFAULT_CHECKPOINT = "results/E2/phasegnn_long/checkpoint_seed0.pt"

_NUM = {"bsonType": ["double", "int", "long", "decimal"]}
_NUM_OR_NULL = {"bsonType": ["double", "int", "long", "decimal", "null"]}
_DAY = {"bsonType": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}

#: Validator `$jsonSchema` (đồng bộ với `schemas/mongo/model_forecasts/v1.json`).
MODEL_FORECASTS_VALIDATOR: dict = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "run_date", "station", "lead_day", "target_date", "model", "peaks", "source",
                 "fence", "updated_at", "schema_version"],
    "properties": {
        "_id": {"bsonType": "string"},
        "run_date": _DAY,
        "station": {"bsonType": "string"},
        "lead_day": {"bsonType": ["int", "long"], "minimum": 1},
        "target_date": _DAY,
        "model": {"bsonType": "string"},
        "model_version": {"bsonType": ["string", "null"]},
        "checkpoint": {"bsonType": ["string", "null"]},
        "source": {"enum": ["phasegnn"]},
        "peaks": {"bsonType": "array", "minItems": 1, "items": {
            "bsonType": "object", "required": ["peak_idx", "h_q10", "h_q50", "h_q90"],
            "properties": {"peak_idx": {"bsonType": ["int", "long"], "minimum": 0},
                           "h_q10": _NUM, "h_q50": _NUM, "h_q90": _NUM,
                           "t_peak": {"bsonType": ["date", "null"]}}}},
        "h_max_q50": _NUM_OR_NULL,
        "job_id": {"bsonType": ["string", "null"]},
        "fence": {"bsonType": ["int", "long"]},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "schema_version": {"bsonType": ["int", "long"], "minimum": 1},
    }}}

MODEL_FORECASTS_INDEXES: list[dict] = [
    {"name": "run_station_lead_unique", "key": {"run_date": 1, "station": 1, "lead_day": 1}, "unique": True},
    {"name": "station_run_idx", "key": {"station": 1, "run_date": -1}},
    {"name": "target_station_idx", "key": {"target_date": 1, "station": 1}},
]


class ForecastSkipped(Exception):
    """Không chạy được dự báo vì thiếu điều kiện (checkpoint, phasegnn, dữ liệu) — không phải lỗi."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ checkpoint ----
#: Biến môi trường đặt thư mục được phép chứa checkpoint (mặc định ``<repo>/results``).
CHECKPOINT_DIR_ENV = "FORECAST_CHECKPOINT_DIR"
#: Trạng thái registry của bản đang phục vụ (`phasegnn/registry.py`: ``staging|production|archived``).
PRODUCTION_STATUS = "production"


class UnsafeCheckpoint(ValueError):
    """Đường dẫn checkpoint nằm ngoài thư mục cho phép hoặc không phải ``*.pt``."""


def checkpoint_dir(root: Path) -> Path:
    """Thư mục duy nhất được phép chứa checkpoint (đã ``resolve()``)."""
    env = os.getenv(CHECKPOINT_DIR_ENV)
    d = Path(env) if env else Path(root) / "results"
    if not d.is_absolute():
        d = Path(root) / d
    return d.resolve()


def safe_checkpoint_path(raw: str | os.PathLike, root: Path) -> Path:
    """Chuẩn hoá đường dẫn checkpoint và bắt buộc nằm dưới `checkpoint_dir(root)`, đuôi ``.pt``.

    Đường dẫn tương đối tính từ gốc repo. ``resolve()`` trước khi so (chặn ``..`` và symlink trỏ ra ngoài);
    đường dẫn tuyệt đối chỉ hợp lệ khi vẫn nằm trong thư mục cho phép. Lý do: checkpoint được nạp bằng
    ``torch.load`` — giá trị lấy từ `models` (DB) hoặc tham số job không được trỏ tới tệp tùy ý.
    """
    q = Path(str(raw))
    p = (q if q.is_absolute() else Path(root) / q).resolve()
    base = checkpoint_dir(root)
    if p.suffix.lower() != ".pt":
        raise UnsafeCheckpoint(f"checkpoint phải có đuôi .pt: {q.name!r}")
    try:
        p.relative_to(base)
    except ValueError:
        raise UnsafeCheckpoint(f"checkpoint {q.name!r} nằm ngoài thư mục cho phép "
                               f"({CHECKPOINT_DIR_ENV} hoặc <repo>/results)") from None
    return p


def resolve_checkpoint(db, root: Path, model_version: Any = None) -> tuple[Path | None, dict]:
    """Tìm checkpoint; trả ``(đường dẫn | None, thông tin)`` — ``info["tried"]`` liệt kê nơi đã dò.

    Tra `models` theo registry (`phasegnn/registry.py`): chỉ bản ghi ``status="production"``; có
    ``model_version`` (ép về số nguyên) thì lấy đúng phiên bản đó **nếu nó đang production**, không thì bản
    production có ``version`` lớn nhất. Mọi đường dẫn (env, DB, mặc định) qua `safe_checkpoint_path`.
    """
    tried: list[str] = []
    info: dict[str, Any] = {"model_version": None if model_version is None else str(model_version)}

    def _check(raw: str, label: str) -> Path | None:
        tried.append(label)
        try:
            p = safe_checkpoint_path(raw, root)
        except UnsafeCheckpoint as e:
            tried[-1] = f"{label} (bị từ chối: {e})"
            log.warning("bỏ qua checkpoint %s: %s", label, e)
            return None
        return p if p.is_file() else None

    env = os.getenv("FORECAST_CHECKPOINT")
    if env:
        p = _check(env, "FORECAST_CHECKPOINT")
        if p is not None:
            return p, {**info, "origin": "env", "tried": tried}
    try:
        ver = parse_model_version(model_version)
    except ValueError as e:
        tried.append(str(e))
        return None, {**info, "tried": tried}
    flt: dict[str, Any] = {"status": PRODUCTION_STATUS}
    if ver is not None:
        flt["version"] = ver
    try:
        model = (db["models"].find_one(flt, sort=[("version", -1)])
                 if "models" in db.list_collection_names() else None)
    except Exception:  # noqa: BLE001 - không đọc được `models` thì dò tiếp
        model = None
    if model is not None:
        mv = model.get("version", ver)
        info["model_version"] = None if mv is None else str(mv)
        ck = model.get("checkpoint_path") or model.get("artifact_path")
        if ck:
            p = _check(str(ck), f"models[{info['model_version']}].checkpoint_path")
            if p is not None:
                return p, {**info, "origin": "models", "tried": tried}
    elif ver is not None:
        # phiên bản yêu cầu không có hoặc không ở trạng thái production → không chạy bản khác thay thế
        tried.append(f"models[{ver}] (không có bản production)")
        return None, {**info, "tried": tried}
    p = _check(DEFAULT_CHECKPOINT, DEFAULT_CHECKPOINT)
    if p is not None:
        return p, {**info, "origin": "default", "tried": tried}
    return None, {**info, "tried": tried}


def phasegnn_available() -> tuple[bool, str]:
    """Có import được `phasegnn` và `torch` không (không import thật — tránh nạp torch khi không cần)."""
    for mod in ("torch", "phasegnn"):
        try:
            if importlib.util.find_spec(mod) is None:
                return False, f"không có module {mod!r} trong image worker"
        except (ImportError, ValueError) as e:
            return False, f"không dò được module {mod!r}: {e}"
    return True, ""


# ------------------------------------------------------------------ dự báo --------
def phasegnn_predictor(checkpoint: Path, *, run_date: str | None = None, stations: list[str] | None = None,
                       data_root: str | None = None) -> dict:
    """Dự báo bằng PhaseGNN (import lười). Trả ``{run_date, model, records}``.

    Cách nạp/dự báo giống `packages/phasegnn/scripts/evaluate_checkpoint.py`: `load_g5`, `load_graph`,
    `load_checkpoint`, `build_datasets` (scaler fit trên train), rồi `predict_split` trên các mẫu của đúng
    một ngày phát bản tin.
    """
    from phasegnn.data import build_datasets, default_paths, load_g5, load_graph
    from phasegnn.forecast import load_checkpoint, predict_split
    from phasegnn.model import seed_everything

    paths = default_paths(Path(data_root) if data_root else None)
    df = load_g5(paths=paths)
    graph = load_graph(paths=paths)
    model, tcfg, _ = load_checkpoint(Path(checkpoint))
    seed_everything(tcfg.seed)
    datasets = build_datasets(df, t_in=tcfg.t_in, t_out=tcfg.t_out,
                              holdout_stations=tuple(tcfg.holdout_stations or ()))
    want = set(stations or [])
    best: tuple[str, str] | None = None  # (ngày, tập)
    for split, ds in datasets.items():
        for s in ds.samples:
            if want and s.station not in want:
                continue
            if run_date and s.issue_date > run_date:
                continue
            if best is None or s.issue_date > best[0]:
                best = (s.issue_date, split)
    name = f"{tcfg.variant}-v1"
    if best is None:
        return {"run_date": run_date, "model": name, "records": []}
    day, split = best
    ds = datasets[split]
    ds = replace(ds, samples=[s for s in ds.samples if s.issue_date == day and (not want or s.station in want)])
    records = predict_split(model, tcfg, graph, ds, model_name=name)
    return {"run_date": day, "model": name, "records": records, "split": split}


def _parse_ts(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def doc_id(run_date: str, station: str, lead_day: int) -> str:
    return f"{run_date}:{station}:{int(lead_day)}"


def group_records(records: list[dict], *, run_date: str, model: str, model_version: str | None = None,
                  checkpoint: str | None = None) -> list[dict]:
    """Bản ghi dự báo (2 đỉnh/ngày) → một document mỗi (run_date, station, lead_day)."""
    out: dict[tuple[str, int], dict] = {}
    for r in records:
        st, lead = str(r["station"]), int(r["lead_day"])
        d = out.setdefault((st, lead), {
            "_id": doc_id(run_date, st, lead), "run_date": run_date, "station": st, "lead_day": lead,
            "target_date": str(r.get("target_date"))[:10], "model": str(r.get("model") or model),
            "model_version": model_version, "checkpoint": checkpoint, "source": "phasegnn", "peaks": [],
            "schema_version": SCHEMA_VERSION})
        d["peaks"].append({"peak_idx": int(r.get("peak_idx", len(d["peaks"]))),
                           "h_q10": float(r["h_q10"]), "h_q50": float(r["h_q50"]), "h_q90": float(r["h_q90"]),
                           "t_peak": _parse_ts(r.get("t_peak"))})
    docs = []
    for d in out.values():
        d["peaks"].sort(key=lambda p: p["peak_idx"])
        d["h_max_q50"] = max(p["h_q50"] for p in d["peaks"])
        docs.append(d)
    docs.sort(key=lambda d: (d["station"], d["lead_day"]))
    return docs


# ------------------------------------------------------------------ ghi ----------
def ensure_model_forecasts(db) -> None:
    """Tạo `model_forecasts` (validator, `validationLevel: moderate`) + index nếu chưa có. Idempotent."""
    try:
        if MODEL_FORECASTS not in db.list_collection_names():
            db.create_collection(MODEL_FORECASTS, validator=MODEL_FORECASTS_VALIDATOR,
                                 validationLevel="moderate", validationAction="error")
    except Exception as e:  # noqa: BLE001 - tạo song song / mongomock không hỗ trợ validator
        log.debug("không tạo được %s kèm validator: %s", MODEL_FORECASTS, e)
    coll = db[MODEL_FORECASTS]
    for ix in MODEL_FORECASTS_INDEXES:
        try:
            coll.create_index(list(ix["key"].items()), name=ix["name"], unique=bool(ix.get("unique")))
        except Exception as e:  # noqa: BLE001 - thiếu quyền tạo index không chặn ghi
            log.warning("không tạo được index %s: %s", ix["name"], e)


def write_model_forecasts(db, docs: list[dict], *, fence: int, job_id: str | None = None, guard=None) -> dict:
    """Upsert có fence (``fence`` của bản ghi ≤ ``fence``), writeConcern majority. Trả số đếm."""
    from pymongo import WriteConcern
    from pymongo.errors import DuplicateKeyError

    if guard is not None:
        guard.check()
    ensure_model_forecasts(db)
    coll = db.get_collection(MODEL_FORECASTS, write_concern=WriteConcern("majority"))
    now = _now()
    out = {"n_docs": len(docs), "upserted": 0, "modified": 0, "matched": 0, "fence_rejected": 0}
    for d in docs:
        if guard is not None and guard.lost:
            guard.check()
        body = {k: v for k, v in d.items() if k != "_id"}
        body.update({"fence": int(fence), "job_id": job_id, "updated_at": now})
        flt = {"_id": d["_id"], "$or": [{"fence": {"$exists": False}}, {"fence": {"$lte": int(fence)}}]}
        try:
            res = coll.update_one(flt, {"$set": body, "$setOnInsert": {"created_at": now}}, upsert=True)
        except DuplicateKeyError:
            # bản ghi đã có với fence lớn hơn (lượt mới hơn) → upsert trùng `_id` → từ chối
            out["fence_rejected"] += 1
            continue
        if res.upserted_id is not None:
            out["upserted"] += 1
        else:
            out["matched"] += res.matched_count
            out["modified"] += res.modified_count
    if out["fence_rejected"]:
        log.warning("%d dự báo bị từ chối do fencing (lượt mới hơn đã ghi)", out["fence_rejected"])
    return out


Predictor = Callable[..., dict]


def run_forecast(db, cache=None, *, root: Path, station: str | None = None, basins: list[str] | None = None,
                 issued_at: str | None = None, model_version: str | None = None, guard=None,
                 job_id: str | None = None, predictor: Predictor | None = None,
                 data_root: str | None = None) -> dict:
    """Toàn bộ job: checkpoint → dự báo → ghi. Thiếu điều kiện → ``{"status": "skipped", "reason": …}``."""
    run_date = str(issued_at)[:10] if issued_at else None
    ckpt, info = resolve_checkpoint(db, root, model_version)
    base = {"basins": list(basins or []), "station": station, "requested_run_date": run_date}
    if ckpt is None:
        return {"status": "skipped", **base,
                "reason": "không có checkpoint PhaseGNN (đã dò: " + ", ".join(info["tried"]) + ")"}
    if predictor is None:
        ok, why = phasegnn_available()
        if not ok:
            return {"status": "skipped", **base, "reason": f"không chạy được PhaseGNN: {why}"}
        predictor = phasegnn_predictor
    try:
        rel = str(ckpt.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        rel = ckpt.name  # không ghi đường dẫn tuyệt đối của máy vào DB
    if guard is not None:
        guard.check()
    pred = predictor(ckpt, run_date=run_date, stations=[station] if station else None, data_root=data_root)
    records = list(pred.get("records") or [])
    if not records:
        return {"status": "skipped", **base, "checkpoint": rel,
                "reason": f"không có mẫu dữ liệu G5 cho ngày ≤ {run_date or 'mới nhất'}"
                          + (f" (trạm {station})" if station else "")}
    day = str(pred.get("run_date"))[:10]
    docs = group_records(records, run_date=day, model=str(pred.get("model") or "phasegnn"),
                         model_version=info.get("model_version"), checkpoint=rel)
    fence = int(getattr(guard, "fence", 0) or 0)
    counts = write_model_forecasts(db, docs, fence=fence, job_id=job_id, guard=guard)
    if cache is not None:
        for name in (MODEL_FORECASTS, "forecasts"):
            try:
                cache.bump_version(name)
            except Exception as e:  # noqa: BLE001
                log.warning("không tăng được ver:%s: %s", name, e)
    return {"status": "ok", **base, "run_date": day, "model": docs[0]["model"] if docs else None,
            "model_version": info.get("model_version"), "checkpoint": rel, "checkpoint_origin": info.get("origin"),
            "collection": MODEL_FORECASTS, "fence": fence, "n_records": len(records),
            "stations": sorted({d["station"] for d in docs}), **counts}
