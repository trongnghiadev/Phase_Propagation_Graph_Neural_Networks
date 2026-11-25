"""Test migration trên MongoDB thật (DB tạm riêng, tạo/xoá trong test).

    docker run --rm --network <mạng MongoDB> -e MONGO_TEST_URI=mongodb://mongo:27017 \
        -v "<gốc repo>:/repo" -w /repo pgnn-testdeps:3.12 python -m pytest migrations/mongo/tests -q

Không có MongoDB → skip. ``MIGRATION_SOURCE_DB`` (tuỳ chọn): DB nguồn để **đọc** `jobs`, `qc_flags`,
`audit_logs` thật, chép sang DB tạm rồi áp migration (kiểm tra 0 document cũ không hợp lệ). Không ghi vào DB nguồn.
"""
from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "packages" / "vngis" / "src"))

import runner  # noqa: E402
from _lib import load_spec  # noqa: E402

pymongo = pytest.importorskip("pymongo")
URI = os.getenv("MONGO_TEST_URI", "mongodb://mongo:27017")
PROTECTED = {"vietnam_gis", "vietnam_analytics", "vietnam_gis_presentation"}


@pytest.fixture()
def client():
    c = pymongo.MongoClient(URI, serverSelectionTimeoutMS=2000, tz_aware=True)
    try:
        c.admin.command("ping")
    except Exception:  # noqa: BLE001
        pytest.skip(f"không kết nối được MongoDB {URI}")
    yield c
    c.close()


@pytest.fixture()
def db(client):
    name = f"pgnn_test_migr_{secrets.token_hex(4)}"
    assert name not in PROTECTED
    yield client[name]
    client.drop_database(name)


def _now():
    return datetime.now(timezone.utc)


def test_schema_files_dong_bo_voi_code():
    from vngis import qc_rules as R

    assert load_spec("qc_flags")["validator"] == R.QC_FLAGS_VALIDATOR
    assert load_spec("qc_flags")["indexes"] == R.QC_FLAGS_INDEXES
    assert load_spec("audit_logs")["validator"] == R.AUDIT_LOGS_VALIDATOR
    assert load_spec("audit_logs")["indexes"] == R.AUDIT_LOGS_INDEXES
    for c in ("qc_flags", "audit_logs", "jobs", "model_forecasts"):
        assert load_spec(c)["validationLevel"] == "moderate"


def test_up_idempotent_verify_down(db):
    jobs = db["jobs"]
    jobs.insert_many([
        {"_id": "20261123T000000Z_aaaaaa", "job": "g0_flatten_bulletins", "status": "ok", "run_id": "x",
         "generated_at": _now(), "finished_at": _now(), "counts": {"a": 1}},
        {"_id": "ingest_bulletins:2026-11-24", "type": "ingest_bulletins", "state": "succeeded",
         "fence": 3, "heartbeat": _now(), "params": {}},
        {"_id": "cu_khong_hop_le", "note": "document cũ không thuộc bộ field nào"},
    ])
    out = runner.up(db)
    rep = {r["id"]: r for r in out}
    assert rep["0006_jobs_validator"]["report"]["n_invalid_existing"] == 1
    assert all(not r["verify"] for r in out)
    assert set(runner.applied(db)) == {m for m, _ in runner.discover()}
    # lần 2: bỏ qua; --force: áp lại không lỗi
    assert all(r.get("skipped") for r in runner.up(db))
    assert all(not r["verify"] for r in runner.up(db, force=True))
    assert not any(runner.verify(db).values())
    # moderate: sửa document cũ không hợp lệ vẫn được; chèn document sai thì bị chặn
    jobs.update_one({"_id": "cu_khong_hop_le"}, {"$set": {"note": "sửa"}})
    with pytest.raises(pymongo.errors.WriteError):
        jobs.insert_one({"_id": "moi_sai", "state": "queued"})
    with pytest.raises(pymongo.errors.WriteError):
        jobs.insert_one({"_id": "moi_sai2", "type": "x", "state": "khong_co"})
    jobs.insert_one({"_id": "moi_dung", "type": "forecast_run", "state": "queued", "params": {}})
    opts = next(db.list_collections(filter={"name": "jobs"}))["options"]
    assert opts["validationLevel"] == "moderate"
    assert {"queue_state_priority_created", "job_status_generated"} <= set(jobs.index_information())
    # model_forecasts: index unique
    mf = db["model_forecasts"]
    assert mf.index_information()["run_station_lead_unique"]["unique"]
    # down: jobs trước đó không có validator → tắt validator
    assert runner.down(db, "0006")["reverted"]
    jobs.insert_one({"_id": "sau_down", "x": 1})
    assert "0006_jobs_validator" not in runner.applied(db)


def test_0008_users_account_code_index(db):
    users = db["users"]
    users.insert_many([{"_id": "a", "username": "a"}, {"_id": "b", "username": "b", "account_code": None},
                       {"_id": "c", "username": "c", "account_code": None},
                       {"_id": "d", "username": "d", "account_code": "acc_000000000001"}])
    out = runner.up(db, only=["0008"])
    rep = out[0]["report"]
    assert not out[0]["verify"] and rep["n_without_code"] == 3 and rep["duplicates"] == []
    ix = users.index_information()["account_code_unique"]
    assert ix["unique"] and ix["partialFilterExpression"] == {"account_code": {"$type": "string"}}
    # null/thiếu trường không va nhau; chuỗi trùng bị chặn
    users.insert_one({"_id": "e", "username": "e", "account_code": None})
    with pytest.raises(pymongo.errors.DuplicateKeyError):
        users.insert_one({"_id": "f", "username": "f", "account_code": "acc_000000000001"})
    # idempotent
    assert all(not r["verify"] for r in runner.up(db, only=["0008"], force=True))
    assert not runner.verify(db, only=["0008"])["0008_users_account_code"]
    # down: index do bước này tạo → xoá
    assert runner.down(db, "0008")["reverted"]
    assert "account_code_unique" not in users.index_information()


def test_0008_dung_khi_co_ma_trung(db):
    db["users"].insert_many([{"_id": "a", "account_code": "acc_000000000002"},
                             {"_id": "b", "account_code": "acc_000000000002"}])
    assert runner.up(db, only=["0008"], dry_run=True)[0]["report"]["duplicates"] == ["acc_000000000002"]
    with pytest.raises(RuntimeError):
        runner.up(db, only=["0008"])
    assert "0008_users_account_code" not in runner.applied(db)


def test_dry_run_khong_doi_gi(db):
    db["jobs"].insert_one({"_id": "a", "job": "x", "status": "ok"})
    out = runner.up(db, dry_run=True, only=["0006"])
    assert out[0]["report"]["dry_run"] and not runner.applied(db)
    assert not next(db.list_collections(filter={"name": "jobs"}))["options"].get("validator")


def test_down_khoi_phuc_validator_cu(db):
    from vngis import qc

    qc.ensure_collections(db)  # validator do code tạo (moderate)
    before = next(db.list_collections(filter={"name": "qc_flags"}))["options"]
    runner.up(db, only=["0004"])
    runner.down(db, "0004")
    after = next(db.list_collections(filter={"name": "qc_flags"}))["options"]
    assert after["validator"] == before["validator"]


@pytest.mark.skipif(not os.getenv("MIGRATION_SOURCE_DB"), reason="đặt MIGRATION_SOURCE_DB để thử trên bản sao dữ liệu thật")
def test_ban_sao_du_lieu_that_khong_co_document_khong_hop_le(client, db):
    src = client[os.environ["MIGRATION_SOURCE_DB"]]
    assert db.name != src.name
    for c in ("jobs", "qc_flags", "audit_logs"):
        docs = list(src[c].find({}))
        if docs:
            db[c].insert_many(docs)
    out = {r["id"]: r for r in runner.up(db)}
    for mid in ("0004_qc_flags_validator", "0005_audit_logs_validator", "0006_jobs_validator"):
        rep = out[mid]["report"]
        assert rep["n_invalid_existing"] == 0, (mid, rep)
        assert not out[mid]["verify"]
    print({m: (r["report"]["n_documents"], r["report"].get("n_invalid_existing")) for m, r in out.items()})
