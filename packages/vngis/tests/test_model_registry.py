"""`vngis.model_registry`: cấp số phiên bản mô hình nguyên tử (mongomock)."""
from __future__ import annotations

import pytest

mongomock = pytest.importorskip("mongomock")

from vngis.model_registry import (COUNTERS_COLLECTION, MODEL_STATUSES, VERSION_COUNTER_ID,  # noqa: E402
                                  allocate_model_version, model_doc_id)


def _db():
    return mongomock.MongoClient(tz_aware=True)["t_model_registry"]


def test_cap_so_tang_dan_bat_dau_tu_1():
    db = _db()
    assert [allocate_model_version(db) for _ in range(3)] == [1, 2, 3]
    assert db[COUNTERS_COLLECTION].find_one({"_id": VERSION_COUNTER_ID})["seq"] == 3


def test_khong_trung_ban_ghi_da_co_truoc_bo_dem():
    db = _db()
    db["models"].insert_many([{"_id": "phasegnn-seed0", "version": 4, "status": "staging"},
                              {"_id": "cu", "version": "v20261124", "status": "staging"}])  # chuỗi cũ: bỏ qua
    assert allocate_model_version(db) == 5
    assert allocate_model_version(db) == 6


def test_quy_uoc():
    assert MODEL_STATUSES == ("staging", "production", "archived")
    assert model_doc_id("phasegnn", 3) == "phasegnn-3"


def test_promote_khong_transaction_thu_tu_ghi():
    from vngis.model_registry import find_production, promote_model, supports_transactions

    db = _db()
    db["models"].insert_many([{"_id": f"phasegnn-{i}", "version": i, "status": "staging"} for i in (1, 2, 3)])
    assert supports_transactions(db) is False
    o1 = promote_model(db, "phasegnn-3", actor="t")
    o2 = promote_model(db, "phasegnn-1", actor="t")
    assert o1["transaction"] is False and o2["archived"] == ["phasegnn-3"] and o2["superseded"] is False
    assert [d["_id"] for d in db["models"].find({"status": "production"})] == ["phasegnn-1"]
    assert find_production(db)["_id"] == "phasegnn-1" and find_production(db, version=3) is None
    assert db["models"].find_one({"_id": "phasegnn-1"})["promote_seq"] == 2


def test_repair_production_giu_luot_promote_moi_nhat():
    from vngis.model_registry import find_production, promote_model, repair_production

    db = _db()
    db["models"].insert_many([
        {"_id": "a", "version": 9, "status": "production"},                    # bản cũ, chưa có promote_seq
        {"_id": "b", "version": 2, "status": "production", "promote_seq": 5},
        {"_id": "c", "version": 3, "status": "staging"},
    ])
    assert find_production(db)["_id"] == "b", "promote_seq ưu tiên hơn version"
    assert repair_production(db) == ["a"] and repair_production(db) == []
    db["models"].update_one({"_id": "a"}, {"$set": {"status": "production"}})
    out = promote_model(db, "c", actor="t")   # promote tự sửa trạng thái sót trước khi ghi
    assert out["repaired"] == ["a"] and out["archived"] == ["b"]
    assert [d["_id"] for d in db["models"].find({"status": "production"})] == ["c"]



def test_promote_cung_ban_hai_luot_luot_cu_thanh_cong():
    """Hai lượt promote CÙNG bản xen kẽ (không transaction): lượt seq nhỏ hơn không báo superseded."""
    from vngis.model_registry import promote_model

    db = _db()
    db["models"].insert_many([{"_id": "a", "version": 1, "status": "production", "promote_seq": 1},
                              {"_id": "b", "version": 2, "status": "staging", "train_state": "succeeded"}])
    inner = {}

    def hook(step):
        if step == "seq" and not inner:
            inner["out"] = promote_model(db, "b", actor="t2", use_transaction=False)

    out = promote_model(db, "b", actor="t1", use_transaction=False, _hook=hook)
    assert inner["out"]["superseded"] is False
    assert out["superseded"] is False and out.get("same_winner") is True
    assert [d["_id"] for d in db["models"].find({"status": "production"})] == ["b"]


def test_promote_dieu_kien_train_state_trong_lenh_ghi():
    from vngis.model_registry import promote_model

    db = _db()
    db["models"].insert_many([{"_id": "a", "version": 1, "status": "production", "promote_seq": 1},
                              {"_id": "b", "version": 2, "status": "staging", "train_state": "running"}])
    out = promote_model(db, "b", actor="t", use_transaction=False)
    assert out["not_trained"] is True and out["superseded"] is False
    assert db["models"].find_one({"_id": "b"})["status"] == "staging"
    assert db["models"].find_one({"_id": "a"})["status"] == "production"


def test_ensure_model_indexes_partial_unique():
    from vngis.model_registry import VERSION_INDEX_NAME, ensure_model_indexes

    db = _db()
    # partialFilterExpression (chuỗi trùng được phép) chỉ kiểm trên MongoDB thật: migrations/mongo/tests
    db["models"].insert_many([{"_id": "x", "version": "v1"}, {"_id": "y", "version": "v2"}])
    assert ensure_model_indexes(db) == [VERSION_INDEX_NAME]
    assert ensure_model_indexes(db) == [VERSION_INDEX_NAME]
    db["models"].insert_one({"_id": "z", "version": 3})
    with pytest.raises(Exception):
        db["models"].insert_one({"_id": "w", "version": 3})
