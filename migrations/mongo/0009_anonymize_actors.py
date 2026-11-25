"""Migration 0009_anonymize_actors: thay tên đăng nhập còn sót trong bản ghi cũ bằng mã tài khoản.

Bản ghi mới (API) đã ghi người thực hiện dạng ``user:<account_code>:<role>``. Bản ghi cũ có thể còn tên đăng
nhập (hoặc `users._id`, mặc định = tên đăng nhập). Bước này:

1. Cấp `account_code` (``acc_<12 hex>``) cho mọi tài khoản chưa có (thiếu / ``null`` / rỗng / không phải
   chuỗi) — lệnh có điều kiện "vẫn chưa có mã" nên không ghi đè mã API vừa cấp song song.
2. Thay giá trị khớp tên đăng nhập hoặc `users._id` đã biết bằng ``user:<account_code>`` ở các trường:

   ============== ================================================================================
   collection     trường
   ============== ================================================================================
   audit_logs     ``actor``, ``reverted_by``, ``revert_claim.by``; trong ``before``/``after``:
                  ``decided_by``, ``retired_by``, ``unretired_by``, ``ack_by``, ``requested_by``,
                  ``promoted_by``, ``reverted_by``; loại ``user_perm``: ``doc_id`` → ``<account_code>``,
                  ``target`` → ``{account_code}`` (bỏ ``user_id``/``username``) — đúng định dạng API hiện ghi
   qc_flags       ``decided_by``, ``retired_by``, ``unretired_by``
   jobs           ``requested_by``
   alerts         ``ack_by``
   models         ``promoted_by``, ``requested_by``
   ============== ================================================================================

**Vai trò:** dùng ``user:<account_code>`` (KHÔNG kèm vai trò). Bản ghi cũ không lưu vai trò lúc thao tác;
gắn vai trò hiện tại có thể sai lịch sử (người đó có thể đã đổi vai trò). ``parse_actor`` của API nhận dạng
không vai trò; khi đọc, `actor_label` cho biết vai trò hiện tại.

**Giữ nguyên:** giá trị đã đúng định dạng ``user:acc_…``, vai trò hệ thống (`SYSTEM_ACTORS`: ``manual_review``,
``qc_job``, ``worker``, ``cli``, …), ``null``, và chuỗi lạ không khớp tài khoản nào (đếm vào
``n_unmapped`` — chỉ đếm, không in giá trị). Tên trùng một vai trò hệ thống được coi là vai trò hệ thống.

**audit_logs không bất biến tuyệt đối:** validator 0005 (`moderate`) chỉ kiểm tra kiểu, không chặn update;
code vốn cập nhật bản ghi (``state`` pending→applied, ``reverted_*``, ``revert_claim``). Tuy vậy đây là nhật
ký kiểm toán, nên khi có thay đổi bước này chèn MỘT bản ghi `audit_logs` cho chính việc ẩn danh
(``action: "anonymize_actors"``, ``actor: "migration:0009"``, ``after.counts`` = số bản ghi đã đổi theo
trường — chỉ số lượng, không tên).

Mỗi lệnh sửa có điều kiện giá trị cũ (``{_id, field: <tên>}``) → không ghi đè thay đổi đồng thời.
**Idempotent:** chạy lại không còn tên để đổi → 0 thay đổi, không chèn thêm bản ghi audit.
``dry_run``: chỉ đếm (theo collection/trường), không ghi gì; runner không ghi `_migrations` khi dry-run.
``verify``: không còn giá trị nào bằng tên đăng nhập / `users._id` đã biết ở các trường trên.
**down: không khả thi** — không lưu bản đồ ngược (cố ý: không giữ tên trong DB ngoài `users`); `down` báo lỗi
và runner giữ nguyên bản ghi `_migrations`. Tra ngược khi cần: `users.account_code` → tài khoản.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any

COLLECTION = "audit_logs+qc_flags+jobs+alerts+models+users"
DESCRIPTION = "Ẩn danh hoá người thực hiện trong bản ghi cũ: tên đăng nhập → user:<account_code>"

MIGRATION_ACTOR = "migration:0009"

#: Người thực hiện là hệ thống (không phải tài khoản) — giữ nguyên.
SYSTEM_ACTORS = frozenset({"manual_review", "qc_job", "qc_review", "worker", "scheduler", "cli", "pipeline",
                           "api", "system", "migration", "auto", "ingest", "spark"})

#: Trùng `apps/api/src/api/auth/users.py::_ACTOR_RE` (migration không import `apps/`).
_ACTOR_RE = re.compile(r"^user:(acc_[0-9a-f]{6,32})(?::([A-Za-z0-9_.-]+))?$")

#: Khoá trong `audit_logs.before` / `.after` có thể chứa người thực hiện.
NESTED_ACTOR_KEYS = ("decided_by", "retired_by", "unretired_by", "ack_by", "requested_by", "promoted_by",
                     "reverted_by")

#: collection → các trường (đường dẫn chấm) chứa người thực hiện.
FIELDS: dict[str, tuple[str, ...]] = {
    "audit_logs": ("actor", "reverted_by", "revert_claim.by",
                   *(f"{side}.{k}" for side in ("before", "after") for k in NESTED_ACTOR_KEYS)),
    "qc_flags": ("decided_by", "retired_by", "unretired_by"),
    "jobs": ("requested_by",),
    "alerts": ("ack_by",),
    "models": ("promoted_by", "requested_by"),
}

_MISSING_CODE = {"$or": [{"account_code": {"$in": [None, ""]}}, {"account_code": {"$not": {"$type": "string"}}}]}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _has(db, name: str) -> bool:
    return name in db.list_collection_names()


def _is_formatted(v: Any) -> bool:
    return isinstance(v, str) and bool(_ACTOR_RE.match(v))


def _users(db) -> list[dict]:
    if not _has(db, "users"):
        return []
    return list(db["users"].find({}, {"username": 1, "account_code": 1}))


def _name_index(users: list[dict]) -> dict[str, Any]:
    """Tên đăng nhập / `_id` (chuỗi) → `_id` tài khoản. Bỏ tên trùng vai trò hệ thống hoặc đã đúng định dạng."""
    out: dict[str, Any] = {}
    for u in users:
        for key in (u.get("username"), u.get("_id")):
            if isinstance(key, str) and key and key not in SYSTEM_ACTORS and not _is_formatted(key):
                out.setdefault(key, u["_id"])
    return out


def _new_code() -> str:
    return f"acc_{secrets.token_hex(6)}"


def _ensure_codes(db, users: list[dict], *, dry_run: bool) -> tuple[dict[Any, str], int]:
    """`_id` → account_code (cấp mã cho tài khoản chưa có). Trả (bản đồ, số tài khoản được/sẽ được cấp)."""
    from pymongo.errors import DuplicateKeyError

    codes: dict[Any, str] = {}
    n_new = 0
    for u in users:
        code = u.get("account_code")
        if isinstance(code, str) and code:
            codes[u["_id"]] = code
            continue
        n_new += 1
        if dry_run:
            codes[u["_id"]] = "acc_dryrun000000"
            continue
        for _ in range(5):
            try:
                db["users"].update_one({"_id": u["_id"], **_MISSING_CODE}, {"$set": {"account_code": _new_code()}})
                break
            except DuplicateKeyError:
                continue
        cur = (db["users"].find_one({"_id": u["_id"]}, {"account_code": 1}) or {}).get("account_code")
        if not (isinstance(cur, str) and cur):
            raise RuntimeError("không cấp được account_code cho một tài khoản")
        codes[u["_id"]] = cur
    return codes, n_new


def _get(doc: dict, path: str) -> Any:
    cur: Any = doc
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _plan(db, names: dict[str, Any], codes: dict[Any, str]) -> tuple[dict[str, list], dict[str, int], dict]:
    """Kế hoạch sửa: ``{collection: [(filter, update)]}``, số thay đổi theo ``collection.field``, số chưa khớp."""
    ops: dict[str, list] = {}
    counts: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    keys = sorted(names)
    for coll, fields in FIELDS.items():
        if not _has(db, coll):
            continue
        for f in fields:
            n = 0
            if keys:
                for d in db[coll].find({f: {"$in": keys}}, {f: 1}):
                    old = _get(d, f)
                    code = codes.get(names.get(old))
                    if not code:
                        continue
                    ops.setdefault(coll, []).append(({"_id": d["_id"], f: old}, {"$set": {f: f"user:{code}"}}))
                    n += 1
            if n:
                counts[f"{coll}.{f}"] = n
            # chuỗi lạ (không phải tên đã biết, vai trò hệ thống, định dạng mới) — chỉ đếm
            flt = {f: {"$type": "string", "$nin": [*keys, *SYSTEM_ACTORS], "$not": _ACTOR_RE}}
            u = db[coll].count_documents(flt)
            if u:
                unmapped[f"{coll}.{f}"] = u
    if _has(db, "audit_logs"):
        n_doc = n_tgt = 0
        q = {"action": "user_perm", "$or": [{"doc_id": {"$in": keys}}, {"target.user_id": {"$exists": True}},
                                             {"target.username": {"$exists": True}}]}
        for d in db["audit_logs"].find(q, {"doc_id": 1, "target": 1}) if keys else []:
            tgt = d.get("target") if isinstance(d.get("target"), dict) else {}
            uid = names.get(d.get("doc_id")) or names.get(tgt.get("user_id")) or names.get(tgt.get("username"))
            code = codes.get(uid)
            if not code:
                continue
            flt: dict[str, Any] = {"_id": d["_id"], "doc_id": d.get("doc_id")}
            upd: dict[str, Any] = {"$set": {"target.account_code": code}}
            if d.get("doc_id") in names:
                upd["$set"]["doc_id"] = code
                n_doc += 1
            unset = {f"target.{k}": "" for k in ("user_id", "username") if k in tgt}
            if unset:
                upd["$unset"] = unset
                n_tgt += 1
            if len(upd) == 1 and tgt.get("account_code") == code:
                continue
            ops.setdefault("audit_logs", []).append((flt, upd))
        if n_doc:
            counts["audit_logs.user_perm.doc_id"] = n_doc
        if n_tgt:
            counts["audit_logs.user_perm.target"] = n_tgt
    return ops, counts, unmapped


def _by_collection(ops: dict[str, list]) -> dict[str, int]:
    """Số **document** sẽ đổi theo collection (một document có thể đổi nhiều trường)."""
    return {c: len({flt["_id"] for flt, _ in lst}) for c, lst in ops.items()}


def up(db, *, dry_run: bool = False) -> dict:
    users = _users(db)
    codes, n_new = _ensure_codes(db, users, dry_run=dry_run)
    names = _name_index(users)
    ops, counts, unmapped = _plan(db, names, codes)
    report: dict[str, Any] = {"collection": COLLECTION, "n_users": len(users),
                              "n_users_code_assigned": n_new, "role_policy": "user:<account_code> (không vai trò)",
                              "fields_changed": counts, "documents_changed": _by_collection(ops),
                              "n_unmapped": unmapped, "previous": None, "dry_run": dry_run}
    if dry_run or not ops:
        report["audit_id"] = None
        return report
    from pymongo import UpdateOne

    applied: dict[str, int] = {}
    for coll, lst in ops.items():
        res = db[coll].bulk_write([UpdateOne(f, u) for f, u in lst], ordered=False)
        applied[coll] = int(res.modified_count)
    report["documents_modified"] = applied
    audit = {"at": _now(), "actor": MIGRATION_ACTOR, "action": "anonymize_actors", "source": "migration",
             "collection": "audit_logs", "doc_id": "0009_anonymize_actors",
             "target": {"collections": sorted(ops)}, "before": None,
             "after": {"fields_changed": counts, "documents_modified": applied,
                       "n_users_code_assigned": n_new},
             "reason": "ẩn danh hoá người thực hiện trong bản ghi cũ (tên đăng nhập → user:<account_code>)",
             "state": "applied"}
    report["audit_id"] = str(db["audit_logs"].insert_one(audit).inserted_id)
    return report


def verify(db) -> list[str]:
    users = _users(db)
    names = sorted(_name_index(users))
    errs: list[str] = []
    n_missing = db["users"].count_documents(_MISSING_CODE) if users else 0
    if n_missing:
        errs.append(f"users: {n_missing} tài khoản chưa có account_code")
    if not names:
        return errs
    for coll, fields in FIELDS.items():
        if not _has(db, coll):
            continue
        for f in fields:
            n = db[coll].count_documents({f: {"$in": names}})
            if n:
                errs.append(f"{coll}.{f}: còn {n} bản ghi chứa tên đăng nhập")
    if _has(db, "audit_logs"):
        n = db["audit_logs"].count_documents({"action": "user_perm",
                                              "$or": [{"doc_id": {"$in": names}},
                                                      {"target.user_id": {"$in": names}},
                                                      {"target.username": {"$in": names}}]})
        if n:
            errs.append(f"audit_logs(user_perm): còn {n} bản ghi chứa tên đăng nhập ở doc_id/target")
    return errs


def down(db, previous: dict | None) -> None:
    raise RuntimeError("0009_anonymize_actors: không hoàn tác được — không lưu bản đồ ngược tên đăng nhập "
                       "(cố ý). Tra `users.account_code` để biết tài khoản của từng mã.")
