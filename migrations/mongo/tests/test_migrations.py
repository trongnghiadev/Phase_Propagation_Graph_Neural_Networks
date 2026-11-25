"""Test migration trên MongoDB thật (DB tạm riêng, tạo/xoá trong test).

    docker run --rm --network <mạng MongoDB> -e MONGO_TEST_URI=mongodb://mongo:27017 \
        -v "<gốc repo>:/repo" -w /repo pgnn-testdeps:3.12 python -m pytest migrations/mongo/tests -q

Không có MongoDB → skip. ``MIGRATION_SOURCE_DB`` (tuỳ chọn): DB nguồn để **đọc** `jobs`, `qc_flags`,
`audit_logs`, `users`, `alerts`, `models` thật, chép sang DB tạm rồi áp migration (kiểm tra 0 document cũ không hợp lệ). Không ghi vào DB nguồn.
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


def test_0010_models_version_unique(db):
    models = db["models"]
    models.insert_many([{"_id": "cu-a", "version": "v20240101"}, {"_id": "cu-b", "version": "v20240101"},
                        {"_id": "khong-so"}, {"_id": "phasegnn-1", "version": 1}])
    out = runner.up(db, only=["0010"])
    rep = out[0]["report"]
    assert not out[0]["verify"] and rep["duplicates"] == [] and rep["n_non_numeric_version"] == 3
    ix = models.index_information()["version_unique_number"]
    assert ix["unique"] and ix["partialFilterExpression"] == {"version": {"$type": "number"}}
    models.insert_one({"_id": "cu-c", "version": "v20240101"})  # chuỗi: ngoài partial
    with pytest.raises(pymongo.errors.DuplicateKeyError):
        models.insert_one({"_id": "phasegnn-1b", "version": 1})
    assert all(not r["verify"] for r in runner.up(db, only=["0010"], force=True))
    assert not runner.verify(db, only=["0010"])["0010_models_version_unique"]
    assert runner.down(db, "0010")["reverted"]
    assert "version_unique_number" not in models.index_information()


def test_0010_dung_khi_co_version_trung(db):
    db["models"].insert_many([{"_id": "a", "version": 2}, {"_id": "b", "version": 2}])
    assert runner.up(db, only=["0010"], dry_run=True)[0]["report"]["duplicates"] == [2]
    with pytest.raises(RuntimeError):
        runner.up(db, only=["0010"])
    assert "0010_models_version_unique" not in runner.applied(db)


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


def _seed_0009(db):
    """Dữ liệu cũ tổng hợp: tên đăng nhập giả (không phải người thật) lẫn giá trị phải giữ nguyên."""
    db["users"].insert_many([
        {"_id": "nv_a", "username": "nv_a", "role": "reviewer"},                       # chưa có mã
        {"_id": "nv_b", "username": "nv_b", "role": "admin", "account_code": "acc_0000000000b1"},
        {"_id": "uid_c", "username": "nv_c", "role": "viewer", "account_code": None},   # _id khác tên
    ])
    t = _now()
    db["audit_logs"].insert_many([
        {"_id": "a1", "at": t, "actor": "nv_a", "action": "qc_decision", "collection": "qc_flags", "doc_id": "f1",
         "before": {"status": "suspect", "decided_by": "nv_b"},
         "after": {"status": "confirmed_ok", "decided_by": "nv_a"},
         "reason": "r", "state": "applied", "reverted_by": "nv_c", "revert_claim": {"by": "uid_c", "at": t}},
        {"_id": "a2", "at": t, "actor": "manual_review", "action": "qc_decision", "collection": "qc_flags",
         "doc_id": "f2", "before": None, "after": {"decided_by": "manual_review"}, "reason": "r", "state": "applied"},
        {"_id": "a3", "at": t, "actor": "user:acc_0000000000b1:admin", "action": "user_perm", "collection": "users",
         "doc_id": "nv_a", "target": {"user_id": "nv_a"}, "before": {"perm": {}}, "after": None, "reason": "r",
         "state": "applied"},
        {"_id": "a4", "at": t, "actor": "ai_do_la", "action": "x", "collection": "c", "doc_id": "d",
         "before": None, "after": None, "reason": "r", "state": "applied"},
    ])
    db["qc_flags"].insert_many([{"_id": "f1", "decided_by": "nv_a", "retired_by": "nv_c"},
                                {"_id": "f2", "decided_by": "qc_job"}, {"_id": "f3", "decided_by": None}])
    db["jobs"].insert_one({"_id": "j1", "type": "train", "state": "queued", "requested_by": "nv_b", "params": {}})
    db["alerts"].insert_one({"_id": "al1", "ack_by": "uid_c"})
    db["models"].insert_one({"_id": "m1", "version": 1, "status": "production", "promoted_by": "nv_a"})


def _walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)
    else:
        yield x


def test_0009_an_danh_hoa_actor(db):
    _seed_0009(db)
    runner.up(db, only=["0005"])   # validator audit_logs (moderate): bản ghi sau khi sửa vẫn phải hợp lệ
    snap = {c: list(db[c].find({})) for c in ("audit_logs", "qc_flags", "jobs", "alerts", "models", "users")}
    dry = runner.up(db, only=["0009"], dry_run=True)[0]["report"]
    assert dry["dry_run"] and dry["n_users_code_assigned"] == 2
    assert dry["fields_changed"] == {
        "audit_logs.actor": 1, "audit_logs.reverted_by": 1, "audit_logs.revert_claim.by": 1,
        "audit_logs.before.decided_by": 1, "audit_logs.after.decided_by": 1, "qc_flags.decided_by": 1,
        "qc_flags.retired_by": 1, "jobs.requested_by": 1, "alerts.ack_by": 1, "models.promoted_by": 1,
        "audit_logs.user_perm.doc_id": 1, "audit_logs.user_perm.target": 1}
    assert dry["documents_changed"] == {"audit_logs": 2, "qc_flags": 1, "jobs": 1, "alerts": 1, "models": 1}
    assert dry["n_unmapped"] == {"audit_logs.actor": 1}
    # dry-run: không ghi gì, kể cả `_migrations`
    assert {c: list(db[c].find({})) for c in snap} == snap
    assert "0009_anonymize_actors" not in runner.applied(db)
    assert runner.verify(db, only=["0009"])["0009_anonymize_actors"]   # còn tên → verify báo

    out = runner.up(db, only=["0009"])[0]
    assert not out["verify"] and out["report"]["audit_id"]
    code = {u["_id"]: u["account_code"] for u in db["users"].find({})}
    assert code["nv_b"] == "acc_0000000000b1" and all(c.startswith("acc_") for c in code.values())
    a1 = db["audit_logs"].find_one({"_id": "a1"})
    assert a1["actor"] == f"user:{code['nv_a']}" and a1["reverted_by"] == f"user:{code['uid_c']}"
    assert a1["revert_claim"]["by"] == f"user:{code['uid_c']}"
    assert a1["before"]["decided_by"] == f"user:{code['nv_b']}"
    assert a1["after"]["decided_by"] == f"user:{code['nv_a']}"
    a3 = db["audit_logs"].find_one({"_id": "a3"})
    assert a3["doc_id"] == code["nv_a"] and a3["target"] == {"account_code": code["nv_a"]}
    assert a3["actor"] == "user:acc_0000000000b1:admin"   # đã đúng định dạng → giữ
    assert db["audit_logs"].find_one({"_id": "a2"})["actor"] == "manual_review"
    assert db["audit_logs"].find_one({"_id": "a4"})["actor"] == "ai_do_la"  # không khớp tài khoản → giữ, chỉ đếm
    assert db["qc_flags"].find_one({"_id": "f1"})["retired_by"] == f"user:{code['uid_c']}"
    assert db["qc_flags"].find_one({"_id": "f2"})["decided_by"] == "qc_job"
    assert db["jobs"].find_one({"_id": "j1"})["requested_by"] == f"user:{code['nv_b']}"
    assert db["alerts"].find_one({"_id": "al1"})["ack_by"] == f"user:{code['uid_c']}"
    assert db["models"].find_one({"_id": "m1"})["promoted_by"] == f"user:{code['nv_a']}"
    # bản ghi audit cho chính việc ẩn danh (chỉ số lượng); mọi bản ghi vẫn hợp lệ với validator 0005
    meta = db["audit_logs"].find_one({"action": "anonymize_actors"})
    assert meta["actor"] == "migration:0009" and meta["after"]["fields_changed"]["audit_logs.actor"] == 1
    assert db["audit_logs"].count_documents({"$nor": [load_spec("audit_logs")["validator"]]}) == 0
    names = {"nv_a", "nv_b", "nv_c", "uid_c"}
    assert not names & {str(v) for d in db["_migrations"].find({}) for v in _walk(d)}
    assert not names & {str(v) for v in _walk(meta)}
    # idempotent: lần 2 không đổi gì, không chèn thêm audit
    again = runner.up(db, only=["0009"], force=True)[0]
    assert again["report"]["fields_changed"] == {} and again["report"]["audit_id"] is None
    assert db["audit_logs"].count_documents({"action": "anonymize_actors"}) == 1
    # down không khả thi: lỗi rõ, giữ `_migrations`
    with pytest.raises(RuntimeError):
        runner.down(db, "0009")
    assert "0009_anonymize_actors" in runner.applied(db)


@pytest.mark.skipif(not os.getenv("MIGRATION_SOURCE_DB"), reason="đặt MIGRATION_SOURCE_DB để thử trên bản sao dữ liệu thật")
def test_ban_sao_du_lieu_that_khong_co_document_khong_hop_le(client, db):
    src = client[os.environ["MIGRATION_SOURCE_DB"]]
    assert db.name != src.name
    for c in ("jobs", "qc_flags", "audit_logs", "users", "alerts", "models"):
        docs = list(src[c].find({}))
        if docs:
            db[c].insert_many(docs)
    out = {r["id"]: r for r in runner.up(db)}
    for mid in ("0004_qc_flags_validator", "0005_audit_logs_validator", "0006_jobs_validator"):
        rep = out[mid]["report"]
        assert rep["n_invalid_existing"] == 0, (mid, rep)
        assert not out[mid]["verify"]
    assert not out["0009_anonymize_actors"]["verify"]
    assert db["audit_logs"].count_documents({"$nor": [load_spec("audit_logs")["validator"]]}) == 0
    print({m: (r["report"].get("n_documents"), r["report"].get("n_invalid_existing")) for m, r in out.items()})
    rep9 = out["0009_anonymize_actors"]["report"]
    print("0009", {k: rep9[k] for k in ("n_users", "n_users_code_assigned", "documents_changed", "n_unmapped")})
