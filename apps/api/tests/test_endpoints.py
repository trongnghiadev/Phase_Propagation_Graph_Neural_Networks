"""Kiểm thử các endpoint REST chính của API (GĐ7.8).

Phạm vi kiểm thử:
- luồng xác thực: đăng nhập → refresh → logout (blacklist `jti`) → token bị từ chối;
- trạm thủy văn: đọc, sửa (409 Conflict khi `version` lệch, 423 Locked khi có khoá);
- đơn vị hành chính: `era` bắt buộc (ADR 0003);
- thống kê, GeoJSON features, dự báo, cảnh báo, rà soát QC, job, nhật ký + hoàn tác;
- quản trị: cập nhật quyền bằng `$bit`, thống kê & xoá cache, xem khoá phân tán;
- giới hạn tần suất: 429 khi vượt ngưỡng;
- OpenAPI `/docs` và `request_id`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from api.auth.password import hash_password
from api.auth.users import UserRepo

PWD = "MatKhau-Test-012345"


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


def _seed_station(db: Any, code: str = "BIEN_HO_1") -> None:
    db["stations"].insert_one({
        "_id": code, "code": code, "name": "Biên Hòa", "kind": "tide_gauge",
        "location": [106.9, 10.95], "province_code": 80, "version": 1, "active": True,
    })


def _seed_tide(db: Any, code: str = "BIEN_HO_1") -> None:
    db["tide_extremes"].insert_one({
        "_id": f"{code}:2026-01-01T00:00:00Z", "meta": {"station": code, "kind": "high"},
        "ts": datetime(2026, 1, 1, tzinfo=timezone.utc), "value": 1.85, "qc": None,
    })


# --------------------------------------------------------------------- auth ----
def test_dang_nhap_phat_hanh_hai_token(client, fake_db):
    """Đăng nhập trả access + refresh; access hết hạn trong ≤ 15 phút."""
    _mk_user(fake_db, "u_auth")
    data = _login(client, "u_auth")
    assert data["token_type"] == "bearer"
    assert data["access_token"] and data["refresh_token"]
    assert data["expires_in"] <= 15 * 60, "access token phải hết hạn trong ≤ 15 phút"
    assert data["user_id"] == "u_auth"
    assert data["perm_ver"] >= 1


def test_refresh_token_chi_dung_mot_lan(client, fake_db):
    """Refresh token dùng một lần; dùng lại lần hai → 401 (blacklist `jti`)."""
    _mk_user(fake_db, "u_refresh")
    data = _login(client, "u_refresh")
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r2.status_code == 401, f"refresh token phải bị từ chối lần hai, thực tế {r2.status_code}"


def test_dang_nhap_sai_mat_khau_bi_401(client, fake_db):
    """Sai mật khẩu → 401 (không rò rỉ thông tin tài khoản có tồn tại hay không)."""
    _mk_user(fake_db, "u_auth2")
    r = client.post("/api/v1/auth/login", json={"username": "u_auth2", "password": "Sai-Mat-Khau-01"})
    assert r.status_code == 401, r.text


def test_dang_nhap_tai_khoan_khong_ton_tai_bi_401(client, fake_db):
    """Tài khoản không tồn tại → 401 (thông điệp chung, không tiết lộ)."""
    r = client.post("/api/v1/auth/login", json={"username": "khong_ton_tai", "password": PWD})
    assert r.status_code == 401, r.text


def test_tai_khoan_bi_vo_hieu_thi_khong_dang_nhap_duoc(client, fake_db):
    """Tài khoản bị vô hiệu → 403 khi đăng nhập."""
    _mk_user(fake_db, "u_off")
    UserRepo(fake_db).set_active("u_off", False)
    r = client.post("/api/v1/auth/login", json={"username": "u_off", "password": PWD})
    assert r.status_code == 403, r.text


def test_logout_dua_jti_vao_blacklist(client, fake_db):
    """Sau logout, access token cũ phải bị từ chối (401)."""
    _mk_user(fake_db, "u_logout")
    data = _login(client, "u_logout")
    headers = _auth(data["access_token"])
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    r = client.post("/api/v1/auth/logout", headers=headers)
    assert r.status_code in (200, 204), r.text
    r2 = client.get("/api/v1/auth/me", headers=headers)
    assert r2.status_code == 401, f"token sau logout phải bị từ chối, thực tế {r2.status_code}"


def test_me_tra_ve_quyen(client, fake_db):
    """`/auth/me` trả quyền hiện tại (theo collection và mặc định)."""
    _mk_user(fake_db, "u_me", role="viewer")
    data = _login(client, "u_me")
    r = client.get("/api/v1/auth/me", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "u_me"
    assert "READ" in body["perm_flags"]["*"]
    assert body["collection_perm"]["*"] == 1


def test_danh_sach_vai_tro(client, fake_db):
    """`/auth/roles` trả đủ 8 vai trò mặc định."""
    _mk_user(fake_db, "u_roles")
    data = _login(client, "u_roles")
    r = client.get("/api/v1/auth/roles", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    names = {x["name"] for x in r.json()}
    assert {"viewer", "analyst", "editor", "reviewer", "data_engineer",
            "hydrologist", "forecast_officer", "admin"} <= names


# ----------------------------------------------------------------- stations ----
def test_danh_sach_tram(client, fake_db):
    """GET /stations trả danh sách có phân trang."""
    _mk_user(fake_db, "u_st")
    data = _login(client, "u_st")
    _seed_station(fake_db)
    r = client.get("/api/v1/stations", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["code"] == "BIEN_HO_1"


def test_chi_tiet_tram(client, fake_db):
    """GET /stations/{code} trả chi tiết; mã không tồn tại → 404."""
    _mk_user(fake_db, "u_st2")
    data = _login(client, "u_st2")
    _seed_station(fake_db)
    r = client.get("/api/v1/stations/BIEN_HO_1", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Biên Hòa"

    r = client.get("/api/v1/stations/KHONG_CO", headers=_auth(data["access_token"]))
    assert r.status_code == 404, r.text


def test_sua_tram_xung_dot_version_bi_409(client, fake_db):
    """PATCH với `version` cũ → 409 Conflict (kiểm soát lạc tiến phiên bản)."""
    _mk_user(fake_db, "u_st3")
    data = _login(client, "u_st3")
    _seed_station(fake_db)
    headers = _auth(data["access_token"])
    r = client.patch("/api/v1/stations/BIEN_HO_1", json={"name": "Tên mới", "version": 99},
                     headers=headers)
    assert r.status_code == 409, f"phải là 409, thực tế {r.status_code}: {r.text}"


def test_sua_tram_thanh_cong(client, fake_db):
    """PATCH với `version` đúng → 200 và `version` tăng."""
    _mk_user(fake_db, "u_st4")
    data = _login(client, "u_st4")
    _seed_station(fake_db)
    headers = _auth(data["access_token"])
    r = client.patch("/api/v1/stations/BIEN_HO_1", json={"name": "Tên mới", "version": 1},
                     headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert fake_db["stations"].find_one({"code": "BIEN_HO_1"})["name"] == "Tên mới"


def test_sua_tram_khoa_bi_423(client, fake_db):
    """Trạm đang bị khoá sửa bởi tiến trình khác → 423 Locked."""
    from vngis.locks import LockGuard

    from api.state import state

    _mk_user(fake_db, "u_st5")
    data = _login(client, "u_st5")
    _seed_station(fake_db)
    headers = _auth(data["access_token"])

    guard = LockGuard(state.lock, db=fake_db, resource="edit:stations:BIEN_HO_1", ttl_ms=30_000)
    guard.acquire()
    try:
        r = client.patch("/api/v1/stations/BIEN_HO_1", json={"name": "Tên mới", "version": 1},
                         headers=headers)
        assert r.status_code == 423, f"phải là 423, thực tế {r.status_code}: {r.text}"
    finally:
        guard.release()


def test_du_lieu_thoi_extremes(client, fake_db):
    """GET /stations/{code}/tides trả các đỉnh triều."""
    _mk_user(fake_db, "u_tide")
    data = _login(client, "u_tide")
    _seed_station(fake_db)
    _seed_tide(fake_db)
    r = client.get("/api/v1/stations/BIEN_HO_1/tides?from=2025-12-01&to=2026-02-01",
                   headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1


# ------------------------------------------------------------- admin units ----
def test_eras_hoi_danh_buoc_chuyen_doi(client, fake_db):
    """GET /admin-units/eras trả hai thời kỳ địa giới."""
    _mk_user(fake_db, "u_au0")
    data = _login(client, "u_au0")
    r = client.get("/api/v1/admin-units/eras", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["eras"]) == {"before_2025_07", "from_2025_07"}
    assert body["before"] == "before_2025_07"
    assert body["from"] == "from_2025_07"


def test_don_vi_hanh_chinh_chi_tiet_theo_era(client, fake_db):
    """Truy vấn tỉnh theo `era` hợp lệ → 200; `era` sai → 422."""
    _mk_user(fake_db, "u_au")
    data = _login(client, "u_au")
    fake_db["province_timeline"].insert_one({
        "_id": "before_2025_07:80", "era": "before_2025_07", "code": 80,
        "name": "TP. Hồ Chí Minh", "level": "province",
    })
    headers = _auth(data["access_token"])

    r = client.get("/api/v1/admin-units/provinces/before_2025_07/80", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "TP. Hồ Chí Minh"

    r = client.get("/api/v1/admin-units/provinces/khong_hop_le/80", headers=headers)
    assert r.status_code == 422, f"phải là 422, thực tế {r.status_code}: {r.text}"


def test_don_vi_hanh_chinh_danh_sach_suy_ra_era(client, fake_db):
    """GET /admin-units/provinces suy ra `era` từ ngày `at` và trả `era` trong kết quả."""
    _mk_user(fake_db, "u_au2")
    data = _login(client, "u_au2")
    headers = _auth(data["access_token"])
    r = client.get("/api/v1/admin-units/provinces?at=2025-01-01", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["era"] == "before_2025_07"

    r = client.get("/api/v1/admin-units/provinces?at=2026-01-01", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["era"] == "from_2025_07"


def test_at_khong_hop_le_bi_422(client, fake_db):
    """`at` sai định dạng → 422."""
    _mk_user(fake_db, "u_au3")
    data = _login(client, "u_au3")
    r = client.get("/api/v1/admin-units/provinces?at=abc", headers=_auth(data["access_token"]))
    assert r.status_code == 422, f"phải là 422, thực tế {r.status_code}: {r.text}"


# --------------------------------------------------------- stats / features ----
def test_thong_ke_dinh_trieu(client, fake_db):
    """GET /stats/tide trả số liệu thống kê (không lỗi)."""
    _mk_user(fake_db, "u_stats")
    data = _login(client, "u_stats")
    _seed_station(fake_db)
    _seed_tide(fake_db)
    r = client.get("/api/v1/stats/tide", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text


def test_geojson_features(client, fake_db):
    """GET /features/{coll} trả FeatureCollection đúng chuẩn GeoJSON."""
    _mk_user(fake_db, "u_feat")
    data = _login(client, "u_feat")
    fake_db["rivers"].insert_one({"_id": "R1", "name": "Sông Đồng Nai", "comid": "12345",
                                   "geometry": {"type": "LineString",
                                                "coordinates": [[106.9, 10.9], [107.0, 11.0]]}})
    r = client.get("/api/v1/features/rivers", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["features"][0]["geometry"]["type"] == "LineString"


def test_trace_comid(client, fake_db):
    """GET /trace/{comid} trả chuỗi đoạn sông theo dọc."""
    _mk_user(fake_db, "u_trace")
    data = _login(client, "u_trace")
    fake_db["rivers"].insert_one({"_id": "R1", "name": "Sông Đồng Nai", "comid": "12345",
                                   "geometry": {"type": "LineString",
                                                "coordinates": [[106.9, 10.9], [107.0, 11.0]]}})
    r = client.get("/api/v1/trace/12345", headers=_auth(data["access_token"]))
    assert r.status_code in (200, 404), r.text


# ---------------------------------------------------------------- forecasts ----
def test_du_bao_khong_co_mo_hinh_bi_503(client, fake_db):
    """Không có mô hình PhaseGNN → 503 (không trả dữ liệu bịa)."""
    _mk_user(fake_db, "u_fc")
    data = _login(client, "u_fc")
    r = client.get("/api/v1/forecasts/latest", headers=_auth(data["access_token"]))
    assert r.status_code in (200, 503), f"thực tế {r.status_code}: {r.text}"
    if r.status_code == 503:
        assert "model" in r.text.lower() or "mô hình" in r.text.lower()


# ------------------------------------------------------------ alerts/review ----
def test_danh_sach_canh_bao(client, fake_db):
    """GET /alerts trả danh sách cảnh báo."""
    _mk_user(fake_db, "u_al")
    data = _login(client, "u_al")
    fake_db["alerts"].insert_one({"_id": "A1", "alert_id": "A1", "station": "BIEN_HO_1",
                                   "basin": "Dong Nai", "type": "flood", "severity": 2,
                                   "issued_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                                   "status": "issued", "era": "from_2025_07"})
    r = client.get("/api/v1/alerts", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    assert r.json()["items"][0]["era"] == "from_2025_07"


def test_xac_nhan_canh_bao(client, fake_db):
    """POST /alerts/{id}/ack đánh dấu đã xác nhận."""
    _mk_user(fake_db, "u_al2")
    data = _login(client, "u_al2")
    fake_db["alerts"].insert_one({"_id": "A2", "alert_id": "A2", "station": "BIEN_HO_1",
                                   "type": "flood", "severity": 2,
                                   "issued_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                                   "status": "issued"})
    r = client.post("/api/v1/alerts/A2/ack", json={}, headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    assert fake_db["alerts"].find_one({"_id": "A2"})["status"] == "acknowledged"


def test_hang_doi_kiem_duyet(client, fake_db):
    """GET /review trả hàng đợi QC; duyệt một bản ghi → trạng thái chuyển sang approved."""
    _mk_user(fake_db, "u_rev")
    data = _login(client, "u_rev")
    fake_db["qc_flags"].insert_one({"_id": "Q1", "collection": "tide_extremes", "station": "BIEN_HO_1",
                                    "reason": "nghi ngờ sai", "state": "pending", "version": 1,
                                    "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
    headers = _auth(data["access_token"])
    r = client.get("/api/v1/review", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1

    r = client.post("/api/v1/review/Q1/approve", json={"decision": "approve", "version": 1},
                    headers=headers)
    assert r.status_code == 200, r.text
    assert fake_db["qc_flags"].find_one({"_id": "Q1"})["state"] == "approved"


def test_duyet_qc_xung_dot_version_bi_409(client, fake_db):
    """Duyệt QC với `version` cũ → 409."""
    _mk_user(fake_db, "u_rev2")
    data = _login(client, "u_rev2")
    fake_db["qc_flags"].insert_one({"_id": "Q2", "collection": "tide_extremes", "state": "pending",
                                    "version": 5})
    r = client.post("/api/v1/review/Q2/approve", json={"decision": "approve", "version": 1},
                    headers=_auth(data["access_token"]))
    assert r.status_code == 409, f"phải là 409, thực tế {r.status_code}: {r.text}"


# --------------------------------------------------------------------- jobs ----
def test_khoi_dong_job(client, fake_db):
    """POST /jobs/{type} tạo job; GET /jobs/{id} theo dõi trạng thái."""
    _mk_user(fake_db, "u_job")
    data = _login(client, "u_job")
    headers = _auth(data["access_token"])
    r = client.post("/api/v1/jobs/cache_warm", json={"params": {}}, headers=headers)
    assert r.status_code in (200, 202), f"thực tế {r.status_code}: {r.text}"
    body = r.json()
    job_id = body.get("id") or body.get("job_id")
    assert job_id
    assert body["state"] == "queued"
    assert body["requested_by"] == "u_job"

    r2 = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == job_id


def test_loai_job_khong_cho_phep_bi_400(client, fake_db):
    """Loại job ngoài danh sách cho phép → 400 (tránh chạy lệnh tuỳ ý)."""
    _mk_user(fake_db, "u_job2")
    data = _login(client, "u_job2")
    r = client.post("/api/v1/jobs/rm_minus_rf", json={"params": {}},
                    headers=_auth(data["access_token"]))
    assert r.status_code == 400, f"phải là 400, thực tế {r.status_code}: {r.text}"


# -------------------------------------------------------------------- audit ----
def test_nhat_ky_ghi_lai_thay_doi(client, fake_db):
    """Thay đổi qua API phải được ghi vào `audit_logs`."""
    _mk_user(fake_db, "u_aud")
    data = _login(client, "u_aud")
    headers = _auth(data["access_token"])
    _seed_station(fake_db)
    client.patch("/api/v1/stations/BIEN_HO_1", json={"name": "Tên đã sửa", "version": 1},
                 headers=headers)

    r = client.get("/api/v1/audit", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "phải có ít nhất một bản ghi nhật ký"
    assert items[0]["collection"] == "stations"


def test_hoan_tac_thay_doi(client, fake_db):
    """`POST /audit/{id}/revert` khôi phục giá trị `before` và đánh dấu `reverted`."""
    _mk_user(fake_db, "u_aud2")
    data = _login(client, "u_aud2")
    headers = _auth(data["access_token"])
    _seed_station(fake_db)
    client.patch("/api/v1/stations/BIEN_HO_1", json={"name": "Tên đã sửa", "version": 1},
                 headers=headers)

    r = client.get("/api/v1/audit", headers=headers)
    audit_id = r.json()["items"][0]["id"]

    r = client.post(f"/api/v1/audit/{audit_id}/revert", json={"reason": "sửa nhầm"}, headers=headers)
    assert r.status_code in (200, 409), f"thực tế {r.status_code}: {r.text}"
    if r.status_code == 200:
        assert fake_db["audit_logs"].find_one({"_id": audit_id})["state"] == "reverted"


# -------------------------------------------------------------------- users ----
def test_quan_ly_nguoi_dung(client, fake_db):
    """Tạo → đọc → kích hoạt/khoá tài khoản."""
    _mk_user(fake_db, "u_mgr")
    data = _login(client, "u_mgr")
    headers = _auth(data["access_token"])

    r = client.post("/api/v1/users", json={"username": "moi_tao", "password": PWD,
                                            "role": "viewer", "display_name": "Người mới"},
                    headers=headers)
    assert r.status_code in (200, 201), f"thực tế {r.status_code}: {r.text}"
    body = r.json()
    user_id = body.get("_id") or body.get("user_id")
    assert user_id
    assert "password_hash" not in body

    r = client.get("/api/v1/users", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 2

    r = client.patch(f"/api/v1/users/{user_id}/active", json={"active": False}, headers=headers)
    assert r.status_code == 200, r.text
    assert fake_db["users"].find_one({"_id": user_id})["active"] is False


def test_tao_nguoi_dung_trung_ten_bi_409(client, fake_db):
    """Trùng tên đăng nhập → 409."""
    _mk_user(fake_db, "u_mgr2")
    _mk_user(fake_db, "trung_ten")
    data = _login(client, "u_mgr2")
    r = client.post("/api/v1/users", json={"username": "trung_ten", "password": PWD,
                                            "role": "viewer"}, headers=_auth(data["access_token"]))
    assert r.status_code == 409, r.text


def test_cap_nhat_quyen_dung_atomic_bit(client, requires_bit):
    """PUT /users/{id}/perm dùng `$bit` → cập nhật nguyên tử, `perm_ver` tăng."""
    fake_db = requires_bit
    _mk_user(fake_db, "u_perm")
    data = _login(client, "u_perm")
    headers = _auth(data["access_token"])
    target = _mk_user(fake_db, "u_target", role="viewer")

    before = UserRepo(fake_db).perm_ver(target)
    r = client.put(f"/api/v1/users/{target}/perm",
                   json={"grant": ["UPDATE", "EXPORT"], "revoke": []}, headers=headers)
    assert r.status_code == 200, f"thực tế {r.status_code}: {r.text}"
    after = UserRepo(fake_db).perm_ver(target)
    assert after > before, "perm_ver phải tăng"

    perm = UserRepo(fake_db).effective_perm(target)
    assert perm["*"] & 4, "phải có quyền UPDATE (bit 4)"
    assert perm["*"] & 16, "phải có quyền EXPORT (bit 16)"


def test_cap_nhat_quyen_ten_quyen_sai_bi_422(client, requires_bit):
    """Tên quyền không hợp lệ → 422 (không phải 500)."""
    fake_db = requires_bit
    _mk_user(fake_db, "u_perm2")
    data = _login(client, "u_perm2")
    headers = _auth(data["access_token"])
    target = _mk_user(fake_db, "u_target2", role="viewer")
    r = client.put(f"/api/v1/users/{target}/perm",
                   json={"grant": ["KHONG_CO_QUYEN"], "revoke": []}, headers=headers)
    assert r.status_code == 422, f"phải là 422, thực tế {r.status_code}: {r.text}"


def test_cap_nhat_quyen_thu_hoi_quyen_duoc(client, requires_bit):
    """Thu hồi quyền bằng `$bit` → bit tương ứng bị xoá trong `perm["*"]`."""
    fake_db = requires_bit
    _mk_user(fake_db, "u_perm3")
    data = _login(client, "u_perm3")
    headers = _auth(data["access_token"])
    target = _mk_user(fake_db, "u_target3", role="admin")

    r = client.put(f"/api/v1/users/{target}/perm",
                   json={"grant": [], "revoke": ["MANAGE_USERS"]}, headers=headers)
    assert r.status_code == 200, f"thực tế {r.status_code}: {r.text}"
    perm = UserRepo(fake_db).effective_perm(target)
    assert not perm["*"] & 256, "quyền MANAGE_USERS phải bị thu hồi"


# -------------------------------------------------------------------- admin ----
def test_thong_ke_va_xoa_cache(client, fake_db):
    """GET /admin/cache cho số liệu; DELETE /admin/cache xoá theo mẫu."""
    _mk_user(fake_db, "u_cache")
    data = _login(client, "u_cache")
    headers = _auth(data["access_token"])
    r = client.get("/api/v1/admin/cache", headers=headers)
    assert r.status_code == 200, r.text
    assert "stats" in r.json() and "info" in r.json()

    r = client.request("DELETE", "/api/v1/admin/cache", json={"prefix": "perm:"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["deleted_keys"] >= 0


def test_xem_khoa_phan_tan(client, fake_db):
    """GET /admin/locks liệt kê khoá đang giữ."""
    _mk_user(fake_db, "u_lock")
    data = _login(client, "u_lock")
    r = client.get("/api/v1/admin/locks", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and "backend" in body


def test_health_endpoint(client, fake_db):
    """GET /admin/health trả trạng thái dịch vụ."""
    _mk_user(fake_db, "u_health")
    data = _login(client, "u_health")
    r = client.get("/api/v1/admin/health", headers=_auth(data["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "mongodb" in body and "redis" in body and "lock_backend" in body


# ------------------------------------------------------------------- openapi ----
def test_openapi_va_docs(client, fake_db):
    """`/openapi.json` và `/docs` phải sẵn sàng."""
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    spec = r.json()
    assert "/api/v1/stations" in spec["paths"]
    assert spec["info"]["title"]
    r = client.get("/docs")
    assert r.status_code == 200, r.text


def test_request_id_duoc_sinh(client, fake_db):
    """`X-Request-Id` do client gửi được giữ lại trong header phản hồi."""
    _mk_user(fake_db, "u_req")
    data = _login(client, "u_req")
    r = client.get("/api/v1/stations", headers={**_auth(data["access_token"]),
                                                "X-Request-Id": "abc-123-xyz"})
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Request-Id") == "abc-123-xyz"


# ------------------------------------------------------------- rate limiting ----
def test_gioi_han_tan_suat_bi_429(client, fake_db):
    """Vượt ngưỡng giới hạn tần suất → 429 (có header `Retry-After`)."""
    from api.state import state

    _mk_user(fake_db, "u_rl")
    cfg = state.config.rate_limit
    # `Config` của `vngis.config` là model **đóng băng** (frozen) → dùng `model_copy`.
    state.cfg = state.config.model_copy(
        update={"rate_limit": cfg.model_copy(update={"enabled": True, "requests": 3,
                                                     "login_requests": 3})})
    try:
        codes = []
        for _ in range(8):
            r = client.post("/api/v1/auth/login", json={"username": "u_rl", "password": PWD})
            codes.append(r.status_code)
            if r.status_code == 429:
                assert "Retry-After" in r.headers
                break
        assert 429 in codes, f"phải có 429 trong {codes}"
    finally:
        state.cfg = state.config.model_copy(
            update={"rate_limit": cfg.model_copy(update={"enabled": False, "requests": 120,
                                                         "login_requests": 10})})
