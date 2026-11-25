"""Các job nền của worker (GĐ7.6, GĐ7.7).

| Job | Lịch mặc định | Khoá (tài nguyên) | `job_id` | Việc |
|---|---|---|---|---|
| `ingest_bulletins` | 06:00 (`scheduler.ingest_hour`) | `lock:reload:bulletins` | `ingest_bulletins:{ngày VN}` | parse + nạp staging → worker kiểm tra khoá → promote |
| `reload_bulletins` | theo hàng đợi | `lock:reload:bulletins` | id việc của API | nạp lại **nguyên tử** toàn bộ bản tin (ADR 0007) |
| `forecast_run` | 08:00 (`scheduler.forecast_hour`/`forecast_minute`) | `lock:forecast_run` | `forecast_run:{ngày VN}` | dự báo PhaseGNN từ checkpoint → `model_forecasts` (`worker.forecast`) |
| `cache_warm` | mỗi 10 phút | `lock:cache_warm` | `cache_warm` (một bản ghi, ghi đè) | làm nóng các khoá cache thường dùng |

Mọi tác vụ ghi `hydromet_bulletins` (cron ingest, hàng đợi ingest/reload, API `POST /bulletins/reload`)
dùng **chung** tài nguyên khoá `reload:bulletins` (khoá Redis `lock:reload:bulletins`, bộ đếm fence
`lock_fences["reload:bulletins"]`) nên loại trừ nhau. Tham số từ hàng đợi được kiểm tra theo danh sách
trắng `vngis.job_params` — `repo_root`, `source_dir`, `documents`, `actor`, `run_g0`, `run_id` chỉ do
hệ thống truyền.

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
import time
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
#: Đường dẫn mặc định (tương đối gốc repo) — khớp hướng dẫn chạy trong `pipelines/ingest/kttv/`.
DEFAULT_RAW_DIR = "data/raw/kttv"
#: Thư mục làm việc của từng lượt ingest (mỗi lượt một thư mục con theo `run_id`).
DEFAULT_RUNS_DIR = "data/interim/kttv/runs"


def _subprocess_env(db=None) -> dict:
    """Môi trường cho tiến trình con: cùng MongoDB với worker.

    Worker kết nối theo `vngis.config` (ví dụ ``APP_ENV=test`` → DB test riêng); tiến trình con
    (`kttv_load_mongo.py`) chỉ đọc biến môi trường ⇒ phải truyền ``MONGO_URI`` (từ cấu hình) và
    ``MONGO_DB`` (= tên DB mà worker đang dùng), nếu không loader sẽ ghi vào DB mặc định.
    """
    env = dict(os.environ)
    uri = None
    try:
        from vngis.config import get_config
        uri = get_config().mongo.uri
    except Exception:  # noqa: BLE001 - không đọc được cấu hình → dùng biến môi trường sẵn có
        uri = env.get("MONGO_URI") or env.get("MONGODB_URI")
    if uri:
        env["MONGO_URI"] = uri
    name = getattr(db, "name", None)
    if isinstance(name, str) and name:
        env["MONGO_DB"] = name
    env.setdefault("PYTHONUTF8", "1")
    return env


def _run_proc(cmd: list[str], *, cwd: Path, env: dict, timeout_s: int, guard=None,
              on_tick=None, tick_s: float = 30.0) -> subprocess.CompletedProcess:
    """Chạy tiến trình con; mất khoá (``guard.lost``) hoặc quá giờ → dừng tiến trình.

    Mất khoá → ném `LockLost` (scheduler ghi ``state="lost"``), không để tiến trình con tiếp tục ghi
    song song với instance mới đang giữ khoá. `on_tick` (nếu có) được gọi mỗi `tick_s` giây khi tiến trình
    còn chạy (ví dụ heartbeat của `models`); lỗi trong `on_tick` chỉ ghi log.
    """
    from vngis.locks import LockLost

    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    deadline = time.monotonic() + timeout_s
    next_tick = time.monotonic() + tick_s
    while True:
        try:
            out, err = proc.communicate(timeout=1.0)
            return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            if guard is not None and guard.lost:
                proc.kill()
                proc.communicate()
                raise LockLost("mất khoá khi đang chạy tiến trình con; đã dừng tiến trình") from None
            if on_tick is not None and time.monotonic() >= next_tick:
                next_tick = time.monotonic() + tick_s
                try:
                    on_tick()
                except Exception as e:  # noqa: BLE001 - heartbeat lỗi không dừng tiến trình con
                    log.warning("on_tick lỗi: %s", e)
            if time.monotonic() > deadline:
                proc.kill()
                out, err = proc.communicate()
                return subprocess.CompletedProcess(cmd, -9, out, (err or "") + "\n[timeout]")


def _tail(proc: subprocess.CompletedProcess, n: int = 2000) -> dict:
    return {"returncode": proc.returncode, "stdout_tail": (proc.stdout or "")[-n:],
            "stderr_tail": (proc.stderr or "")[-n:]}


def ingest_bulletins(db, cache=None, *, source_dir: str | None = None, run_g0: bool = False,
                     repo_root: str | None = None, guard=None) -> dict:
    """Nạp lại bản tin (gồm bản mới của ngày) theo quy trình của `pipelines/ingest/kttv/`.

    1. `kttv_parser.py <raw_dir> <runs/<run_id>/kttv_ban_tin_parsed.json>` — parse toàn bộ PDF; **mỗi
       lượt một thư mục JSON riêng** theo `run_id` (hai lượt không ghi đè file của nhau);
    2. `kttv_load_mongo.py <parsed.json> <out.json> <raw_dir> --staging --run-id <id>` — chỉ nạp vào
       `hydromet_bulletins__staging_<id>` (index + validator). Script đọc đường dẫn ở **vị trí**
       argv[1..3], nên cờ phải đứng sau. Tiến trình con nhận ``MONGO_URI``/``MONGO_DB`` của worker;
    3. **Worker** (không phải tiến trình con) áp lại sửa metadata đã duyệt (`apply_bulletin_overrides`),
       kiểm tra collection tạm, gọi ``guard.check()`` rồi
       `vngis.atomic_reload.promote_staging` (kiểm tra khoá ngay trước rename, writeConcern majority,
       xoá `__prev` sau khi kiểm tra). Lỗi ở bất kỳ bước nào → xoá collection tạm, bản chính giữ nguyên.

    `source_dir`, `repo_root`, `run_g0` chỉ do hệ thống truyền (cron/test), không nhận từ hàng đợi.
    Thiếu script hoặc thư mục PDF → ``status="skipped"`` thay vì ném lỗi.
    """
    import shutil

    from vngis.atomic_reload import (ReloadError, apply_bulletin_overrides, new_run_id, promote_staging,
                                     staging_name, validate_ingest_staging)

    root = Path(repo_root) if repo_root else _repo_root()
    kttv = root / "pipelines" / "ingest" / "kttv"
    parser, loader = kttv / "kttv_parser.py", kttv / "kttv_load_mongo.py"
    if not parser.exists() or not loader.exists():
        log.warning("không tìm thấy script nạp bản tin trong %s", kttv)
        return {"status": "skipped", "reason": "không có script nạp bản tin"}
    raw_dir = Path(source_dir or os.getenv("BULLETIN_SOURCE_DIR") or (root / DEFAULT_RAW_DIR))
    if not raw_dir.is_dir():
        return {"status": "skipped", "reason": "không có thư mục PDF bản tin (BULLETIN_SOURCE_DIR)"}
    run_id = new_run_id()
    work = Path(os.getenv("BULLETIN_WORK_DIR") or (root / DEFAULT_RUNS_DIR)) / run_id
    work.mkdir(parents=True, exist_ok=False)
    parsed, out_json = work / "kttv_ban_tin_parsed.json", work / "hydromet_bulletins.json"
    timeout_s = int(os.getenv("BULLETIN_INGEST_TIMEOUT_S", "1800"))
    env = _subprocess_env(db)
    staging = staging_name(run_id)
    py = [sys.executable, "-X", "utf8"]
    out: dict[str, Any] = {"run_id": run_id, "staging": staging}
    promoted = False
    try:
        p1 = _run_proc(py + [str(parser), str(raw_dir), str(parsed)], cwd=root, env=env,
                       timeout_s=timeout_s, guard=guard)
        out["parse"] = _tail(p1)
        if p1.returncode != 0:
            raise RuntimeError(f"parse bản tin lỗi (mã {p1.returncode}): {(p1.stderr or '')[-300:]}")
        if guard is not None:
            guard.check()
        p2 = _run_proc(py + [str(loader), str(parsed), str(out_json), str(raw_dir),
                             "--staging", "--run-id", run_id],
                       cwd=root, env=env, timeout_s=timeout_s, guard=guard)
        out["load"] = _tail(p2)
        if p2.returncode != 0:
            raise RuntimeError(f"nạp bản tin lỗi (mã {p2.returncode}): {(p2.stderr or '')[-300:]}")
        # Áp lại sửa metadata đã duyệt (nguồn `audit_logs`); loader đã áp — gọi lại là no-op (idempotent),
        # nhưng bảo đảm đúng cả khi loader cũ/chạy tay không áp.
        ov = apply_bulletin_overrides(db, staging)
        out["overrides"] = {k: ov[k] for k in ("applied", "unchanged", "fields")}
        out["overrides"]["conflicts"] = [(c["source_file"], c["field"]) for c in ov["conflicts"]]
        out["overrides"]["missing"] = ov["missing"][:50]
        checks = validate_ingest_staging(db, staging)
        out["checks"] = checks
        failed = [c for c in checks if not c["ok"]]
        if failed:
            raise ReloadError("kiểm tra collection tạm hỏng (bản chính giữ nguyên): "
                              + "; ".join(f"{c['name']}: {c['detail']}" for c in failed))
        if guard is not None:
            guard.check()
        out["promote"] = promote_staging(db, staging, guard=guard)
        promoted = True
    finally:
        if not promoted:
            try:
                db[staging].drop()
            except Exception:  # noqa: BLE001
                log.warning("không xoá được collection tạm %s", staging)
        if os.getenv("BULLETIN_KEEP_RUN_FILES") != "1":
            shutil.rmtree(work, ignore_errors=True)
    if cache is not None:
        try:
            cache.bump_version(BULLETINS)
        except Exception as e:  # noqa: BLE001
            log.warning("không tăng được ver:%s: %s", BULLETINS, e)
    if run_g0:
        out["g0"] = rerun_g0(root, db=db, guard=guard)
    out["status"] = "ok"
    return out


def reload_bulletins(db, cache=None, *, documents: list[dict] | None = None,
                     actor: str = "worker", run_id: str | None = None, guard=None) -> dict:
    """Nạp lại **nguyên tử** toàn bộ bản tin (ADR 0007).

    Từ hàng đợi: `documents`/`run_id` không bao giờ được nhận (danh sách trắng `vngis.job_params`);
    `actor` do scheduler lấy từ `requested_by` của bản ghi việc. Khi `documents` là ``None`` hàm đọc
    bản tin hiện có trong `hydromet_bulletins` và nạp lại chúng (tái tạo index/validator, kiểm thử
    rollback). `guard` được kiểm tra ngay trước bước rename.
    """
    from vngis.atomic_reload import new_run_id

    run_id = run_id or f"reload-{new_run_id()}"
    if documents is None:
        documents = list(db[BULLETINS].find({}))
    result = reload_bulletins_atomic(db, actor=actor, documents=list(documents),
                                     run_id=run_id, cache=cache, guard=guard)
    return {"run_id": run_id, **result}


def rerun_g0(repo_root: Path, *, db=None, guard=None) -> dict:
    """Dựng lại validator + index của collection chính (`kttv_load_mongo.py ... --g0-only`).

    Script mở file JSON ở argv[1] ngay khi import nên phải truyền đủ ba đường dẫn vị trí trước cờ;
    chế độ `--g0-only` không dùng nội dung file nên dùng một file JSON rỗng ``[]`` tạm (không đụng file
    của lượt khác).
    """
    import tempfile

    script = repo_root / "pipelines" / "ingest" / "kttv" / "kttv_load_mongo.py"
    if not script.exists():
        return {"status": "skipped", "reason": "không có script G0"}
    with tempfile.TemporaryDirectory(prefix="g0_") as tmp:
        empty = Path(tmp) / "empty.json"
        empty.write_text("[]", encoding="utf-8")
        proc = _run_proc([sys.executable, "-X", "utf8", str(script), str(empty), str(Path(tmp) / "out.json"),
                          str(repo_root / DEFAULT_RAW_DIR), "--g0-only"],
                         cwd=repo_root, env=_subprocess_env(db), timeout_s=1800, guard=guard)
    return {"status": "ok" if proc.returncode == 0 else "failed", **_tail(proc, 1000)}


def _repo_root() -> Path:
    """Thư mục gốc repo (dùng `PGNN_REPO_ROOT` nếu có, ngược lại suy ra từ vị trí file)."""
    env = os.getenv("PGNN_REPO_ROOT")
    if env:
        return Path(env).resolve()
    # apps/worker/src/worker/jobs.py -> lên 4 cấp
    return Path(__file__).resolve().parents[4]


# ------------------------------------------------------------------ dự báo ------
def forecast_run(db, cache=None, *, basins: list[str] | None = None, horizon_h: int = 72,
                 station: str | None = None, basin: str | None = None, issued_at: str | None = None,
                 model_version: str | None = None, force_sample: bool = False, guard=None,
                 job_id: str | None = None, repo_root: str | None = None, predictor=None) -> dict:
    """Chạy dự báo PhaseGNN từ checkpoint và ghi `model_forecasts` (xem `worker.forecast`).

    - Thiếu checkpoint / không import được `phasegnn`/`torch` / không có mẫu dữ liệu → ``status="skipped"``
      kèm ``reason`` (scheduler ghi ``state="skipped"``); không bao giờ sinh số liệu giả.
    - ``force_sample=True``: dự báo mẫu do API phục vụ (`results/`), worker không chạy mô hình → ``skipped``.
    - `guard` (khoá ``lock:forecast_run``) cho fence của lệnh ghi; `job_id`, `repo_root`, `predictor` chỉ do
      hệ thống truyền (test dùng `predictor` giả), không nhận từ hàng đợi (`vngis.job_params`).
    - ``horizon_h`` được giữ trong kết quả; số ngày dự báo do checkpoint quyết (``T_out``).
    """
    from worker.forecast import run_forecast

    basins = list(basins or [])
    if basin and basin not in basins:
        basins.append(basin)
    if force_sample:
        return {"status": "skipped", "reason": "force_sample: dự báo mẫu do API phục vụ, worker không chạy mô hình",
                "basins": basins}
    root = Path(repo_root) if repo_root else _repo_root()
    out = run_forecast(db, cache, root=root, station=station, basins=basins, issued_at=issued_at,
                       model_version=model_version, guard=guard, job_id=job_id, predictor=predictor,
                       data_root=os.getenv("FORECAST_DATA_ROOT") or None)
    out["horizon_h"] = horizon_h
    return out


# ------------------------------------------------------------------ huấn luyện ---
def train_run(db, cache=None, *, model_version: int | None = None, epochs: int = 50,
              learning_rate: float = 1e-3, station: str | None = None, basin: str | None = None,
              guard=None, job_id: str | None = None, repo_root: str | None = None, runner=None) -> dict:
    """Huấn luyện PhaseGNN cho bản ghi `models` ``phasegnn-<model_version>`` (xem `worker.train`)."""
    from worker.train import run_training

    root = Path(repo_root) if repo_root else _repo_root()
    return run_training(db, root=root, model_version=model_version, epochs=epochs, learning_rate=learning_rate,
                        station=station, basin=basin, guard=guard, job_id=job_id, runner=runner)


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
