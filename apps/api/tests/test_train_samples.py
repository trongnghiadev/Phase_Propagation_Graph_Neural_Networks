"""`POST /models/train`: giới hạn số mẫu, ``extra="forbid"``, ước lượng thời gian so với ``TRAIN_TIMEOUT_S``."""
from __future__ import annotations

from typing import Any

from api.auth.password import hash_password
from api.auth.users import UserRepo

PWD = "MatKhau-Test-012345"


def _h(client: Any, db: Any, username: str) -> dict:
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password(PWD), role="admin",
                     display_name=f"test {username}")
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_train_gioi_han_mau_vao_job_va_hyperparams(client, fake_db, monkeypatch):
    monkeypatch.delenv("TRAIN_TIMEOUT_S", raising=False)
    monkeypatch.delenv("PGNN_TRAIN_SEC_PER_SAMPLE", raising=False)
    h = _h(client, fake_db, "u_samples")
    r = client.post("/api/v1/models/train", json={"epochs": 20, "max_train_samples": 2000,
                                                  "max_val_samples": 300}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ver = body["model_version"]
    assert body["estimate"]["verdict"] == "ok" and "warning" not in body
    job = fake_db["jobs"].find_one({"_id": f"train:{ver}"})
    assert job["params"] == {"epochs": 20, "max_train_samples": 2000, "max_val_samples": 300,
                             "model_version": ver}
    m = fake_db["models"].find_one({"version": ver})
    assert m["hyperparams"] == {"epochs": 20, "max_train_samples": 2000, "max_val_samples": 300}
    # giao lại giữ giới hạn mẫu cũ (+ trường gửi kèm)
    fake_db["jobs"].update_one({"_id": f"train:{ver}"}, {"$set": {"state": "failed"}})
    fake_db["models"].update_one({"version": ver}, {"$set": {"train_state": "failed"}})
    r2 = client.post("/api/v1/models/train", json={"model_version": ver, "subsample": "head"}, headers=h)
    assert r2.status_code == 200, r2.text
    job2 = fake_db["jobs"].find_one({"_id": r2.json()["job_id"]})
    assert job2["params"]["max_train_samples"] == 2000 and job2["params"]["subsample"] == "head"


def test_train_khoa_la_va_mien_gia_tri_422(client, fake_db):
    h = _h(client, fake_db, "u_forbid")
    for body in ({"epochs": 5, "script": "x.py"}, {"max_train_samples": 10}, {"max_val_samples": 999_999},
                 {"subsample": "all"}, {"epochs": 501}):
        r = client.post("/api/v1/models/train", json=body, headers=h)
        assert r.status_code == 422, (body, r.text)
    assert fake_db["jobs"].count_documents({"type": "train"}) == 0


def test_train_chac_chan_vuot_timeout_422_va_canh_bao(client, fake_db, monkeypatch):
    monkeypatch.delenv("PGNN_TRAIN_SEC_PER_SAMPLE", raising=False)
    h = _h(client, fake_db, "u_budget")
    monkeypatch.setenv("TRAIN_TIMEOUT_S", "3600")
    r = client.post("/api/v1/models/train", json={"epochs": 50}, headers=h)  # toàn bộ: ≥ 11 epoch × ~25 phút
    assert r.status_code == 422, r.text
    assert r.json()["error"]["detail"]["error"] == "train_exceeds_timeout"
    assert fake_db["models"].count_documents({}) == 0, "không cấp phiên bản khi bị từ chối"
    monkeypatch.setenv("TRAIN_TIMEOUT_S", str(6 * 3600))
    r = client.post("/api/v1/models/train", json={"epochs": 50}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["estimate"]["verdict"] == "may_exceed" and "TRAIN_TIMEOUT_S" in r.json()["warning"]
