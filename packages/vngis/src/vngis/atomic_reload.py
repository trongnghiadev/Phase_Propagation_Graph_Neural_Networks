"""Nạp lại nguyên tử collection `hydromet_bulletins` (đề cương 5.6.4, mục GĐ7.6).

Dùng **cùng mẫu** với job G0 (ADR 0007):

1. Ghi vào collection tạm ``hydromet_bulletins__staging_<run_id>`` (có validator và index
   giống bản chính), rồi **áp lại sửa metadata đã duyệt** (`apply_bulletin_overrides`: K2 `bulletin_date`/
   `weekday`/`qc_note`, K6 `source.is_primary`; nguồn `audit_logs` action `bulletin_metadata`).
2. Kiểm tra: số bản tin, số bản chính, SHA-256 khớp ``hydromet_bulletin_files``.
3. Sao lưu bản chính sang ``hydromet_bulletins__prev`` (để lùi lại được nếu hỏng giữa chừng).
4. ``renameCollection(..., dropTarget=True)`` (writeConcern majority) — thay thế nguyên tử, người đọc
   không thấy trạng thái dở dang. Kiểm tra lại số bản tin sau rename rồi **xoá ``__prev``**.
5. Tăng ``ver:hydromet_bulletins`` (Redis) để vô hiệu cache; ghi ``jobs`` (``replacing``/``replaced``).

**Chính sách ``__prev`` (một chính sách cho mọi đường ghi: API, worker, `kttv_load_mongo.py`).**
``__prev`` chỉ tồn tại trong khoảng sao lưu → rename → kiểm tra. Còn ``__prev`` khi bắt đầu nghĩa là
lượt trước hỏng giữa chừng ⇒ **từ chối** (`ReloadBlocked`, mã 4) cho tới khi người vận hành lùi lại /
dọn tay; không bao giờ tự xoá ``__prev`` sót lại. Mọi đường promote dùng `promote_staging`.

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
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, WriteConcern
from pymongo.database import Database

log = logging.getLogger("vngis.atomic_reload")

__all__ = ["ReloadError", "ReloadBlocked", "new_run_id", "reload_bulletins_atomic", "promote_staging",
           "bulletin_overrides", "apply_bulletin_overrides", "OVERRIDE_ACTION",
           "validate_ingest_staging", "staging_name", "BULLETIN_VALIDATOR", "BULLETIN_INDEXES",
           "STAGING_PREFIX", "PREV_SUFFIX", "BULLETINS", "LOCK_RESOURCE"]

BULLETINS = "hydromet_bulletins"
#: Tài nguyên khoá chung của MỌI tác vụ ghi `hydromet_bulletins` (API reload, worker ingest/reload).
#: Khoá Redis ``lock:reload:bulletins``; bộ đếm fence ``lock_fences["reload:bulletins"]``.
LOCK_RESOURCE = "reload:bulletins"
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


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def staging_name(run_id: str) -> str:
    """Tên collection tạm của lượt `run_id` (chỉ chữ/số/``_``/``-`` để không chèn được tên lạ)."""
    if not _RUN_ID_RE.match(str(run_id)):
        raise ReloadError(f"run_id không hợp lệ: {run_id!r}")
    return f"{STAGING_PREFIX}{run_id}"


def new_run_id(now: datetime | None = None) -> str:
    """`YYYYMMDDTHHMMSSZ_<6 hex>` (giờ UTC) — cùng định dạng với job G0."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(3)


#: Action của `audit_logs` ghi sửa metadata bản tin đã duyệt (`vngis.qc.update_bulletin_fields`; cả bản hoàn
#: tác qua API `source = api_revert` cũng dùng action này).
OVERRIDE_ACTION = "bulletin_metadata"


def bulletin_overrides(db: Database) -> dict[str, dict[str, dict]]:
    """Sửa metadata bản tin đã duyệt, gom từ `audit_logs` (``action = bulletin_metadata``, ``state = applied``).

    **Nguồn sự thật = `audit_logs`** (không thêm collection mới): mỗi lệnh sửa qua `vngis.qc` / API đã ghi đúng một
    bản audit kèm `before`/`after`. Với mỗi (`source_file`, field) xét các bản audit theo thời gian (`at`, `_id`):
    ``original`` = `before` của bản đầu (giá trị gốc do parser sinh), ``value`` = `after` của bản cuối (giá trị
    hiệu lực, kể cả khi đã hoàn tác). Trả ``{source_file: {field: {"original", "value", "n"}}}``.
    """
    from vngis.qc import BULLETIN_FIELDS

    out: dict[str, dict[str, dict]] = {}
    if "audit_logs" not in db.list_collection_names():
        return out
    cur = db["audit_logs"].find({"action": OVERRIDE_ACTION, "state": "applied", "collection": BULLETINS},
                                {"doc_id": 1, "before": 1, "after": 1, "at": 1}).sort([("at", 1), ("_id", 1)])
    for a in cur:
        before, after = a.get("before") or {}, a.get("after") or {}
        for field, value in after.items():
            if field not in BULLETIN_FIELDS:
                continue
            ent = out.setdefault(str(a["doc_id"]), {}).get(field)
            if ent is None:
                out[str(a["doc_id"])][field] = {"original": before.get(field), "value": value, "n": 1}
            else:
                ent["value"] = value
                ent["n"] += 1
    return out


def apply_bulletin_overrides(db: Database, collection: str, *, overrides: dict | None = None) -> dict:
    """Áp lại sửa metadata đã duyệt (`bulletin_overrides`) lên `collection` (staging của lượt nạp lại/ingest).

    Idempotent: field đã đúng giá trị hiệu lực → ``unchanged``; đang bằng giá trị gốc (hoặc chưa có, khi gốc là
    ``null``) → đặt giá trị hiệu lực (``applied``); khác cả hai (nguồn PDF/parser đã đổi) → **không ghi đè**,
    báo ``conflicts`` để người xem lại; bản tin không có trong `collection` → ``missing``. Không ghi `audit_logs`
    (job tự động không ghi nhật ký — `audit_logs` vẫn là nguồn). Ghi writeConcern majority.
    """
    from vngis.qc import _MISSING, _get_path, _same

    ov = bulletin_overrides(db) if overrides is None else overrides
    coll = db.get_collection(collection, write_concern=WriteConcern("majority"))
    rep: dict[str, Any] = {"collection": collection, "documents": len(ov), "fields": 0, "applied": 0,
                           "unchanged": 0, "conflicts": [], "missing": []}
    for sf, fields in sorted(ov.items()):
        doc = coll.find_one({"source_file": sf}, {f: 1 for f in fields})
        if doc is None:
            rep["missing"].append(sf)
            continue
        sets = {}
        for f, ent in fields.items():
            rep["fields"] += 1
            got = _get_path(doc, f)
            got = None if got is _MISSING else got
            if _same(got, ent["value"]):
                rep["unchanged"] += 1
            elif _same(got, ent["original"]):
                sets[f] = ent["value"]
            else:
                rep["conflicts"].append({"source_file": sf, "field": f, "current": got,
                                         "original": ent["original"], "value": ent["value"]})
        if sets:
            coll.update_one({"_id": doc["_id"]}, {"$set": sets})
            rep["applied"] += len(sets)
    if rep["conflicts"]:
        log.warning("%d sửa metadata không áp được vì giá trị nguồn đã đổi: %s", len(rep["conflicts"]),
                    [(c["source_file"], c["field"]) for c in rep["conflicts"]])
    return rep


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


def validate_ingest_staging(db: Database, staging: str) -> list[dict]:
    """Kiểm tra collection tạm của lượt **ingest** (bản tin mới ⇒ số bản tin được phép tăng).

    Khác `_checks` (nạp lại cùng dữ liệu ⇒ số bản tin phải bằng bản chính):

    * số bản tin > 0;
    * không lệch SHA-256 so với `hydromet_bulletin_files` (với bản tin có cả hai giá trị);
    * số bản chính khớp `hydromet_bulletin_files` (nếu hồ sơ có đánh dấu bản chính);
    * không trùng `source_file`.
    """
    out: list[dict] = []
    col = db[staging]
    n_docs = col.count_documents({})
    out.append({"name": "co_ban_tin", "ok": n_docs > 0, "detail": f"staging={n_docs}"})
    sha_by_file: dict[str, str] = {}
    n_primary_files = 0
    if "hydromet_bulletin_files" in db.list_collection_names():
        for f in db["hydromet_bulletin_files"].find({}, {"_id": 0, "sha256": 1, "sha256_file": 1, "source_file": 1,
                                                         "path": 1, "nguon.la_ban_chinh": 1, "is_primary": 1}):
            sha, name = f.get("sha256") or f.get("sha256_file"), f.get("source_file") or f.get("path")
            if sha and name:
                sha_by_file[name] = sha
            if f.get("is_primary") or (f.get("nguon") or {}).get("la_ban_chinh"):
                n_primary_files += 1
    mismatch = n_primary = 0
    for d in col.find({}, {"_id": 0, "source_file": 1, "source.sha256": 1, "source.is_primary": 1,
                           "source.la_ban_chinh": 1}):
        src = d.get("source") or {}
        if src.get("is_primary") or src.get("la_ban_chinh"):
            n_primary += 1
        got, want = src.get("sha256"), sha_by_file.get(d.get("source_file"))
        if got and want and got != want:
            mismatch += 1
    out.append({"name": "sha256_khop_noi_dung", "ok": mismatch == 0,
                "detail": f"{mismatch} bản tin lệch SHA-256 so với hydromet_bulletin_files"})
    out.append({"name": "so_ban_chinh", "ok": not n_primary_files or n_primary == n_primary_files,
                "detail": f"staging={n_primary}, hồ sơ={n_primary_files}"})
    dup = len(list(col.aggregate([{"$group": {"_id": "$source_file", "n": {"$sum": 1}}},
                                  {"$match": {"n": {"$gt": 1}}}, {"$count": "n"}])))
    out.append({"name": "khong_trung_source_file", "ok": dup == 0, "detail": f"{dup} source_file trùng"})
    return out


def promote_staging(db: Database, staging: str, *, guard=None, target: str = BULLETINS,
                    prev: str = PREV_SUFFIX) -> dict:
    """Thay `target` bằng `staging` một cách nguyên tử (dùng chung API / worker / loader).

    1. Còn `prev` → `ReloadBlocked` (lượt trước hỏng giữa chừng; không tự xoá).
    2. ``guard.check()`` → sao lưu `target` sang `prev` (``$out``, writeConcern majority).
    3. ``guard.check()`` **ngay trước** ``renameCollection(dropTarget=True)`` (writeConcern majority).
    4. Kiểm tra số bản tin của `target` = số bản tin của staging trước rename; khớp → xoá `prev`.
       Không khớp → giữ `prev` (lượt sau bị chặn cho tới khi người vận hành xem lại) và ném `ReloadError`.

    Giới hạn: giữa ``guard.check()`` và lệnh rename vẫn có một khoảng rất ngắn; khoá có watchdog gia hạn
    mỗi TTL/3 nên chỉ mất khoá khi Redis/mạng hỏng lâu hơn TTL. MongoDB không có rename có điều kiện.
    """
    wc = WriteConcern("majority")
    if prev in db.list_collection_names():
        raise ReloadBlocked(
            f"còn collection {prev} (lần nạp lại trước bị hỏng giữa chừng): phải lùi lại trước khi nạp mới")
    if staging not in db.list_collection_names():
        raise ReloadError(f"không có collection tạm {staging}")
    n_stage = db[staging].count_documents({})
    if guard is not None:
        guard.check()
    has_target = target in db.list_collection_names()
    backed_up = False
    try:
        if has_target:
            db.get_collection(target, write_concern=wc).aggregate([{"$match": {}}, {"$out": prev}],
                                                                 allowDiskUse=True)
            backed_up = True
        if guard is not None:
            guard.check()
    except BaseException:
        # Chưa gửi lệnh rename ⇒ bản chính chắc chắn còn nguyên: bản sao `prev` vừa tạo không cần giữ.
        if backed_up:
            db.get_collection(prev, write_concern=wc).drop()
        raise
    # Từ đây lệnh rename có thể đã áp dụng dù client báo lỗi (mạng) ⇒ lỗi thì GIỮ `prev`.
    db.client.admin.command({"renameCollection": f"{db.name}.{staging}", "to": f"{db.name}.{target}",
                             "dropTarget": True, "writeConcern": {"w": "majority"}})
    n_after = db[target].count_documents({})
    if n_after != n_stage:
        raise ReloadError(f"sau rename {target} có {n_after} bản tin, staging có {n_stage}: "
                          f"giữ {prev} để lùi lại")
    if has_target:
        db.get_collection(prev, write_concern=wc).drop()
    return {"target": target, "count": n_after, "backup_dropped": has_target}


def _override_summary(rep: dict) -> dict:
    """Tóm tắt báo cáo áp sửa metadata để ghi `jobs` (giá trị có thể là datetime — giữ nguyên kiểu BSON)."""
    return {"applied": rep["applied"], "unchanged": rep["unchanged"], "fields": rep["fields"],
            "conflicts": [{k: c[k] for k in ("source_file", "field")} for c in rep["conflicts"]],
            "missing": rep["missing"][:50]}


def reload_bulletins_atomic(db: Database, *, actor: str = "system", documents: list[dict] | None = None,
                            run_id: str | None = None, cache=None, guard=None,
                            apply_overrides: bool = True) -> dict:
    """Nạp lại nguyên tử `hydromet_bulletins`.

    `documents`: danh sách document bản tin mới (nếu ``None`` thì dùng lại nội dung bản chính hiện tại
    — dùng để kiểm thử quy trình mà không cần parse lại PDF).

    Trả về báo cáo `{run_id, status, checks, counts, expected}`; ném `ReloadError` khi hỏng
    (bản chính giữ nguyên) hoặc `ReloadBlocked` khi còn `__prev` cần lùi lại.
    """
    run_id = run_id or new_run_id()
    now = datetime.now(timezone.utc)
    staging = staging_name(run_id)
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
        # `key` phải là document có thứ tự (dict), không phải danh sách cặp (MongoDB báo TypeMismatch).
        db.command({"createIndexes": staging,
                    "indexes": [{**ix, "key": dict(ix["key"])} for ix in BULLETIN_INDEXES]})

        # --- ghi dữ liệu ---
        if documents is None:
            src = db[BULLETINS].find({})
            docs = list(src)
        else:
            docs = list(documents)
        if docs:
            db.get_collection(staging, write_concern=wc).insert_many(docs, ordered=False)

        # --- áp lại sửa metadata đã duyệt (K2/K6, nguồn `audit_logs`) trước khi kiểm tra ---
        overrides = apply_bulletin_overrides(db, staging) if apply_overrides else None
        if overrides is not None:
            jobs.update_one({"_id": run_id}, {"$set": {"overrides": _override_summary(overrides)}})

        # --- kiểm tra ---
        checks = _checks(db, staging)
        failed = [c for c in checks if not c["ok"]]
        if failed:
            jobs.update_one({"_id": run_id}, {"$set": {"status": "failed", "checks": checks,
                                                       "finished_at": datetime.now(timezone.utc)}})
            raise ReloadError("kiểm tra hỏng (bản chính giữ nguyên): "
                              + "; ".join(f"{c['name']}: {c['detail']}" for c in failed))

        # --- kiểm tra khoá → sao lưu __prev → rename (majority) → kiểm tra → xoá __prev ---
        jobs.update_one({"_id": run_id}, {"$set": {"replacing": BULLETINS}})
        promote_staging(db, staging, guard=guard, prev=prev)
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
                "counts": {BULLETINS: db[BULLETINS].count_documents({})}, "expected": {},
                "overrides": _override_summary(overrides) if overrides is not None else None}
    except ReloadError:
        # dọn staging, giữ nguyên bản chính
        try:
            db[staging].drop()
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "LockLost":
            # Mất khoá (vngis.locks.LockLost): giữ nguyên loại lỗi để người gọi ghi `state="lost"`.
            jobs.update_one({"_id": run_id}, {"$set": {"status": "lost", "error": str(e)[:500],
                                                       "finished_at": datetime.now(timezone.utc)}})
            try:
                db[staging].drop()
            except Exception:  # noqa: BLE001
                pass
            raise
        log.exception("nạp lại bản tin thất bại (run_id=%s)", run_id)
        jobs.update_one({"_id": run_id}, {"$set": {"status": "failed", "error": str(e)[:500],
                                                   "finished_at": datetime.now(timezone.utc)}})
        try:
            db[staging].drop()
        except Exception:  # noqa: BLE001
            pass
        raise ReloadError(f"lỗi không lường trước: {e}") from e
