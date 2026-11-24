"""Kiểm thử các lỗi bảo mật/đồng thời đã sửa theo review (mục 1–12).

Không cần dịch vụ ngoài: mongomock + fakeredis (xem `conftest.py`).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import redis

from api.auth.password import hash_password
from api.auth.users import PermissionChanged, UserRepo

PWD = "MatKhau-Test-012345"
TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ------------------------------------------------------------------ helpers ----
def _mk_user(db: Any, username: str, role: str = "admin") -> str:
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password(PWD), role=role)
    return username


def _login(client: Any, username: str, password: str = PWD) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client: Any, db: Any, name: str = "u_admin") -> dict:
    _mk_user(db, name)
    return _auth(_login(client, name)["access_token"])


def _seed_tide(db: Any, code: str = "BIEN_HO_1", value: float = 1.85) -> None:
    db["tide_extremes"].insert_one({"meta": {"station": code, "kind": "high"}, "ts": TS, "value": value,
                                    "raw_value": value, "qc": None, "source_file": "2026/01/01/a.pdf"})


def _seed_flag(db: Any, fid: str = "F1", version: int = 1) -> None:
    db["qc_flags"].insert_one({"_id": fid, "collection": "tide_extremes", "station": "BIEN_HO_1",
                               "kind": "high", "ts": TS, "value": 1.85, "origin": "auto", "rules": [],
                               "status": "suspect", "corrected_value": None, "reason": "R1",
                               "version": version, "created_at": TS})


class DeadRedis:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise redis.ConnectionError("Redis chết")
        return fail


# ---------------------------------------------------------- 1. log driver -------
def test_log_debug_khong_in_document_users():
    from api.state import SensitiveDriverLogFilter, configure_logging

    configure_logging("DEBUG", instance="t")
    assert logging.getLogger("pymongo.command").getEffectiveLevel() >= logging.INFO
    assert logging.getLogger("api").getEffectiveLevel() == logging.DEBUG
    f = SensitiveDriverLogFilter()
    rec = logging.LogRecord("pymongo.command", logging.INFO, __file__, 1,
                            '{"commandName": "find", "reply": {"cursor": {"firstBatch": '
                            '[{"username": "a", "password_hash": "$argon2id$..."}]}}, "collection": "users"}',
                            None, None)
    assert f.filter(rec) is False
    ok = logging.LogRecord("pymongo.command", logging.INFO, __file__, 1, '{"commandName": "ping"}', None, None)
    assert f.filter(ok) is True
    assert f.filter(logging.LogRecord("api", logging.INFO, __file__, 1, "users ok", None, None)) is True
    assert any(isinstance(x, SensitiveDriverLogFilter) for h in logging.getLogger().handlers for x in h.filters)
    configure_logging("INFO", instance="t")


# ---------------------------------------------------------- 2. revert ----------
def _decide_via_api(client, headers, fid="F1", status="rejected", version=1):
    r = client.post(f"/api/v1/review/{fid}/approve",
                    json={"status": status, "reason": "PDF in sai", "version": version}, headers=headers)
    assert r.status_code == 200, r.text


def _audit_id(db, **q) -> str:
    return str(db["audit_logs"].find_one(q, sort=[("at", -1)])["_id"])


def test_revert_qc_decision_qua_decide_va_chan_lap(client, fake_db):
    h = _admin(client, fake_db)
    _seed_flag(fake_db)
    _decide_via_api(client, h)
    aid = _audit_id(fake_db, action="qc_decision", doc_id="F1")

    r = client.post(f"/api/v1/audit/{aid}/revert", json={"reason": "duyệt nhầm"}, headers=h)
    assert r.status_code == 200, r.text
    flag = fake_db["qc_flags"].find_one({"_id": "F1"})
    assert (flag["status"], flag["version"]) == ("suspect", 3)
    orig = fake_db["audit_logs"].find_one({"_id": fake_db["audit_logs"].find_one(
        {"action": "qc_decision", "source": "api"})["_id"]})
    assert orig["state"] == "applied" and orig["reverted_at"] is not None and "revert_claim" not in orig
    new = fake_db["audit_logs"].find_one({"source": "api_revert"})
    assert new is not None and new["state"] == "applied" and aid in new["reason"]
    assert r.json()["revert_audit_id"] == str(new["_id"])

    r2 = client.post(f"/api/v1/audit/{aid}/revert", json={"reason": "lần nữa"}, headers=h)
    assert r2.status_code == 409, r2.text
    assert fake_db["qc_flags"].find_one({"_id": "F1"})["version"] == 3


def test_revert_khi_co_thay_doi_moi_hon_bi_409_va_go_danh_dau(client, fake_db):
    h = _admin(client, fake_db)
    _seed_flag(fake_db)
    _decide_via_api(client, h, status="rejected", version=1)
    aid = _audit_id(fake_db, action="qc_decision", doc_id="F1")
    _decide_via_api(client, h, status="confirmed_ok", version=2)
    r = client.post(f"/api/v1/audit/{aid}/revert", json={"reason": "x"}, headers=h)
    assert r.status_code == 409, r.text
    from bson import ObjectId
    doc = fake_db["audit_logs"].find_one({"_id": ObjectId(aid)})
    assert "revert_claim" not in doc and doc.get("reverted_at") is None
    assert fake_db["qc_flags"].find_one({"_id": "F1"})["status"] == "confirmed_ok"


def test_revert_chi_co_view_audit_bi_403(client, fake_db):
    h = _admin(client, fake_db)
    _seed_flag(fake_db)
    _decide_via_api(client, h)
    aid = _audit_id(fake_db, action="qc_decision", doc_id="F1")
    _mk_user(fake_db, "u_hydro", role="hydrologist")         # có VIEW_AUDIT, không có APPROVE
    hh = _auth(_login(client, "u_hydro")["access_token"])
    r = client.post(f"/api/v1/audit/{aid}/revert", json={"reason": "x"}, headers=hh)
    assert r.status_code == 403, r.text
    assert fake_db["qc_flags"].find_one({"_id": "F1"})["status"] == "rejected"


def test_revert_audit_collection_tuy_y_bi_422(client, fake_db):
    """Audit giả mạo trỏ tới `users` (leo thang quyền cũ) → 422, không ghi gì."""
    h = _admin(client, fake_db)
    _mk_user(fake_db, "nan_nhan", role="viewer")
    res = fake_db["audit_logs"].insert_one({"at": TS, "actor": "x", "action": "api_update", "source": "api",
                                           "collection": "users", "doc_id": "nan_nhan",
                                           "before": {"perm": {"*": 4095}}, "after": None, "reason": "",
                                           "state": "applied"})
    r = client.post(f"/api/v1/audit/{res.inserted_id}/revert", json={"reason": "x"}, headers=h)
    assert r.status_code == 422, r.text
    assert fake_db["users"].find_one({"_id": "nan_nhan"})["perm"] == {"*": 1}
    # qc_decision nhưng collection lạ → 422
    res = fake_db["audit_logs"].insert_one({"at": TS, "actor": "x", "action": "qc_decision", "source": "api",
                                           "collection": "users", "doc_id": "nan_nhan",
                                           "before": {"status": "suspect"}, "after": {"version": 2},
                                           "reason": "", "state": "applied"})
    r = client.post(f"/api/v1/audit/{res.inserted_id}/revert", json={"reason": "x"}, headers=h)
    assert r.status_code == 422, r.text


def test_revert_bulletin_metadata_qua_update_bulletin_fields(client, fake_db):
    from vngis import qc

    h = _admin(client, fake_db)
    fake_db["hydromet_bulletins"].insert_one({"source_file": "2026/01/02/a.pdf", "source": {"is_primary": True},
                                              "qc_note": None})
    qc.update_bulletin_fields(fake_db, "2026/01/02/a.pdf", {"source.is_primary": (True, False)},
                              reason="bản trùng", actor="u_admin")
    aid = _audit_id(fake_db, action="bulletin_metadata")
    r = client.post(f"/api/v1/audit/{aid}/revert", json={"reason": "nhầm"}, headers=h)
    assert r.status_code == 200, r.text
    assert fake_db["hydromet_bulletins"].find_one({"source_file": "2026/01/02/a.pdf"})["source"]["is_primary"] is True
    assert fake_db["audit_logs"].count_documents({"action": "bulletin_metadata"}) == 2
    assert client.post(f"/api/v1/audit/{aid}/revert", json={"reason": "x"}, headers=h).status_code == 409


# ---------------------------------------------------------- 3. PATCH tides -----
def test_patch_tide_tao_co_va_quyet_dinh_khong_ghi_tide_extremes(client, fake_db):
    h = _admin(client, fake_db)
    _seed_tide(fake_db)
    before = fake_db["tide_extremes"].find_one({})
    r = client.patch("/api/v1/tides/BIEN_HO_1/2026-01-01T00:00:00Z",
                     json={"status": "corrected", "corrected_value": 1.5, "reason": "PDF in 1.50"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flag_created"] is True and body["status"] == "corrected" and body["version"] == 2
    after = fake_db["tide_extremes"].find_one({})
    assert after == before, "API không được ghi tide_extremes"
    flag = fake_db["qc_flags"].find_one({"_id": body["flag_id"]})
    assert flag["origin"] == "manual" and flag["value"] == 1.85 and flag["corrected_value"] == 1.5
    actions = sorted(a["action"] for a in fake_db["audit_logs"].find({"doc_id": body["flag_id"]}))
    assert actions == ["qc_decision", "qc_flag_create"]

    # cờ đã có: thiếu version → 428; version cũ → 409; đúng → 200
    url = "/api/v1/tides/BIEN_HO_1/2026-01-01T00:00:00Z"
    assert client.patch(url, json={"status": "rejected", "reason": "x"}, headers=h).status_code == 428
    assert client.patch(url, json={"status": "rejected", "reason": "x", "version": 1},
                        headers=h).status_code == 409
    r = client.patch(url, json={"status": "rejected", "reason": "x", "version": 2}, headers=h)
    assert r.status_code == 200, r.text
    assert fake_db["qc_flags"].find_one({"_id": body["flag_id"]})["value"] == 1.85
    assert fake_db["tide_extremes"].find_one({}) == before


def test_patch_tide_quyet_dinh_sai_khong_tao_co(client, fake_db):
    h = _admin(client, fake_db)
    _seed_tide(fake_db)
    r = client.patch("/api/v1/tides/BIEN_HO_1/2026-01-01T00:00:00Z",
                     json={"status": "corrected", "reason": "thiếu giá trị"}, headers=h)
    assert r.status_code == 422, r.text
    assert fake_db["qc_flags"].count_documents({}) == 0


# ---------------------------------------------------------- 4. JWT secret ------
def test_resolve_secret_theo_moi_truong():
    from api.auth.jwt import JwtConfigError, resolve_secret

    with pytest.raises(JwtConfigError, match="AUTH_SECRET"):
        resolve_secret("AUTH_SECRET", "cluster", environ={})
    with pytest.raises(JwtConfigError, match="32"):
        resolve_secret("AUTH_SECRET", "cluster", environ={"AUTH_SECRET": "ngan"})
    good = "x" * 32
    assert resolve_secret("AUTH_SECRET", "cluster", environ={"AUTH_SECRET": good}) == good
    assert resolve_secret("AUTH_SECRET", "dev", environ={})          # khoá tạm, chỉ cảnh báo
    assert resolve_secret("AUTH_SECRET", "test", environ={"AUTH_SECRET": "ngan"}) == "ngan"


def test_khoi_dong_dung_khi_thieu_khoa_o_cluster(fake_db, fake_cache, fake_lock, cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from api.auth.jwt import JwtConfigError
    from api.main import create_app
    from api.state import state

    monkeypatch.delenv("AUTH_SECRET", raising=False)
    state.override(db=fake_db, cache=fake_cache, lock=fake_lock, redis=fake_cache.redis, jwt=None,
                   cfg=cfg.model_copy(update={"env": "cluster"}))
    try:
        with pytest.raises(JwtConfigError, match="AUTH_SECRET"):
            with TestClient(create_app()):
                pass
    finally:
        state.reset()
        state.cfg = None


def test_env_example_dung_ten_bien_cua_code():
    root = Path(__file__).resolve().parents[3]
    text = (root / ".env.example").read_text(encoding="utf-8")
    assert "AUTH_SECRET=" in text and "JWT_SECRET" not in text
    from vngis.config import AuthConfig
    assert AuthConfig().secret_env == "AUTH_SECRET"


# ---------------------------------------------------------- 5. refresh ---------
def test_refresh_dung_lai_thu_hoi_moi_phien(client, fake_db):
    _mk_user(fake_db, "u_rf")
    first = _login(client, "u_rf")
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200, r.text
    second = r.json()
    assert client.get("/api/v1/auth/me", headers=_auth(second["access_token"])).status_code == 200
    ver = UserRepo(fake_db).perm_ver("u_rf")

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 401, r.text
    assert UserRepo(fake_db).perm_ver("u_rf") == ver + 1
    assert client.get("/api/v1/auth/me", headers=_auth(second["access_token"])).status_code == 401
    assert client.post("/api/v1/auth/refresh",
                       json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_consume_refresh_nguyen_tu(fake_redis):
    from api.auth.blacklist import TokenBlacklist

    bl = TokenBlacklist(fake_redis)
    wins: list[bool] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        wins.append(bl.consume_refresh("jti-1", 60))

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins.count(True) == 1 and len(wins) == 8


# ---------------------------------------------------------- 6. rate limit ------
def _request(peer: str, xff: str | None = None):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                    "client": (peer, 1234), "query_string": b""})


def test_client_ip_mac_dinh_khong_tin_xff(cfg):
    from api.deps import client_ip
    from api.state import state

    state.override(cfg=cfg)
    try:
        assert client_ip(_request("203.0.113.7", "1.2.3.4")) == "203.0.113.7"
        state.override(cfg=cfg.model_copy(update={"rate_limit": cfg.rate_limit.model_copy(
            update={"trusted_proxies": ["10.0.0.0/8"]})}))
        # proxy tin cậy: lấy IP không tin cậy ngoài cùng bên phải (client giả mạo phần bên trái)
        assert client_ip(_request("10.0.0.2", "6.6.6.6, 198.51.100.9, 10.0.0.3")) == "198.51.100.9"
        assert client_ip(_request("203.0.113.7", "1.2.3.4")) == "203.0.113.7"   # peer không tin cậy
        assert client_ip(_request("10.0.0.2", "rác")) == "10.0.0.2"
    finally:
        state.cfg = None


def test_rate_limit_chi_expire_khi_incr_tra_1(client, fake_db, monkeypatch):
    from api.state import state

    _mk_user(fake_db, "u_rl2")
    base = state.config
    state.cfg = base.model_copy(update={"rate_limit": base.rate_limit.model_copy(
        update={"enabled": True, "login_requests": 50})})
    calls = []
    real = state.redis.expire
    monkeypatch.setattr(state.redis, "expire", lambda k, t: calls.append(k) or real(k, t))
    try:
        for _ in range(4):
            client.post("/api/v1/auth/login", json={"username": "u_rl2", "password": "sai-mat-khau-0"})
        assert len([k for k in calls if k.startswith("rl:login:")]) == 1
    finally:
        state.cfg = base


def test_rate_limit_login_fail_closed_khi_redis_loi(client, fake_db):
    from api.state import state

    _mk_user(fake_db, "u_rl3")
    base = state.config
    good = state.redis
    state.cfg = base.model_copy(update={"rate_limit": base.rate_limit.model_copy(update={"enabled": True})})
    state.override(redis=DeadRedis())
    try:
        r = client.post("/api/v1/auth/login", json={"username": "u_rl3", "password": PWD})
        assert r.status_code == 503 and "Retry-After" in r.headers, r.text
    finally:
        state.override(redis=good)
        state.cfg = base


# ---------------------------------------------------------- 7. Redis chết ------
def test_blacklist_redis_loi_tra_503_khong_500(client, fake_db):
    from api.auth.blacklist import TokenBlacklist
    from api.state import state

    _mk_user(fake_db, "u_bl")
    tok = _login(client, "u_bl")["access_token"]
    good = state.blacklist
    state.override(blacklist=TokenBlacklist(DeadRedis()))
    try:
        r = client.get("/api/v1/auth/me", headers=_auth(tok))
        assert r.status_code == 503, r.text
    finally:
        state.override(blacklist=good)


def test_endpoint_doc_van_chay_khi_cache_redis_chet(client, fake_db):
    from vngis.cache import Cache

    from api.state import state

    h = _admin(client, fake_db)
    fake_db["stations"].insert_one({"_id": "S1", "code": "S1", "name": "Trạm", "version": 1})
    good = state.cache
    state.override(cache=Cache(DeadRedis()))
    try:
        r = client.get("/api/v1/stations", headers=h)
        assert r.status_code == 200 and r.json()["count"] == 1, r.text
    finally:
        state.override(cache=good)


# ---------------------------------------------------------- 8. perm ------------
def test_perm_ten_collection_sai_bi_422(client, requires_bit):
    db = requires_bit
    h = _admin(client, db)
    _mk_user(db, "u_t", role="viewer")
    for bad in ("a.b", "$set", "khong_co_collection", ""):
        r = client.put("/api/v1/users/u_t/perm", json={"collections": {bad: ["READ"]}, "reason": "x"}, headers=h)
        assert r.status_code == 422, (bad, r.text)
    assert db["users"].find_one({"_id": "u_t"})["perm"] == {"*": 1}


def test_perm_dung_perm_ver_va_ghi_audit(client, requires_bit):
    db = requires_bit
    h = _admin(client, db)
    _mk_user(db, "u_t2", role="viewer")
    r = client.put("/api/v1/users/u_t2/perm",
                   json={"collections": {"tide_extremes": ["READ", "UPDATE"]}, "perm_ver": 7, "reason": "x"},
                   headers=h)
    assert r.status_code == 409, r.text
    r = client.put("/api/v1/users/u_t2/perm",
                   json={"grant": ["EXPORT"], "collections": {"tide_extremes": ["READ", "UPDATE"]},
                         "perm_ver": 1, "reason": "cấp quyền sửa triều"}, headers=h)
    assert r.status_code == 200, r.text
    doc = db["users"].find_one({"_id": "u_t2"})
    assert doc["perm"] == {"*": 17, "tide_extremes": 5} and doc["perm_ver"] == 2
    a = db["audit_logs"].find_one({"action": "user_perm", "doc_id": "u_t2"})
    assert a["state"] == "applied" and a["reason"] == "cấp quyền sửa triều"
    assert a["before"]["perm_ver"] == 1 and a["after"]["perm"]["tide_extremes"] == 5
    r = client.put("/api/v1/users/u_t2/perm", json={"grant": ["EXPORT"]}, headers=h)
    assert r.status_code == 422, "thiếu reason phải bị từ chối"


def test_set_collection_perm_nguyen_tu_co_dieu_kien_perm_ver(fake_db):
    repo = UserRepo(fake_db)
    repo.ensure_roles()
    repo.create_user("u_cas", "h", role="viewer")
    repo.set_collection_perm("u_cas", "stations", 5)
    assert repo.perm_ver("u_cas") == 2
    with pytest.raises(PermissionChanged):
        repo.set_collection_perm("u_cas", "stations", 1, expected_perm_ver=1)
    assert fake_db["users"].find_one({"_id": "u_cas"})["perm"]["stations"] == 5
    with pytest.raises(ValueError):
        repo.set_collection_perm("u_cas", "perm.x", 1)


# ---------------------------------------------------------- 9. PDF -------------
def test_route_pdf_va_files_goi_duoc(client, fake_db):
    h = _admin(client, fake_db)
    sf = "2026/01/02/bản tin số 1.pdf"
    fake_db["hydromet_bulletins"].insert_one({"source_file": sf, "bulletin_type": "daily"})
    fake_db["hydromet_bulletin_files"].insert_one({"source_file": sf, "data": b"%PDF-1.4 test",
                                                   "sha256": "abc123", "size_bytes": 13,
                                                   "content_type": "application/pdf"})
    r = client.get(f"/api/v1/bulletins/{sf}/pdf", headers=h)
    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.4 test"
    assert r.headers["cache-control"].startswith("private")
    assert r.headers["etag"] == '"abc123"' and "filename*=UTF-8''" in r.headers["content-disposition"]
    r = client.get(f"/api/v1/bulletins/{sf}/files", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] == "abc123" and "data" not in r.json()
    r = client.get(f"/api/v1/bulletins/{sf}", headers=h)
    assert r.status_code == 200 and r.json()["bulletin_type"] == "daily", r.text


# ---------------------------------------------------------- 11. guard.check ----
def test_endpoint_mat_khoa_truoc_khi_ghi_tra_503(client, fake_db, monkeypatch):
    from vngis import locks

    h = _admin(client, fake_db)
    fake_db["stations"].insert_one({"_id": "S2", "code": "S2", "name": "A", "version": 1})

    def lost(self):
        raise locks.LockLost("mất khoá (giả lập)")

    monkeypatch.setattr(locks.LockGuard, "check", lost)
    r = client.patch("/api/v1/stations/S2", json={"name": "B", "version": 1}, headers=h)
    assert r.status_code == 503, r.text
    assert fake_db["stations"].find_one({"code": "S2"})["name"] == "A"


# ---------------------------------------------------------- 12. login ----------
def test_dang_nhap_luon_chay_verify_password(client, fake_db, monkeypatch):
    import api.router.auth as auth_router

    calls = []
    real = auth_router.verify_password
    monkeypatch.setattr(auth_router, "verify_password", lambda h, p: calls.append(h) or real(h, p))
    r = client.post("/api/v1/auth/login", json={"username": "khong_ton_tai", "password": PWD})
    assert r.status_code == 401 and len(calls) == 1 and calls[0].startswith("$argon2")


def test_tai_khoan_khoa_hoac_vo_hieu_chi_bao_khi_mat_khau_dung(client, fake_db):
    _mk_user(fake_db, "u_lk")
    fake_db["users"].update_one({"_id": "u_lk"},
                                {"$set": {"locked_until": datetime.now(timezone.utc) + timedelta(minutes=5)}})
    wrong = client.post("/api/v1/auth/login", json={"username": "u_lk", "password": "sai-mat-khau-01"})
    missing = client.post("/api/v1/auth/login", json={"username": "khong_co", "password": "sai-mat-khau-01"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["error"]["detail"] == missing.json()["error"]["detail"]
    assert client.post("/api/v1/auth/login", json={"username": "u_lk", "password": PWD}).status_code == 423

    _mk_user(fake_db, "u_off2")
    fake_db["users"].update_one({"_id": "u_off2"}, {"$set": {"active": False}})
    assert client.post("/api/v1/auth/login",
                       json={"username": "u_off2", "password": "sai-mat-khau-01"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "u_off2", "password": PWD}).status_code == 403
