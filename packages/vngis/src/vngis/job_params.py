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
           "WARMABLE_COLLECTIONS", "validate_job_params", "parse_model_version"]

#: Tham số do hệ thống truyền (không được nhận từ `params` của hàng đợi).
INTERNAL_PARAMS = frozenset({"db", "cache", "guard", "actor", "documents", "repo_root", "source_dir",
                             "run_g0", "run_id"})

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


class TrainParams(_Params):
    """Tham số huấn luyện (khớp `TrainRequest` của API)."""
    model_version: str | None = Field(default=None, pattern=_CODE)
    epochs: int = Field(default=50, ge=1, le=2000)
    learning_rate: float = Field(default=1e-3, gt=0, le=1)
    station: str | None = Field(default=None, pattern=_CODE)
    basin: str | None = Field(default=None, pattern=_CODE)


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
