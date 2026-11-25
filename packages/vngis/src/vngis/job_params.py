"""Danh sách trắng tham số cho từng loại việc trong hàng đợi `jobs` (dùng chung API ↔ worker).

`apps/api` (`POST /jobs/{type}`, `/forecasts/run`, `/models/train`) chèn việc ``state="queued"``
kèm `params`; `apps/worker` nhận việc và gọi hàm job. Trước đây worker truyền **mọi** khoá của
`params` trùng tên tham số hàm job ⇒ người dùng API có thể chèn tham số nội bộ (ví dụ
``documents``/``actor`` cho `reload_bulletins`, ``repo_root``/``source_dir`` cho `ingest_bulletins`).

Quy tắc:

* Mỗi loại việc có một model pydantic với ``extra="forbid"``: khoá lạ → lỗi (API trả 422, worker
  đánh dấu việc ``failed``).
* Tham số nội bộ (`INTERNAL_PARAMS`) **không bao giờ** nhận từ hàng đợi: không có trong model nào.
  Người thực hiện (`actor`) lấy từ `requested_by` của bản ghi việc, không từ `params`.

Module chỉ phụ thuộc pydantic (không import pymongo) để API và worker cùng dùng.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = ["JobParamsError", "JOB_PARAM_MODELS", "JOB_TYPES", "INTERNAL_PARAMS",
           "WARMABLE_COLLECTIONS", "validate_job_params", "parse_model_version", "TRAIN_SAMPLE_PARAMS",
           "estimate_train_seconds", "train_timeout_s", "effective_train_device", "train_sec_per_sample",
           "MAX_TRAIN_EPOCHS"]

#: Tham số do hệ thống truyền (không được nhận từ `params` của hàng đợi).
INTERNAL_PARAMS = frozenset({"db", "cache", "guard", "actor", "documents", "repo_root", "source_dir",
                             "run_g0", "run_id", "job_id", "runner", "predictor"})

#: Collection được phép làm nóng cache qua hàng đợi.
WARMABLE_COLLECTIONS = ("stations", "tide_extremes", "reservoir_daily", "hydromet_bulletins")

_CODE = r"^[A-Za-z0-9_.:\-]{1,64}$"


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IngestBulletinsParams(_Params):
    """Nạp bản tin: không có tham số nào từ hàng đợi (thư mục nguồn/gốc repo do cấu hình worker quyết)."""


class ReloadBulletinsParams(_Params):
    """Nạp lại nguyên tử: không nhận `documents`/`actor`/`run_id` từ hàng đợi."""


class ForecastRunParams(_Params):
    """Tham số chạy dự báo (khớp `ForecastRunRequest` của API)."""
    station: str | None = Field(default=None, pattern=_CODE)
    basin: str | None = Field(default=None, pattern=_CODE)
    basins: list[str] | None = Field(default=None, max_length=50)
    horizon_h: int = Field(default=72, ge=1, le=720)
    issued_at: str | None = Field(default=None, max_length=40)
    model_version: str | None = Field(default=None, pattern=_CODE)
    force_sample: bool = False

    @field_validator("basins")
    @classmethod
    def _basins(cls, v: list[str] | None) -> list[str] | None:
        for b in v or []:
            if not re.match(_CODE, b):
                raise ValueError(f"mã lưu vực không hợp lệ: {b!r}")
        return v


#: Trần số epoch của việc ``train`` (dừng sớm `patience` của `phasegnn.train` thường dừng trước đó).
MAX_TRAIN_EPOCHS = 500
#: Các khoá giới hạn mẫu của `TrainParams` (API giữ lại trong ``hyperparams`` để giao lại cùng cấu hình).
TRAIN_SAMPLE_PARAMS = ("max_train_samples", "max_val_samples", "subsample")
#: Ước lượng thô (đo trên CPU của bài thử end-to-end): toàn bộ ≈ 19,6 nghìn mẫu train ≈ 25 phút/epoch.
FULL_TRAIN_SAMPLES = 19_600
FULL_VAL_SAMPLES = 4_200
#: Giây cho một mẫu train trong một epoch (val chỉ suy luận ≈ 1/3); ghi đè bằng ``PGNN_TRAIN_SEC_PER_SAMPLE``.
TRAIN_SEC_PER_SAMPLE = 1500.0 / (FULL_TRAIN_SAMPLES + FULL_VAL_SAMPLES / 3)
#: `patience` mặc định của `phasegnn.train` ⇒ chạy ít nhất ``min(epochs, patience + 1)`` epoch.
TRAIN_PATIENCE = 10
#: Thời gian tối đa của tiến trình huấn luyện (khớp ``TRAIN_TIMEOUT_S`` của worker, mặc định 6 h).
DEFAULT_TRAIN_TIMEOUT_S = 6 * 3600


def train_timeout_s() -> int:
    """``TRAIN_TIMEOUT_S`` (giây) — API và worker cùng đọc một biến."""
    import os

    try:
        return max(60, int(os.getenv("TRAIN_TIMEOUT_S", str(DEFAULT_TRAIN_TIMEOUT_S))))
    except ValueError:
        return DEFAULT_TRAIN_TIMEOUT_S


def estimate_train_seconds(params: dict, *, timeout_s: int | None = None) -> dict:
    """Ước lượng thời gian huấn luyện từ tham số đã kiểm tra (chỉ là ước lượng thô, phụ thuộc máy).

    Trả ``{per_epoch_s, min_s, max_s, timeout_s, verdict}``: ``min_s`` = ``min(epochs, patience+1)`` epoch
    (dừng sớm sớm nhất), ``max_s`` = đủ ``epochs``. ``verdict``: ``exceeds`` (``min_s`` > timeout — chắc chắn
    vượt), ``may_exceed`` (chỉ ``max_s`` vượt), ``ok``. Tốc độ theo ``device`` hiệu lực (`effective_train_device`:
    tham số → ``PGNN_TRAIN_DEVICE`` → ``auto``): ``cuda`` dùng `train_sec_per_sample("cuda")` (≈ CPU/4 theo bench,
    ghi đè ``PGNN_TRAIN_SEC_PER_SAMPLE_CUDA``); ``cpu``/``auto`` dùng tốc độ CPU.
    """
    n_tr = min(int(params.get("max_train_samples") or FULL_TRAIN_SAMPLES), FULL_TRAIN_SAMPLES)
    n_va = min(int(params.get("max_val_samples") or FULL_VAL_SAMPLES), FULL_VAL_SAMPLES)
    epochs = int(params.get("epochs") or 50)
    device = effective_train_device(params.get("device"))
    # ``auto``: không biết worker nhận việc có GPU hay không ⇒ ước lượng theo CPU (thận trọng, như trước khi có
    # ``device``). Cụm chỉ có worker GPU: đặt ``PGNN_TRAIN_DEVICE=cuda`` cho cả API lẫn worker.
    per_epoch = train_sec_per_sample("cuda" if device == "cuda" else "cpu") * (n_tr + n_va / 3)
    lim = int(timeout_s if timeout_s is not None else train_timeout_s())
    lo, hi = per_epoch * min(epochs, TRAIN_PATIENCE + 1), per_epoch * epochs
    verdict = "exceeds" if lo > lim else ("may_exceed" if hi > lim else "ok")
    return {"per_epoch_s": round(per_epoch, 1), "min_s": round(lo), "max_s": round(hi), "timeout_s": lim,
            "train_samples": n_tr, "val_samples": n_va, "device": device, "verdict": verdict}


#: Hệ số tăng tốc GPU so với CPU (bench ``--device cuda`` ≈ CPU/4); ghi đè bằng ``PGNN_TRAIN_SEC_PER_SAMPLE_CUDA``.
CUDA_SPEEDUP = 4.0
TRAIN_DEVICES = ("auto", "cpu", "cuda")


def effective_train_device(device: Any = None) -> str:
    """Thiết bị hiệu lực: tham số việc → ``PGNN_TRAIN_DEVICE`` → ``auto`` (giá trị lạ của biến môi trường → ``auto``)."""
    import os

    dev = str(device or os.getenv("PGNN_TRAIN_DEVICE") or "auto").strip().lower()
    if dev.startswith("cuda:"):
        dev = "cuda"
    return dev if dev in TRAIN_DEVICES else "auto"


def train_sec_per_sample(device: str = "cpu") -> float:
    """Giây/mẫu/epoch: CPU ``PGNN_TRAIN_SEC_PER_SAMPLE``; CUDA ``PGNN_TRAIN_SEC_PER_SAMPLE_CUDA`` (mặc định CPU/4)."""
    import os

    try:
        cpu = float(os.getenv("PGNN_TRAIN_SEC_PER_SAMPLE") or TRAIN_SEC_PER_SAMPLE)
    except ValueError:
        cpu = TRAIN_SEC_PER_SAMPLE
    if device != "cuda":
        return cpu
    try:
        return float(os.getenv("PGNN_TRAIN_SEC_PER_SAMPLE_CUDA") or cpu / CUDA_SPEEDUP)
    except ValueError:
        return cpu / CUDA_SPEEDUP


class TrainParams(_Params):
    """Tham số huấn luyện (khớp `TrainRequest` của API).

    ``model_version`` là số nguyên của registry (`vngis.model_registry`; nhận ``3``/``"3"``/``"v3"``) — API cấp
    số mới nguyên tử khi người dùng bỏ trống.
    """
    model_version: int | None = None
    epochs: int = Field(default=50, ge=1, le=MAX_TRAIN_EPOCHS)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    station: str | None = Field(default=None, pattern=_CODE)
    basin: str | None = Field(default=None, pattern=_CODE)
    #: Giới hạn số mẫu train/val (cờ ``--max-train-samples``/``--max-val-samples`` của `phasegnn.train`);
    #: bỏ trống → toàn bộ (~19,6 nghìn mẫu train, ~25 phút/epoch trên CPU).
    max_train_samples: int | None = Field(default=None, ge=100, le=50_000)
    max_val_samples: int | None = Field(default=None, ge=50, le=50_000)
    #: Cách chọn khi giới hạn mẫu (``--subsample``): ``spread`` lấy đều các trạm/thời gian, ``head`` lấy đầu.
    subsample: Literal["spread", "head"] = "spread"
    #: Thiết bị huấn luyện (cờ ``--device`` của `phasegnn.train`); bỏ trống → biến môi trường
    #: ``PGNN_TRAIN_DEVICE`` của worker, không có thì ``auto`` (GPU nếu image/container có CUDA).
    device: Literal["auto", "cpu", "cuda"] | None = None

    @field_validator("model_version", mode="before")
    @classmethod
    def _version(cls, v: Any) -> int | None:
        ver = parse_model_version(v)
        if ver is not None and ver < 1:
            raise ValueError("model_version phải ≥ 1")
        return ver


class CacheWarmParams(_Params):
    collections: list[Literal["stations", "tide_extremes", "reservoir_daily", "hydromet_bulletins"]] | None =         Field(default=None, max_length=len(WARMABLE_COLLECTIONS))


class NoParams(_Params):
    """Loại việc chưa có tham số."""


#: Loại việc → model tham số. Đây cũng là danh sách loại việc API được phép giao.
JOB_PARAM_MODELS: dict[str, type[_Params]] = {
    "ingest_bulletins": IngestBulletinsParams,
    "reload_bulletins": ReloadBulletinsParams,
    "forecast_run": ForecastRunParams,
    "train": TrainParams,
    "cache_warm": CacheWarmParams,
    "build_features": NoParams,
    "qc_review": NoParams,
}
JOB_TYPES = frozenset(JOB_PARAM_MODELS)


class JobParamsError(ValueError):
    """Tham số việc không hợp lệ; `errors` là danh sách lỗi dạng pydantic (đã rút gọn)."""

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.errors = errors or []


def validate_job_params(job_type: str, params: Any) -> dict:
    """Kiểm tra `params` theo danh sách trắng của `job_type`.

    Trả về dict **chỉ gồm các khoá người gọi đã đặt** (``exclude_unset``) để hàm job dùng mặc định
    của chính nó cho phần còn lại. Ném `JobParamsError` nếu loại việc lạ, `params` không phải dict,
    có khoá lạ / khoá nội bộ, hoặc sai kiểu.
    """
    model = JOB_PARAM_MODELS.get(job_type)
    if model is None:
        raise JobParamsError(f"loại việc {job_type!r} không hợp lệ; cho phép: {sorted(JOB_TYPES)}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JobParamsError("`params` phải là object")
    internal = sorted(set(params) & INTERNAL_PARAMS)
    if internal:
        raise JobParamsError(f"không được truyền tham số nội bộ qua hàng đợi: {internal}",
                             [{"loc": ["params", k], "msg": "tham số nội bộ", "type": "forbidden"}
                              for k in internal])
    try:
        obj = model.model_validate(params)
    except ValidationError as e:
        errs = [{"loc": ["params", *[str(x) for x in err.get("loc", ())]], "msg": err.get("msg"),
                 "type": err.get("type")} for err in e.errors()]
        raise JobParamsError(f"tham số {job_type!r} không hợp lệ: "
                             + "; ".join(f"{'.'.join(x['loc'])}: {x['msg']}" for x in errs), errs) from None
    return obj.model_dump(exclude_unset=True)


def parse_model_version(v: Any) -> int | None:
    """Ép ``model_version`` về số nguyên của registry (`phasegnn/registry.py`: `models.version` là int).

    Nhận ``3``, ``"3"`` hoặc ``"v3"`` → 3; rỗng/None → None; còn lại → ``ValueError``. Dùng chung cho
    worker (`forecast_run` chọn bản ``status="production"``) và API (`POST /models/{version}/promote`).
    """
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ValueError(f"model_version không hợp lệ: {v!r}")
    if isinstance(v, int):
        if v < 0:
            raise ValueError(f"model_version không hợp lệ: {v!r}")
        return v
    s = str(v).strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    if not s.isdigit() or len(s) > 18:
        raise ValueError(f"model_version phải là số nguyên (registry `models.version`): {v!r}")
    return int(s)
