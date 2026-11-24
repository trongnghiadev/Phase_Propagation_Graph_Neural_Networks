"""Nạp lại nguyên tử collection `hydromet_bulletins` (đề cương 5.6.4, mục GĐ7.6).

Dùng **cùng mẫu** với job G0 (ADR 0007):

1. Ghi vào collection tạm ``hydromet_bulletins__staging_<run_id>`` (có validator và index
   giống bản chính).
2. Kiểm tra: số bản tin, số bản chính, SHA-256 khớp ``hydromet_bulletin_files``.
3. Sao lưu bản chính sang ``hydromet_bulletins__prev`` (để lùi lại được).
4. ``renameCollection(..., dropTarget=True)`` — thay thế nguyên tử, người đọc không thấy
   trạng thái dở dang.
5. Tăng ``ver:hydromet_bulletins`` (Redis) để vô hiệu cache; ghi ``jobs`` (``replacing``/``replaced``).

Mã thoát / mã lỗi (dùng chung với job G0):

===== ==========================
Mã    Nghĩa
===== ==========================
0     thành công
1     kiểm tra hỏng (bản chính giữ nguyên)
3     khóa còn hạn (lượt khác đang chạy)
4     cần lùi lại (còn ``__prev``)
5     mất khóa giữa chừng
6     lỗi không lường trước
===== ==========================
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, WriteConcern
from pymongo.database import Database

log = logging.getLogger("vngis.atomic_reload")

__all__ = ["ReloadError", "ReloadBlocked", "new_run_id", "reload_bulletins_atomic",
           "BULLETIN_VALIDATOR", "BULLETIN_INDEXES", "STAGING_PREFIX", "PREV_SUFFIX"]

STAGING_PREFIX = "hydromet_bulletins__staging_"
PREV_SUFFIX = "hydromet_bulletins__prev"

#: Validator của `hydromet_bulletins` (giống bản chính; xem `pipelines/ingest/kttv/kttv_load_mongo.py`).
BULLETIN_VALIDATOR: dict = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "source_file", "source", "bulletin_type", "parse"],
    "properties": {
        "_id": {"bsonType": "objectId"},
        "source_file": {"bsonType": "string"},
        "bulletin_type": {"enum": ["tide_forecast_5day", "spring_tide_warning", "salinity_intrusion_survey",
                                   "no_text_layer", "other", "error"]},
        "bulletin_date": {"bsonType": ["date", "null"]},
    }}}

#: Index của bản chính (đặt trên collection tạm trước khi kiểm tra).
BULLETIN_INDEXES: list[dict] = [
    {"name": "source_file_unique", "key": [("source_file", ASCENDING)], "unique": True},
    {"name": "bulletin_date_idx", "key": [("bulletin_date", DESCENDING)]},
    {"name": "bulletin_type_date_idx", "key": [("bulletin_type", ASCENDING), ("bulletin_date", ASCENDING)]},
    {"name": "year_month_idx", "key": [("year", ASCENDING), ("month", ASCENDING)]},
    {"name": "bulletin_no_idx", "key": [("bulletin_no", ASCENDING)]},
    {"name": "forecast_station_date_idx",
     "key": [("forecast_water_levels.station", ASCENDING), ("forecast_water_levels.date", ASCENDING)]},
    {"name": "observed_station_date_idx",
     "key": [("observed_water_levels.station", ASCENDING), ("observation_date", ASCENDING)]},
    {"name": "reservoir_date_idx",
     "key": [("reservoirs.reservoir", ASCENDING), ("observation_date", ASCENDING)]},
    {"name": "rainfall_station_date_idx",
     "key": [("rainfall.stations.station", ASCENDING), ("observation_date", ASCENDING)]},
]


class ReloadError(RuntimeError):
    """Nạp lại thất bại; bản chính GIỮ NGUYÊN."""


class ReloadBlocked(ReloadError):
    """Còn bản ``__prev`` từ lần chạy hỏng → phải lùi lại trước (mã thoát 4)."""


def new_run_id(now: datetime | None = None) -> str:
    """`YYYYMMDDTHHMMSSZ_<6 hex>` (giờ UTC) — cùng định dạng với job G0."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(3)


def _checks(db: Database, staging: str) -> list[dict]:
    """Chạy các kiểm tra trên collection tạm; trả về danh sách `{name, ok, detail}`."""
    out: list[dict] = []
    main = db["hydromet_bulletins"]
    files = db["hydromet_bulletin_files"]

    n_stage = db[staging].count_documents({})
    n_main = main.count_documents({})
    out.append({"name": "so_ban_tin", "ok": n_stage == n_main,
                "detail": f"staging={n_stage}, bản chính={n_main}"})

    p_stage = db[staging].count_documents({"source.is_primary": True})
    p_main = main.count_documents({"source.is_primary": True})
    out.append({"name": "so_ban_chinh", "ok": p_stage == p_main,
                "detail": f"staging={p_stage}, bản chính={p_main}"})

    # SHA-256: mọi bản tin trong staging có SHA-256 khớp bản ghi trong hydromet_bulletin_files
    files_by_name = {d["source_file"]: d for d in files.find({}, {"source_file": 1, "sha256": 1})}
    missing = 0
    mismatch = 0
    for doc in db[staging].find({}, {"source_file": 1, "source.sha256": 1, "source.size_bytes": 1}):
        f = files_by_name.get(doc.get("source_file"))
        if f is None:
            missing += 1
            continue
        sha_stage = (doc.get("source") or {}).get("sha256")
        if sha_stage and f.get("sha256") and sha_stage != f["sha256"]:
            mismatch += 1
    out.append({"name": "sha256_khop_ho_so", "ok": missing == 0,
                "detail": f"{missing} bản tin không có trong hydromet_bulletin_files"})
    out.append({"name": "sha256_khop_noi_dung", "ok": mismatch == 0,
                "detail": f"{mismatch} bản tin lệch SHA-256 so với hydromet_bulletin_files"})

    # Trùng `source_file` (index unique đã chặn, nhưng kiểm tra lại)
    dup = len(list(db[staging].aggregate([
        {"$group": {"_id": "$source_file", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}, {"$count": "n"}])))
    out.append({"name": "khong_trung_source_file", "ok": dup == 0, "detail": f"{dup} source_file trùng"})
    return out


def reload_bulletins_atomic(db: Database, *, actor: str = "system", documents: list[dict] | None = None,
                            run_id: str | None = None, cache=None) -> dict:
    """Nạp lại nguyên tử `hydromet_bulletins`.

    `documents`: danh sách document bản tin mới (nếu ``None`` thì dùng lại nội dung bản chính hiện tại
    — dùng để kiểm thử quy trình mà không cần parse lại PDF).

    Trả về báo cáo `{run_id, status, checks, counts, expected}`; ném `ReloadError` khi hỏng
    (bản chính giữ nguyên) hoặc `ReloadBlocked` khi còn `__prev` cần lùi lại.
    """
    run_id = run_id or new_run_id()
    now = datetime.now(timezone.utc)
    staging = f"{STAGING_PREFIX}{run_id}"
    prev = PREV_SUFFIX
    wc = WriteConcern("majority")

    # --- chặn khi còn __prev do lần chạy hỏng giữa chừng (ADR 0007 mục 6) ---
    if prev in db.list_collection_names():
        raise ReloadBlocked(
            f"còn collection {prev} (lần nạp lại trước bị hỏng giữa chừng): phải lùi lại trước khi nạp mới")

    jobs = db["jobs"]
    jobs.update_one({"_id": run_id}, {"$set": {"job": "reload_bulletins", "run_id": run_id,
                                               "generated_at": now, "status": "running",
                                               "actor": actor, "staging": {BULLETINS: staging}}}, upsert=True)
    try:
        # --- tạo collection tạm + validator + index ---
        try:
            db.create_collection(staging, validator=BULLETIN_VALIDATOR, validationLevel="strict",
                                 validationAction="error")
        except Exception:  # noqa: BLE001 - đã tồn tại (chạy lại cùng run_id)
            db.command({"collMod": staging, "validator": BULLETIN_VALIDATOR,
                        "validationLevel": "strict", "validationAction": "error"})
        db.command({"createIndexes": staging, "indexes": BULLETIN_INDEXES})

        # --- ghi dữ liệu ---
        if documents is None:
            src = db[BULLETINS].find({})
            docs = list(src)
        else:
            docs = list(documents)
        if docs:
            db.get_collection(staging, write_concern=wc).insert_many(docs, ordered=False)

        # --- kiểm tra ---
        checks = _checks(db, staging)
        failed = [c for c in checks if not c["ok"]]
        if failed:
            jobs.update_one({"_id": run_id}, {"$set": {"status": "failed", "checks": checks,
                                                       "finished_at": datetime.now(timezone.utc)}})
            raise ReloadError("kiểm tra hỏng (bản chính giữ nguyên): "
                              + "; ".join(f"{c['name']}: {c['detail']}" for c in failed))

        # --- sao lưu bản chính sang __prev (để lùi lại được) ---
        # `$out` với `bypassDocumentValidation` để chép được cả bản tin không khớp validator cũ.
        db[BULLETINS].aggregate([{"$match": {}}, {"$out": prev}], writeConcern=wc,
                                allowDiskUse=True)

        # --- thay thế nguyên tử ---
        jobs.update_one({"_id": run_id}, {"$set": {"replacing": BULLETINS}})
        db.client.admin.command({"renameCollection": f"{db.name}.{staging}",
                                 "to": f"{db.name}.hydromet_bulletins", "dropTarget": True},
                                write_concern=wc)
        jobs.update_one({"_id": run_id}, {"$set": {"replaced": [BULLETINS], "replacing": None,
                                                   "counts": {BULLETINS: db[BULLETINS].count_documents({})},
                                                   "status": "ok",
                                                   "finished_at": datetime.now(timezone.utc)}})

        # --- vô hiệu cache ---
        if cache is not None:
            try:
                cache.bump_version(BULLETINS)
            except Exception as e:  # noqa: BLE001
                log.warning("không tăng được ver:%s trong Redis: %s", BULLETINS, e)
        return {"run_id": run_id, "status": "ok", "checks": checks,
                "counts": {BULLETINS: db[BULLETINS].count_documents({})}, "expected": {}}
    except ReloadError:
        # dọn staging, giữ nguyên bản chính
        try:
            db[staging].drop()
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("nạp lại bản tin thất bại (run_id=%s)", run_id)
        jobs.update_one({"_id": run_id}, {"$set": {"status": "failed", "error": str(e)[:500],
                                                   "finished_at": datetime.now(timezone.utc)}})
        try:
            db[staging].drop()
        except Exception:  # noqa: BLE001
            pass
        raise ReloadError(f"lỗi không lường trước: {e}") from e
