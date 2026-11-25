"""Chuẩn hoá tọa độ trạm khi đọc/ghi qua API.

MongoDB luu `stations.location` theo GeoJSON Point (`{"type": "Point", "coordinates": [lon, lat]}`
- bat buoc cho index 2dsphere), nhung giao dien web va client khac dung mang `[lon, lat]`.

Cac test o day bao dam API luon tra `location` dang mang `[lon, lat]` va ghi lai dung
GeoJSON Point (khong lam hong index 2dsphere).
"""
from __future__ import annotations

from typing import Any

from api.auth.password import hash_password
from api.auth.users import UserRepo

PWD = "MatKhau-Test-012345"


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


# ------------------------------------------------------------------ helpers ----
def test_to_lonlat_chuan_hoa_moi_dang():
    """`to_lonlat` nhận GeoJSON Point, mảng, tuple; trả về `[lon, lat]` hoặc None."""
    from api.router.common import to_lonlat

    assert to_lonlat({"type": "Point", "coordinates": [106.9, 10.95]}) == [106.9, 10.95]
    assert to_lonlat([106.9, 10.95]) == [106.9, 10.95]
    assert to_lonlat((106.9, 10.95)) == [106.9, 10.95]
    # Toa do khong hop le -> None (client bo qua tram do), khong nem loi.
    assert to_lonlat(None) is None
    assert to_lonlat([]) is None
    assert to_lonlat([1.0]) is None
    assert to_lonlat("abc") is None
    assert to_lonlat({"type": "Polygon", "coordinates": []}) is None
    assert to_lonlat({"type": "Point", "coordinates": ["x", "y"]}) is None


def test_to_point_tao_geojson_point():
    """`to_point` đưa mảng `[lon, lat]` về GeoJSON Point; giữ nguyên Point sẵn có."""
    from api.router.common import to_point

    assert to_point([106.9, 10.95]) == {"type": "Point", "coordinates": [106.9, 10.95]}
    same = {"type": "Point", "coordinates": [106.9, 10.95]}
    assert to_point(same) is same
    assert to_point(None) is None
    assert to_point("abc") is None


# ------------------------------------------------------------------ stations ---
def test_danh_sach_tram_tra_location_dang_mang(client, fake_db):
    """GET /stations trả `location` là mảng `[lon, lat]` dù DB lưu GeoJSON Point."""
    _mk_user(fake_db, "u_loc1")
    headers = _auth(_login(client, "u_loc1")["access_token"])
    fake_db["stations"].insert_one({
        "_id": "BIEN_HO_1", "code": "BIEN_HO_1", "name": "Bien Hoa", "kind": "water_level",
        "location": {"type": "Point", "coordinates": [106.9, 10.95]}, "version": 1, "active": True,
    })

    r = client.get("/api/v1/stations", headers=headers)
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["location"] == [106.9, 10.95], f"location phai la mang [lon, lat], thuc te {item['location']!r}"


def test_chi_tiet_tram_tra_location_dang_mang(client, fake_db):
    """GET /stations/{code} cũng trả `location` dạng mảng."""
    _mk_user(fake_db, "u_loc2")
    headers = _auth(_login(client, "u_loc2")["access_token"])
    fake_db["stations"].insert_one({
        "_id": "BIEN_HO_1", "code": "BIEN_HO_1", "name": "Bien Hoa", "kind": "water_level",
        "location": {"type": "Point", "coordinates": [106.9, 10.95]}, "version": 1, "active": True,
    })

    r = client.get("/api/v1/stations/BIEN_HO_1", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["location"] == [106.9, 10.95]


def test_chi_tiet_tram_khong_co_location(client, fake_db):
    """Trạm chưa có tọa độ → `location` là None (không phải object rỗng)."""
    _mk_user(fake_db, "u_loc3")
    headers = _auth(_login(client, "u_loc3")["access_token"])
    fake_db["stations"].insert_one({
        "_id": "CHUA_CO", "code": "CHUA_CO", "name": "Chua co toa do", "kind": "water_level",
        "location": None, "version": 1, "active": True,
    })

    r = client.get("/api/v1/stations/CHUA_CO", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["location"] is None


def test_sua_tram_ghi_location_thanh_geojson_point(client, fake_db):
    """PATCH /stations gửi mảng `[lon, lat]` → DB lưu GeoJSON Point, API trả mảng."""
    _mk_user(fake_db, "u_loc4")
    headers = _auth(_login(client, "u_loc4")["access_token"])
    fake_db["stations"].insert_one({
        "_id": "BIEN_HO_1", "code": "BIEN_HO_1", "name": "Bien Hoa", "kind": "water_level",
        "location": {"type": "Point", "coordinates": [106.9, 10.95]}, "version": 1, "active": True,
    })

    r = client.patch("/api/v1/stations/BIEN_HO_1", json={"location": [107.1, 11.2], "version": 1},
                     headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["location"] == [107.1, 11.2]

    stored = fake_db["stations"].find_one({"code": "BIEN_HO_1"})["location"]
    assert stored == {"type": "Point", "coordinates": [107.1, 11.2]}, \
        f"DB phai luu GeoJSON Point de khong hong index 2dsphere, thuc te {stored!r}"


def test_sua_tram_location_sai_dung_bi_422(client, fake_db):
    """PATCH `location` sai định dạng → 422, không ghi."""
    _mk_user(fake_db, "u_loc5")
    headers = _auth(_login(client, "u_loc5")["access_token"])
    fake_db["stations"].insert_one({
        "_id": "BIEN_HO_1", "code": "BIEN_HO_1", "name": "Bien Hoa", "kind": "water_level",
        "location": {"type": "Point", "coordinates": [106.9, 10.95]}, "version": 1, "active": True,
    })

    r = client.patch("/api/v1/stations/BIEN_HO_1", json={"location": [1.0], "version": 1},
                     headers=headers)
    assert r.status_code == 422, r.text
    stored = fake_db["stations"].find_one({"code": "BIEN_HO_1"})["location"]
    assert stored == {"type": "Point", "coordinates": [106.9, 10.95]}


# ----------------------------------------------------------------- forecasts ---
def test_ban_do_du_bao_dung_toa_do_dang_mang(client, fake_db, monkeypatch):
    """GET /forecasts/map dựng geometry từ `location` dạng GeoJSON Point trong DB."""
    from datetime import datetime, timezone

    import api.router.forecasts as fc

    _mk_user(fake_db, "u_loc6")
    headers = _auth(_login(client, "u_loc6")["access_token"])
    fake_db["stations"].insert_one({
        "_id": "PHU_AN", "code": "PHU_AN", "name": "Phu An", "kind": "water_level",
        "location": {"type": "Point", "coordinates": [106.722, 10.775]}, "version": 1,
    })
    fake_db["forecasts"].insert_one({
        "_id": "F1", "station": "PHU_AN", "basin": "Dong Nai",
        "issued_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "peak_ts": datetime(2026, 1, 2, tzinfo=timezone.utc), "peak_value": 1.8,
        "model_version": 1, "source": "model",
    })
    monkeypatch.setattr(fc, "_sample_payload", lambda station, n: None)

    r = client.get("/api/v1/forecasts/map", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "model" and body["count"] == 1, r.text
    geom = body["features"][0]["geometry"]
    assert geom["type"] == "Point"
    assert geom["coordinates"] == [106.722, 10.775], \
        f"toa do phai la mang [lon, lat], thuc te {geom['coordinates']!r}"
