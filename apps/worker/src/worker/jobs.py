"""Các job nền của worker (GĐ7.6, GĐ7.7).

| Job | Lịch mặc định | Khoá | Việc |
|---|---|---|---|
| `ingest_bulletins` | 06:00 hằng ngày | `lock:ingest:{date}` | nạp bản tin mới của ngày |
| `reload_bulletins` | thủ công / 02:00 | `lock:reload:bulletins` | nạp lại **nguyên tử** toàn bộ bản tin (ADR 0007) |
| `forecast_run` | 07:00 hằng ngày | `lock:forecast:{basin}:{date}` | chạy dự báo cho các lưu vực |
| `cache_warm` | mỗi 10 phút | `lock:cache_warm` | làm nóng các khoá cache thường dùng |

**Quan trọng:** nạp lại bản tin dùng `vngis.atomic_reload.reload_bulletins_atomic` — staging
`<coll>__staging_<run_id>` → kiểm tra (số bản tin, số bản chính, SHA-256) → sao lưu `__prev`
→ `renameCollection(..., dropTarget=True)` → tăng `ver:hydromet_bulletins` → chạy lại G0.
Không bao giờ xoá rồi nạp.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vngis.admin import VN_TZ
from vngis.atomic_reload import reload_bulletins_atomic

log = logging.getLogger("worker.jobs")

BULLETINS = "hydromet_bulletins"
BULLETIN_FILES = "hydromet_bulletin_files"
JOBS_COLLECTION = "jobs"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_vn() -> str:
    return _now().astimezone(VN_TZ).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ nạp bản tin ----
def ingest_bulletins(db, cache=None, *, source_dir: str | None = None,
                     run_g0: bool = True, repo_root: str | None = None) -> dict:
    """Nạp bản tin mới của ngày (gọi bởi pipeline `pipelines/ingest/kttv/kttv_load_mongo.py`).

    Việc nạp thực hiện trong tiến trình con (script của pipeline) để tách bản đặc biệt của
    G0; nếu không có script thì trả về kết quả "không có gì để nạp" thay vì ném lỗi.
    """
    src = source_dir or os.getenv("BULLETIN_SOURCE_DIR")
    root = repo_root or _repo_root()
    script = root / "pipelines" / "ingest" / "kttv" / "kttv_load_mongo.py"
    if not script.exists():
        log.warning("không tìm thấy script nạp bản tin: %s", script)
        return {"status": "skipped", "reason": "không có script nạp bản tin", "script": str(script)}
    cmd = [sys.executable, str(script), "--staging", "--run-id", _today_vn()]
    if src:
        cmd += ["--source-dir", str(src)]
    env = dict(os.environ)
    env["MONGO_URI"] = env.get("MONGO_URI", os.getenv("MONGODB_URI", ""))
    proc = subprocess.run(cmd, cwd=str(root), env=env, capture_output=True, text=True,
                          timeout=int(os.getenv("BULLETIN_INGEST_TIMEOUT_S", "1800")))
    out = {"status": "ok" if proc.returncode == 0 else "failed",
           "returncode": proc.returncode, "stdout_tail": proc.stdout[-2000:],
           "stderr_tail": proc.stderr[-2000:]}
    if proc.returncode == 0 and run_g0:
        out["g0"] = rerun_g0(root)
    return out


def reload_bulletins(db, cache=None, *, documents: list[dict] | None = None,
                     actor: str = "worker", run_id: str | None = None) -> dict:
    """Nạp lại **nguyên tử** toàn bộ bản tin (ADR 0007).

    Nếu `documents` không được truyền, hàm đọc bản tin hiện có trong `hydromet_bulletins`
    và nạp lại chúng (dùng cho kiểm thử rollback / tái tạo index). Người gọi thực tế
    (API `POST /bulletins/reload`) sẽ truyền danh sách bản tin đã parse.
    """
    run_id = run_id or f"reload-{_now().strftime('%Y%m%dT%H%M%SZ')}"
    if documents is None:
        documents = list(db[BULLETINS].find({}))
    result = reload_bulletins_atomic(db, actor=actor, documents=list(documents),
                                      run_id=run_id, cache=cache)
    return {"run_id": run_id, **result}


def rerun_g0(repo_root: Path) -> dict:
    """Chạy lại bước G0 (dựng lại collection chính và index) sau khi nạp lại bản tin."""
    script = repo_root / "pipelines" / "ingest" / "kttv" / "kttv_load_mongo.py"
    if not script.exists():
        return {"status": "skipped", "reason": "không có script G0"}
    proc = subprocess.run([sys.executable, str(script), "--g0-only"],
                          cwd=str(repo_root), capture_output=True, text=True, timeout=1800)
    return {"status": "ok" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode, "stderr_tail": proc.stderr[-1000:]}


def _repo_root() -> Path:
    """Thư mục gốc repo (dùng `PGNN_REPO_ROOT` nếu có, ngược lại suy ra từ vị trí file)."""
    env = os.getenv("PGNN_REPO_ROOT")
    if env:
        return Path(env).resolve()
    # apps/worker/src/worker/jobs.py -> lên 4 cấp
    return Path(__file__).resolve().parents[4]


# ------------------------------------------------------------------ dự báo ------
def forecast_run(db, cache=None, *, basins: list[str] | None = None,
                 horizon_h: int = 72) -> dict:
    """Chạy dự báo cho các lưu vực.

    Hiện tại **chưa có mô hình PhaseGNN đã huấn luyện** trong repo, nên job ghi lại trạng
    thái `skipped_no_model` thay vì bịa số liệu. Khi có mô hình, chỗ này sẽ gọi runner
    của `packages/phasegnn` (giữ ranh giới package: `apps/worker` được phép import
    `phasegnn`, ngược lại `apps/api` thì không).
    """
    basins = basins or []
    model = db["models"].find_one({"state": "promoted"}) if "models" in db.list_collection_names() else None
    if model is None:
        return {"status": "skipped_no_model",
                "note": "chưa có mô hình PhaseGNN được promote; không sinh dự báo giả",
                "basins": basins, "horizon_h": horizon_h}
    # TODO(GĐ8): nối runner của packages/phasegnn khi mô hình sẵn sàng.
    return {"status": "not_implemented", "model_version": model.get("version"),
            "note": "cần nối runner phasegnn (GĐ8)"}


# ------------------------------------------------------------------ cache -------
def warm_cache(db, cache, *, collections: list[str] | None = None) -> dict:
    """Làm nóng cache cho các truy vấn thường dùng (giảm tải MongoDB)."""
    if cache is None:
        return {"status": "skipped", "reason": "không có cache"}
    warmed = []
    for coll in (collections or ["stations", "tide_extremes", "reservoir_daily"]):
        try:
            key = cache.versioned_key("warm", coll, "count")
            cache.get_or_compute(key, "warm", lambda c=coll: db[c].count_documents({}))
            warmed.append(coll)
        except Exception as e:  # noqa: BLE001 - làm nóng cache lỗi không được chặn job khác
            log.warning("không làm nóng được cache %s: %s", coll, e)
    return {"status": "ok", "warmed": warmed}


# ------------------------------------------------------------------ kiểm tra ----
def bulletin_checksums(db, *, limit: int = 1000) -> dict:
    """Đối chiếu số bản tin / số bản chính / SHA-256 với `hydromet_bulletin_files`.

    Dùng để kiểm tra trước khi nạp lại (ADR 0007) và trong test.
    """
    files = list(db[BULLETIN_FILES].find({}).limit(limit))
    bulletins = list(db[BULLETINS].find({}).limit(limit))
    n_primary = sum(1 for f in files if f.get("is_primary"))
    sha = {}
    for f in files:
        p = f.get("sha256") or f.get("sha256_file")
        if p:
            sha[p] = f.get("source_file") or f.get("path")
    return {"n_bulletins": len(bulletins), "n_files": len(files), "n_primary": n_primary,
            "n_with_sha": len(sha), "sha_index": sha}
