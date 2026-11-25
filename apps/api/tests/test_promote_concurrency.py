"""`POST /models/{version}/promote`: an toàn đồng thời (khoá `models:promote`, thứ tự ghi `promote_seq`).

Hai lệnh ghi (đặt production, archive bản khác) không nguyên tử khi không có replica set; test bảo đảm
không bao giờ còn 2 bản production sau khi các lượt kết thúc, kể cả khi các lượt xen kẽ nhau.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from api.auth.password import hash_password
from api.auth.users import UserRepo

PWD = "MatKhau-Test-012345"


def _login(client: Any, db: Any, username: str) -> dict:
    repo = UserRepo(db)
    repo.ensure_roles()
    repo.create_user(username=username, password_hash=hash_password(PWD), role="admin",
                     display_name=f"test {username}")
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _models(db: Any, n: int = 4) -> None:
    db["models"].insert_many([{"_id": f"phasegnn-{i}", "name": "phasegnn", "version": i,
                               "status": "production" if i == 1 else "staging", "train_state": "succeeded"}
                              for i in range(1, n + 1)])


def _prod(db: Any) -> list[int]:
    return sorted(d["version"] for d in db["models"].find({"status": "production"}))


def test_promote_ghi_promote_seq_va_archive(client, fake_db):
    h = _login(client, fake_db, "u_pc1")
    _models(fake_db)
    r = client.post("/api/v1/models/3/promote", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["transaction"] is False and "phasegnn-1" in r.json()["archived"]
    assert _prod(fake_db) == [3]
    s1 = fake_db["models"].find_one({"_id": "phasegnn-3"})["promote_seq"]
    assert client.post("/api/v1/models/2/promote", headers=h).status_code == 200
    assert _prod(fake_db) == [2] and fake_db["models"].find_one({"_id": "phasegnn-2"})["promote_seq"] > s1


def test_promote_khoa_ban_tra_423(client, fake_db, fake_lock):
    from vngis.locks import LockGuard

    h = _login(client, fake_db, "u_pc2")
    _models(fake_db)
    g = LockGuard(fake_lock, db=fake_db, resource="models:promote", ttl_ms=60_000)
    g.acquire()
    try:
        assert client.post("/api/v1/models/2/promote", headers=h).status_code == 423
    finally:
        g.release()
    assert _prod(fake_db) == [1]


def test_promote_dong_thoi_qua_api_chi_mot_production(client, fake_db):
    """Nhiều request promote song song (luồng thật): mỗi lượt 200 hoặc 423/409; kết thúc đúng 1 production."""
    h = _login(client, fake_db, "u_pc3")
    _models(fake_db, 6)
    codes: list[int] = []
    barrier = threading.Barrier(5)

    def go(v: int) -> None:
        barrier.wait()
        codes.append(client.post(f"/api/v1/models/{v}/promote", headers=h).status_code)

    ts = [threading.Thread(target=go, args=(v,)) for v in range(2, 7)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert set(codes) <= {200, 409, 423} and 200 in codes, codes
    assert len(_prod(fake_db)) == 1


class _Stepper:
    """Chạy `promote_model` thật trong một luồng, dừng ở mỗi `hook` cho tới khi được cho đi tiếp."""

    def __init__(self, db: Any, doc_id: str):
        from vngis import model_registry as MR

        self._go, self._at = threading.Semaphore(0), threading.Semaphore(0)
        self.out: dict = {}
        self.done = False

        def hook(step: str) -> None:
            self._at.release()
            assert self._go.acquire(timeout=10), f"kẹt ở bước {step}"

        def run() -> None:
            try:
                self.out = MR.promote_model(db, doc_id, actor="t", use_transaction=False, _hook=hook)
            finally:
                self.done = True
                self._at.release()

        self.t = threading.Thread(target=run, daemon=True)
        self.t.start()
        assert self._at.acquire(timeout=10)  # đã cấp seq, dừng trước lệnh ghi đầu tiên

    def step(self) -> None:
        """Chạy tới hook kế tiếp (hoặc tới hết)."""
        if not self.done:
            self._go.release()
            assert self._at.acquire(timeout=10)

    def finish(self) -> dict:
        while not self.done:
            self.step()
        self.t.join(10)
        return self.out


@pytest.mark.parametrize("order", ["a_then_b", "b_set_before_a_archive", "a_set_after_b_done", "b_then_a_tail"])
def test_promote_xen_ke_khong_khoa_tu_sua(fake_db, order):
    """Hai lượt `promote_model` thật KHÔNG giữ khoá, xen kẽ từng lệnh ghi: luôn hội tụ về đúng một bản
    production — bản của lượt có `promote_seq` lớn hơn (lượt bắt đầu sau)."""
    from vngis import model_registry as MR

    _models(fake_db)
    a = _Stepper(fake_db, "phasegnn-2")   # seq nhỏ hơn
    b = _Stepper(fake_db, "phasegnn-3")   # seq lớn hơn
    if order == "a_then_b":
        a.finish(), b.finish()
    elif order == "b_set_before_a_archive":
        a.step(), b.step(), a.step(), b.step(), a.finish(), b.finish()
    elif order == "a_set_after_b_done":   # request của a bị trễ: b xong hết rồi a mới ghi
        b.finish(), a.finish()
    else:                                 # a đặt production + archive, b xong, rồi a kiểm tra lượt mới hơn
        a.step(), a.step(), b.finish(), a.finish()
    assert _prod(fake_db) == [3], (order, a.out, b.out)
    assert a.out["superseded"] is (order != "a_then_b")
    assert b.out["superseded"] is False
    # trạng thái sót (2 production, ví dụ tiến trình chết giữa hai lệnh ghi) → worker vẫn chọn đúng, repair sửa
    fake_db["models"].update_one({"_id": "phasegnn-4"}, {"$set": {"status": "production", "promote_seq": 0}})
    assert MR.find_production(fake_db)["version"] == 3
    assert MR.repair_production(fake_db) == ["phasegnn-4"] and _prod(fake_db) == [3]


def test_promote_model_ham_thuc_chay_xen_ke(fake_db):
    """`promote_model` thật: lượt có seq cũ ghi sau cùng (bị trễ) không giành lại production."""
    from vngis import model_registry as MR

    _models(fake_db)
    MR.promote_model(fake_db, "phasegnn-3", actor="t")
    seq3 = fake_db["models"].find_one({"_id": "phasegnn-3"})["promote_seq"]
    # mô phỏng lượt cũ (seq nhỏ hơn) chạm phasegnn-3 sau → điều kiện `promote_seq < seq` chặn ghi đè
    old = {"$or": [{"promote_seq": {"$exists": False}}, {"promote_seq": {"$lt": seq3 - 1}}]}
    assert fake_db["models"].update_one({"_id": "phasegnn-3", **old}, {"$set": {"x": 1}}).matched_count == 0
    out = MR.promote_model(fake_db, "phasegnn-2", actor="t")
    assert out["superseded"] is False and out["archived"] == ["phasegnn-3"] and _prod(fake_db) == [2]
    assert MR.supports_transactions(fake_db) is False


def test_promote_model_transaction_tren_replica_set():
    """MongoDB thật (`PGNN_TEST_MONGO_URI`, DB test riêng): replica set → transaction; đơn → thứ tự ghi."""
    import os

    uri = os.environ.get("PGNN_TEST_MONGO_URI")
    if not uri:
        pytest.skip("không có PGNN_TEST_MONGO_URI")
    from pymongo import MongoClient

    from vngis import model_registry as MR

    cli = MongoClient(uri, serverSelectionTimeoutMS=3000)
    db = cli["pgnn_test_promote_tx"]
    try:
        tx = MR.supports_transactions(db)
        db["models"].drop()
        db["counters"].drop()
        _models(db)
        out = MR.promote_model(db, "phasegnn-4", actor="t")
        assert out["transaction"] is tx and _prod(db) == [4]
        # MongoDB thật, nhiều luồng promote song song không khoá → vẫn hội tụ đúng 1 production
        ts = [threading.Thread(target=MR.promote_model, args=(db, f"phasegnn-{v}"), kwargs={"actor": "t"})
              for v in (1, 2, 3, 4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        assert len(_prod(db)) == 1
        if not tx:
            pytest.skip("MongoDB không phải replica set: chỉ kiểm thứ tự ghi, chưa kiểm transaction")
    finally:
        cli.drop_database("pgnn_test_promote_tx")
        cli.close()


def test_get_models_liet_ke_train_state_va_promotable(client, fake_db):
    h = _login(client, fake_db, "u_pc4")
    fake_db["models"].insert_many([
        {"_id": "phasegnn-1", "version": 1, "status": "production", "train_state": "succeeded", "promote_seq": 1,
         "checkpoint_path": "results/models/phasegnn-1/checkpoint_seed0.pt"},
        {"_id": "phasegnn-2", "version": 2, "status": "staging", "train_state": "skipped",
         "train_reason": "image worker không có phasegnn/torch"},
        {"_id": "phasegnn-3", "version": 3, "status": "staging", "train_state": "succeeded"},
        {"_id": "cu", "version": "v20260101", "status": "staging"},          # chuỗi cũ: bỏ qua
    ])
    r = client.get("/api/v1/models", headers=h)
    assert r.status_code == 200, r.text
    items = {it["version"]: it for it in r.json()["items"]}
    assert list(items) == [3, 2, 1]
    assert items[1]["serving"] is True and items[1]["promotable"] is False
    assert items[2]["promotable"] is False and "torch" in items[2]["train_reason"]
    assert items[3]["promotable"] is True and items[3]["id"] == "phasegnn-3"
    assert "checkpoint_path" not in items[1], "không lộ đường dẫn checkpoint"
