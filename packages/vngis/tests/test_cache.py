"""Test `vngis.cache.Cache` khi Redis chết: bỏ qua cache, gọi `compute()`, có log cảnh báo (không cần Redis thật)."""
from __future__ import annotations

import logging

import pytest
import redis

from vngis.cache import NOCACHE_PREFIX, VERSION_UNAVAILABLE, Cache


class DictRedis:
    """Redis tối giản trong bộ nhớ: GET/SET (NX, EX, PX)/DEL/INCR/HINCRBY/EVAL(RELEASE)."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None, px=None, nx=False):
        if nx and k in self.store:
            return None
        self.store[k] = v if isinstance(v, bytes) else str(v).encode()
        return True

    def delete(self, *ks):
        return sum(1 for k in ks if self.store.pop(k, None) is not None)

    def incr(self, k):
        n = int(self.store.get(k, b"0")) + 1
        self.store[k] = str(n).encode()
        return n

    def hincrby(self, *a):
        return 1

    def eval(self, script, n, key, token):
        if self.store.get(key) == (token.encode() if isinstance(token, str) else token):
            del self.store[key]
            return 1
        return 0


class DeadRedis:
    """Mọi lệnh ném `redis.ConnectionError` (Redis chết / Sentinel failover)."""

    def __getattr__(self, name):
        def fail(*a, **k):
            raise redis.ConnectionError("Redis chết")
        return fail


def test_redis_chet_get_or_compute_goi_compute_va_canh_bao(caplog):
    c = Cache(DeadRedis(), ttl_s={"stations": 30})
    calls = []
    with caplog.at_level(logging.WARNING, logger="vngis.cache"):
        v = c.get_or_compute("st:v1:all", "stations", lambda: calls.append(1) or {"ok": True})
    assert v == {"ok": True} and calls == [1]
    assert any("bỏ qua cache" in r.getMessage() for r in caplog.records)


def test_redis_chet_get_set_version_khong_nem_loi(caplog):
    c = Cache(DeadRedis())
    with caplog.at_level(logging.WARNING, logger="vngis.cache"):
        assert c.get("k") is None
        assert c.get_stale("k") is None
        assert c.set("k", {"a": 1}, 10) == 0
        assert c.version("stations") == VERSION_UNAVAILABLE
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) >= 4


def test_khong_biet_phien_ban_thi_key_nocache_khong_cham_redis():
    """Redis chập chờn: đọc `ver:` lỗi → KHÔNG dùng v0 (có thể là dữ liệu cũ) mà dùng key nocache."""
    r = DictRedis()
    c = Cache(r)
    r.store["st:v0:all"] = b'J{"cu": true}'          # bản cũ còn sót
    real_get = r.get
    r.get = lambda k: (_ for _ in ()).throw(redis.ConnectionError("x")) if k.startswith("ver:") else real_get(k)
    key = c.versioned_key("st", "stations", "all")
    assert key.startswith(NOCACHE_PREFIX)
    assert c.get(key) is None and c.set(key, 1) == 0
    assert c.get_or_compute(key, "stations", lambda: {"moi": True}) == {"moi": True}
    assert not any(k.startswith(NOCACHE_PREFIX) for k in r.store)


def test_redis_song_van_cache_binh_thuong():
    c = Cache(DictRedis(), jitter=0.0)
    n = []
    for _ in range(3):
        assert c.get_or_compute("st:v0:x", "stations", lambda: n.append(1) or [1, 2]) == [1, 2]
    assert n == [1]


def test_loi_cua_compute_van_nem_ra():
    """Chỉ lỗi Redis bị nuốt; lỗi nghiệp vụ trong compute (ví dụ 404) phải đi ra ngoài."""
    c = Cache(DeadRedis())
    with pytest.raises(KeyError):
        c.get_or_compute("k", "x", lambda: {}["thieu"])
