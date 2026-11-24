"""Test khóa phân tán (GĐ7.5).

- 100 tác vụ tranh một khoá → **đúng 1** bên giữ.
- Nhả bằng token sai → không xoá được khoá.
- Khoá tự hết hạn (TTL) sau thời gian chờ.
- Watchdog gia hạn khoá và đặt cờ `lost` khi gia hạn thất bại.
- Fencing token: ghi với `last_fence < fence` → bị từ chối khi đã có fence lớn hơn.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from vngis.locks import (FenceRejected, LockBusy, LockGuard, LockLost, SingleLock, Watchdog,
                         build_lock, next_fence)


def test_100_tac_vu_tranh_mot_khoa(fake_lock):
    """100 thread tranh cùng một khoá → chỉ đúng một bên giữ được."""
    n_workers = 100
    winners: list[Any] = []
    losers: list[Any] = []
    barrier = threading.Barrier(n_workers)
    lock = threading.Lock()

    def worker(i: int) -> None:
        barrier.wait()
        lk, votes = fake_lock.acquire(f"lock:tranh", ttl_ms=5000)
        if lk is not None:
            with lock:
                winners.append((i, lk.token, votes))
        else:
            with lock:
                losers.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(winners) == 1, f"phải đúng 1 bên giữ khoá, thực tế {len(winners)}"
    assert len(losers) == n_workers - 1
    # Khoá vẫn còn trong Redis với token của người giữ.
    assert fake_lock.client.get("lock:tranh").decode() == winners[0][1]


def test_nha_voi_token_sai_khong_xoa_duoc(fake_lock):
    """Nhả khoá bằng token sai → khoá vẫn còn."""
    lk, _ = fake_lock.acquire("lock:sai-token", ttl_ms=5000)
    assert lk is not None
    # Tạo một Lock khác với token sai rồi thử nhả.
    from vngis.locks import Lock
    fake = Lock(lk.key, "token-sai", 1, 5000.0, ttl_ms=5000)
    fake_lock.release(fake)
    assert fake_lock.client.get("lock:sai-token") is not None, "khoá đã bị xoá bởi token sai"

    # Nhả đúng token → xoá được.
    fake_lock.release(lk)
    assert fake_lock.client.get("lock:sai-token") is None


def test_khoa_tu_het_han(fake_lock):
    """Khoá tự hết hạn sau TTL → có thể lấy lại."""
    lk, _ = fake_lock.acquire("lock:ttl", ttl_ms=200)
    assert lk is not None
    assert fake_lock.client.get("lock:ttl") is not None
    time.sleep(0.35)
    assert fake_lock.client.get("lock:ttl") is None, "khoá phải tự hết hạn"
    lk2, _ = fake_lock.acquire("lock:ttl", ttl_ms=5000)
    assert lk2 is not None, "phải lấy lại được khoá đã hết hạn"


def test_extend_gia_han_lua(fake_lock):
    """Gia hạn (Lua `PEXPIRE`) giữ khoá sống lâu hơn."""
    lk, _ = fake_lock.acquire("lock:extend", ttl_ms=300)
    assert lk is not None
    time.sleep(0.15)
    assert fake_lock.extend(lk, 3000) is True
    time.sleep(0.25)
    assert fake_lock.client.get("lock:extend") is not None, "khoá phải còn sau khi gia hạn"
    fake_lock.release(lk)


def test_extend_voi_token_sai_that_bai(fake_lock):
    """Gia hạn bằng token sai → thất bại (không gia hạn được khoá của người khác)."""
    from vngis.locks import Lock
    lk, _ = fake_lock.acquire("lock:extend-sai", ttl_ms=300)
    fake = Lock(lk.key, "token-sai", 1, 300.0, ttl_ms=300)
    assert fake_lock.extend(fake, 3000) is False


def test_watchdog_gia_han_va_dat_co_lost(fake_lock):
    """Watchdog gia hạn khoá; khi gia hạn thất bại → `lock.lost = True`."""
    lk, _ = fake_lock.acquire("lock:wd", ttl_ms=300)
    wd = Watchdog(fake_lock, lk, ttl_ms=300)
    wd.start()
    time.sleep(0.5)          # > TTL nhưng watchdog gia hạn mỗi TTL/3
    assert lk.lost is False, "watchdog phải giữ khoá sống"
    assert wd.extends >= 1, "watchdog phải gia hạn ít nhất một lần"
    wd.stop()

    # Mô phỏng mất khoá: đổi token trong Redis → watchdog gia hạn thất bại → lost.
    lk2, _ = fake_lock.acquire("lock:wd2", ttl_ms=300)
    fake_lock.client.set("lock:wd2", "token-khac", px=300)
    wd2 = Watchdog(fake_lock, lk2, ttl_ms=300)
    wd2.start()
    time.sleep(0.5)
    assert lk2.lost is True, "phải đặt cờ lost khi gia hạn thất bại"
    wd2.stop()


def test_lock_guard_nham_khoa(fake_lock):
    """`LockGuard`: khoá bận → `LockBusy`."""
    g1 = LockGuard(fake_lock, resource="guard:1", ttl_ms=3000)
    g1.acquire()
    g2 = LockGuard(fake_lock, resource="guard:1", ttl_ms=3000)
    with pytest.raises(LockBusy):
        g2.acquire()
    g1.release()
    g2.acquire()
    g2.release()


def test_fencing_token_tang_don(fake_db, fake_lock):
    """Fencing token tăng đơn điệu theo từng lần lấy khoá."""
    f1 = next_fence(fake_db, "res:1")
    f2 = next_fence(fake_db, "res:1")
    assert f2 > f1
    assert next_fence(fake_db, "res:2") >= 1, "mỗi resource có bộ đếm riêng"


def test_fencing_tu_choi_khi_co_fence_lon_hon(fake_db, fake_lock):
    """Ghi có điều kiện `last_fence < fence` → bị từ chối nếu đã có fence lớn hơn."""
    guard = LockGuard(fake_lock, db=fake_db, resource="res:fence", ttl_ms=3000)
    guard.acquire()
    fence = guard.fence
    fake_db["job_state"].insert_one({"_id": "res:fence", "last_fence": fence, "state": "done"})
    # Ghi lại với cùng fence → điều kiện `last_fence < fence` không còn thoả → bị từ chối.
    with pytest.raises(FenceRejected):
        guard.write({}, {"state": "again"}, collection="job_state", doc_id="res:fence")
    guard.release()


def test_fencing_cho_phep_khi_fence_lon_hon(fake_db, fake_lock):
    """Fence mới lớn hơn → ghi được (tiến trình mới thay tiến trình cũ)."""
    g1 = LockGuard(fake_lock, db=fake_db, resource="res:fence2", ttl_ms=3000)
    g1.acquire()
    g1.write({}, {"last_fence": g1.fence, "state": "v1"}, collection="job_state", doc_id="res:fence2")
    g1.release()

    g2 = LockGuard(fake_lock, db=fake_db, resource="res:fence2", ttl_ms=3000)
    g2.acquire()
    res = g2.write({}, {"last_fence": g2.fence, "state": "v2"}, collection="job_state", doc_id="res:fence2")
    assert res is not None
    assert fake_db["job_state"].find_one({"_id": "res:fence2"})["state"] == "v2"
    g2.release()


def test_lock_lost_khi_gia_han_that_bai(fake_db, fake_lock):
    """Sau khi mất khoá, `check()` ném `LockLost` (không được ghi tiếp)."""
    guard = LockGuard(fake_lock, db=fake_db, resource="res:lost", ttl_ms=3000)
    guard.acquire()
    guard.lock.mark_lost()
    assert guard.lost is True
    with pytest.raises(LockLost):
        guard.check()
    guard.release()


def test_build_lock_theo_backend():
    """`build_lock` hỗ trợ `single` và `redlock`."""
    single = build_lock("single", [("127.0.0.1", 6379)])
    assert isinstance(single, SingleLock)
    assert single.backend == "single"
    rl = build_lock("redlock", [("127.0.0.1", 6379), ("127.0.0.1", 6380)])
    assert rl.backend == "redlock"
    with pytest.raises(ValueError):
        build_lock("khong-hop-le", [("127.0.0.1", 6379)])


def test_list_locks(fake_lock):
    """Liệt kê khoá đang giữ (`GET /admin/locks`)."""
    lk, _ = fake_lock.acquire("lock:liet-ke", ttl_ms=3000)
    items = fake_lock.list_locks(pattern="lock:*")
    assert any(i["key"] == "lock:liet-ke" for i in items)
    fake_lock.release(lk)
