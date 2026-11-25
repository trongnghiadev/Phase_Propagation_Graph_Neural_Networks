"""Refresh token trong cookie httpOnly + chống CSRF + CORS; `actor` ổn định; `flag_version` trong chuỗi triều.

- Trình duyệt (header `X-Requested-With`): login/refresh đặt cookie `HttpOnly; SameSite=Strict; Path=`
  endpoint refresh/logout, không trả `refresh_token` trong JSON; refresh/logout bằng cookie phải qua CSRF.
- Client khác: vẫn nhận/gửi `refresh_token` trong body (tương thích).
- `audit_logs.actor`, `jobs.requested_by`: ``user:<account_code>:<role>``, không chứa tên đăng nhập;
  bản ghi cũ (tên) vẫn đọc/lọc được.
- `GET /stations/{code}/tides`: mỗi điểm có cờ QC kèm `flag_id`, `flag_version`, `flag_status`.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest

from api.auth.password import hash_password
from api.auth.users import UserRepo, parse_actor

PWD = "MatKhau-Test-012345"
TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
XRW = {"X-Requested-With": "XMLHttpRequest"}
ALLOWED = "http://web.test:3000"
COOKIE = "pgnn_refresh"
ACTOR_RE = re.compile(r"^user:acc_[0-9a-f]{12}:admin$")


def _mk_user(db: Any, username: str, role: str = "admin") -> str:
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password(PWD), role=role)
    return username


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _set_auth(**kw: Any) -> None:
    """Ghi đè `auth.*` của cấu hình đang dùng (model đóng băng → `model_copy`)."""
    from api.state import state
    base = state.config
    state.override(cfg=base.model_copy(update={"auth": base.auth.model_copy(update=kw)}))


@pytest.fixture()
def web(client):
    """Client đóng vai trình duyệt: cookie không Secure (http://testserver), có origin web được phép."""
    _set_auth(cookie_secure=False, cors_origins=[ALLOWED])
    return client


def _set_cookies(resp) -> list[str]:
    return [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]


def _browser_login(client, username: str):
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD}, headers=XRW)
    assert r.status_code == 200, r.text
    return r


# ------------------------------------------------------------------ cookie --------
def test_login_trinh_duyet_dat_cookie_httponly_khong_tra_refresh(web, fake_db):
    _mk_user(fake_db, "u_ck1")
    r = _browser_login(web, "u_ck1")
    body = r.json()
    assert body["access_token"] and body["refresh_token"] is None
    cookies = _set_cookies(r)
    paths = sorted(re.search(r"Path=([^;]+)", c).group(1) for c in cookies)
    assert paths == ["/api/v1/auth/logout", "/api/v1/auth/refresh"]
    for c in cookies:
        low = c.lower()
        assert c.startswith(f"{COOKIE}=") and "httponly" in low and "samesite=strict" in low
        assert "secure" not in low.replace("samesite", "")      # dev http://localhost: tắt Secure


def test_cookie_co_secure_khi_bat(client, fake_db):
    _set_auth(cookie_secure=True)
    _mk_user(fake_db, "u_ck2")
    r = client.post("/api/v1/auth/login", json={"username": "u_ck2", "password": PWD}, headers=XRW)
    assert r.status_code == 200
    assert all("; secure" in c.lower() for c in _set_cookies(r))


def test_client_khong_phai_trinh_duyet_van_dung_body(web, fake_db):
    _mk_user(fake_db, "u_ck3")
    r = web.post("/api/v1/auth/login", json={"username": "u_ck3", "password": PWD})
    assert r.status_code == 200 and r.json()["refresh_token"]
    assert _set_cookies(r) == []


def test_refresh_bang_cookie_xoay_vong_va_dung_lai_bi_chan(web, fake_db):
    _mk_user(fake_db, "u_ck4")
    _browser_login(web, "u_ck4")
    old = web.cookies.get(COOKIE, path="/api/v1/auth/refresh")
    assert old
    r = web.post("/api/v1/auth/refresh", headers={**XRW, "Origin": ALLOWED})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] and r.json()["refresh_token"] is None
    new = re.search(rf"{COOKIE}=([^;]+)", _set_cookies(r)[0]).group(1)
    assert new and new != old
    assert web.get("/api/v1/auth/me", headers=_auth(r.json()["access_token"])).status_code == 200
    # Dùng lại cookie cũ → 401 (và thu hồi mọi phiên).
    web.cookies.clear()
    web.cookies.set(COOKIE, old, path="/api/v1/auth/refresh")
    assert web.post("/api/v1/auth/refresh", headers=XRW).status_code == 401


@pytest.mark.parametrize("headers", [
    {},                                                           # thiếu header tuỳ biến
    {**XRW, "Origin": "http://evil.example"},                     # origin lạ
    {**XRW, "Sec-Fetch-Site": "cross-site"},                      # chéo site
])
def test_refresh_bang_cookie_chong_csrf(web, fake_db, headers):
    _mk_user(fake_db, "u_ck5")
    _browser_login(web, "u_ck5")
    r = web.post("/api/v1/auth/refresh", headers=headers)
    assert r.status_code == 403, r.text


def test_refresh_cung_origin_qua_proxy(web, fake_db, monkeypatch):
    """Origin trùng X-Forwarded-Host → hợp lệ **chỉ khi** request đến từ proxy tin cậy (`trusted_proxies`)."""
    from api.auth import cookies

    _mk_user(fake_db, "u_ck6")
    _browser_login(web, "u_ck6")
    hdr = {**XRW, "Origin": "http://gis.local:8088", "X-Forwarded-Host": "gis.local:8088"}
    # client trực tiếp (không phải proxy tin cậy) tự đặt X-Forwarded-Host → bỏ qua → 403
    assert web.post("/api/v1/auth/refresh", headers=hdr).status_code == 403
    monkeypatch.setattr(cookies, "_from_trusted_proxy", lambda request: True)
    r = web.post("/api/v1/auth/refresh", headers=hdr)
    assert r.status_code == 200, r.text


def test_refresh_khong_co_token_401(web, fake_db):
    web.cookies.clear()
    assert web.post("/api/v1/auth/refresh", headers=XRW).status_code == 401


def test_logout_bang_cookie_xoa_cookie_va_blacklist(web, fake_db):
    _mk_user(fake_db, "u_ck7")
    login = _browser_login(web, "u_ck7").json()
    old = web.cookies.get(COOKIE, path="/api/v1/auth/refresh")
    # Thiếu header tuỳ biến khi dùng cookie → 403, không đăng xuất.
    assert web.post("/api/v1/auth/logout", headers=_auth(login["access_token"])).status_code == 403
    r = web.post("/api/v1/auth/logout", headers={**_auth(login["access_token"]), **XRW, "Origin": ALLOWED})
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == 2
    cleared = _set_cookies(r)
    assert len(cleared) == 2 and all("max-age=0" in c.lower() for c in cleared)
    assert web.get("/api/v1/auth/me", headers=_auth(login["access_token"])).status_code == 401
    web.cookies.clear()
    web.cookies.set(COOKIE, old, path="/api/v1/auth/refresh")
    assert web.post("/api/v1/auth/refresh", headers=XRW).status_code == 401


def test_logout_chi_con_cookie(web, fake_db):
    """Access token đã mất (tải lại trang) vẫn đăng xuất được bằng cookie refresh."""
    _mk_user(fake_db, "u_ck8")
    _browser_login(web, "u_ck8")
    old = web.cookies.get(COOKIE, path="/api/v1/auth/refresh")
    r = web.post("/api/v1/auth/logout", headers=XRW)
    assert r.status_code == 200 and r.json()["revoked"] == 1
    web.cookies.clear()
    web.cookies.set(COOKIE, old, path="/api/v1/auth/refresh")
    assert web.post("/api/v1/auth/refresh", headers=XRW).status_code == 401


def test_logout_khong_token_401(web, fake_db):
    web.cookies.clear()
    assert web.post("/api/v1/auth/logout").status_code == 401


def test_cors_chi_origin_cau_hinh_kem_credentials(fake_db, fake_cache, fake_lock, cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.state import state

    state.override(db=fake_db, cache=fake_cache, lock=fake_lock, redis=fake_cache.redis,
                   cfg=cfg.model_copy(update={"auth": cfg.auth.model_copy(update={"cors_origins": [ALLOWED]}),
                                              "rate_limit": cfg.rate_limit.model_copy(update={"enabled": False})}))
    try:
        with TestClient(create_app()) as c:
            pre = {"Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "x-requested-with,content-type"}
            r = c.options("/api/v1/auth/refresh", headers={"Origin": ALLOWED, **pre})
            assert r.status_code == 200
            assert r.headers["access-control-allow-origin"] == ALLOWED
            assert r.headers["access-control-allow-credentials"] == "true"
            r = c.options("/api/v1/auth/refresh", headers={"Origin": "http://evil.example", **pre})
            assert "access-control-allow-origin" not in r.headers
    finally:
        state.reset()


def test_cau_hinh_cors_tu_choi_wildcard():
    from vngis.config import AuthConfig
    with pytest.raises(Exception):
        AuthConfig(cors_origins=["*"])
    assert AuthConfig(cors_origins="http://a.test:3000, https://b.test").cors_origins == \
        ["http://a.test:3000", "https://b.test"]


# ------------------------------------------------------------------ actor ---------
def _seed_station(db: Any, code: str = "BIEN_HO_1") -> None:
    db["stations"].insert_one({"_id": code, "code": code, "name": "Biên Hòa", "kind": "tide_gauge",
                               "location": [106.9, 10.95], "version": 1, "active": True})


def test_audit_actor_on_dinh_khong_chua_ten(client, fake_db):
    _mk_user(fake_db, "u_actor")
    tok = client.post("/api/v1/auth/login", json={"username": "u_actor", "password": PWD}).json()["access_token"]
    _seed_station(fake_db)
    r = client.patch("/api/v1/stations/BIEN_HO_1", json={"note": "x", "reason": "thử"}, headers=_auth(tok))
    assert r.status_code == 200, r.text
    doc = fake_db["audit_logs"].find_one({"action": "api_update"})
    assert ACTOR_RE.match(doc["actor"]), doc["actor"]
    assert "u_actor" not in doc["actor"]
    code = fake_db["users"].find_one({"_id": "u_actor"})["account_code"]
    assert parse_actor(doc["actor"]) == (code, "admin")
    # Bản ghi cũ ghi tên đăng nhập vẫn đọc/lọc được cùng bản ghi mới.
    fake_db["audit_logs"].insert_one({"at": TS, "actor": "u_actor", "action": "api_update", "source": "api",
                                      "collection": "stations", "doc_id": "OLD", "before": None, "after": {},
                                      "reason": "cũ", "state": "applied"})
    fake_db["audit_logs"].insert_one({"at": TS, "actor": "cli", "action": "api_update", "source": "cli",
                                      "collection": "stations", "doc_id": "CLI", "before": None, "after": {},
                                      "reason": "khác", "state": "applied"})
    for q in ("u_actor", f"user:{code}", doc["actor"]):
        items = client.get("/api/v1/audit", params={"actor": q}, headers=_auth(tok)).json()["items"]
        ids = {i["doc_id"] for i in items}
        assert "BIEN_HO_1" in ids and "CLI" not in ids, (q, ids)
        if q == "u_actor":
            assert "OLD" in ids
    items = client.get("/api/v1/audit", headers=_auth(tok)).json()["items"]
    by_doc = {i["doc_id"]: i for i in items}
    assert by_doc["BIEN_HO_1"]["actor_label"]["username"] == "u_actor"
    assert by_doc["BIEN_HO_1"]["actor_label"]["role"] == "admin"
    assert by_doc["OLD"]["actor_label"]["legacy"] is True
    assert by_doc["CLI"]["actor_label"] is None


def test_tai_khoan_cu_duoc_cap_ma(client, fake_db):
    """Tài khoản tạo trước khi có `account_code` được cấp mã ở request đầu, mã giữ nguyên về sau."""
    _mk_user(fake_db, "u_old")
    fake_db["users"].update_one({"_id": "u_old"}, {"$unset": {"account_code": ""}})
    tok = client.post("/api/v1/auth/login", json={"username": "u_old", "password": PWD}).json()["access_token"]
    me = client.get("/api/v1/auth/me", headers=_auth(tok)).json()
    code = fake_db["users"].find_one({"_id": "u_old"})["account_code"]
    assert re.fullmatch(r"acc_[0-9a-f]{12}", code)
    assert me["account_code"] == code
    client.get("/api/v1/auth/me", headers=_auth(tok))
    assert fake_db["users"].find_one({"_id": "u_old"})["account_code"] == code


# ------------------------------------------------------------------ flag_version --
def test_tides_kem_flag_version_va_patch_dung_version(client, fake_db):
    from vngis import qc_rules

    _mk_user(fake_db, "u_flag")
    tok = client.post("/api/v1/auth/login", json={"username": "u_flag", "password": PWD}).json()["access_token"]
    for ts, kind in ((TS, "high"), (datetime(2026, 1, 1, 6, tzinfo=timezone.utc), "low")):
        fake_db["tide_extremes"].insert_one({"meta": {"station": "BIEN_HO_1", "kind": kind}, "ts": ts,
                                             "value": 1.85, "raw_value": 1.85, "qc": None,
                                             "source_file": "2026/01/01/a.pdf"})
    fid = qc_rules.flag_id("BIEN_HO_1", "high", TS)
    fake_db["qc_flags"].insert_one({"_id": fid, "collection": "tide_extremes", "station": "BIEN_HO_1",
                                    "kind": "high", "ts": TS, "value": 1.85, "origin": "auto", "rules": [],
                                    "status": "suspect", "corrected_value": None, "reason": "R1",
                                    "version": 3, "created_at": TS})
    r = client.get("/api/v1/stations/BIEN_HO_1/tides", params={"from": "2025-12-31", "to": "2026-01-02"},
                   headers=_auth(tok))
    assert r.status_code == 200, r.text
    items = {i["kind"]: i for i in r.json()["items"]}
    assert (items["high"]["flag_id"], items["high"]["flag_version"], items["high"]["flag_status"]) == \
        (fid, 3, "suspect")
    assert items["high"]["flag_retired"] is False
    assert items["low"]["flag_id"] is None and items["low"]["flag_version"] is None
    # Web gửi đúng version lấy từ chuỗi triều → không cần đi qua 428.
    r = client.patch("/api/v1/tides/BIEN_HO_1/2026-01-01T00:00:00Z",
                     json={"status": "confirmed_ok", "kind": "high", "reason": "đã kiểm tra",
                           "version": items["high"]["flag_version"]}, headers=_auth(tok))
    assert r.status_code == 200, r.text
    flag = fake_db["qc_flags"].find_one({"_id": fid})
    assert flag["version"] == 4 and flag["status"] == "confirmed_ok"
    assert ACTOR_RE.match(flag["decided_by"])
