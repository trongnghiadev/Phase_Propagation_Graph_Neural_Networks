"""Ma trận phân quyền: 8 vai trò × các endpoint (GĐ7.3).

Mỗi endpoint được kiểm tra bằng **hai** tài khoản thuộc vai trò đó:
- tài khoản có quyền đủ → kỳ vọng **200** (hoặc 404/422/503 vì dữ liệu, KHÔNG phải 401/403);
- tài khoản bị **thu hồi** đúng quyền cần cho endpoint đó → kỳ vọng **403**.

Nếu endpoint không yêu cầu token (công khai) thì bỏ qua — hiện tại mọi endpoint nghiệp vụ
đều yêu cầu token.

Cũng kiểm tra: token cũ mất hiệu lực sau khi đổi quyền (`perm_ver` tăng).
"""
from __future__ import annotations

import itertools
from typing import Any

import pytest

from api.auth.password import hash_password
from api.auth.users import UserRepo
from api.permissions import P, ROLE_MASKS, ROLES, flag_names

#: (endpoint, method, collection, flag) — danh sách endpoint nghiệp vụ cần kiểm tra.
#: `collection` + `flag` dùng để *tước quyền* rồi kỳ vọng 403.
ENDPOINTS: list[tuple[str, str, str, int]] = [
    ("/api/v1/stations", "GET", "stations", P.READ),
    ("/api/v1/stations/BIEN_HO_1", "GET", "stations", P.READ),
    ("/api/v1/stations/BIEN_HO_1/tides", "GET", "tide_extremes", P.READ),
    ("/api/v1/admin-units/provinces", "GET", "admin_units", P.READ),
    ("/api/v1/admin-units/provinces/before_2025_07/80", "GET", "province_timeline", P.READ),
    ("/api/v1/admin-units/wards/before_2025_07/80/mapping", "GET", "admin_unit_mappings", P.READ),
    ("/api/v1/reservoirs", "GET", "reservoir_daily", P.READ),
    ("/api/v1/stats/tide", "GET", "tide_extremes", P.READ),
    ("/api/v1/forecasts/latest", "GET", "forecasts", P.READ),
    ("/api/v1/forecasts/compare", "GET", "forecasts", P.READ),
    ("/api/v1/forecasts/map", "GET", "forecasts", P.READ),
    ("/api/v1/forecasts/BIEN_HO_1/propagation", "GET", "forecasts", P.READ),
    ("/api/v1/alerts", "GET", "alerts", P.READ),
    ("/api/v1/features/rivers", "GET", "admin_units", P.READ),
    ("/api/v1/trace/12345", "GET", "rivers", P.READ),
    ("/api/v1/review", "GET", "qc_flags", P.READ),
    ("/api/v1/jobs", "GET", "jobs", P.READ),
    ("/api/v1/audit", "GET", "audit_logs", P.VIEW_AUDIT),
    ("/api/v1/users", "GET", "users", P.MANAGE_USERS),
    ("/api/v1/admin/cache", "GET", "cache", P.MANAGE_CACHE),
    ("/api/v1/admin/locks", "GET", "locks", P.MANAGE_CACHE),
]


def _make_user(db: Any, username: str, role: str) -> str:
    """Tạo tài khoản với quyền theo vai trò; trả user_id."""
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password("MatKhau-Test-012345"),
                     role=role, display_name=f"test {role}")
    return username


def _login(client: Any, username: str) -> str:
    """Đăng nhập và trả access token."""
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "MatKhau-Test-012345"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _revoke(db: Any, username: str, collection: str, flag: int) -> None:
    """Thu hồi đúng quyền `flag` trên `collection` (nguyên tử bằng `$bit`)."""
    repo = UserRepo(db)
    repo.revoke(username, collection, int(flag))
    # Vô hiệu cache quyền để lần đọc kế tiếp thấy quyền mới.
    repo.cache = None

def _seed_station(db: Any) -> None:
    """Tạo một trạm tối thiểu để endpoint không rơi vào 404 do thiếu dữ liệu."""
    db["stations"].insert_one({"_id": "BIEN_HO_1", "code": "BIEN_HO_1", "name": "Biên Hòa",
                               "location": [106.9, 10.95], "version": 1})


@pytest.mark.parametrize("role", sorted(ROLES))
def test_vai_tro_duoc_phep_doc(client, fake_db, role):
    """Vai trò có quyền đọc → không bị 401/403 (200 hoặc lỗi nghiệp vụ khác)."""
    _seed_station(fake_db)
    _make_user(fake_db, f"u_{role}", role)
    token = _login(client, f"u_{role}")
    headers = {"Authorization": f"Bearer {token}"}
    for path, method, _coll, flag in ENDPOINTS:
        if not (ROLE_MASKS[role] & int(flag)):
            continue
        r = client.request(method, path, headers=headers)
        assert r.status_code not in (401, 403), (
            f"{role} (mask={ROLE_MASKS[role]}) bị chặn ở {path}: {r.status_code} {r.text}")


@pytest.mark.parametrize("role", sorted(ROLES))
def test_thieu_quyen_bi_403(client, requires_bit, role):
    """Thu hồi quyền của endpoint → 403 (không phải 401).

    Lưu ý: thu hồi quyền làm `perm_ver` tăng → token cũ **không còn hiệu lực** (401).
    Vì vậy phải đăng nhập lại sau khi thu hồi, rồi mới kiểm tra 403.

    Cách thu hồi: đặt override riêng cho collection (`perm.<coll>`) chỉ chứa các bit còn
    lại — đây là cơ chế `set_collection_perm` của API, và nó **không** ảnh hưởng tới
    quyền của các collection khác (giữ cho các lượt kiểm tra độc lập).
    """
    fake_db = requires_bit
    _seed_station(fake_db)
    username = _make_user(fake_db, f"v_{role}", role)
    repo = UserRepo(fake_db)
    for path, method, coll, flag in ENDPOINTS:
        if not (ROLE_MASKS[role] & int(flag)):
            continue
        # Ghi đè quyền của collection: bỏ đúng bit cần cho endpoint này.
        current = repo.mask_for(username, coll)
        repo.set_collection_perm(username, coll, current & ~int(flag) & 4095)
        # Đăng nhập lại để token mang `perm_ver` mới.
        token = _login(client, username)
        headers = {"Authorization": f"Bearer {token}"}
        r = client.request(method, path, headers=headers)
        assert r.status_code == 403, (
            f"{role} thiếu quyền {flag_names(flag)} trên {coll} nhưng {path} trả {r.status_code}")
        # Khôi phục lại quyền gốc để lượt kiểm tra kế tiếp độc lập.
        repo.set_collection_perm(username, coll, current)


def test_token_cu_bi_401_khi_doi_quyen(client, requires_bit):
    """Thu hồi quyền làm `perm_ver` tăng → token cũ trả 401 (không phải 403)."""
    fake_db = requires_bit
    username = _make_user(fake_db, "u_token_cu", "admin")
    token = _login(client, username)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/stations", headers=headers).status_code == 200
    repo = UserRepo(fake_db)
    repo.revoke(username, "stations", int(P.READ))
    r = client.get("/api/v1/stations", headers=headers)
    assert r.status_code == 401, f"token cũ phải bị từ chối (401), thực tế {r.status_code}"


def test_vai_tro_khong_quyen_bi_403(client, fake_db):
    """Vai trò không có quyền (viewer chỉ READ) → 403 ở endpoint cần quyền ghi."""
    _seed_station(fake_db)
    username = _make_user(fake_db, "u_viewer", "viewer")
    token = _login(client, username)
    headers = {"Authorization": f"Bearer {token}"}
    for path, method in [("/api/v1/users", "GET"), ("/api/v1/admin/cache", "GET"),
                         ("/api/v1/admin/locks", "GET"), ("/api/v1/audit", "GET")]:
        r = client.request(method, path, headers=headers)
        assert r.status_code == 403, f"{path} phải là 403 với viewer, thực tế {r.status_code}"


def test_khong_token_bi_401(client):
    """Không có token → 401."""
    r = client.get("/api/v1/stations")
    assert r.status_code == 401, r.text


def test_token_sai_bi_401(client):
    """Token không hợp lệ → 401."""
    r = client.get("/api/v1/stations", headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 401, r.text


def test_doi_quyen_phan_voi_token_cu(client, requires_bit):
    """Sau khi đổi quyền, `perm_ver` tăng → token cũ không dùng được nữa (401)."""
    fake_db = requires_bit
    _seed_station(fake_db)
    username = _make_user(fake_db, "u_perm", "viewer")
    token = _login(client, username)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/stations", headers=headers).status_code == 200

    repo = UserRepo(fake_db)
    before = repo.perm_ver(username)
    repo.grant(username, "stations", int(P.UPDATE))
    after = repo.perm_ver(username)
    assert after > before, "perm_ver phải tăng khi đổi quyền"

    # Token cũ mang perm_ver cũ → 401.
    r = client.get("/api/v1/stations", headers=headers)
    assert r.status_code == 401, r.text


def test_bang_vai_tro_dung_gia_tri():
    """Bảng vai trò phải đúng giá trị bitmask theo đề cương."""
    expected = {
        "viewer": 1, "analyst": 17, "editor": 23, "reviewer": 183,
        "data_engineer": 255, "hydrologist": 1169, "forecast_officer": 3249, "admin": 4095,
    }
    assert ROLE_MASKS == expected, ROLE_MASKS
    assert len(ROLES) == 8


def test_bitmask_12_bit():
    """Bitmask đúng 12 bit (tối đa 4095)."""
    assert int(P.MANAGE_MODEL) == 2048
    assert max(ROLE_MASKS.values()) == 4095
    assert max(ROLE_MASKS.values()) < 4096
