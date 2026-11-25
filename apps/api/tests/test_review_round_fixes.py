"""Test các sửa theo vòng review 25/11/2026.

- `users.account_code`: index unique partial (khởi động API + migration 0008), cấp mã cho cả ``null``;
- nhật ký `user_perm` ghi `account_code` của người bị tác động; actor không bao giờ dự phòng bằng tên (503);
- nhãn actor (`*_label`) cho `alerts.ack_by`, `qc_flags.decided_by`, `promoted_by`;
- promote theo registry (`status: production`, `version` int);
- dự báo mẫu chỉ lấy tệp dự báo đúng cột (không lấy bảng chỉ số `results/E5`);
- `X-Forwarded-Host` chỉ tin từ proxy trong `rate_limit.trusted_proxies`.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from api.auth.password import hash_password
from api.auth.users import MISSING_ACCOUNT_CODE, USERS_INDEXES, UserRepo

PWD = "MatKhau-Test-012345"
TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[3]


def _mk_user(db: Any, username: str, role: str = "admin") -> str:
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password(PWD), role=role,
                     display_name=f"test {username}")
    return username


def _h(client: Any, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _code(db: Any, uid: str) -> str:
    return db["users"].find_one({"_id": uid})["account_code"]


# ------------------------------------------------------------ account_code / index --
def test_index_account_code_idempotent_va_unique(fake_db):
    from pymongo.errors import DuplicateKeyError

    repo = UserRepo(fake_db)
    repo.ensure_indexes()
    repo.ensure_indexes()     # lần 2: no-op
    info = fake_db["users"].index_information()
    assert info["account_code_unique"]["unique"] is True
    # tài khoản cũ chưa có mã (thiếu trường / null) không va nhau
    fake_db["users"].insert_many([{"_id": "a", "username": "a"},
                                  {"_id": "b", "username": "b", "account_code": None},
                                  {"_id": "c", "username": "c", "account_code": None}])
    fake_db["users"].insert_one({"_id": "d", "username": "d", "account_code": "acc_000000000001"})
    with pytest.raises(DuplicateKeyError):
        fake_db["users"].insert_one({"_id": "e", "username": "e", "account_code": "acc_000000000001"})


def test_index_khop_migration_0008():
    path = REPO / "migrations" / "mongo" / "0008_users_account_code.py"
    spec = importlib.util.spec_from_file_location("m0008_users_account_code", path)
    sys.path.insert(0, str(path.parent))
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        sys.path.pop(0)
    assert mod.INDEXES == USERS_INDEXES


@pytest.mark.parametrize("bad", [None, "", 123])
def test_cap_ma_cho_ca_null_rong_khong_phai_chuoi(fake_db, bad):
    repo = UserRepo(fake_db)
    fake_db["users"].insert_one({"_id": "u_null", "username": "u_null", "account_code": bad})
    code = repo.ensure_account_code("u_null")
    assert isinstance(code, str) and code.startswith("acc_")
    assert _code(fake_db, "u_null") == code
    assert repo.ensure_account_code("u_null") == code
    # bộ lọc "chưa có mã" không còn khớp tài khoản đã có mã
    assert fake_db["users"].count_documents({"_id": "u_null", **MISSING_ACCOUNT_CODE}) == 0


def test_audit_user_perm_ghi_account_code_khong_ghi_ten(client, requires_bit):
    db = requires_bit
    _mk_user(db, "adm_p")
    h = _h(client, "adm_p")
    _mk_user(db, "nguoi_bi_doi", role="viewer")
    db["users"].update_one({"_id": "nguoi_bi_doi"}, {"$set": {"account_code": None}})   # tài khoản cũ
    r = client.put("/api/v1/users/nguoi_bi_doi/perm", json={"grant": ["EXPORT"], "reason": "thử"}, headers=h)
    assert r.status_code == 200, r.text
    code = _code(db, "nguoi_bi_doi")
    assert code and code.startswith("acc_")
    a = db["audit_logs"].find_one({"action": "user_perm"})
    assert a["doc_id"] == code and a["target"] == {"account_code": code}
    assert "nguoi_bi_doi" not in str(a) and "adm_p" not in str(a)


def test_actor_khong_du_phong_bang_ten_503(client, fake_db, monkeypatch):
    from api.state import state

    _mk_user(fake_db, "u_noacc")
    h = _h(client, "u_noacc")
    fake_db["users"].update_one({"_id": "u_noacc"}, {"$unset": {"account_code": ""}})

    def _fail(*a, **k):
        raise RuntimeError("không ghi được")
    monkeypatch.setattr(state.repo, "ensure_account_code", _fail)
    fake_db["stations"].insert_one({"_id": "BIEN_HO_1", "code": "BIEN_HO_1", "name": "Biên Hòa",
                                    "kind": "tide_gauge", "location": [106.9, 10.95], "version": 1,
                                    "active": True})
    # đọc không cần actor vẫn chạy
    assert client.get("/api/v1/stations/BIEN_HO_1", headers=h).status_code == 200
    # thao tác ghi cần actor → 503, không ghi nhật ký chứa tên
    r = client.patch("/api/v1/stations/BIEN_HO_1", json={"note": "x", "reason": "thử"}, headers=h)
    assert r.status_code == 503, r.text
    for d in fake_db["audit_logs"].find({}):
        assert "u_noacc" not in str(d)


def test_user_context_actor_khong_dung_user_id():
    from fastapi import HTTPException

    from api.deps import UserContext
    u = UserContext(user_id="ten_dang_nhap", username="ten_dang_nhap", role="admin")
    with pytest.raises(HTTPException) as e:
        _ = u.actor
    assert e.value.status_code == 503
    assert "ten_dang_nhap" not in str(e.value.detail)
    assert UserContext(user_id="x", role="admin", account_code="acc_0123456789ab").actor == \
        "user:acc_0123456789ab:admin"


# ------------------------------------------------------------ nhãn actor ----------
def test_nhan_ack_by_va_decided_by(client, fake_db):
    from vngis import qc_rules

    _mk_user(fake_db, "u_lbl")
    h = _h(client, "u_lbl")
    fake_db["alerts"].insert_one({"_id": "AL", "alert_id": "AL", "station": "BIEN_HO_1", "type": "flood",
                                  "severity": 2, "issued_at": TS, "status": "issued"})
    r = client.post("/api/v1/alerts/AL/ack", json={}, headers=h)
    assert r.status_code == 200 and r.json()["ack_by_label"]["username"] == "u_lbl", r.text
    it = client.get("/api/v1/alerts", headers=h).json()["items"][0]
    assert it["ack_by"].startswith("user:acc_") and it["ack_by_label"]["display_name"] == "test u_lbl"
    assert client.get("/api/v1/alerts/AL", headers=h).json()["ack_by_label"]["role"] == "admin"

    fid = qc_rules.flag_id("BIEN_HO_1", "high", TS)
    fake_db["qc_flags"].insert_one({"_id": fid, "collection": "tide_extremes", "station": "BIEN_HO_1",
                                    "kind": "high", "ts": TS, "value": 1.85, "origin": "auto",
                                    "rules": [{"rule": "R1"}], "status": "suspect", "corrected_value": None,
                                    "reason": "nghi ngờ", "version": 1, "created_at": TS})
    fake_db["tide_extremes"].insert_one({"_id": "t1", "meta": {"station": "BIEN_HO_1", "kind": "high"},
                                         "ts": TS, "value": 1.85, "qc": None})
    r = client.post(f"/api/v1/review/{fid}/approve",
                    json={"status": "confirmed_ok", "reason": "đúng", "version": 1}, headers=h)
    assert r.status_code == 200 and r.json()["decided_by_label"]["username"] == "u_lbl", r.text
    items = client.get("/api/v1/review", params={"status": "confirmed_ok"}, headers=h).json()["items"]
    assert items[0]["decided_by_label"]["username"] == "u_lbl"
    tides = client.get("/api/v1/stations/BIEN_HO_1/tides", params={"from": "2025-12-01", "to": "2026-01-31"},
                       headers=h).json()["items"]
    assert tides[0]["flag_decided_by"].startswith("user:acc_")
    assert tides[0]["flag_decided_by_label"]["username"] == "u_lbl"


# ------------------------------------------------------------ promote (registry) ---
def test_promote_theo_registry(client, fake_db):
    _mk_user(fake_db, "u_prom")
    h = _h(client, "u_prom")
    fake_db["models"].insert_many([
        {"_id": "phasegnn-1", "name": "phasegnn", "version": 1, "status": "production"},
        {"_id": "phasegnn-2", "name": "phasegnn", "version": 2, "status": "staging"},
    ])
    r = client.post("/api/v1/models/v2/promote", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_version"] == 2 and body["status"] == "production"
    assert body["promoted_by"].startswith("user:acc_") and body["promoted_by_label"]["username"] == "u_prom"
    assert fake_db["models"].find_one({"_id": "phasegnn-2"})["status"] == "production"
    assert fake_db["models"].find_one({"_id": "phasegnn-1"})["status"] == "archived"
    assert client.post("/api/v1/models/abc/promote", headers=h).status_code == 422
    assert client.post("/api/v1/models/9/promote", headers=h).status_code == 404


# ------------------------------------------------------------ dự báo mẫu ----------
_PRED_HEADER = "model,station,issued_date,target_date,lead_day,peak_idx,h_q10,h_q50,h_q90,t_peak\n"


def test_du_bao_mau_chi_lay_tep_du_bao_dung_cot(tmp_path, monkeypatch):
    from api.router import forecasts as fc

    monkeypatch.setattr(fc, "RESULTS_DIR", tmp_path.resolve())
    (tmp_path / "E5").mkdir()
    (tmp_path / "E5" / "holdout_station_metrics.csv").write_text("model,station,mae\nx,PHU_AN,0.1\n", "utf-8")
    assert fc._sample_payload(None, 10) is None, "bảng chỉ số E5 không phải dự báo mẫu"
    bad = tmp_path / "baselines" / "B0"
    bad.mkdir(parents=True)
    (bad / "predictions_test.csv").write_text("station,value\nPHU_AN,1\n", "utf-8")
    assert fc._sample_payload(None, 10) is None, "thiếu cột dự báo → không trả mẫu"
    good = tmp_path / "E2" / "phasegnn_long" / "test_full"
    good.mkdir(parents=True)
    (good / "predictions_test.csv").write_text(
        _PRED_HEADER + "phasegnn-long,BEN_LUC,2024-07-15,2024-07-15,1,0,0.1,0.4,0.7,\n"
        + "phasegnn-long,PHU_AN,2024-07-15,2024-07-15,1,0,0.2,0.5,0.8,\n", "utf-8")
    out = fc._sample_payload(None, 10)
    assert out["file"] == "E2/phasegnn_long/test_full/predictions_test.csv" and len(out["rows"]) == 2
    out = fc._sample_payload("PHU_AN", 10)
    assert [r["station"] for r in out["rows"]] == ["PHU_AN"]
    assert fc._sample_payload("NHA_BE", 10) is None, "không có trạm → không trả dữ liệu trạm khác"


# ------------------------------------------------------------ X-Forwarded-Host -----
def _req(peer: str, host: str, fwd: str):
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "client": (peer, 1234),
                    "headers": [(b"host", host.encode()), (b"x-forwarded-host", fwd.encode())]})


def test_same_host_chi_tin_x_forwarded_host_tu_proxy(cfg):
    from api.auth.cookies import _same_host
    from api.state import state

    state.override(cfg=cfg)
    try:
        r = _req("10.0.0.2", "api:8000", "gis.local:8088")
        assert _same_host(r, "http://api:8000")
        assert not _same_host(r, "http://gis.local:8088"), "mặc định không tin proxy nào"
        state.override(cfg=cfg.model_copy(update={"rate_limit": cfg.rate_limit.model_copy(
            update={"trusted_proxies": ["10.0.0.0/8"]})}))
        assert _same_host(r, "http://gis.local:8088")
        assert not _same_host(_req("203.0.113.7", "api:8000", "gis.local:8088"), "http://gis.local:8088")
    finally:
        state.cfg = None
