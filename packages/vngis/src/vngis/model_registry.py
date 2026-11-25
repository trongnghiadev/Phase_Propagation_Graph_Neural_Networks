"""Quy ước collection `models` dùng chung API ↔ worker ↔ `phasegnn.registry`.

- ``version``: **số nguyên** tăng dần, cấp nguyên tử bằng bộ đếm `counters` (``_id: "models.version"``,
  ``find_one_and_update`` + ``$inc``) — không còn chuỗi ``v<timestamp>``. Trước khi tăng, bộ đếm được đưa
  lên ít nhất bằng ``version`` lớn nhất đang có trong `models` (``$max``, idempotent) để bản ghi do
  registry ghi trước khi có bộ đếm không bị trùng số.
- ``status``: ``staging`` (mới đăng ký/đang huấn luyện) | ``production`` (đang phục vụ, worker chỉ chọn bản
  này) | ``archived``.

- ``train_state`` (`TRAIN_STATES`): ``queued`` (API vừa giao) → ``running`` → ``succeeded`` | ``failed`` |
  ``skipped`` (worker `train`, ghi có fence ``train_fence``).

**Promote an toàn** (`promote_model`): có replica set → các lệnh ghi trong một transaction. Không có
(MongoDB đơn, mongomock) → thứ tự ghi có điều kiện, tự sửa được:

1. cấp ``promote_seq`` tăng dần (bộ đếm ``counters._id="models.promote"``, ``$inc`` nguyên tử);
2. đặt bản mới ``production`` + ``promoted_at`` + ``promote_seq`` — chỉ khi bản ghi chưa mang ``promote_seq``
   lớn hơn (lượt promote mới hơn đã chạm bản này thì lượt cũ không ghi đè) **và** ``train_state`` ∈
   {thiếu, ``succeeded``} (`TRAINED_FILTER`, kiểm tra trong chính lệnh ghi; không khớp → ``not_trained``).
   Lượt mới hơn đã promote CHÍNH bản này (hai lượt cùng bản) → lượt cũ trả thành công (``same_winner``);
3. archive mọi bản ``production`` khác có ``promote_seq`` nhỏ hơn (hoặc không có);
4. nếu đã có bản ``production`` khác với ``promote_seq`` lớn hơn (lượt mới hơn chạy xen giữa) → tự archive bản
   của mình (điều kiện ``promote_seq`` của mình).

Giữa bước 2 và 3 có thể có 2 bản production trong chốc lát; người đọc (worker) luôn chọn theo
`PRODUCTION_SORT` (``promote_seq`` ↓, ``promoted_at`` ↓, ``version`` ↓) nên vẫn ra bản của lượt promote mới
nhất. `repair_production` dọn trạng thái sót (tiến trình chết giữa bước 2 và 3); `promote_model` gọi nó
trước mỗi lượt. API giữ thêm khoá ``models:promote`` để các lượt promote tuần tự trong trường hợp thường.

Module chỉ dùng API của đối tượng database pymongo truyền vào (không tự kết nối); `apps/api` dùng được vì
không import `phasegnn` (luật phụ thuộc).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("vngis.model_registry")

__all__ = ["MODELS_COLLECTION", "COUNTERS_COLLECTION", "VERSION_COUNTER_ID", "MODEL_STATUSES",
           "STAGING", "PRODUCTION", "ARCHIVED", "allocate_model_version", "model_doc_id",
           "TRAIN_STATES", "PROMOTE_COUNTER_ID", "PROMOTE_LOCK_RESOURCE", "PRODUCTION_SORT",
           "find_production", "promote_model", "TRAINED_STATES", "TRAINED_FILTER", "repair_production",
           "supports_transactions", "VERSION_INDEX_NAME", "MODELS_INDEXES", "ensure_model_indexes"]

MODELS_COLLECTION = "models"
COUNTERS_COLLECTION = "counters"
VERSION_COUNTER_ID = "models.version"

STAGING, PRODUCTION, ARCHIVED = "staging", "production", "archived"
MODEL_STATUSES = (STAGING, PRODUCTION, ARCHIVED)

#: Trạng thái huấn luyện (`models.train_state`).
TRAIN_STATES = ("queued", "running", "succeeded", "failed", "skipped")

#: Bộ đếm thứ tự promote và tài nguyên khoá của thao tác promote (khoá Redis ``lock:models:promote``).
PROMOTE_COUNTER_ID = "models.promote"
PROMOTE_LOCK_RESOURCE = "models:promote"

#: ``train_state`` được promote: thiếu (bản ghi cũ / đăng ký thủ công) hoặc ``succeeded``.
TRAINED_STATES = (None, "succeeded")
#: Điều kiện tương ứng trong bộ lọc lệnh ghi (đặt production).
TRAINED_FILTER = {"$or": [{"train_state": {"$exists": False}}, {"train_state": None},
                          {"train_state": "succeeded"}]}

#: Thứ tự chọn bản đang phục vụ khi (tạm thời) có nhiều bản ``production``: lượt promote mới nhất thắng.
PRODUCTION_SORT = [("promote_seq", -1), ("promoted_at", -1), ("version", -1)]


#: Index unique ``models.version`` (partial: chỉ bản ghi có ``version`` kiểu số — bản ghi cũ ``v<timestamp>``
#: dạng chuỗi không bị chặn). Migration ``0010_models_version_unique`` dùng đúng định nghĩa này (test kiểm tra).
VERSION_INDEX_NAME = "version_unique_number"
MODELS_INDEXES: list[dict] = [
    {"name": VERSION_INDEX_NAME, "key": {"version": 1}, "unique": True,
     "partialFilterExpression": {"version": {"$type": "number"}}},
]


def ensure_model_indexes(db: Any) -> list[str]:
    """Tạo (idempotent) các index `MODELS_INDEXES` trên `models`; trả tên index."""
    out = []
    for ix in MODELS_INDEXES:
        opts = {k: v for k, v in ix.items() if k not in ("key",)}
        out.append(db[MODELS_COLLECTION].create_index(list(ix["key"].items()), **opts))
    return out


def _max_existing_version(db: Any) -> int:
    top = db[MODELS_COLLECTION].find_one({"version": {"$type": "number"}}, {"version": 1},
                                         sort=[("version", -1)])
    try:
        return max(0, int((top or {}).get("version") or 0))
    except (TypeError, ValueError):
        return 0


def allocate_model_version(db: Any) -> int:
    """Cấp số phiên bản mô hình mới (int ≥ 1), nguyên tử ở tầng CSDL.

    Hai request đồng thời nhận hai số khác nhau: giá trị lấy từ kết quả của chính lệnh
    ``find_one_and_update`` (``$inc``), không đọc lại sau đó (trừ khi driver không trả document — chỉ gặp ở
    một số bản `mongomock`).
    """
    counters = db[COUNTERS_COLLECTION]
    floor = _max_existing_version(db)
    if floor:
        counters.update_one({"_id": VERSION_COUNTER_ID}, {"$max": {"seq": floor}}, upsert=True)
    doc = counters.find_one_and_update({"_id": VERSION_COUNTER_ID}, {"$inc": {"seq": 1}}, upsert=True,
                                       return_document=True)  # True == ReturnDocument.AFTER
    if not doc or "seq" not in doc:
        doc = counters.find_one({"_id": VERSION_COUNTER_ID}) or {}
    if "seq" not in doc:
        raise RuntimeError("không cấp được số phiên bản mô hình")
    return int(doc["seq"])


def model_doc_id(name: str, version: int) -> str:
    """``_id`` mặc định của bản ghi `models`: ``<name>-<version>`` (ví dụ ``phasegnn-3``)."""
    return f"{name}-{int(version)}"


# ------------------------------------------------------------------ promote --------
def _s(session: Any) -> dict:
    """Tham số ``session`` cho lệnh pymongo (bỏ hẳn khi không có — mongomock không cần)."""
    return {"session": session} if session is not None else {}


def _next_seq(db: Any, counter_id: str, session: Any = None) -> int:
    doc = db[COUNTERS_COLLECTION].find_one_and_update({"_id": counter_id}, {"$inc": {"seq": 1}}, upsert=True,
                                                      return_document=True, **_s(session))
    if not doc or "seq" not in doc:
        doc = db[COUNTERS_COLLECTION].find_one({"_id": counter_id}, **_s(session)) or {}
    if "seq" not in doc:
        raise RuntimeError(f"không cấp được số thứ tự {counter_id!r}")
    return int(doc["seq"])


def supports_transactions(db: Any) -> bool:
    """MongoDB hỗ trợ transaction không (replica set hoặc mongos). Lỗi / mongomock → ``False``."""
    try:
        hello = db.client.admin.command("hello")
    except Exception:  # noqa: BLE001 - mongomock / bản cũ / thiếu quyền
        try:
            hello = db.client.admin.command("isMaster")
        except Exception:  # noqa: BLE001
            return False
    if not isinstance(hello, dict):
        return False
    return bool(hello.get("setName")) or hello.get("msg") == "isdbgrid"


def find_production(db: Any, *, version: int | None = None, name: str | None = None,
                    projection: dict | None = None) -> dict | None:
    """Bản ``production`` đang phục vụ theo `PRODUCTION_SORT`; có `version` → chỉ bản đó (nếu đang production)."""
    flt: dict[str, Any] = {"status": PRODUCTION}
    if version is not None:
        flt["version"] = int(version)
    if name:
        flt["name"] = name
    return db[MODELS_COLLECTION].find_one(flt, projection, sort=PRODUCTION_SORT)


def repair_production(db: Any, *, session: Any = None, now: datetime | None = None) -> list[Any]:
    """Giữ đúng MỘT bản ``production`` (lượt promote mới nhất), archive các bản còn lại; trả `_id` đã archive.

    Idempotent — tự sửa khi một lượt promote chết giữa hai lệnh ghi (không có transaction).
    """
    models = db[MODELS_COLLECTION]
    top = models.find_one({"status": PRODUCTION}, {"_id": 1}, sort=PRODUCTION_SORT, **_s(session))
    if top is None:
        return []
    others = [d["_id"] for d in models.find({"status": PRODUCTION, "_id": {"$ne": top["_id"]}}, {"_id": 1},
                                            **_s(session))]
    if others:
        now = now or datetime.now(timezone.utc)
        models.update_many({"_id": {"$in": others}, "status": PRODUCTION},
                           {"$set": {"status": ARCHIVED, "archived_at": now, "updated_at": now,
                                     "archived_reason": "repair: đã có bản production mới hơn"}}, **_s(session))
        log.warning("models: có nhiều bản production, đã archive %s", others)
    return others


def _promote_steps(db: Any, doc_id: Any, actor: str, now: datetime, session: Any = None,
                   hook: Any = None) -> dict:
    """Các bước promote; `hook(tên_bước)` (chỉ test) được gọi giữa các lệnh ghi để mô phỏng xen kẽ."""
    def _at(step: str) -> None:
        if hook is not None:
            hook(step)

    models = db[MODELS_COLLECTION]
    repaired = repair_production(db, session=session, now=now)
    # Bộ đếm không bao giờ nhỏ hơn `promote_seq` đã có (dữ liệu nhập tay/khôi phục) — `$max` idempotent.
    top = models.find_one({"promote_seq": {"$type": "number"}}, {"promote_seq": 1}, sort=[("promote_seq", -1)],
                          **_s(session))
    if top and int(top.get("promote_seq") or 0) > 0:
        db[COUNTERS_COLLECTION].update_one({"_id": PROMOTE_COUNTER_ID}, {"$max": {"seq": int(top["promote_seq"])}},
                                           upsert=True, **_s(session))
    seq = _next_seq(db, PROMOTE_COUNTER_ID, session)
    _at("seq")
    older = {"$or": [{"promote_seq": {"$exists": False}}, {"promote_seq": None}, {"promote_seq": {"$lt": seq}}]}
    res = models.update_one({"_id": doc_id, "$and": [older, TRAINED_FILTER]},
                            {"$set": {"status": PRODUCTION, "promoted_at": now, "promoted_by": actor,
                                      "promote_seq": seq, "updated_at": now},
                             "$unset": {"archived_at": "", "archived_reason": ""}}, **_s(session))
    if res.matched_count == 0:
        cur = models.find_one({"_id": doc_id}, {"train_state": 1, "status": 1}, **_s(session))
        if cur is None:
            return {"promote_seq": seq, "superseded": True, "missing": True, "archived": [], "repaired": repaired}
        if cur.get("train_state") not in TRAINED_STATES:
            # Điều kiện "đã huấn luyện xong" kiểm tra ngay trong lệnh ghi (API kiểm tra trước, nhưng trạng thái
            # có thể đổi giữa chừng, ví dụ giao huấn luyện lại).
            return {"promote_seq": seq, "superseded": False, "not_trained": True,
                    "train_state": cur.get("train_state"), "archived": [], "repaired": repaired}
        top_now = models.find_one({"status": PRODUCTION}, {"_id": 1}, sort=PRODUCTION_SORT, **_s(session))
        if (top_now or {}).get("_id") == doc_id:
            # Lượt promote mới hơn cho CHÍNH bản này đã thắng → kết quả mong muốn đã đạt, không báo superseded.
            return {"promote_seq": seq, "superseded": False, "same_winner": True, "archived": [],
                    "repaired": repaired}
        # Lượt promote mới hơn (bản khác) đã chạm bản ghi này → không ghi đè.
        return {"promote_seq": seq, "superseded": True, "archived": [], "repaired": repaired}
    _at("set")
    archived = [d["_id"] for d in models.find({"status": PRODUCTION, "_id": {"$ne": doc_id}, **older}, {"_id": 1},
                                              **_s(session))]
    if archived:
        models.update_many({"_id": {"$in": archived}, "status": PRODUCTION, **older},
                           {"$set": {"status": ARCHIVED, "archived_at": now, "updated_at": now}}, **_s(session))
    _at("archived")
    newer = models.find_one({"status": PRODUCTION, "_id": {"$ne": doc_id}, "promote_seq": {"$gt": seq}},
                            {"_id": 1}, **_s(session))
    superseded = False
    if newer is not None:
        # Lượt mới hơn đã ghi xen giữa: nhường, không để 2 bản production tồn tại lâu.
        models.update_one({"_id": doc_id, "status": PRODUCTION, "promote_seq": seq},
                          {"$set": {"status": ARCHIVED, "archived_at": now, "updated_at": now,
                                    "archived_reason": "lượt promote mới hơn đã thắng"}}, **_s(session))
        superseded = True
    return {"promote_seq": seq, "superseded": superseded, "archived": archived, "repaired": repaired}


def promote_model(db: Any, doc_id: Any, *, actor: str, now: datetime | None = None,
                  use_transaction: bool | None = None, _hook: Any = None) -> dict:
    """Promote bản ghi `doc_id` thành ``production``; mọi bản production khác → ``archived``.

    ``use_transaction=None`` → tự dò (`supports_transactions`). Trả ``{promote_seq, superseded, archived,
    repaired, transaction}``; ``superseded=True``: một lượt promote mới hơn đã thắng (bản này không phục vụ);
    ``not_trained=True``: bản ghi chưa huấn luyện xong lúc ghi (không đổi gì).
    """
    now = now or datetime.now(timezone.utc)
    tx = supports_transactions(db) if use_transaction is None else bool(use_transaction)
    if tx:
        from pymongo.read_concern import ReadConcern  # noqa: PLC0415
        from pymongo.write_concern import WriteConcern  # noqa: PLC0415

        with db.client.start_session() as s:
            out = s.with_transaction(lambda sess: _promote_steps(db, doc_id, actor, now, sess, _hook),
                                     read_concern=ReadConcern("snapshot"),
                                     write_concern=WriteConcern("majority"))
        return {**out, "transaction": True}
    return {**_promote_steps(db, doc_id, actor, now, hook=_hook), "transaction": False}
