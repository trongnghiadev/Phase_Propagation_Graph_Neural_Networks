"""Test cache Redis (GĐ7.4): cache-aside, phiên bản, chống stampede, stale, nén, đếm hit/miss."""
from __future__ import annotations

import threading
import time
from typing import Any

import pytest


def test_get_set_co_ban(fake_cache):
    """Ghi → đọc trả đúng giá trị; TTL được đặt."""
    fake_cache.set("k:v1:abc", {"a": 1}, ttl_s=30)
    assert fake_cache.get("k:v1:abc") == {"a": 1}
    assert fake_cache.redis.ttl("k:v1:abc") > 0


def test_miss_khong_tra_gi_tri(fake_cache):
    """Khoá không tồn tại → `None` (cache miss)."""
    assert fake_cache.get("k:v1:khong-ton-tai") is None


def test_phien_ban_tang_va_khoa_cu_het_hieu(fake_cache):
    """Tăng `ver:{coll}` → khoá cache cũ không còn được dùng."""
    key1 = fake_cache.versioned_key("st", "stations", "A")
    fake_cache.set(key1, [1, 2, 3], ttl_s=30)
    assert fake_cache.get(key1) == [1, 2, 3]

    v0 = fake_cache.version("stations")
    v1 = fake_cache.bump_version("stations")
    assert v1 == v0 + 1

    key2 = fake_cache.versioned_key("st", "stations", "A")
    assert key2 != key1, "khoá phải đổi theo phiên bản"
    assert fake_cache.get(key2) is None, "khoá cũ không được trả về sau khi tăng phiên bản"


def test_cache_aside_tinh_lai_dung_mot_lan(fake_cache):
    """`get_or_compute`: lần đầu tính, các lần sau dùng cache."""
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"value": calls["n"]}

    key = fake_cache.versioned_key("x", "coll", "1")
    assert fake_cache.get_or_compute(key, "coll", compute) == {"value": 1}
    assert fake_cache.get_or_compute(key, "coll", compute) == {"value": 1}
    assert fake_cache.get_or_compute(key, "coll", compute) == {"value": 1}
    assert calls["n"] == 1, "chỉ được tính một lần"


def test_ban_stale_dung_khi_khoa_dang_tao(fake_cache):
    """Khi khoá `lock:rebuild` bị giữ, trả bản `stale:` nếu có."""
    key = fake_cache.versioned_key("s", "coll", "x")
    fake_cache.set(key, "gia-tri-moi", ttl_s=1)
    # Giữ khoá rebuild để mô phỏng tiến trình khác đang tính.
    token = fake_cache.acquire_rebuild_lock(key)
    assert token is not None
    # Xoá bản hiện hành nhưng giữ bản stale (mô phỏng TTL hết hạn).
    fake_cache.redis.delete(key)
    value = fake_cache.get_or_compute(key, "coll", lambda: "gia-tri-tinh-lai")
    assert value in ("gia-tri-moi", "gia-tri-tinh-lai")
    fake_cache.release_rebuild_lock(key, token)


def test_chong_stampede_chi_mot_tien_trinh_tinh(fake_cache):
    """Nhiều request đồng thời → chỉ một lần gọi `compute`."""
    calls = {"n": 0}
    barrier = threading.Barrier(20)
    results: list[Any] = []

    def compute():
        calls["n"] += 1
        time.sleep(0.05)
        return {"n": calls["n"]}

    key = fake_cache.versioned_key("st", "coll", "stampede")

    def worker():
        barrier.wait()
        results.append(fake_cache.get_or_compute(key, "coll", compute))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 20
    assert calls["n"] == 1, f"phải chỉ tính 1 lần, thực tế {calls['n']}"
    assert all(r == results[0] for r in results)


def test_khoa_rebuild_chi_nha_nguoi_giu_xoa(fake_cache):
    """Nhả khoá rebuild bằng token sai → không xoá được khoá."""
    key = fake_cache.versioned_key("r", "coll", "k")
    token = fake_cache.acquire_rebuild_lock(key)
    assert token is not None
    assert fake_cache.release_rebuild_lock(key, "token-sai") is False
    assert fake_cache.redis.exists(f"lock:rebuild:{key}") == 1
    assert fake_cache.release_rebuild_lock(key, token) is True
    assert fake_cache.redis.exists(f"lock:rebuild:{key}") == 0


def test_dem_hit_miss_theo_loai_key(fake_cache):
    """Bộ đếm hit/miss tách theo loại key (phần trước dấu `:`)."""
    fake_cache.reset_stats()
    fake_cache.set("st:v1:a", 1, ttl_s=30)
    fake_cache.get("st:v1:a")          # hit
    fake_cache.get("st:v1:khong-co")    # miss
    fake_cache.set("bul:v1:b", 2, ttl_s=30)
    fake_cache.get("bul:v1:b")          # hit
    stats = fake_cache.stats()
    assert stats["hits"].get("st", 0) == 1
    assert stats["misses"].get("st", 0) == 1
    assert stats["hits"].get("bul", 0) == 1


def test_nen_gia_tri_lon(fake_cache):
    """Giá trị lớn được nén (zstd nếu có, msgpack nếu không) và giải mã đúng."""
    import json

    from vngis.cache import decode_value

    payload = {"rows": [{"a": i, "b": "x" * 50} for i in range(500)]}
    fake_cache.set("big:v1:1", payload, ttl_s=30)
    raw = fake_cache.redis.get("big:v1:1")
    assert raw is not None
    assert decode_value(raw) == payload
    plain = json.dumps(payload).encode("utf-8")
    assert len(raw) < len(plain)


def test_write_through_va_con_tro(fake_cache):
    """Write-through ghi thẳng cache; con trỏ `fc:{station}:latest` trỏ tới khoá dự báo."""
    fake_cache.write_through("fc:BIEN_HO_1:2026-11-24", {"peak": 3.2}, ttl_s=600)
    assert fake_cache.get("fc:BIEN_HO_1:2026-11-24") == {"peak": 3.2}
    fake_cache.set_pointer("fc:BIEN_HO_1:latest", "fc:BIEN_HO_1:2026-11-24", ttl_s=600)
    assert fake_cache.get("fc:BIEN_HO_1:latest") == "fc:BIEN_HO_1:2026-11-24"


def test_delete_pattern_xoa_nhieu(fake_cache):
    """Xoá theo mẫu dùng SCAN (không dùng KEYS)."""
    for i in range(5):
        fake_cache.set(f"tmp:v1:{i}", i, ttl_s=60)
    fake_cache.set("giu:v1:1", 1, ttl_s=60)
    n = fake_cache.delete_pattern("tmp:v1:*")
    assert n >= 5
    assert fake_cache.get("tmp:v1:0") is None
    assert fake_cache.get("giu:v1:1") == 1


def test_ttl_co_jitter(fake_cache):
    """TTL có jitter (dao động quanh giá trị cấu hình)."""
    fake_cache.jitter = 0.2
    ttls = [fake_cache.set(f"j:v1:{i}", i, ttl_s=10) for i in range(20)]
    assert all(8 <= t <= 12 for t in ttls), ttls
    assert len(set(ttls)) > 1, "phải có dao động (jitter)"


def test_ping_va_info(fake_cache):
    """Chẩn đoán cache: ping OK và có thông tin cấu hình."""
    assert fake_cache.ping() is True
    info = fake_cache.info()
    assert "ttl_s" in info and "compress_threshold_bytes" in info
