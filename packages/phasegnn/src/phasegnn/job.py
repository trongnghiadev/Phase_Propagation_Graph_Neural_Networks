"""Job du bao hang ngay (checklist 6.8).

**Luong chay.**

1. Lay khoa `lock:forecast:{basin}:{date}` tren Redis (Redlock hoac `single`).
2. Lay **fencing token** tu MongoDB (`lock_fences`) — moi lan lay khoa tang dan.
3. Doc mo hinh `production` tu collection `models`.
4. Du bao → ghi `forecasts`, `alerts` → ghi Redis cache → nha khoa.

**Fencing token.** Moi lan ghi deu di kem dieu kien `last_fence < fence`. Neu mot tien
trinh khac da ghi voi fence lon hon (tức tien trinh nay da loi thoi), lenh ghi bi tu choi
bang `FenceRejected` — nho vay job chay song song khong ghi de len nhau.

**Che do kho.** Neu khong co Redis/MongoDB (may may, chay thu), job chay o che do
`--no-lock` va ghi ra file cuc bo; nhung lenh ghi co dieu kien fencing van duoc ap dung
neu co database.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

#: Ten khoa theo checklist 6.8.
LOCK_KEY_TEMPLATE = "lock:forecast:{basin}:{date}"


def lock_key(basin: str, day: str) -> str:
    """Khoa theo lưu vực và ngày — ``lock:forecast:{basin}:{date}``."""
    return LOCK_KEY_TEMPLATE.format(basin=basin, date=day)


@dataclass
class ForecastJobResult:
    """Ket qua mot lan chay job."""

    day: str
    basin: str
    model_name: str
    n_forecasts: int = 0
    n_alerts: int = 0
    fence: int = 0
    locked: bool = False
    wrote_db: bool = False
    wrote_cache: bool = False
    out_path: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "day": self.day, "basin": self.basin, "model_name": self.model_name,
            "n_forecasts": self.n_forecasts, "n_alerts": self.n_alerts,
            "fence": self.fence, "locked": self.locked, "wrote_db": self.wrote_db,
            "wrote_cache": self.wrote_cache, "out_path": self.out_path,
            "errors": self.errors,
        }


def _try_backend():
    """Tao backend khoa tu cau hinh (`vngis.locks.make_backend`)."""
    try:
        from vngis.locks import make_backend  # noqa: PLC0415
        return make_backend()
    except Exception:  # noqa: BLE001
        return None


def _try_db():
    """Ket noi MongoDB (None neu khong duoc)."""
    try:
        from vngis.db import get_db  # noqa: PLC0415
        from vngis.config import get_config  # noqa: PLC0415
        return get_db(get_config())
    except Exception:  # noqa: BLE001
        return None


def build_alerts(records: Sequence[dict], thresholds: dict[str, float]) -> list[dict]:
    """Tao canh bao bao dong tu du bao.

    `thresholds` la mapping `station -> nguong` (m). Ban ghi du bao co `h_q50` >= nguong
    se sinh mot canh bao.
    """
    out: list[dict] = []
    for r in records:
        st = str(r.get("station"))
        thr = thresholds.get(st)
        if thr is None:
            continue
        h50 = r.get("h_q50")
        if h50 is None:
            continue
        try:
            h = float(h50)
        except (TypeError, ValueError):
            continue
        if h >= float(thr):
            out.append({
                "station": st,
                "target_date": r.get("target_date"),
                "issued_date": r.get("issued_date"),
                "lead_day": r.get("lead_day"),
                "level": "I",
                "predicted_h": h,
                "threshold": float(thr),
                "model": r.get("model"),
            })
    return out


def run_forecast_job(*, day: str, basin: str = "DONG_RAI", records: Sequence[dict],
                     model_name: str = "phasegnn-v1", thresholds: dict[str, float] | None = None,
                     out_dir: str | os.PathLike | None = None, ttl_ms: int = 120_000,
                     use_lock: bool = True, db=None, log=print) -> ForecastJobResult:
    """Chay job du bao cho mot ngay: khoa → ghi → nha khoa.

    Tham so
    -------
    day : ngay phat ban tin (ISO).
    records : du bao theo dinh dang chung (`schemas/prediction/v1.schema.json`).
    thresholds : nguong bao dong theo tram (m) — dung sinh canh bao.
    use_lock : `False` de chay khoa (may khong co Redis).
    """
    res = ForecastJobResult(day=day, basin=basin, model_name=model_name,
                            n_forecasts=len(records))
    thresholds = thresholds or {}
    alerts = build_alerts(records, thresholds)
    res.n_alerts = len(alerts)

    database = db if db is not None else _try_db()
    backend = _try_backend() if use_lock else None

    guard = None
    if backend is not None:
        try:
            from vngis.locks import LockGuard  # noqa: PLC0415
            resource = f"forecast:{basin}:{day}"
            guard = LockGuard(backend, database, resource=resource,
                              key=lock_key(basin, day), ttl_ms=ttl_ms,
                              use_fence=database is not None)
            guard.acquire()
            res.locked = True
            res.fence = guard.fence
            log(f"[job] da lay khoa {lock_key(basin, day)} (fence={res.fence})")
        except Exception as exc:  # noqa: BLE001
            res.errors.append(f"khong lay duoc khoa: {exc}")
            log(f"[job] khong lay duoc khoa ({exc}) -> chay khong co khoa")
            guard = None

    try:
        # --- ghi `forecasts` + `alerts` (co dieu kien fencing) ------------
        if database is not None:
            try:
                col_f = database["forecasts"]
                col_a = database["alerts"]
                fence_filter = {"last_fence": {"$lt": res.fence}} if res.fence else {}
                for rec in records:
                    doc = dict(rec)
                    doc["last_fence"] = res.fence
                    doc["model_name"] = model_name
                    key = {"model_name": model_name, "station": rec.get("station"),
                           "issued_date": rec.get("issued_date"),
                           "target_date": rec.get("target_date"),
                           "lead_day": rec.get("lead_day"),
                           "peak_idx": rec.get("peak_idx")}
                    if guard is not None and database is not None:
                        guard.write(fence_filter, {"$set": doc}, collection="forecasts")
                        # `guard.write` dùng `_id`; ta cần upsert theo khoa logic
                        col_f.update_one(key, {"$set": doc}, upsert=True)
                    else:
                        col_f.update_one(key, {"$set": doc}, upsert=True)
                for al in alerts:
                    doc = dict(al)
                    doc["last_fence"] = res.fence
                    key = {"model_name": model_name, "station": al["station"],
                           "target_date": al["target_date"], "lead_day": al["lead_day"],
                           "level": al["level"]}
                    col_a.update_one(key, {"$set": doc}, upsert=True)
                res.wrote_db = True
                log(f"[job] da ghi {len(records)} du bao va {len(alerts)} canh bao vao MongoDB")
            except Exception as exc:  # noqa: BLE001
                res.errors.append(f"ghi MongoDB that bai: {exc}")
                log(f"[job] ghi MongoDB that bai ({exc})")

        # --- ghi Redis cache ---------------------------------------------
        try:
            from vngis.db import redis_cache  # noqa: PLC0415
            cache = redis_cache()
            key = f"forecast:{basin}:{day}"
            cache.set(key, json.dumps(records, ensure_ascii=False, default=str))
            res.wrote_cache = True
        except Exception as exc:  # noqa: BLE001
            res.errors.append(f"ghi Redis that bai: {exc}")

        # --- ghi file cuc lo ----------------------------------------------
        if out_dir is not None:
            try:
                out = Path(out_dir)
                out.mkdir(parents=True, exist_ok=True)
                path = out / f"forecast_{day}.json"
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"day": day, "basin": basin, "model_name": model_name,
                               "fence": res.fence, "forecasts": list(records),
                               "alerts": alerts}, fh,
                              ensure_ascii=False, indent=1, default=str)
                    fh.write("\n")
                res.out_path = str(path)
            except Exception as exc:  # noqa: BLE001
                res.errors.append(f"ghi file that bai: {exc}")
    finally:
        if guard is not None:
            try:
                guard.release()
                log("[job] da nha khoa")
            except Exception as exc:  # noqa: BLE001
                res.errors.append(f"nha khoa that bai: {exc}")

    return res
