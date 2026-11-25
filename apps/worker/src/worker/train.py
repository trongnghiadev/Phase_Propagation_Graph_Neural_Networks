"""Huấn luyện PhaseGNN theo hàng đợi (`POST /models/train` → việc ``train:<n>``; giao lại ``train:<n>:<lượt>``).

API cấp ``version`` nguyên tử và ghi trước `models` ``{_id: "phasegnn-<n>", version: n, status: "staging",
train_state: "queued", job_id}``; worker nhận việc ``train`` dưới khoá tài nguyên ``train`` (một lượt huấn
luyện mỗi lúc trên toàn cụm) và:

1. kiểm tra image có `phasegnn` + `torch` (không có → ``train_state="skipped"`` + ``train_reason``, việc
   ``skipped``);
2. ``train_state: queued|running|failed → running`` có fence (``train_fence`` ≤ fence của lượt; bản ghi đã
   ``succeeded`` → bỏ qua, idempotent);
3. chạy tiến trình con ``python -m phasegnn.train`` — tham số chỉ lấy từ `vngis.job_params.TrainParams` đã kiểm
   tra (``epochs``, ``learning_rate``; ``station``/``basin`` chưa được trình huấn luyện hỗ trợ → ghi
   ``params_ignored``), thư mục ra ``results/models/phasegnn-<n>/`` (``*.pt`` và ``results/models/`` bị
   gitignore), ``--model-version n --train-fence <fence>`` để tiến trình con chỉ cập nhật đúng bản ghi
   ``phasegnn-<n>``; heartbeat ``models.train_heartbeat`` mỗi `heartbeat_s`; mất khoá → dừng tiến trình con,
   không ghi thêm; scheduler trả việc về ``queued`` (``attempts += 1``, tối đa ``max_attempts``) và lượt mới
   (fence lớn hơn) ghi đè ``train_state``; hết lượt → `abandon_training` đánh ``train_state="failed"``;
4. thành công → ``train_state="succeeded"``, ``checkpoint_path`` (tương đối gốc repo, dưới ``results/``, kiểm
   tra bằng `worker.forecast.safe_checkpoint_path`), chỉ số val (``metrics``); lỗi → ``train_state="failed"``
   + ``train_error`` (việc ``failed``).

``status`` giữ ``staging`` — promote là thao tác riêng (`POST /models/{version}/promote`).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vngis.model_registry import MODELS_COLLECTION, model_doc_id

log = logging.getLogger("worker.train")

#: Thư mục ra của mỗi phiên bản (tương đối gốc repo).
MODELS_OUT_DIR = "results/models"
#: Seed của lượt huấn luyện theo hàng đợi (tên tệp ``checkpoint_seed0.pt``).
TRAIN_SEED = 0
#: Trạng thái được phép chuyển sang ``running`` (``running``: lượt trước chết, việc được nhận lại).
_RESTARTABLE = ("queued", "running", "failed", None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fence_filter(doc_id: str, fence: int | None) -> dict:
    flt: dict[str, Any] = {"_id": doc_id}
    if fence is not None:
        flt["$or"] = [{"train_fence": {"$exists": False}}, {"train_fence": None},
                      {"train_fence": {"$lte": int(fence)}}]
    return flt


class _ModelWriter:
    """Ghi `models[phasegnn-<n>]` có điều kiện fence; kiểm tra khoá ngay trước mỗi lệnh ghi."""

    def __init__(self, db, doc_id: str, guard=None):
        self.db, self.doc_id, self.guard = db, doc_id, guard
        fence = getattr(guard, "fence", None) if guard is not None else None
        # `fence=0` là giá trị hợp lệ (không coi như thiếu) ⇒ so `is not None`.
        self.fence = int(fence) if fence is not None else None

    def set(self, fields: dict, *, extra: dict | None = None) -> bool:
        if self.guard is not None:
            self.guard.check()
        flt = {**_fence_filter(self.doc_id, self.fence), **(extra or {})}
        res = self.db[MODELS_COLLECTION].update_one(flt, {"$set": {**fields, "updated_at": _now()}})
        if res.matched_count == 0:
            log.warning("models[%s]: ghi bị từ chối (fence=%s, điều kiện %s)", self.doc_id, self.fence, extra)
        return res.matched_count == 1


def abandon_training(db, job_doc: dict, reason: str) -> bool:
    """Hook của scheduler khi việc ``train`` bị bỏ hẳn (mất khoá / worker chết quá ``max_attempts`` lần).

    Đánh `models[phasegnn-<n>]` ``train_state="failed"`` + ``train_error`` — chỉ khi bản ghi còn trỏ tới đúng
    việc này (``job_id``) và đang ``queued``/``running`` (không đụng lượt giao lại mới hơn). Sau đó giao lại
    được qua `POST /models/train` với ``model_version=n``.
    """
    from vngis.job_params import parse_model_version

    try:
        ver = parse_model_version(job_doc.get("model_version") or (job_doc.get("params") or {}).get("model_version"))
    except ValueError:
        return False
    if not ver:
        return False
    res = db[MODELS_COLLECTION].update_one(
        {"_id": model_doc_id("phasegnn", ver), "job_id": job_doc.get("_id"),
         "train_state": {"$in": ["queued", "running"]}},
        {"$set": {"train_state": "failed", "train_error": f"việc {job_doc.get('_id')} bị bỏ: {reason}"[:500],
                  "train_finished_at": _now(), "updated_at": _now()}})
    return res.modified_count == 1


def _default_runner(cmd: list[str], *, cwd: Path, env: dict, timeout_s: int, guard=None,
                    on_tick: Callable[[], None] | None = None, tick_s: float = 30.0):
    from worker.jobs import _run_proc

    return _run_proc(cmd, cwd=cwd, env=env, timeout_s=timeout_s, guard=guard, on_tick=on_tick, tick_s=tick_s)


def build_command(*, model_version: int, epochs: int, learning_rate: float, out_dir: str,
                  fence: int | None, data_root: str | None = None) -> list[str]:
    """Dòng lệnh tiến trình con — chỉ từ giá trị đã qua `TrainParams` (số đã ép kiểu), không chuỗi tự do."""
    cmd = [sys.executable, "-X", "utf8", "-m", "phasegnn.train", "--variant", "phasegnn",
           "--epochs", str(int(epochs)), "--lr", repr(float(learning_rate)), "--seed", str(TRAIN_SEED),
           "--out-dir", out_dir, "--no-mlflow", "--model-version", str(int(model_version))]
    if fence is not None:
        cmd += ["--train-fence", str(int(fence))]
    if data_root:
        cmd += ["--data-root", data_root]
    return cmd


def _read_record(out_abs: Path) -> dict:
    p = out_abs / f"model_record_seed{TRAIN_SEED}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _clean_metrics(m: Any) -> dict:
    out: dict[str, Any] = {}
    for k, v in (m or {}).items() if isinstance(m, dict) else ():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[str(k)] = None if v != v else v  # NaN → None
    return out


def run_training(db, *, root: Path, model_version: Any, epochs: int = 50, learning_rate: float = 1e-3,
                 station: str | None = None, basin: str | None = None, guard=None, job_id: str | None = None,
                 runner=None, available: Callable[[], tuple[bool, str]] | None = None,
                 heartbeat_s: float | None = None, timeout_s: int | None = None) -> dict:
    """Chạy một lượt huấn luyện cho ``phasegnn-<model_version>``; trả dict kết quả cho `jobs.result`.

    `runner`, `available` chỉ do test truyền (mô hình/tiến trình giả); không nhận từ hàng đợi.
    """
    from vngis.job_params import parse_model_version

    from worker.forecast import UnsafeCheckpoint, phasegnn_available, safe_checkpoint_path

    ver = parse_model_version(model_version)
    if ver is None or ver < 1:
        raise ValueError("việc train thiếu model_version (API luôn cấp số)")
    doc_id = model_doc_id("phasegnn", ver)
    doc = db[MODELS_COLLECTION].find_one({"_id": doc_id})
    if doc is None or doc.get("version") != ver:
        raise ValueError(f"không có bản ghi models[{doc_id}] (version={ver})")
    out: dict[str, Any] = {"model_version": ver, "model_id": doc_id}
    if doc.get("train_state") == "succeeded":
        return {**out, "status": "skipped", "reason": "phiên bản đã huấn luyện xong (train_state=succeeded)"}
    if doc.get("status") != "staging":
        return {**out, "status": "skipped", "reason": f"chỉ huấn luyện bản staging (status={doc.get('status')!r})"}
    w = _ModelWriter(db, doc_id, guard)
    ignored = [k for k, v in (("station", station), ("basin", basin)) if v]
    if ignored:
        out["params_ignored"] = ignored

    ok, why = (available or phasegnn_available)()
    if not ok:
        reason = f"image worker không có phasegnn/torch: {why}"
        w.set({"train_state": "skipped", "train_reason": reason, "train_job_id": job_id, "train_finished_at": _now(),
               "train_fence": w.fence})
        return {**out, "status": "skipped", "reason": reason}

    if not w.set({"train_state": "running", "train_started_at": _now(), "train_heartbeat": _now(),
                  "train_job_id": job_id, "train_fence": w.fence, "train_error": None, "train_reason": None},
                 extra={"train_state": {"$in": list(_RESTARTABLE)}}):
        return {**out, "status": "skipped", "reason": "không chuyển được sang running (fence cũ hoặc trạng thái đổi)"}

    out_rel = f"{MODELS_OUT_DIR}/{doc_id}"
    out_abs = Path(root) / out_rel
    out_abs.mkdir(parents=True, exist_ok=True)
    cmd = build_command(model_version=ver, epochs=epochs, learning_rate=learning_rate, out_dir=out_rel,
                        fence=w.fence, data_root=os.getenv("TRAIN_DATA_ROOT") or os.getenv("FORECAST_DATA_ROOT"))
    from worker.jobs import _subprocess_env

    env = _subprocess_env(db)
    tick = float(heartbeat_s if heartbeat_s is not None else os.getenv("TRAIN_HEARTBEAT_S", "30"))
    limit = int(timeout_s if timeout_s is not None else os.getenv("TRAIN_TIMEOUT_S", str(6 * 3600)))

    def _beat() -> None:
        if guard is not None and guard.lost:
            return
        w.set({"train_heartbeat": _now()})

    from vngis.locks import LockLost

    try:
        proc = (runner or _default_runner)(cmd, cwd=Path(root), env=env, timeout_s=limit, guard=guard,
                                           on_tick=_beat, tick_s=tick)
    except LockLost:
        # Không ghi gì nữa: scheduler trả việc về hàng đợi (`Scheduler.requeue_lost`); lượt mới (fence lớn hơn)
        # chuyển `train_state` running → running và ghi đè. Hết lượt → `abandon_training` đánh ``failed``.
        raise
    except Exception as e:
        w.set({"train_state": "failed", "train_error": f"không chạy được tiến trình huấn luyện: {e}"[:500],
               "train_finished_at": _now()})
        raise
    out["returncode"] = proc.returncode
    out["stdout_tail"] = (proc.stdout or "")[-2000:]
    out["stderr_tail"] = (proc.stderr or "")[-2000:]
    if proc.returncode != 0:
        err = f"huấn luyện lỗi (mã {proc.returncode}): {(proc.stderr or '')[-300:]}"
        w.set({"train_state": "failed", "train_error": err[:500], "train_finished_at": _now()})
        raise RuntimeError(err)

    ckpt_rel = f"{out_rel}/checkpoint_seed{TRAIN_SEED}.pt"
    try:
        ck = safe_checkpoint_path(ckpt_rel, Path(root))
    except UnsafeCheckpoint as e:
        ck, err = None, str(e)
    else:
        err = "" if ck.is_file() else f"không thấy checkpoint {ckpt_rel}"
    if ck is None or err:
        w.set({"train_state": "failed", "train_error": err[:500], "train_finished_at": _now()})
        raise RuntimeError(err)
    rec = _read_record(out_abs)
    metrics = _clean_metrics(rec.get("metrics"))
    fields = {"train_state": "succeeded", "train_finished_at": _now(), "checkpoint_path": ckpt_rel,
              "artifact_path": ckpt_rel, "metrics": metrics, "train_error": None,
              "train_params": {"epochs": int(epochs), "learning_rate": float(learning_rate), "seed": TRAIN_SEED}}
    if not w.set(fields, extra={"train_state": "running"}):
        raise RuntimeError(f"không ghi được kết quả huấn luyện vào models[{doc_id}] (fence cũ hoặc trạng thái đổi)")
    out.update({"status": "ok", "checkpoint_path": ckpt_rel, "metrics": metrics})
    return out
