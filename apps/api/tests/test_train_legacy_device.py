"""`POST /models/train`: giao lại bản `models` cũ có ``hyperparams.epochs`` > 500 (trước khi siết trần) và trường
``device`` (``auto|cpu|cuda``) cùng ước lượng thời gian theo thiết bị."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.auth.password import hash_password
from api.auth.users import UserRepo

PWD = "MatKhau-Test-012345"
NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)


def _h(client: Any, db: Any, username: str) -> dict:
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password(PWD), role="admin",
                     display_name=f"test {username}")
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _legacy(db, ver: int = 9, epochs: int = 1500) -> None:
    db["models"].insert_one({"_id": f"phasegnn-{ver}", "model_id": f"phasegnn-{ver}", "name": "phasegnn",
                             "version": ver, "status": "staging", "train_state": "failed", "job_id": f"train:{ver}",
                             "train_attempt": 1, "hyperparams": {"epochs": epochs, "learning_rate": 1e-3,
                                                                 "max_train_samples": 500, "max_val_samples": 100},
                             "created_at": NOW, "updated_at": NOW})


def test_giao_lai_ban_cu_epochs_qua_tran_422_ro_rang(client, fake_db, monkeypatch):
    monkeypatch.delenv("TRAIN_TIMEOUT_S", raising=False)
    h = _h(client, fake_db, "u_legacy")
    _legacy(fake_db)
    r = client.post("/api/v1/models/train", json={"model_version": 9}, headers=h)
    assert r.status_code == 422, r.text
    d = r.json()["error"]["detail"]
    assert d["error"] == "legacy_hyperparams_invalid" and d["fields"] == ["epochs"]
    assert d["legacy_values"] == {"epochs": 1500} and d["limits"]["epochs"]["max"] == 500
    assert "epochs" in d["reason"]
    m = fake_db["models"].find_one({"version": 9})
    assert m["train_state"] == "failed" and m["train_attempt"] == 1, "không đổi bản ghi khi từ chối"
    assert fake_db["jobs"].count_documents({"type": "train"}) == 0
    # gửi kèm epochs mới → giao lại được, các hyperparams khác giữ nguyên
    r2 = client.post("/api/v1/models/train", json={"model_version": 9, "epochs": 40}, headers=h)
    assert r2.status_code == 200, r2.text
    job = fake_db["jobs"].find_one({"_id": r2.json()["job_id"]})
    assert job["params"]["epochs"] == 40 and job["params"]["max_train_samples"] == 500
    assert fake_db["models"].find_one({"version": 9})["hyperparams"]["epochs"] == 40


def test_giao_lai_loi_o_truong_gui_kem_van_422_thuong(client, fake_db):
    h = _h(client, fake_db, "u_legacy2")
    _legacy(fake_db, ver=10, epochs=20)
    r = client.post("/api/v1/models/train", json={"model_version": 10, "epochs": 900}, headers=h)
    assert r.status_code == 422, r.text
    detail = r.json()["error"]["detail"]
    assert not (isinstance(detail, dict) and detail.get("error") == "legacy_hyperparams_invalid"), detail


def test_train_device_vao_job_va_uoc_luong(client, fake_db, monkeypatch):
    for k in ("TRAIN_TIMEOUT_S", "PGNN_TRAIN_SEC_PER_SAMPLE", "PGNN_TRAIN_SEC_PER_SAMPLE_CUDA", "PGNN_TRAIN_DEVICE"):
        monkeypatch.delenv(k, raising=False)
    h = _h(client, fake_db, "u_device")
    r = client.post("/api/v1/models/train", json={"epochs": 10, "max_train_samples": 2000, "device": "cuda"},
                    headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estimate"]["device"] == "cuda"
    job = fake_db["jobs"].find_one({"_id": body["job_id"]})
    assert job["params"]["device"] == "cuda"
    assert "device" not in fake_db["models"].find_one({"version": body["model_version"]})["hyperparams"]
    r_cpu = client.post("/api/v1/models/train", json={"epochs": 10, "max_train_samples": 2000, "device": "cpu"},
                        headers=h)
    assert abs(r_cpu.json()["estimate"]["per_epoch_s"] / body["estimate"]["per_epoch_s"] - 4.0) < 0.05
    assert client.post("/api/v1/models/train", json={"device": "gpu"}, headers=h).status_code == 422


def test_train_cuda_khong_bi_tu_choi_khi_cpu_vuot(client, fake_db, monkeypatch):
    """Toàn bộ dữ liệu, timeout 3600 s: CPU chắc chắn vượt (422) nhưng CUDA (≈ CPU/4) chỉ ``may_exceed``."""
    for k in ("PGNN_TRAIN_SEC_PER_SAMPLE", "PGNN_TRAIN_SEC_PER_SAMPLE_CUDA", "PGNN_TRAIN_DEVICE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRAIN_TIMEOUT_S", "7200")
    h = _h(client, fake_db, "u_device2")
    assert client.post("/api/v1/models/train", json={"epochs": 50}, headers=h).status_code == 422
    r = client.post("/api/v1/models/train", json={"epochs": 50, "device": "cuda"}, headers=h)
    assert r.status_code == 200, r.text
    monkeypatch.setenv("PGNN_TRAIN_DEVICE", "cuda")
    r2 = client.post("/api/v1/models/train", json={"epochs": 50}, headers=h)
    assert r2.status_code == 200 and r2.json()["estimate"]["device"] == "cuda", r2.text
