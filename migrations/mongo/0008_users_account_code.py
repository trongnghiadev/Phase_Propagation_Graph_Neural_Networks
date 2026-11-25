"""Migration 0008_users_account_code: index unique (partial) cho `users.account_code`.

`account_code` (``acc_<12 hex>``) là định danh ổn định ghi vào nhật ký thay cho tên đăng nhập
(``user:<account_code>:<role>``), nên phải duy nhất. Index **partial** ``{account_code: {$type: "string"}}``: tài
khoản cũ chưa có mã (thiếu trường hoặc ``null``) không bị coi là trùng nhau; API cấp mã ở lần đầu cần
(`UserRepo.ensure_account_code`, bộ lọc xử lý cả ``null``/không phải chuỗi).

Định nghĩa index trùng `apps/api/src/api/auth/users.py::USERS_INDEXES` (test API kiểm tra) — API cũng tạo
idempotent khi khởi động. Không có validator. Có mã trùng sẵn → dừng, báo `duplicates` (người vận hành xử lý
trước). Idempotent (`createIndexes` cùng định nghĩa là no-op). Chạy qua `runner.py`.
"""
from __future__ import annotations

COLLECTION = "users"
DESCRIPTION = "users.account_code: index unique partial ({account_code: {$type: string}})"

INDEXES: list[dict] = [
    {"name": "account_code_unique", "key": {"account_code": 1}, "unique": True,
     "partialFilterExpression": {"account_code": {"$type": "string"}}},
]


def _exists(db) -> bool:
    return COLLECTION in db.list_collection_names()


def duplicates(db, limit: int = 20) -> list[str]:
    """Các `account_code` (chuỗi) xuất hiện ở hơn một tài khoản."""
    if not _exists(db):
        return []
    pipe = [{"$match": {"account_code": {"$type": "string"}}},
            {"$group": {"_id": "$account_code", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}, {"$limit": limit}]
    return [d["_id"] for d in db[COLLECTION].aggregate(pipe)]


def up(db, *, dry_run: bool = False) -> dict:
    have = db[COLLECTION].index_information() if _exists(db) else {}
    dups = duplicates(db)
    report = {"collection": COLLECTION, "existed": _exists(db),
              "n_documents": db[COLLECTION].estimated_document_count() if _exists(db) else 0,
              "n_without_code": db[COLLECTION].count_documents(
                  {"$or": [{"account_code": {"$in": [None, ""]}},
                           {"account_code": {"$not": {"$type": "string"}}}]}) if _exists(db) else 0,
              "duplicates": dups, "indexes": [ix["name"] for ix in INDEXES],
              "previous": {"indexes_existed": sorted(ix["name"] for ix in INDEXES if ix["name"] in have)},
              "dry_run": dry_run}
    if dry_run:
        return report
    if dups:
        raise RuntimeError(f"users.account_code bị trùng ({len(dups)} mã) — xử lý trước khi tạo index unique")
    db.command({"createIndexes": COLLECTION, "indexes": [dict(ix) for ix in INDEXES]})
    return report


def verify(db) -> list[str]:
    if not _exists(db):
        return [f"{COLLECTION}: chưa có collection"]
    have = db[COLLECTION].index_information()
    errs = []
    for ix in INDEXES:
        got = have.get(ix["name"])
        if got is None:
            errs.append(f"{COLLECTION}: thiếu index {ix['name']}")
            continue
        if [(k, int(v)) for k, v in got["key"]] != [(k, int(v)) for k, v in ix["key"].items()]:
            errs.append(f"{COLLECTION}: index {ix['name']} khác khoá")
        if bool(got.get("unique")) != bool(ix.get("unique")):
            errs.append(f"{COLLECTION}: index {ix['name']} khác unique")
        if got.get("partialFilterExpression") != ix.get("partialFilterExpression"):
            errs.append(f"{COLLECTION}: index {ix['name']} khác partialFilterExpression")
    return errs


def down(db, previous: dict | None) -> None:
    """Xoá index do bước này tạo (index đã có từ trước — ví dụ API tạo lúc khởi động — được giữ)."""
    if not _exists(db):
        return
    keep = set((previous or {}).get("indexes_existed") or [])
    have = db[COLLECTION].index_information()
    for ix in INDEXES:
        if ix["name"] in have and ix["name"] not in keep:
            db[COLLECTION].drop_index(ix["name"])
