"""Quản lý tác vụ nền (`POST /api/v1/jobs/{type}`, `GET /api/v1/jobs/{id}`).

Mỗi tác vụ được ghi vào collection `jobs` với trạng thái, worker đã chạy, `fence` (fencing
token) và `heartbeat` — theo yêu cầu GĐ7.7. `apps/api` chỉ **giao** việc; `apps/worker`
(APScheduler + khóa phân tán) thực thi, bảo đảm mỗi lượt chỉ một instance chạy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from vngis.job_params import JOB_TYPES as _JOB_TYPES, JobParamsError, validate_job_params

from api.deps import UserContext, require, rate_limit
from api.permissions import P
from api.router.common import clean, to_vn_datetime
from api.state import state

log = logging.getLogger("api.jobs")

router = APIRouter(tags=["jobs"])

JOBS = "jobs"

#: Các loại tác vụ được phép giao (mỗi loại có model tham số riêng trong `vngis.job_params`).
JOB_TYPES = set(_JOB_TYPES)


class JobRequest(BaseModel):
    """Tham số giao tác vụ nền.

    `params` được kiểm tra theo model của từng `job_type` (`vngis.job_params`, ``extra="forbid"``):
    khoá lạ, tham số nội bộ (`documents`, `actor`, `repo_root`, `source_dir`, `run_g0`, `run_id`) hoặc
    sai kiểu → 422. Người thực hiện lấy từ người dùng đăng nhập (`requested_by`), không từ `params`.
    """
    model_config = ConfigDict(extra="forbid")
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=0, le=9)
    scheduled_at: str | None = None


@router.get("/jobs", dependencies=[Depends(require(P.READ, JOBS))])
async def list_jobs(job_state: str | None = Query(None, alias="state"), job_type: str | None = Query(None, alias="type"),
                    limit: int = Query(100, ge=1, le=1000)):
    """Danh sách tác vụ (lọc theo trạng thái / loại)."""
    flt: dict[str, Any] = {}
    if job_state:
        flt["state"] = job_state
    if job_type:
        flt["type"] = job_type
    docs = list(state.db[JOBS].find(flt).sort([("created_at", -1)]).limit(limit))
    labels = _labels([d.get("requested_by") for d in docs])
    items = [_job_view(d, labels) for d in docs]
    return {"count": len(items), "items": items}


@router.post("/jobs/{job_type}", dependencies=[Depends(require(P.RUN_JOB, JOBS)),
                                              Depends(rate_limit(scope="job"))])
async def create_job(job_type: str, body: JobRequest,
                     user: UserContext = Depends(require(P.RUN_JOB, JOBS))):
    """Giao một tác vụ nền cho worker.

    `job_type` phải nằm trong danh sách cho phép để tránh chạy lệnh tuỳ ý.
    """
    if job_type not in JOB_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"loại tác vụ {job_type!r} không hợp lệ; cho phép: {sorted(JOB_TYPES)}")
    try:
        params = validate_job_params(job_type, body.params)
    except JobParamsError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"message": str(e), "errors": e.errors}) from None
    db = state.db
    now = datetime.now(timezone.utc)
    job_id = f"{job_type}:{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    doc = {"_id": job_id, "type": job_type, "state": "queued", "params": params,
           "priority": body.priority, "requested_by": user.actor, "worker": None,
           "fence": None, "heartbeat": None, "created_at": now, "updated_at": now}
    db[JOBS].insert_one(doc)
    return _job_view(doc)


@router.get("/jobs/{job_id}", dependencies=[Depends(require(P.READ, JOBS))])
async def get_job(job_id: str):
    """Trạng thái chi tiết của một tác vụ (kèm heartbeat và fencing token)."""
    doc = state.db[JOBS].find_one({"_id": job_id})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có tác vụ {job_id!r}")
    return _job_view(doc)


def _labels(actors: list[Any]) -> dict[str, dict]:
    """Nhãn hiển thị của actor (`api.router.common.actor_labels`); chỉ khi đọc."""
    from api.router.common import actor_labels
    return actor_labels(actors)


def _job_view(doc: dict, labels: dict[str, dict] | None = None) -> dict:
    """Chuẩn hoá bản trả về của tác vụ (ẩn `_id`, đổi thời gian sang giờ VN, kèm nhãn người giao)."""
    out = clean(doc)
    out["id"] = out.pop("_id", None)
    for f in ("created_at", "updated_at", "heartbeat", "started_at", "finished_at"):
        if f in out:
            out[f] = to_vn_datetime(out.get(f))
    if labels is None:
        labels = _labels([doc.get("requested_by")])
    out["requested_by_label"] = labels.get(doc.get("requested_by")) if doc.get("requested_by") else None
    return out
