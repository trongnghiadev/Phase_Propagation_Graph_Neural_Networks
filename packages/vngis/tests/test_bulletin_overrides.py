"""Áp lại sửa metadata bản tin đã duyệt khi nạp lại (`vngis.atomic_reload.apply_bulletin_overrides`).

Phần đơn vị dùng mongomock. Phần dữ liệu thật (``VNGIS_TEST_SOURCE_DB``, ví dụ ``vietnam_gis``) chỉ **đọc** DB
nguồn, chép sang DB tạm ``VNGIS_TEST_WRITE_DB=vngis_test_<...>`` (bị xoá sau test) rồi nạp lại.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from vngis import atomic_reload as ar

mongomock = pytest.importorskip("mongomock")

T0 = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)
D_OLD = datetime(2025, 11, 6, 17, 0, tzinfo=timezone.utc)
D_NEW = datetime(2025, 11, 7, 17, 0, tzinfo=timezone.utc)


def _audit(sf, before, after, at, state="applied", source="cli"):
    return {"at": at, "actor": "manual_review", "action": "bulletin_metadata", "source": source,
            "collection": "hydromet_bulletins", "doc_id": sf, "target": {"source_file": sf},
            "before": before, "after": after, "reason": "test", "state": state}


def _bulletin(sf, primary=True, date=None, weekday=None):
    d = {"_id": ObjectId(), "source_file": sf, "bulletin_type": "tide_forecast_5day", "parse": {},
         "source": {"is_primary": primary}}
    if date is not None:
        d["bulletin_date"] = date
    if weekday is not None:
        d["weekday"] = weekday
    return d


@pytest.fixture()
def db():
    c = mongomock.MongoClient(tz_aware=True)
    yield c["vngis_test_overrides"]
    c.close()


def _seed_audit(db):
    db["audit_logs"].insert_many([
        _audit("a.pdf", {"source.is_primary": True}, {"source.is_primary": False}, T0),
        _audit("k/b.pdf", {"source.is_primary": False}, {"source.is_primary": True}, T0 + timedelta(seconds=1)),
        _audit("c.pdf", {"bulletin_date": D_OLD, "weekday": "Thứ Sáu", "qc_note": None},
               {"bulletin_date": D_NEW, "weekday": "Thứ Bảy", "qc_note": "ghi chú"}, T0 + timedelta(seconds=2)),
        # pending: không áp; action khác: bỏ qua
        _audit("d.pdf", {"source.is_primary": True}, {"source.is_primary": False}, T0, state="pending"),
        {**_audit("a.pdf", None, None, T0), "action": "review_confirmed"},
    ])


def test_gom_override_theo_thoi_gian_va_hoan_tac(db):
    _seed_audit(db)
    # sửa rồi hoàn tác (api_revert) → giá trị hiệu lực = giá trị gốc
    db["audit_logs"].insert_many([
        _audit("e.pdf", {"source.is_primary": True}, {"source.is_primary": False}, T0),
        _audit("e.pdf", {"source.is_primary": False}, {"source.is_primary": True}, T0 + timedelta(hours=1),
               source="api_revert"),
    ])
    ov = ar.bulletin_overrides(db)
    assert set(ov) == {"a.pdf", "k/b.pdf", "c.pdf", "e.pdf"}
    assert ov["c.pdf"]["qc_note"] == {"original": None, "value": "ghi chú", "n": 1}
    assert ov["e.pdf"]["source.is_primary"] == {"original": True, "value": True, "n": 2}


def test_ap_lai_idempotent_va_xung_dot(db):
    _seed_audit(db)
    st = db["stg"]
    st.insert_many([_bulletin("a.pdf", True), _bulletin("k/b.pdf", False),
                    _bulletin("c.pdf", True, D_OLD, "Thứ Sáu"), _bulletin("x.pdf")])
    r1 = ar.apply_bulletin_overrides(db, "stg")
    assert r1["applied"] == 5 and r1["unchanged"] == 0 and not r1["conflicts"] and not r1["missing"]
    assert st.find_one({"source_file": "a.pdf"})["source"]["is_primary"] is False
    assert st.find_one({"source_file": "k/b.pdf"})["source"]["is_primary"] is True
    c = st.find_one({"source_file": "c.pdf"})
    assert c["weekday"] == "Thứ Bảy" and c["qc_note"] == "ghi chú"
    assert c["bulletin_date"].replace(tzinfo=timezone.utc) == D_NEW
    r2 = ar.apply_bulletin_overrides(db, "stg")
    assert r2["applied"] == 0 and r2["unchanged"] == 5
    # nguồn đã đổi: không ghi đè, báo xung đột
    st.update_one({"source_file": "c.pdf"}, {"$set": {"weekday": "Chủ Nhật"}})
    r3 = ar.apply_bulletin_overrides(db, "stg")
    assert [(x["source_file"], x["field"]) for x in r3["conflicts"]] == [("c.pdf", "weekday")]
    assert st.find_one({"source_file": "c.pdf"})["weekday"] == "Chủ Nhật"


def test_thieu_ban_tin_va_khong_co_audit(db):
    assert ar.apply_bulletin_overrides(db, "stg")["documents"] == 0
    _seed_audit(db)
    db["stg"].insert_one(_bulletin("a.pdf"))
    rep = ar.apply_bulletin_overrides(db, "stg")
    assert sorted(rep["missing"]) == ["c.pdf", "k/b.pdf"] and rep["applied"] == 1


# --------------------------------------------------------------- MongoDB thật -----
def _real_client():
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    c = MongoClient(os.environ.get("VNGIS_TEST_MONGO_URI", "mongodb://localhost:27017"),
                    serverSelectionTimeoutMS=1500, tz_aware=True)
    try:
        c.admin.command("ping")
    except PyMongoError as e:
        c.close()
        pytest.skip(f"không kết nối được MongoDB: {type(e).__name__}")
    return c


@pytest.fixture()
def wdb():
    name = os.environ.get("VNGIS_TEST_WRITE_DB", "")
    if not name.startswith("vngis_test_"):
        pytest.skip("chưa đặt VNGIS_TEST_WRITE_DB=vngis_test_<...>")
    c = _real_client()
    c.drop_database(name)
    yield c[name]
    c.drop_database(name)
    c.close()


@pytest.mark.mongo
def test_reload_nguyen_tu_ap_lai_sua(wdb):
    """Nạp lại từ "bản parse mới" (giá trị gốc) → bản chính vẫn mang các sửa đã duyệt."""
    _seed_audit(wdb)
    main = [_bulletin("a.pdf", False), _bulletin("k/b.pdf", True), _bulletin("c.pdf", True, D_NEW, "Thứ Bảy")]
    main[2]["qc_note"] = "ghi chú"
    wdb["hydromet_bulletins"].insert_many(main)
    wdb["hydromet_bulletin_files"].insert_many([{"source_file": d["source_file"]} for d in main])
    fresh = [_bulletin("a.pdf", True), _bulletin("k/b.pdf", False), _bulletin("c.pdf", True, D_OLD, "Thứ Sáu")]
    r = ar.reload_bulletins_atomic(wdb, actor="test", documents=fresh)
    assert r["status"] == "ok" and r["overrides"]["applied"] == 5
    got = {d["source_file"]: d for d in wdb["hydromet_bulletins"].find({})}
    assert got["a.pdf"]["source"]["is_primary"] is False and got["k/b.pdf"]["source"]["is_primary"] is True
    assert got["c.pdf"]["bulletin_date"] == D_NEW and got["c.pdf"]["qc_note"] == "ghi chú"
    assert wdb["audit_logs"].count_documents({}) == 5  # không ghi thêm audit


@pytest.mark.mongo
@pytest.mark.skipif(not os.environ.get("VNGIS_TEST_SOURCE_DB"), reason="đặt VNGIS_TEST_SOURCE_DB để thử trên dữ liệu thật")
def test_du_lieu_that_giu_10_sua_k2_k6_sau_reload(wdb):
    """Bản sao `hydromet_bulletins` thật, "parse lại" (đưa 10 field về giá trị gốc) → reload → còn đủ 10 sửa."""
    from vngis.qc import _MISSING, _get_path, _same

    c = wdb.client
    src = c[os.environ["VNGIS_TEST_SOURCE_DB"]]
    assert src.name != wdb.name
    audits = list(src["audit_logs"].find({"action": "bulletin_metadata", "state": "applied"}))
    assert len(audits) >= 10
    wdb["audit_logs"].insert_many(audits)
    wdb["hydromet_bulletin_files"].insert_many(src["hydromet_bulletin_files"].find({}, {"content": 0}))
    docs = list(src["hydromet_bulletins"].find({}))
    wdb["hydromet_bulletins"].insert_many(docs)
    flags = list(src["qc_flags"].find({"status": "rejected"}))
    if flags:
        wdb["qc_flags"].insert_many(flags)
    ov = ar.bulletin_overrides(wdb)
    # "bản parse mới": mọi field đã sửa quay về giá trị gốc (như parser sinh từ PDF)
    by_sf = {d["source_file"]: d for d in docs}
    n_fields = 0
    for sf, fields in ov.items():
        d = by_sf[sf]
        for f, ent in fields.items():
            n_fields += 1
            head, _, leaf = f.rpartition(".")
            tgt = d[head] if head else d
            if ent["original"] is None:
                tgt.pop(leaf, None)
            else:
                tgt[leaf] = ent["original"]
    assert n_fields == 12 and len(ov) == 10  # 9 × is_primary (K6) + 3 field của bản K2
    r = ar.reload_bulletins_atomic(wdb, actor="test", documents=docs)
    assert r["status"] == "ok", r
    assert r["overrides"]["applied"] == 12 and not r["overrides"]["conflicts"]
    main = wdb["hydromet_bulletins"]
    for sf, fields in ov.items():
        cur = main.find_one({"source_file": sf})
        for f, ent in fields.items():
            got = _get_path(cur, f)
            assert _same(None if got is _MISSING else got, ent["value"]), (sf, f)
    assert main.count_documents({"source.is_primary": True}) == src["hydromet_bulletins"].count_documents(
        {"source.is_primary": True})
    # lần 2: idempotent
    r2 = ar.reload_bulletins_atomic(wdb, actor="test")
    assert r2["overrides"]["applied"] == 0 and r2["overrides"]["unchanged"] == 12
    # K1 (qc_flags, tách khỏi bản tin) không bị nạp lại đụng tới
    assert wdb["qc_flags"].count_documents({"status": "rejected"}) == len(flags)
