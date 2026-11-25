"""Kiểm thử promote nguyên tử `hydromet_bulletins` (`vngis.atomic_reload`) trên MongoDB thật.

Cần database **ghi được, riêng cho test**: đặt ``VNGIS_TEST_WRITE_DB`` (tên phải bắt đầu bằng
``vngis_test_``; database bị xoá sau test) và ``VNGIS_TEST_MONGO_URI``. Không có → bỏ qua.
Không bao giờ chạy trên `vietnam_gis`.
"""
from __future__ import annotations

import os

import pytest
from bson import ObjectId

from vngis import atomic_reload as ar

pytestmark = pytest.mark.mongo


@pytest.fixture()
def wdb():
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    name = os.environ.get("VNGIS_TEST_WRITE_DB", "")
    if not name.startswith("vngis_test_"):
        pytest.skip("chưa đặt VNGIS_TEST_WRITE_DB=vngis_test_<...> (DB ghi được riêng cho test)")
    client = MongoClient(os.environ.get("VNGIS_TEST_MONGO_URI", "mongodb://localhost:27017"),
                         serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        client.close()
        pytest.skip(f"không kết nối được MongoDB: {type(e).__name__}")
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)
    client.close()


def _doc(i: int, sha: str | None = None) -> dict:
    return {"_id": ObjectId(), "source_file": f"b{i}.pdf", "bulletin_type": "other", "parse": {},
            "source": {"is_primary": True, **({"sha256": sha} if sha else {})}}


def _seed(db, n: int = 2) -> None:
    db["hydromet_bulletins"].insert_many([_doc(i) for i in range(n)])
    db["hydromet_bulletin_files"].insert_many(
        [{"source_file": f"b{i}.pdf", "is_primary": True} for i in range(n)])


def test_reload_lien_tiep_khong_bi_chan_boi_prev(wdb):
    """Sau lượt thành công `__prev` bị xoá ⇒ lượt sau KHÔNG bị `ReloadBlocked` (lỗi cũ)."""
    _seed(wdb)
    r1 = ar.reload_bulletins_atomic(wdb, actor="test")
    assert r1["status"] == "ok"
    assert ar.PREV_SUFFIX not in wdb.list_collection_names()
    r2 = ar.reload_bulletins_atomic(wdb, actor="test")
    assert r2["status"] == "ok" and wdb["hydromet_bulletins"].count_documents({}) == 2


def test_con_prev_cu_thi_tu_choi_va_khong_xoa(wdb):
    _seed(wdb)
    wdb[ar.PREV_SUFFIX].insert_one({"x": 1})
    with pytest.raises(ar.ReloadBlocked):
        ar.reload_bulletins_atomic(wdb, actor="test")
    wdb.create_collection(ar.staging_name("r1"))
    wdb[ar.staging_name("r1")].insert_one(_doc(9))
    with pytest.raises(ar.ReloadBlocked):
        ar.promote_staging(wdb, ar.staging_name("r1"))
    assert wdb[ar.PREV_SUFFIX].count_documents({}) == 1          # không tự xoá __prev sót lại
    assert wdb["hydromet_bulletins"].count_documents({}) == 2     # bản chính giữ nguyên


def test_promote_ingest_tang_so_ban_tin(wdb):
    _seed(wdb)
    st = ar.staging_name("ing1")
    wdb[st].insert_many([_doc(i) for i in range(3)])
    wdb["hydromet_bulletin_files"].insert_one({"source_file": "b2.pdf", "is_primary": True})
    checks = ar.validate_ingest_staging(wdb, st)
    assert all(c["ok"] for c in checks), checks
    info = ar.promote_staging(wdb, st)
    assert info["count"] == 3 and info["backup_dropped"]
    assert set(wdb.list_collection_names()) >= {"hydromet_bulletins"}
    assert ar.PREV_SUFFIX not in wdb.list_collection_names() and st not in wdb.list_collection_names()


def test_validate_ingest_phat_hien_lech_sha_va_rong(wdb):
    st = ar.staging_name("ing2")
    wdb.create_collection(st)
    assert not next(c for c in ar.validate_ingest_staging(wdb, st) if c["name"] == "co_ban_tin")["ok"]
    wdb[st].insert_one(_doc(0, sha="aa"))
    wdb["hydromet_bulletin_files"].insert_one({"source_file": "b0.pdf", "sha256": "bb", "is_primary": True})
    bad = {c["name"] for c in ar.validate_ingest_staging(wdb, st) if not c["ok"]}
    assert bad == {"sha256_khop_noi_dung"}


def test_mat_khoa_truoc_rename_giu_nguyen_ban_chinh(wdb):
    """`guard.check()` ném `LockLost` ngay trước rename ⇒ không rename, bản sao `__prev` được dọn."""
    from vngis.locks import LockLost

    _seed(wdb)
    st = ar.staging_name("lost1")
    wdb[st].insert_many([_doc(i) for i in range(5)])

    class Guard:
        n = 0

        def check(self):
            self.n += 1
            if self.n == 2:
                raise LockLost("mất khoá (giả lập)")

    with pytest.raises(LockLost):
        ar.promote_staging(wdb, st, guard=Guard())
    assert wdb["hydromet_bulletins"].count_documents({}) == 2
    assert ar.PREV_SUFFIX not in wdb.list_collection_names()
    # reload qua API/worker: LockLost giữ nguyên loại lỗi, staging bị xoá
    with pytest.raises(LockLost):
        ar.reload_bulletins_atomic(wdb, actor="test", run_id="lost2", guard=Guard())
    assert ar.staging_name("lost2") not in wdb.list_collection_names()


def test_run_id_khong_hop_le():
    with pytest.raises(ar.ReloadError):
        ar.staging_name("a.b$c")
