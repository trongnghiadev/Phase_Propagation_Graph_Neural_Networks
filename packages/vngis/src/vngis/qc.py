"""Ghi quyết định QC có kiểm toán (pymongo): đổi `status`/`corrected_value`/`reason`/`decided_by` của một cờ
trong `qc_flags` bằng `find_one_and_update` có điều kiện `version` (khóa lạc quan), đồng thời ghi `audit_logs`.

Dùng chung cho CLI (`pipelines/qc/qc_decide.py`) và API về sau.

    from vngis.db import get_db
    from vngis import qc
    db = get_db()
    flag = qc.get_flag(db, "tide_extremes:BIEN_HOA:high:2021-04-17T02:00:00Z")
    qc.decide(db, flag["_id"], "rejected", reason="…", decided_by="qc-reviewer", expected_version=flag["version"])

Mọi lệnh ghi (`decide`, `create_manual_flag`, `retire_flag`, `unretire_flag`, `update_bulletin_fields`) đi qua `_audited`, với
write concern `majority`:

- Server hỗ trợ transaction (replica set, mongos): lệnh ghi dữ liệu và bản ghi `audit_logs` (`state: "applied"`)
  nằm trong MỘT transaction (write concern `majority`).
- Một node (máy dev): chèn `audit_logs` với `state: "pending"` trước, rồi ghi dữ liệu có điều kiện. Thành công →
  đặt `applied` bằng điều kiện `{_id, state: "pending"}` (không khớp → `AuditStateError`). Chắc chắn KHÔNG ghi được
  (điều kiện không khớp, trùng khóa) → xóa bản pending và báo xung đột. Lỗi mơ hồ (mạng, timeout: không biết lệnh đã
  ghi hay chưa) → GIỮ bản pending, ném lỗi; `reconcile_pending` sau đó so NỘI DUNG hiện tại với `after` để đặt
  `applied` hoặc xóa, chỉ với bản pending cũ hơn `grace_s` (mặc định `qc.audit_pending_grace_s` = 600 s) để không
  đụng lệnh đang chạy.

Bản ghi `audit_logs`: `at`, `actor`, `action` (`qc_decision` | `qc_flag_create` | `qc_flag_retire` | `qc_flag_unretire` |
`bulletin_metadata` | `review_confirmed`), `source`, `collection`, `doc_id`, `target`, `before`/`after`, `reason`,
`state`. Với cờ: `before`/`after` = {status, corrected_value, reason, decided_by, version, retired, value, value_mismatch,
current_value, effective_value, effective_qc}; với bản tin: {field: giá trị}.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pymongo import ReturnDocument, WriteConcern
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, DuplicateKeyError, OperationFailure

from . import qc_rules as R

__all__ = ["QcError", "FlagNotFound", "FlagExists", "VersionConflict", "FieldConflict", "AuditStateError",
           "ensure_collections", "get_flag", "find_flags", "decide", "create_manual_flag", "retire_flag", "unretire_flag",
           "update_bulletin_fields", "record_review", "reconcile_pending", "flag_id", "BULLETIN_FIELDS"]

flag_id = R.flag_id
WC = WriteConcern("majority")


class QcError(RuntimeError):
    pass


class FlagNotFound(QcError):
    pass


class FlagExists(QcError):
    pass


class VersionConflict(QcError):
    """Cờ đã bị sửa (version khác version mong đợi): đọc lại rồi quyết định lại."""

    def __init__(self, flag_id: str, expected: int, current: int | None):
        super().__init__(f"xung đột version cờ {flag_id}: mong đợi {expected}, hiện tại {current}")
        self.flag_id, self.expected, self.current = flag_id, expected, current


class FieldConflict(QcError):
    """Giá trị hiện tại của một field bản tin khác giá trị cũ mong đợi (không thay đổi gì)."""

    def __init__(self, source_file: str, field: str, expected: Any, current: Any):
        super().__init__(f"xung đột {source_file}: field {field} mong đợi {expected!r}, hiện có {current!r}")
        self.source_file, self.field, self.expected, self.current = source_file, field, expected, current


class AuditStateError(QcError):
    """Bản ghi audit pending không còn (bị reconcile/xóa) khi đặt applied: dữ liệu ĐÃ ghi, cần kiểm tra tay."""


class _NotApplied(Exception):
    """Chắc chắn lệnh ghi không có hiệu lực; mang lỗi để ném ra cho người gọi."""

    def __init__(self, exc: Exception):
        super().__init__(str(exc))
        self.exc = exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _c(db: Database, name: str) -> Collection:
    return db.get_collection(name, write_concern=WC)


#: Mức kiểm tra validator — khớp `schemas/mongo/{qc_flags,audit_logs}/v1.json` và migration 0004/0005:
#: ``moderate`` = kiểm tra mọi lệnh chèn và mọi lệnh sửa document đang hợp lệ, không chặn sửa document cũ
#: (trước khi có validator) không hợp lệ. Dữ liệu hiện có đều hợp lệ nên thực tế chặt như ``strict``.
VALIDATION_LEVEL = "moderate"


def _ensure(db: Database, name: str, validator: dict, indexes: list[dict]) -> Collection:
    try:
        db.create_collection(name, validator=validator, validationLevel=VALIDATION_LEVEL, validationAction="error")
    except CollectionInvalid:
        db.command({"collMod": name, "validator": validator, "validationLevel": VALIDATION_LEVEL,
                    "validationAction": "error"})
    except OperationFailure as e:  # NamespaceExists khi tạo song song
        if e.code != 48:
            raise
    if indexes:
        db.command({"createIndexes": name, "indexes": indexes})
    return db[name]


def ensure_collections(db: Database) -> None:
    """Tạo (hoặc cập nhật validator) `qc_flags`, `audit_logs` và index. Gọi được nhiều lần."""
    _ensure(db, R.FLAGS, R.QC_FLAGS_VALIDATOR, R.QC_FLAGS_INDEXES)
    _ensure(db, R.AUDIT, R.AUDIT_LOGS_VALIDATOR, R.AUDIT_LOGS_INDEXES)


def get_flag(db: Database, fid: str) -> dict | None:
    return db[R.FLAGS].find_one({"_id": fid})


def find_flags(db: Database, station: str | None = None, status: str | None = None, rule: str | None = None,
               active_only: bool = False, limit: int = 100, mismatch_only: bool = False) -> list[dict]:
    q: dict[str, Any] = {"collection": R.COLLECTION}
    if station:
        q["station"] = station
    if status:
        q["status"] = status
    if rule:
        q["rules.rule"] = rule
    if mismatch_only:
        q["value_mismatch"] = True
    docs = db[R.FLAGS].find(q).sort([("station", 1), ("ts", 1), ("kind", 1)])
    out = []
    for d in docs:
        if not active_only or R.is_active(d):
            out.append(d)
            if len(out) >= limit:
                break
    return out


def _state(doc: dict) -> dict:
    raw = doc.get("value")
    try:
        ev, eq = R.effective(raw, "ok" if raw is not None else "missing", doc.get("status"), doc.get("corrected_value"))
    except ValueError:
        ev, eq = None, None
    return {"status": doc.get("status"), "corrected_value": doc.get("corrected_value"), "reason": doc.get("reason"),
            "decided_by": doc.get("decided_by"), "version": doc.get("version"), "retired": bool(doc.get("retired")),
            "value": raw, "value_mismatch": bool(doc.get("value_mismatch")), "current_value": doc.get("current_value"),
            "decided_value": doc.get("decided_value"),
            "effective_value": ev, "effective_qc": eq}


def _current_raw(db: Database, flag: dict) -> Any:
    """Giá trị gốc hiện tại của phần tử của cờ — giá trị mà quyết định sẽ gắn vào.

    Thứ tự ưu tiên (mỗi nguồn là "mới nhất" hơn nguồn sau):

    1. `current_value` của cờ khi cờ đang ở trạng thái `value_mismatch`: đây là giá trị job QC ghi lại khi
       phát hiện lệch, và job QC đọc `tide_extremes` NÊN nó không bao giờ cũ hơn `raw_value` trong
       `tide_extremes`. Nếu dùng `raw_value` thay cho nó, khi G0 chưa chạy lại sau lần nạp bản tin mới thì
       `raw_value` vẫn là giá trị CŨ → quyết định lại gắn nhầm giá trị cũ → job QC gắn `value_mismatch`
       lần nữa → cờ mắc kẹt vô hạn, không có đường thoát.
    2. `raw_value` trong `tide_extremes` (G0 mới; null = ô trống), không có field thì `value`.
    3. Phần tử không còn trong `tide_extremes` → `current_value` của cờ, cuối cùng `value` của cờ.
    """
    if flag.get("value_mismatch") and "current_value" in flag:
        return flag.get("current_value")
    e = db[R.COLLECTION].find_one({"meta.station": flag["station"], "meta.kind": flag["kind"], "ts": flag["ts"]},
                                  {"value": 1, "raw_value": 1})
    if e is not None:
        return e["raw_value"] if "raw_value" in e else e.get("value")
    if "current_value" in flag:
        return flag.get("current_value")
    return flag.get("value")


def _target(doc: dict) -> dict:
    return {"collection": doc.get("collection"), "station": doc.get("station"), "kind": doc.get("kind"),
            "ts": doc.get("ts")}


def _supports_txn(db: Database) -> bool:
    hello = db.client.admin.command("hello")
    return bool(hello.get("setName")) or hello.get("msg") == "isdbgrid"


def _audited(db: Database, audit: dict, write: Callable[[Any], Any], after: Callable[[Any], Any] | None = None):
    """Chạy `write(session)` kèm bản ghi `audit_logs` (xem docstring module).

    `write` trả về kết quả, hoặc ném `_NotApplied(lỗi)` khi chắc chắn không ghi được; lỗi khác coi là mơ hồ.
    `after(kết quả)` (tùy chọn) tính lại field `after` của audit từ kết quả thật.
    """
    logs = _c(db, R.AUDIT)
    if _supports_txn(db):
        with db.client.start_session() as s:
            def body(sess):
                res = write(sess)
                doc = {**audit, "state": "applied"}
                if after is not None:
                    doc["after"] = after(res)
                logs.insert_one(doc, session=sess)
                return res
            try:
                return s.with_transaction(body, write_concern=WC)
            except _NotApplied as e:
                raise e.exc from None
    aid = logs.insert_one({**audit, "state": "pending"}).inserted_id
    try:
        res = write(None)
    except _NotApplied as e:
        logs.delete_one({"_id": aid, "state": "pending"})
        raise e.exc from None
    # lỗi mơ hồ (mạng, timeout…) đi thẳng ra ngoài: GIỮ bản pending cho reconcile_pending
    upd = {"state": "applied"}
    if after is not None:
        upd["after"] = after(res)
    if logs.update_one({"_id": aid, "state": "pending"}, {"$set": upd}).matched_count == 0:
        raise AuditStateError(f"audit {aid} ({audit['action']} {audit['doc_id']}) không còn pending khi đặt applied; "
                              f"dữ liệu đã ghi – kiểm tra tay audit_logs")
    return res


def _flag_update(db: Database, fid: str, expected: int, update: dict) -> Callable[[Any], dict]:
    flags = _c(db, R.FLAGS)

    def write(sess):
        res = flags.find_one_and_update({"_id": fid, "version": expected}, update,
                                        return_document=ReturnDocument.AFTER, session=sess)
        if res is None:
            cur = flags.find_one({"_id": fid}, {"version": 1}, session=sess)
            raise _NotApplied(VersionConflict(fid, expected, cur and cur.get("version")))
        return res
    return write


def decide(db: Database, fid: str, status: str, *, reason: str, decided_by: str, expected_version: int,
           corrected_value: float | None = None, source: str = "cli", now: datetime | None = None) -> dict:
    """Ghi quyết định cho cờ `fid` nếu `version` hiện tại = `expected_version`; trả về cờ sau khi ghi.

    `status` ∈ suspect (bỏ quyết định) | confirmed_ok | rejected | corrected (cần `corrected_value`).
    Quyết định gắn với giá trị gốc HIỆN TẠI của phần tử (`_current_raw`): ghi `value` = giá trị đó,
    `decided_value` = giá trị đó, `value_mismatch: false`, `current_value: null` (thoát trạng thái
    value_mismatch: người đã xem giá trị mới). Các field này có trong `before`/`after` của audit.

    `decided_value` là giá trị mà quyết định gắn vào; job QC so giá trị gốc hiện tại với nó (không phải với
    `value`, vốn là giá trị lúc gắn cờ) để gắn `value_mismatch`. Nhờ vậy người xem xong một lần thì cờ KHÔNG bị
    gắn lại mismatch vô hạn khi `tide_extremes` chưa kịp cập nhật (đường thoát duy nhất của cờ value_mismatch).
    Lỗi: `ValueError` (quyết định sai), `FlagNotFound`, `VersionConflict` (không thay đổi gì).
    """
    if corrected_value is not None and not isinstance(corrected_value, bool):
        corrected_value = float(corrected_value)
    R.validate_decision(status, corrected_value, reason, decided_by)
    now = now or _now()
    before = get_flag(db, fid)
    if before is None:
        raise FlagNotFound(f"không có cờ {fid}")
    if before.get("version") != expected_version:
        raise VersionConflict(fid, expected_version, before.get("version"))
    bound = _current_raw(db, before)
    # `value` giữ nguyên giá trị lúc gắn cờ (không ghi đè) để job QC phân biệt "giá trị CŨ, G0 chưa cập nhật" với
    # "giá trị MỚI"; `decided_value` là giá trị quyết định gắn vào.
    # `current_value` = giá trị job QC đã báo là lệch; GIỮ LẠI sau khi quyết định để job QC biết người xem đã thấy
    # giá trị đó (không gắn lại mismatch vô hạn).
    seen = before.get("current_value")
    after_fields = {"status": status, "corrected_value": corrected_value, "reason": reason.strip(),
                    "decided_by": decided_by.strip(), "decided_at": now, "updated_at": now,
                    "decided_value": bound, "value_mismatch": False, "current_value": seen}
    audit = {"at": now, "actor": decided_by.strip(), "action": "qc_decision", "source": source,
             "collection": R.FLAGS, "doc_id": fid, "target": _target(before), "before": _state(before),
             "after": _state({**before, **after_fields, "version": expected_version + 1}),
             "reason": reason.strip()}
    return _audited(db, audit, _flag_update(db, fid, expected_version, {"$set": after_fields, "$inc": {"version": 1}}),
                    _state)


def create_manual_flag(db: Database, station: str, kind: str, ts: datetime, *, reason: str, decided_by: str,
                       source: str = "cli", now: datetime | None = None) -> dict:
    """Tạo cờ thủ công (status suspect, origin manual) cho một phần tử có thật trong `tide_extremes`.

    Dùng khi người kiểm tra tìm ra lỗi mà quy tắc tự động không bắt (ví dụ K2–K6); sau đó `decide`.
    Cờ thủ công luôn có hiệu lực (`qc_rules.is_active`), kể cả khi quy tắc tự động gắn rồi bỏ.
    """
    if not (reason or "").strip() or not (decided_by or "").strip():
        raise ValueError("cần reason và decided_by")
    if ts.tzinfo is None:
        raise ValueError("ts phải có múi giờ")
    ts = ts.astimezone(timezone.utc)
    els = list(db[R.COLLECTION].find({"meta.station": station, "meta.kind": kind, "ts": ts},
                                     {"value": 1, "raw_value": 1, "ts_placeholder": 1, "source_file": 1}).limit(2))
    if len(els) != 1:
        raise FlagNotFound(f"{R.COLLECTION}: {len(els)} phần tử khớp ({station}, {kind}, {R.iso_utc(ts)}), cần đúng 1")
    e = els[0]
    now = now or _now()
    fid = R.flag_id(station, kind, ts)
    doc = {"_id": fid, "collection": R.COLLECTION, "station": station, "kind": kind, "ts": ts,
           "ts_placeholder": bool(e.get("ts_placeholder")),
           "value": e["raw_value"] if "raw_value" in e else e.get("value"), "source_file": e.get("source_file"),
           "origin": "manual", "rules": [], "rules_version": None, "auto_cleared": False,
           "status": "suspect", "corrected_value": None, "reason": reason.strip(), "decided_by": decided_by.strip(),
           "decided_at": now, "first_flagged_run": None, "last_seen_run": None, "created_at": now,
           "updated_at": now, "version": 1}
    audit = {"at": now, "actor": decided_by.strip(), "action": "qc_flag_create", "source": source,
             "collection": R.FLAGS, "doc_id": fid, "target": _target(doc), "before": None, "after": _state(doc),
             "reason": reason.strip()}
    flags = _c(db, R.FLAGS)

    def write(sess):
        try:
            flags.insert_one(doc, session=sess)
        except DuplicateKeyError:
            raise _NotApplied(FlagExists(f"đã có cờ {fid}")) from None
        return doc
    return _audited(db, audit, write)


def retire_flag(db: Database, fid: str, *, reason: str, actor: str, expected_version: int, source: str = "cli",
                now: datetime | None = None) -> dict:
    """Đánh dấu cờ mồ côi (phần tử không còn trong `tide_extremes`, ví dụ sau khi đổi bản chính) là `retired`:
    cờ hết hiệu lực, G0 thôi chặn, job QC không đụng tới nữa. Chỉ được khi phần tử thật sự không còn."""
    if not (reason or "").strip() or not (actor or "").strip():
        raise ValueError("cần reason và actor")
    now = now or _now()
    before = get_flag(db, fid)
    if before is None:
        raise FlagNotFound(f"không có cờ {fid}")
    if before.get("version") != expected_version:
        raise VersionConflict(fid, expected_version, before.get("version"))
    n = db[R.COLLECTION].count_documents({"meta.station": before["station"], "meta.kind": before["kind"],
                                          "ts": before["ts"]})
    if n:
        raise ValueError(f"phần tử của cờ {fid} vẫn còn trong {R.COLLECTION}: không phải cờ mồ côi")
    fields = {"retired": True, "retired_at": now, "retired_by": actor.strip(), "retired_reason": reason.strip(),
              "updated_at": now}
    audit = {"at": now, "actor": actor.strip(), "action": "qc_flag_retire", "source": source,
             "collection": R.FLAGS, "doc_id": fid, "target": _target(before), "before": _state(before),
             "after": _state({**before, **fields, "version": expected_version + 1}), "reason": reason.strip()}
    return _audited(db, audit, _flag_update(db, fid, expected_version, {"$set": fields, "$inc": {"version": 1}}),
                    _state)


def unretire_flag(db: Database, fid: str, *, reason: str, actor: str, expected_version: int, source: str = "cli",
                  now: datetime | None = None) -> dict:
    """Gỡ `retired` khi phần tử của cờ đã có lại trong `tide_extremes` (job QC cảnh báo trường hợp này). Cờ có hiệu
    lực trở lại với quyết định cũ; `value_mismatch` được tính lại ở lần chạy QC/G0 sau."""
    if not (reason or "").strip() or not (actor or "").strip():
        raise ValueError("cần reason và actor")
    now = now or _now()
    before = get_flag(db, fid)
    if before is None:
        raise FlagNotFound(f"không có cờ {fid}")
    if before.get("version") != expected_version:
        raise VersionConflict(fid, expected_version, before.get("version"))
    if not before.get("retired"):
        raise ValueError(f"cờ {fid} không ở trạng thái retired")
    n = db[R.COLLECTION].count_documents({"meta.station": before["station"], "meta.kind": before["kind"],
                                          "ts": before["ts"]})
    if not n:
        raise ValueError(f"phần tử của cờ {fid} chưa có lại trong {R.COLLECTION}")
    fields = {"retired": False, "unretired_at": now, "unretired_by": actor.strip(), "unretired_reason": reason.strip(),
              "updated_at": now}
    audit = {"at": now, "actor": actor.strip(), "action": "qc_flag_unretire", "source": source,
             "collection": R.FLAGS, "doc_id": fid, "target": _target(before), "before": _state(before),
             "after": _state({**before, **fields, "version": expected_version + 1}), "reason": reason.strip()}
    return _audited(db, audit, _flag_update(db, fid, expected_version, {"$set": fields, "$inc": {"version": 1}}),
                    _state)


# Field metadata của `hydromet_bulletins` được phép sửa qua kiểm duyệt (không sửa số liệu bản tin), kèm kiểu.
BULLETIN_FIELDS = ("source.is_primary", "bulletin_date", "weekday", "qc_note")
_MISSING = object()


def _check_type(field: str, v: Any, role: str) -> None:
    """source.is_primary: bool; bulletin_date: datetime có múi giờ; weekday, qc_note: str hoặc null.
    Giá trị cũ được phép null (field chưa có) với mọi field."""
    if v is None and (role == "cũ" or field in ("weekday", "qc_note")):
        return
    ok = {"source.is_primary": isinstance(v, bool),
          "bulletin_date": isinstance(v, datetime) and v.tzinfo is not None,
          "weekday": isinstance(v, str), "qc_note": isinstance(v, str)}[field]
    if not ok:
        need = {"source.is_primary": "bool", "bulletin_date": "datetime có múi giờ",
                "weekday": "chuỗi hoặc null", "qc_note": "chuỗi hoặc null"}[field]
        raise ValueError(f"{field}: giá trị {role} {v!r} sai kiểu (cần {need})")


def _norm(v: Any) -> Any:
    """datetime không múi giờ (pymongo mặc định) coi là UTC, để so với datetime có múi giờ."""
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return v


def _same(a: Any, b: Any) -> bool:
    a, b = _norm(a), _norm(b)
    return type(a) is type(b) and a == b if isinstance(a, bool) or isinstance(b, bool) else a == b


def _get_path(doc: dict, path: str) -> Any:
    cur: Any = doc
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return _MISSING
        cur = cur[k]
    return cur


def update_bulletin_fields(db: Database, source_file: str, changes: dict[str, tuple[Any, Any]], *, reason: str,
                           actor: str, source: str = "cli", now: datetime | None = None) -> dict:
    """Sửa field metadata (`BULLETIN_FIELDS`) của MỘT bản tin, có điều kiện giá trị cũ (khóa lạc quan theo giá trị)
    và ghi `audit_logs` (action `bulletin_metadata`, before/after từng field).

    `changes`: {field: (giá trị cũ mong đợi, giá trị mới)}; giá trị cũ `None` khớp cả field chưa có.
    Lỗi: `ValueError` (field không được phép, sai kiểu, thiếu lý do), `FlagNotFound` (không có bản tin),
    `FieldConflict` (giá trị hiện tại khác giá trị cũ mong đợi; không thay đổi gì).
    """
    bad = sorted(set(changes) - set(BULLETIN_FIELDS))
    if bad or not changes:
        raise ValueError(f"field không được phép sửa: {bad or 'rỗng'} (chỉ {BULLETIN_FIELDS})")
    if not (reason or "").strip() or not (actor or "").strip():
        raise ValueError("cần reason và actor")
    for f, (old, new) in changes.items():
        _check_type(f, old, "cũ")
        _check_type(f, new, "mới")
    coll = _c(db, R.COLLECTION_BULLETINS)
    cur = coll.find_one({"source_file": source_file}, {f: 1 for f in changes})
    if cur is None:
        raise FlagNotFound(f"không có bản tin {source_file}")
    now = now or _now()
    q: dict[str, Any] = {"_id": cur["_id"]}
    for f, (old, _new) in changes.items():
        got = _get_path(cur, f)
        got = None if got is _MISSING else got
        if not _same(got, old):
            raise FieldConflict(source_file, f, old, got)
        q[f] = old
    before = {f: old for f, (old, _n) in changes.items()}
    after = {f: new for f, (_o, new) in changes.items()}
    audit = {"at": now, "actor": actor.strip(), "action": "bulletin_metadata", "source": source,
             "collection": R.COLLECTION_BULLETINS, "doc_id": source_file, "target": {"source_file": source_file},
             "before": before, "after": after, "reason": reason.strip()}

    def write(sess):
        res = coll.update_one(q, {"$set": after}, session=sess)
        if res.matched_count != 1:
            now_doc = coll.find_one({"_id": cur["_id"]}, {f: 1 for f in changes}, session=sess) or {}
            f = next(iter(changes))
            got = _get_path(now_doc, f)
            raise _NotApplied(FieldConflict(source_file, f, changes[f][0], None if got is _MISSING else got))
        return res
    _audited(db, audit, write)
    return {**audit, "state": "applied"}


def record_review(db: Database, collection: str, doc_id: str, *, reason: str, actor: str, target: dict | None = None,
                  source: str = "cli", now: datetime | None = None) -> dict:
    """Ghi `audit_logs` cho một lần kiểm tra tay KHÔNG đổi dữ liệu (action `review_confirmed`, before = after)."""
    if not (reason or "").strip() or not (actor or "").strip():
        raise ValueError("cần reason và actor")
    now = now or _now()
    doc = {"at": now, "actor": actor.strip(), "action": "review_confirmed", "source": source,
           "collection": collection, "doc_id": doc_id, "target": target, "before": None, "after": None,
           "reason": reason.strip(), "state": "applied"}
    _c(db, R.AUDIT).insert_one(doc)
    return doc


# ---------------------------------------------------------------------------
# Đối soát bản ghi pending (chỉ chế độ một node)
# ---------------------------------------------------------------------------
def _flag_matches(a: dict, cur: dict | None) -> bool:
    """Cờ hiện tại có phản ánh đúng thay đổi trong audit `a` không (so nội dung, không so version)."""
    if cur is None:
        return False
    want = a.get("after") or {}
    at = _norm(a["at"])
    if a["action"] == "qc_decision":
        return (cur.get("status") == want.get("status") and cur.get("decided_by") == want.get("decided_by")
                and cur.get("corrected_value") == want.get("corrected_value")
                and cur.get("reason") == want.get("reason") and _norm(cur.get("decided_at")) == at
                and ("value" not in want or cur.get("value") == want["value"])
                and ("decided_value" not in want or cur.get("decided_value") == want["decided_value"])
                and ("value_mismatch" not in want or bool(cur.get("value_mismatch")) == bool(want["value_mismatch"])))
    if a["action"] == "qc_flag_create":
        return (cur.get("origin") == "manual" and _norm(cur.get("created_at")) == at
                and cur.get("reason") == want.get("reason") and cur.get("decided_by") == want.get("decided_by"))
    if a["action"] == "qc_flag_retire":
        return bool(cur.get("retired")) and _norm(cur.get("retired_at")) == at
    if a["action"] == "qc_flag_unretire":
        return not cur.get("retired") and _norm(cur.get("unretired_at")) == at
    raise ValueError(a["action"])


def _bulletin_matches(a: dict, cur: dict | None) -> bool:
    if cur is None:
        return False
    for f, v in (a.get("after") or {}).items():
        got = _get_path(cur, f)
        if not _same(None if got is _MISSING else got, v):
            return False
    return True


def reconcile_pending(db: Database, grace_s: float = 600, now: datetime | None = None) -> dict[str, int]:
    """Xử lý bản ghi `audit_logs` còn `pending` cũ hơn `grace_s` giây (process chết hoặc lỗi mơ hồ giữa hai bước):
    dữ liệu hiện tại khớp `after` của audit → `applied`; không khớp → xóa (thay đổi đã không xảy ra).

    So theo `action`: `qc_decision` (status, decided_by, corrected_value, reason, decided_at = at, value, value_mismatch), `qc_flag_create`
    (cờ thủ công có created_at = at, cùng reason/decided_by), `qc_flag_retire` (retired, retired_at = at),
    `qc_flag_unretire` (không retired, unretired_at = at),
    `bulletin_metadata` (mọi field trong `after` có giá trị đúng như vậy). Action khác: giữ nguyên, đếm `unknown`.
    Bản pending trẻ hơn `grace_s` không bị đụng (`young`). Cập nhật/xóa đều có điều kiện `state: "pending"`.
    """
    now = now or _now()
    logs = _c(db, R.AUDIT)
    out = {"applied": 0, "deleted": 0, "young": 0, "unknown": 0}
    cutoff = now - timedelta(seconds=grace_s)
    for a in logs.find({"state": "pending"}):
        if _norm(a["at"]) > cutoff:
            out["young"] += 1
            continue
        if a["action"] in ("qc_decision", "qc_flag_create", "qc_flag_retire", "qc_flag_unretire"):
            done = _flag_matches(a, db[R.FLAGS].find_one({"_id": a["doc_id"]}))
        elif a["action"] == "bulletin_metadata":
            done = _bulletin_matches(a, db[R.COLLECTION_BULLETINS].find_one(
                {"source_file": a["doc_id"]}, {f: 1 for f in (a.get("after") or {})}))
        else:
            out["unknown"] += 1
            continue
        if done:
            n = logs.update_one({"_id": a["_id"], "state": "pending"}, {"$set": {"state": "applied"}}).matched_count
            out["applied"] += n
        else:
            out["deleted"] += logs.delete_one({"_id": a["_id"], "state": "pending"}).deleted_count
    return out
