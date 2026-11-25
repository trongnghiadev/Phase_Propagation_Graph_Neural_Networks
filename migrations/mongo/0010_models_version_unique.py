"""Migration 0010_models_version_unique: index unique (partial) cho `models.version`.

``version`` là số nguyên do bộ đếm `counters._id="models.version"` cấp (`vngis.model_registry`); index unique
chặn hai bản ghi cùng số (ví dụ nhập tay/khôi phục). **Partial** ``{version: {$type: "number"}}``: bản ghi cũ có
``version`` dạng chuỗi ``v<timestamp>`` hoặc thiếu trường không bị coi là trùng.

Định nghĩa index trùng `packages/vngis/src/vngis/model_registry.py::MODELS_INDEXES` (test API kiểm tra) — API
cũng tạo idempotent khi khởi động. Không có validator. Có số trùng sẵn → dừng, báo `duplicates` (người vận hành
xử lý trước). Idempotent (`createIndexes` cùng định nghĩa là no-op). Chạy qua `runner.py`.
"""
from __future__ import annotations

COLLECTION = "models"
DESCRIPTION = "models.version: index unique partial ({version: {$type: number}})"

INDEXES: list[dict] = [
    {"name": "version_unique_number", "key": {"version": 1}, "unique": True,
     "partialFilterExpression": {"version": {"$type": "number"}}},
]


def _exists(db) -> bool:
    return COLLECTION in db.list_collection_names()


def duplicates(db, limit: int = 20) -> list:
    """Các `version` (kiểu số) xuất hiện ở hơn một bản ghi."""
    if not _exists(db):
        return []
    pipe = [{"$match": {"version": {"$type": "number"}}},
            {"$group": {"_id": "$version", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}, {"$sort": {"_id": 1}}, {"$limit": limit}]
    return [d["_id"] for d in db[COLLECTION].aggregate(pipe)]


def up(db, *, dry_run: bool = False) -> dict:
    have = db[COLLECTION].index_information() if _exists(db) else {}
    dups = duplicates(db)
    report = {"collection": COLLECTION, "existed": _exists(db),
              "n_documents": db[COLLECTION].estimated_document_count() if _exists(db) else 0,
              "n_non_numeric_version": db[COLLECTION].count_documents(
                  {"version": {"$not": {"$type": "number"}}}) if _exists(db) else 0,
              "duplicates": dups, "indexes": [ix["name"] for ix in INDEXES],
              "previous": {"indexes_existed": sorted(ix["name"] for ix in INDEXES if ix["name"] in have)},
              "dry_run": dry_run}
    if dry_run:
        return report
    if dups:
        raise RuntimeError(f"models.version bị trùng ({len(dups)} số) — xử lý trước khi tạo index unique")
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
