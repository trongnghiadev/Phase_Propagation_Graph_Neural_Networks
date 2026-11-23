"""Test `vngis.qc` trên MongoDB thật, trong database tạm riêng (tạo rồi xóa; không đụng `vietnam_gis`)."""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

import pytest

from vngis import qc
from vngis import qc_rules as R

pytestmark = pytest.mark.mongo
TS = datetime(2021, 4, 17, 2, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


@pytest.fixture()
def db(mongo_db):
    from pymongo.errors import OperationFailure
    d = mongo_db.client[f"vngis_test_qc_{secrets.token_hex(4)}"]
    try:
        qc.ensure_collections(d)
    except OperationFailure as e:
        pytest.skip(f"không tạo được database tạm: {e}")
    d[R.COLLECTION].insert_one({"ts": TS, "meta": {"station": "BIEN_HOA", "kind": "high"}, "value": None,
                                "raw_value": 11.18, "qc": "rejected", "ts_placeholder": False,
                                "source_file": "2021/04/18/x.pdf"})
    d[R.COLLECTION_BULLETINS].insert_one({"source_file": "a.pdf", "source": {"is_primary": True},
                                          "bulletin_date": datetime(2025, 11, 6, 17), "weekday": "Thứ Sáu"})
    yield d
    mongo_db.client.drop_database(d.name)


def auto_flag(db) -> dict:
    hit = {"station": "BIEN_HOA", "kind": "high", "ts": int(TS.timestamp()), "ts_placeholder": False,
           "value": 11.18, "source_file": "2021/04/18/x.pdf",
           "rules": [{"rule": "R1", "detail": "d", "metric": {"value": 11.18}}]}
    doc = R.new_flag_doc(hit, "run1", NOW, 1)
    db[R.FLAGS].insert_one(doc)
    return doc


def audits(db, **q):
    return list(db[R.AUDIT].find(q))


def test_decide_ghi_audit(db):
    f = auto_flag(db)
    out = qc.decide(db, f["_id"], "rejected", reason="PDF in 11.18", decided_by="manual_review", expected_version=1)
    assert (out["status"], out["version"], out["value"]) == ("rejected", 2, 11.18)
    a = audits(db)
    assert len(a) == 1 and a[0]["state"] == "applied" and a[0]["action"] == "qc_decision"
    assert a[0]["before"]["status"] == "suspect" and a[0]["before"]["effective_value"] == 11.18
    assert a[0]["after"]["status"] == "rejected" and a[0]["after"]["effective_value"] is None
    assert a[0]["after"]["version"] == 2 and a[0]["target"]["station"] == "BIEN_HOA"


def test_xung_dot_version(db):
    f = auto_flag(db)
    qc.decide(db, f["_id"], "confirmed_ok", reason="đúng", decided_by="a", expected_version=1)
    with pytest.raises(qc.VersionConflict) as ei:
        qc.decide(db, f["_id"], "rejected", reason="sai", decided_by="b", expected_version=1)
    assert ei.value.current == 2
    cur = qc.get_flag(db, f["_id"])
    assert (cur["status"], cur["decided_by"], cur["version"]) == ("confirmed_ok", "a", 2)
    assert len(audits(db)) == 1 and not audits(db, state="pending")


def test_xung_dot_dong_thoi_chi_mot_ben_thang(db):
    f = auto_flag(db)
    ok, conflicts = [], []
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        try:
            qc.decide(db, f["_id"], "rejected", reason=f"r{i}", decided_by=f"u{i}", expected_version=1)
            ok.append(i)
        except qc.VersionConflict:
            conflicts.append(i)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(ok) == 1 and len(conflicts) == 5
    assert qc.get_flag(db, f["_id"])["version"] == 2
    assert len(audits(db, state="applied")) == 1 and not audits(db, state="pending")


def test_quyet_dinh_sai_khong_ghi_gi(db):
    f = auto_flag(db)
    with pytest.raises(ValueError):
        qc.decide(db, f["_id"], "corrected", reason="x", decided_by="a", expected_version=1)
    with pytest.raises(qc.FlagNotFound):
        qc.decide(db, "khong_co", "rejected", reason="x", decided_by="a", expected_version=1)
    assert qc.get_flag(db, f["_id"])["version"] == 1 and not audits(db)


def test_corrected_va_validator(db):
    from pymongo.errors import WriteError
    f = auto_flag(db)
    out = qc.decide(db, f["_id"], "corrected", reason="gõ thừa", decided_by="a", expected_version=1,
                    corrected_value=1.18)
    assert out["corrected_value"] == 1.18
    with pytest.raises(WriteError):        # validator: corrected cần corrected_value
        db[R.FLAGS].update_one({"_id": f["_id"]}, {"$set": {"corrected_value": None}})
    out = qc.decide(db, f["_id"], "suspect", reason="bỏ quyết định", decided_by="a", expected_version=2)
    assert out["status"] == "suspect" and out["corrected_value"] is None and R.is_decided(out)


def test_co_thu_cong(db):
    d = qc.create_manual_flag(db, "BIEN_HOA", "high", TS, reason="K1", decided_by="manual_review")
    assert d["_id"] == R.flag_id("BIEN_HOA", "high", TS) and d["value"] == 11.18 and d["origin"] == "manual"
    with pytest.raises(qc.FlagExists):
        qc.create_manual_flag(db, "BIEN_HOA", "high", TS, reason="K1", decided_by="manual_review")
    with pytest.raises(qc.FlagNotFound):
        qc.create_manual_flag(db, "BIEN_HOA", "low", TS, reason="K1", decided_by="manual_review")
    assert len(audits(db, action="qc_flag_create", state="applied")) == 1 and not audits(db, state="pending")


def test_sua_metadata_ban_tin(db):
    out = qc.update_bulletin_fields(db, "a.pdf", {"source.is_primary": (True, False)}, reason="K6", actor="m")
    assert out["before"] == {"source.is_primary": True} and out["after"] == {"source.is_primary": False}
    assert db[R.COLLECTION_BULLETINS].find_one({"source_file": "a.pdf"})["source"]["is_primary"] is False
    with pytest.raises(qc.FieldConflict) as ei:      # giá trị cũ không còn đúng
        qc.update_bulletin_fields(db, "a.pdf", {"source.is_primary": (True, False)}, reason="K6", actor="m")
    assert (ei.value.field, ei.value.expected, ei.value.current) == ("source.is_primary", True, False)
    assert "mong đợi True, hiện có False" in str(ei.value)
    with pytest.raises(ValueError):
        qc.update_bulletin_fields(db, "a.pdf", {"observed_water_levels": (None, [])}, reason="x", actor="m")
    new_date = datetime(2025, 11, 7, 17, tzinfo=timezone.utc)
    qc.update_bulletin_fields(db, "a.pdf", {"bulletin_date": (datetime(2025, 11, 6, 17, tzinfo=timezone.utc), new_date),
                                            "qc_note": (None, "ghi chú")}, reason="K2", actor="m")
    doc = db[R.COLLECTION_BULLETINS].find_one({"source_file": "a.pdf"})
    assert doc["bulletin_date"] == new_date.replace(tzinfo=None) and doc["qc_note"] == "ghi chú"
    assert len(audits(db, action="bulletin_metadata", state="applied")) == 2


def test_sua_metadata_sai_kieu(db):
    for changes in ({"source.is_primary": (True, "false")}, {"source.is_primary": (True, None)},
                    {"source.is_primary": (1, False)}, {"weekday": ("Thứ Sáu", True)},
                    {"bulletin_date": (None, "2025-11-07")}, {"bulletin_date": (None, datetime(2025, 11, 7))},
                    {"qc_note": (None, 5)}):
        with pytest.raises(ValueError):
            qc.update_bulletin_fields(db, "a.pdf", changes, reason="x", actor="m")
    assert not audits(db)


def test_loi_mo_ho_giu_pending(db, monkeypatch):
    from pymongo.errors import NetworkTimeout
    f = auto_flag(db)
    real = qc._flag_update

    def boom(*a, **k):
        def w(sess):
            raise NetworkTimeout("giả lập timeout")
        return w
    monkeypatch.setattr(qc, "_flag_update", boom)
    with pytest.raises(NetworkTimeout):
        qc.decide(db, f["_id"], "rejected", reason="x", decided_by="a", expected_version=1)
    monkeypatch.setattr(qc, "_flag_update", real)
    assert len(audits(db, state="pending")) == 1          # giữ lại cho reconcile


def test_dat_applied_khi_pending_mat_bao_loi(db, monkeypatch):
    f = auto_flag(db)
    real = qc._flag_update

    def steal(*a, **k):
        w = real(*a, **k)

        def wrapped(sess):
            res = w(sess)
            db[R.AUDIT].delete_many({"state": "pending"})   # giả lập reconcile chạy đè
            return res
        return wrapped
    monkeypatch.setattr(qc, "_flag_update", steal)
    with pytest.raises(qc.AuditStateError):
        qc.decide(db, f["_id"], "rejected", reason="x", decided_by="a", expected_version=1)


def _pending(db, action, doc_id, after, at):
    db[R.AUDIT].insert_one({"at": at, "actor": "a", "action": action, "collection": R.FLAGS, "doc_id": doc_id,
                            "before": None, "after": after, "reason": "x", "state": "pending"})


def test_reconcile_ba_loai_va_grace(db):
    from datetime import timedelta
    old = datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc)
    now = old + timedelta(hours=1)
    f = auto_flag(db)
    # qc_decision đã ghi thật (status/decided_by/decided_at khớp) → applied
    qc.decide(db, f["_id"], "rejected", reason="r", decided_by="u", expected_version=1, now=old)
    db[R.AUDIT].update_many({}, {"$set": {"state": "pending"}})
    # qc_decision KHÔNG xảy ra (status khác) → xóa, dù version của cờ đã cao hơn
    _pending(db, "qc_decision", f["_id"], {"status": "confirmed_ok", "decided_by": "v", "reason": "r",
                                           "corrected_value": None, "version": 2}, old)
    # qc_flag_create: cờ có thật, khớp → applied; cờ không có → xóa
    db[R.COLLECTION].insert_one({"ts": TS + timedelta(hours=1), "meta": {"station": "BIEN_HOA", "kind": "low"},
                                 "value": -1.0, "qc": "ok", "ts_placeholder": False, "source_file": "x"})
    m = qc.create_manual_flag(db, "BIEN_HOA", "low", TS + timedelta(hours=1), reason="K5", decided_by="m", now=old)
    db[R.AUDIT].update_many({"doc_id": m["_id"]}, {"$set": {"state": "pending"}})
    _pending(db, "qc_flag_create", "tide_extremes:X:high:2020-01-01T00:00:00Z",
             {"reason": "K5", "decided_by": "m"}, old)
    # bulletin_metadata: field khớp after → applied; không khớp → xóa
    qc.update_bulletin_fields(db, "a.pdf", {"weekday": ("Thứ Sáu", "Thứ Bảy")}, reason="K2", actor="m", now=old)
    db[R.AUDIT].update_many({"action": "bulletin_metadata"}, {"$set": {"state": "pending"}})
    db[R.AUDIT].insert_one({"at": old, "actor": "m", "action": "bulletin_metadata", "collection": "hydromet_bulletins",
                            "doc_id": "a.pdf", "before": {"qc_note": None}, "after": {"qc_note": "chưa ghi"},
                            "reason": "x", "state": "pending"})
    # pending trẻ (trong grace) không bị đụng
    _pending(db, "qc_decision", f["_id"], {"status": "suspect"}, now - timedelta(seconds=30))
    out = qc.reconcile_pending(db, grace_s=600, now=now)
    assert out == {"applied": 3, "deleted": 3, "young": 1, "unknown": 0}
    left = {(a["action"], a["state"]) for a in audits(db)}
    assert ("bulletin_metadata", "applied") in left and ("qc_flag_create", "applied") in left
    assert len(audits(db, state="pending")) == 1


def test_retire_co_mo_coi(db):
    from datetime import timedelta
    f = auto_flag(db)
    with pytest.raises(ValueError):                      # phần tử còn: không phải mồ côi
        qc.retire_flag(db, f["_id"], reason="x", actor="m", expected_version=1)
    db[R.COLLECTION].delete_many({})
    with pytest.raises(qc.VersionConflict):
        qc.retire_flag(db, f["_id"], reason="x", actor="m", expected_version=5)
    out = qc.retire_flag(db, f["_id"], reason="bản chính đổi", actor="m", expected_version=1,
                         now=NOW + timedelta(seconds=1))
    assert out["retired"] and out["version"] == 2 and not R.is_active(out)
    a = audits(db, action="qc_flag_retire")
    assert len(a) == 1 and a[0]["state"] == "applied" and a[0]["after"]["retired"] is True


def test_decide_thoat_value_mismatch(db):
    """Cờ value_mismatch (giá trị gốc đổi sau khi quyết định) → quyết định lại gắn giá trị gốc hiện tại.

    `decided_value` là giá trị quyết định gắn vào; `value` giữ nguyên giá trị lúc gắn cờ (để job QC phân biệt
    giá trị cũ với giá trị mới). `current_value` giữ giá trị đã báo lệch (người xem đã thấy)."""
    f = auto_flag(db)
    db[R.FLAGS].update_one({"_id": f["_id"]}, {"$set": {"status": "rejected", "reason": "cũ", "decided_by": "a",
                                                         "decided_at": NOW, "value_mismatch": True,
                                                         "current_value": 1.18}})
    db[R.COLLECTION].update_one({}, {"$set": {"raw_value": 1.18}})
    out = qc.decide(db, f["_id"], "confirmed_ok", reason="xem lại giá trị mới", decided_by="b", expected_version=1)
    assert (out["decided_value"], out["value_mismatch"], out["current_value"]) == (1.18, False, 1.18)
    assert out["value"] == 11.18                     # giá trị lúc gắn cờ, không ghi đè
    a = audits(db, action="qc_decision")[0]
    assert (a["before"]["value"], a["before"]["value_mismatch"], a["before"]["current_value"]) == (11.18, True, 1.18)
    assert (a["after"]["value"], a["after"]["decided_value"], a["after"]["value_mismatch"]) == (11.18, 1.18, False)
    # reconcile so cả value/value_mismatch/decided_value: đặt lại pending, nội dung khớp → applied
    db[R.AUDIT].update_many({}, {"$set": {"state": "pending", "at": a["at"]}})
    from datetime import timedelta
    assert qc.reconcile_pending(db, grace_s=0, now=datetime.now(timezone.utc) + timedelta(seconds=1))["applied"] == 1


def test_decide_thoat_value_mismatch_khi_g0_chua_cap_nhat(db):
    """G0 chưa chạy lại sau khi bản tin đổi: `tide_extremes.raw_value` còn GIÁ TRỊ CŨ.

    Đường thoát vẫn phải thoát được: quyết định lại gắn giá trị đã báo lệch (`current_value`), và job QC chạy lại
    KHÔNG gắn mismatch lần nữa (giá trị quan sát còn là giá trị cũ lúc gắn cờ → không phải giá trị mới)."""
    f = auto_flag(db)
    db[R.FLAGS].update_one({"_id": f["_id"]}, {"$set": {"status": "rejected", "reason": "cũ", "decided_by": "a",
                                                         "decided_at": NOW, "value_mismatch": True,
                                                         "current_value": 1.18}})
    # tide_extremes vẫn giá trị CŨ (G0 chưa chạy lại)
    out = qc.decide(db, f["_id"], "confirmed_ok", reason="xem lại", decided_by="b", expected_version=1)
    assert out["decided_value"] == 1.18 and out["value_mismatch"] is False
    # job QC quan sát giá trị CŨ 11.18 → KHÔNG gắn mismatch lại
    hit = {"station": "BIEN_HOA", "kind": "high", "ts": int(TS.timestamp()), "ts_placeholder": False,
           "value": 11.18, "source_file": "2021/04/18/x.pdf",
           "rules": [{"rule": "R1", "detail": "d", "metric": {"value": 11.18}}]}
    op = R.plan_one(out, hit, "run2", NOW, 1)
    assert op["op"] == "touch" or not op.get("set", {}).get("value_mismatch")


def test_decide_bang_suspect_thoat_value_mismatch(db):
    """Bỏ quyết định (`suspect`) cũng là một đường thoát cho cờ value_mismatch."""
    f = auto_flag(db)
    db[R.FLAGS].update_one({"_id": f["_id"]}, {"$set": {"status": "rejected", "reason": "cũ", "decided_by": "a",
                                                         "decided_at": NOW, "value_mismatch": True,
                                                         "current_value": 1.18}})
    out = qc.decide(db, f["_id"], "suspect", reason="bỏ quyết định", decided_by="b", expected_version=1)
    assert out["status"] == "suspect" and out["value_mismatch"] is False and out["decided_value"] == 1.18


def test_decide_phan_tu_mat_dung_current_value(db):
    f = auto_flag(db)
    db[R.FLAGS].update_one({"_id": f["_id"]}, {"$set": {"value_mismatch": True, "current_value": 2.0}})
    db[R.COLLECTION].delete_many({})
    out = qc.decide(db, f["_id"], "rejected", reason="x", decided_by="b", expected_version=1)
    assert out["decided_value"] == 2.0 and out["value_mismatch"] is False


def test_unretire(db):
    from datetime import timedelta
    f = auto_flag(db)
    el = db[R.COLLECTION].find_one({}, {"_id": 0})
    db[R.COLLECTION].delete_many({})
    qc.retire_flag(db, f["_id"], reason="mồ côi", actor="m", expected_version=1, now=NOW)
    with pytest.raises(ValueError):                      # phần tử chưa có lại
        qc.unretire_flag(db, f["_id"], reason="x", actor="m", expected_version=2)
    db[R.COLLECTION].insert_one(el)
    with pytest.raises(qc.VersionConflict):
        qc.unretire_flag(db, f["_id"], reason="x", actor="m", expected_version=1)
    out = qc.unretire_flag(db, f["_id"], reason="bản tin có lại", actor="m", expected_version=2,
                           now=NOW + timedelta(seconds=5))
    assert out["retired"] is False and out["version"] == 3 and R.is_active(out)
    with pytest.raises(ValueError):                      # không còn retired
        qc.unretire_flag(db, f["_id"], reason="x", actor="m", expected_version=3)
    a = audits(db, action="qc_flag_unretire")
    assert len(a) == 1 and a[0]["state"] == "applied" and a[0]["before"]["retired"] is True
    db[R.AUDIT].update_many({"action": "qc_flag_unretire"}, {"$set": {"state": "pending"}})
    assert qc.reconcile_pending(db, grace_s=0, now=NOW + timedelta(hours=1))["applied"] == 1


# ---------------------------------------------------------------- nhánh transaction ----
@pytest.fixture()
def rs_db():
    """Database tạm trên replica set: `VNGIS_TEST_RS_URI` (ví dụ mongodb://mongo1:27101/?replicaSet=rs0, cụm profile
    `ha`); không đặt thì dùng `VNGIS_TEST_MONGO_URI`. Server không phải replica set → bỏ qua."""
    import os
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    uri = os.environ.get("VNGIS_TEST_RS_URI") or os.environ.get("VNGIS_TEST_MONGO_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=1500)
    try:
        hello = client.admin.command("hello")
    except PyMongoError as e:
        client.close()
        pytest.skip(f"không kết nối được {uri}: {type(e).__name__}")
    if not hello.get("setName"):
        client.close()
        pytest.skip("server không phải replica set: bỏ qua test nhánh transaction")
    d = client[f"vngis_test_qc_rs_{secrets.token_hex(4)}"]
    qc.ensure_collections(d)
    d[R.COLLECTION].insert_one({"ts": TS, "meta": {"station": "BIEN_HOA", "kind": "high"}, "value": 11.18,
                                "raw_value": 11.18, "qc": "ok", "ts_placeholder": False, "source_file": "x"})
    yield d
    client.drop_database(d.name)
    client.close()


@pytest.mark.replset
def test_transaction_decide_va_xung_dot(rs_db):
    assert qc._supports_txn(rs_db)
    f = auto_flag(rs_db)
    out = qc.decide(rs_db, f["_id"], "rejected", reason="r", decided_by="u", expected_version=1)
    assert out["version"] == 2
    assert [a["state"] for a in audits(rs_db)] == ["applied"]          # không qua pending
    with pytest.raises(qc.VersionConflict):
        qc.decide(rs_db, f["_id"], "confirmed_ok", reason="r", decided_by="v", expected_version=1)
    assert len(audits(rs_db)) == 1                                      # transaction hủy: không có audit thừa
    with pytest.raises(qc.FlagExists):
        qc.create_manual_flag(rs_db, "BIEN_HOA", "high", TS, reason="x", decided_by="m")
    assert len(audits(rs_db)) == 1
