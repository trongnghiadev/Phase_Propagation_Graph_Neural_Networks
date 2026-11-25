"""Kiểm thử lỗi 500 ở `GET /api/v1/alerts` khi `_id` là `ObjectId` (không có `alert_id`).

**Bối cảnh.** Cảnh báo do job `phasegnn` sinh (`packages/phasegnn/src/phasegnn/job.py`)
lưu vào collection `alerts` với `_id` kiểu `ObjectId` của MongoDB. Bản ghi cũ (hoặc bản ghi
ghi tay) **không có** trường `alert_id` ⇒ API fallback sang `_id` trả về `ObjectId` — vốn
không serialize được bởi JSON ⇒ `TypeError: 'ObjectId' object is not iterable` ⇒ HTTP 500.

Ba endpoint liên quan được kiểm tra:

- `GET /alerts` — phải 200 và `items[0]["alert_id"]` là **chuỗi**;
- `GET /alerts/{alert_id}` — phải tìm được bản ghi khi truyền chuỗi hoặc `ObjectId`;
- `POST /alerts/{alert_id}/ack` — phải cập nhật đúng bản ghi.

Bản ghi có `alert_id` chuỗi bình thường vẫn phải hoạt động như cũ (không phá vỡ hành vi).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from bson import ObjectId

from api.auth.password import hash_password
from api.auth.users import UserRepo

PWD = "MatKhau-Test-012345"

#: `_id` cố định để kiểm tra việc tra cứu bằng `ObjectId`.
OID = ObjectId("6b04e06a0e3cc5c8fdf9d370")


# ------------------------------------------------------------------ helpers ----
def _mk_user(db: Any, username: str, role: str = "admin") -> str:
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password(PWD), role=role,
                     display_name=f"test {username}")
    return username


def _login(client: Any, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_alert_objectid(db: Any) -> ObjectId:
    """Bản ghi cảnh báo có `_id` là `ObjectId` và **không** có `alert_id` (bản ghi cũ)."""
    doc = {
        "_id": OID,
        "model_name": "phasegnn-v1", "lead_day": 1, "level": "I",
        "station": "PHU_AN", "target_date": "2026-11-24", "issued_date": "2026-11-24",
        "last_fence": 0, "model": "phasegnn-v1", "predicted_h": 1.5, "threshold": 1.2,
    }
    db["alerts"].insert_one(dict(doc))
    return OID


def _seed_alert_with_id(db: Any, alert_id: str = "phasegnn-v1:PHU_AN:2026-11-24:1:I") -> str:
    """Bản ghi cảnh báo có `alert_id` chuỗi bình thường (dạng job sinh ra sau khi sửa)."""
    db["alerts"].insert_one({
        "_id": alert_id, "alert_id": alert_id, "station": "PHU_AN", "basin": "Dong Nai",
        "type": "flood", "severity": 2, "level": "I",
        "issued_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "status": "issued", "era": "from_2025_07",
    })
    return alert_id


# --------------------------------------------------------------------- tests ---
def test_danh_sach_canh_bao_objectid_khong_gay_500(client, fake_db):
    """`GET /alerts` với bản ghi `_id` là `ObjectId` → 200, `alert_id` là chuỗi."""
    _mk_user(fake_db, "u_al_oid")
    data = _login(client, "u_al_oid")
    _seed_alert_objectid(fake_db)

    r = client.get("/api/v1/alerts", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    item = body["items"][0]
    # `alert_id` phải là chuỗi (không phải `ObjectId` → không 500 khi JSON encode).
    assert isinstance(item["alert_id"], str)
    assert item["alert_id"] == str(OID)
    assert item["station"] == "PHU_AN"


def test_danh_sach_canh_bao_objectid_serializable_json(client, fake_db):
    """Toàn bộ payload phải serialize được thành JSON (nguồn gốc lỗi 500)."""
    _mk_user(fake_db, "u_al_json")
    data = _login(client, "u_al_json")
    _seed_alert_objectid(fake_db)

    r = client.get("/api/v1/alerts", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    # `r.json()` đã ép serialize; kiểm tra lại bằng `json.dumps` cho chắc.
    import json

    json.dumps(r.json(), ensure_ascii=False)


def test_chi_tiet_canh_bao_objectid(client, fake_db):
    """`GET /alerts/{alert_id}` với `alert_id` là chuỗi hoá `_id` → 200."""
    _mk_user(fake_db, "u_al_det")
    data = _login(client, "u_al_det")
    _seed_alert_objectid(fake_db)

    r = client.get(f"/api/v1/alerts/{OID}", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["station"] == "PHU_AN"
    assert isinstance(body["alert_id"], str)


def test_xac_nhan_canh_bao_objectid(client, fake_db):
    """`POST /alerts/{alert_id}/ack` với `alert_id` là chuỗi hoá `_id` → 200 và cập nhật đúng bản ghi."""
    _mk_user(fake_db, "u_al_ack")
    data = _login(client, "u_al_ack")
    _seed_alert_objectid(fake_db)

    r = client.post(f"/api/v1/alerts/{OID}/ack",
                    json={"note": "test", "status": "acknowledged"},
                    headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "acknowledged"
    assert isinstance(body["alert_id"], str)

    saved = fake_db["alerts"].find_one({"_id": OID})
    assert saved is not None, "ack phải cập nhật đúng bản ghi có `_id` là ObjectId"
    assert saved["status"] == "acknowledged"
    assert saved["ack_note"] == "test"


def test_canh_bao_co_alert_id_chuoi_van_hoat_dong(client, fake_db):
    """Bản ghi có `alert_id` chuỗi → không đổi hành vi (list + detail + ack)."""
    _mk_user(fake_db, "u_al_ok")
    data = _login(client, "u_al_ok")
    alert_id = _seed_alert_with_id(fake_db)

    r = client.get("/api/v1/alerts", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["alert_id"] == alert_id
    assert items[0]["era"] == "from_2025_07"

    r2 = client.get(f"/api/v1/alerts/{alert_id}", headers=_auth(data["access_token"]))
    assert r2.status_code == 200, r2.text
    assert r2.json()["alert_id"] == alert_id

    r3 = client.post(f"/api/v1/alerts/{alert_id}/ack", json={},
                     headers=_auth(data["access_token"]))
    assert r3.status_code == 200, r3.text
    assert fake_db["alerts"].find_one({"alert_id": alert_id})["status"] == "acknowledged"


def test_alert_id_khong_ton_tai_tra_404(client, fake_db):
    """`alert_id` không tồn tại → 404 (không phải 500)."""
    _mk_user(fake_db, "u_al_404")
    data = _login(client, "u_al_404")
    r = client.get("/api/v1/alerts/khong-ton-tai-123", headers=_auth(data["access_token"]))
    assert r.status_code == 404, r.text
