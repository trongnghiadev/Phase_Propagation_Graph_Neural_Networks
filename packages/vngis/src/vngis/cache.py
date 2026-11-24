"""Cache Redis dùng chung cho API và worker (đề cương 5.5, mục GĐ7.4).

Tính năng chính:

- **Cache-aside** cho dữ liệu đọc theo yêu cầu; **write-through** cho dự báo.
- **Khóa theo phiên bản**: key đọc ghi dạng ``<prefix>:v{ver}:<rest>`` với ``ver`` lấy từ Redis
  (``ver:{coll}``). Tăng ``ver:{coll}`` khi dữ liệu nguồn đổi → mọi key cũ tự hết hiệu lực,
  không cần ``SCAN`` + ``DEL``.
- **TTL có jitter** (± ``cache.jitter``) để các key cùng lúc hết hạn không tạo đợt tải đồng loạt.
- **Bản ``stale:``**: giữ giá trị cũ lâu hơn (``stale_factor`` × TTL) để phục vụ khi không tính lại được.
- **Chống stampede**: khoá ``lock:rebuild:{key}``; người gọi thứ hai chờ tối đa ``cache.max_wait`` lần
  rồi tự tính, không xếp hàng vô hạn.
- **Nén zstd + msgpack** cho giá trị lớn (``compress_threshold_bytes``); giá trị nhỏ để JSON thô.
- **Đếm hit/miss theo loại key** (Redis hash ``cache:stats``).
- **Redis lỗi thì bỏ qua cache**: ``get``/``get_stale`` trả miss, ``set`` không ghi, ``version`` trả
  ``VERSION_UNAVAILABLE`` (key có phiên bản thành ``nocache:…``, không bao giờ đọc/ghi Redis), và
  ``get_or_compute`` gọi thẳng ``compute()``; mỗi lần đều ghi log cảnh báo. Không trả phiên bản 0 khi
  Redis lỗi, vì như thế có thể đọc trúng key ``v0`` cũ khi Redis chập chờn.

Giá trị được mã hoá theo thứ tự ưu tiên: ``zstd(msgpack)`` > ``json``. Tiền tố byte đánh dấu
để đọc lại đúng kiểu, nên có thể đổi quy tắc mà vẫn đọc được giá trị cũ.

    from vngis.db import redis_cache
    from vngis.cache import Cache
    c = Cache(redis_cache())
    v = await c.get("stats:tide:VUNG_TAU:2026")
    if v is None:
        v = await c.get_or_compute("stats:tide:VUNG_TAU:2026", "stats", compute)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

try:  # msgpack và zstd là tuỳ chọn: thiếu thì cache chỉ dùng JSON thô
    import msgpack
except ImportError:  # pragma: no cover - phụ thuộc tuỳ chọn
    msgpack = None

try:
    import zstandard
except ImportError:  # pragma: no cover - phụ thuộc tuỳ chọn
    zstandard = None

__all__ = ["Cache", "CacheStats", "encode_value", "decode_value", "version_key", "STATS_KEY",
           "VERSION_UNAVAILABLE", "NOCACHE_PREFIX"]

log = logging.getLogger("vngis.cache")

STATS_KEY = "cache:stats"
#: Phiên bản trả về khi không đọc được ``ver:{coll}`` (Redis lỗi).
VERSION_UNAVAILABLE = -1
#: Tiền tố key "không cache": ``versioned_key`` trả key này khi không biết phiên bản.
NOCACHE_PREFIX = "nocache:"
#: Tiền tố byte đánh dấu kiểu mã hoá.
_CODEC_JSON = b"J"
_CODEC_MSGPACK = b"M"
_CODEC_ZSTD = b"Z"


# ---------------------------------------------------------------- mã hoá/giải mã ----
def encode_value(value: Any, threshold: int = 4096) -> bytes:
    """Mã hoá giá trị cache. Giá trị nhỏ → JSON thô; giá trị lớn → zstd(msgpack) nếu có thư viện."""
    if msgpack is None:
        return _CODEC_JSON + json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    try:
        raw = msgpack.packb(value, use_bin_type=True, default=_fallback)
    except Exception:  # noqa: BLE001 - dữ liệu không serializable → JSON thô
        return _CODEC_JSON + json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    if len(raw) > threshold and zstandard is not None:
        cctx = zstandard.ZstdCompressor(level=3)
        return _CODEC_ZSTD + cctx.compress(raw)
    return _CODEC_MSGPACK + raw


def decode_value(raw: bytes | str | None) -> Any:
    """Giải mã giá trị cache; ``None`` nếu không có hoặc hỏng."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not raw:
        return None
    codec, body = raw[:1], raw[1:]
    try:
        if codec == _CODEC_JSON:
            return json.loads(body.decode("utf-8"))
        if codec == _CODEC_MSGPACK:
            return msgpack.unpackb(body, raw=False) if msgpack is not None else None
        if codec == _CODEC_ZSTD:
            if zstandard is None:
                return None
            dctx = zstandard.ZstdDecompressor()
            plain = dctx.decompress(body)
            return msgpack.unpackb(plain, raw=False) if msgpack is not None else None
    except Exception:  # noqa: BLE001 - giá trị hỏng → coi như cache miss
        return None
    return None


def _fallback(obj: Any) -> Any:
    """Chuyển kiểu không serializable (datetime, ObjectId…) sang chuỗi."""
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "hex"):
        return str(obj)
    return str(obj)


def version_key(coll: str) -> str:
    """Khoá phiên bản của một collection: ``ver:{coll}``."""
    return f"ver:{coll}"


# ---------------------------------------------------------------- số liệu cache ----
@dataclass
class CacheStats:
    """Bộ đếm hit/miss theo loại key (loại = phần trước dấu ``:`` của key, bỏ hậu tố ``v{ver}``)."""

    hits: dict[str, int] = field(default_factory=dict)
    misses: dict[str, int] = field(default_factory=dict)
    stale_hits: dict[str, int] = field(default_factory=dict)
    rebuilds: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def kind_of(key: str) -> str:
        """Loại của một key: ``bul:v3:2026/x.pdf`` → ``bul``."""
        parts = [p for p in key.split(":") if p]
        if not parts:
            return "unknown"
        if len(parts) > 1 and parts[1].startswith("v") and parts[1][1:].isdigit():
            return parts[0]
        return parts[0]

    def merge(self, other: "CacheStats") -> None:
        """Cộng dồn số liệu của hai bộ đếm (dùng khi gộp nhiều instance)."""
        for name in ("hits", "misses", "stale_hits", "rebuilds"):
            src = getattr(other, name)
            dst = getattr(self, name)
            for k, v in src.items():
                dst[k] = dst.get(k, 0) + v

    def to_dict(self) -> dict:
        return {"hits": dict(self.hits), "misses": dict(self.misses),
                "stale_hits": dict(self.stale_hits), "rebuilds": dict(self.rebuilds)}


# ---------------------------------------------------------------- cache chính -------
class Cache:
    """Cache-aside trên Redis (Sentinel hoặc một node) theo cấu hình ``vngis.config``.

    ``redis`` là client ``redis.Redis`` (đồng bộ). Các hàm ở đây đều đồng bộ; lớp ``AsyncCache``
    trong ``apps/api`` bọc lại bằng ``asyncio.to_thread`` để không chặn vòng lặp sự kiện.
    """

    def __init__(self, redis_client: Any, *, ttl_s: dict[str, int] | None = None, jitter: float = 0.1,
                 stale_factor: int = 4, compress_threshold_bytes: int = 4096, max_wait: int = 20,
                 rebuild_lock_ttl_ms: int = 30000):
        self.redis = redis_client
        self.ttl_s = dict(ttl_s or {})
        self.jitter = jitter
        self.stale_factor = stale_factor
        self.compress_threshold_bytes = compress_threshold_bytes
        self.max_wait = max_wait
        self.rebuild_lock_ttl_ms = rebuild_lock_ttl_ms

    # ------------------------------------------------------------ phiên bản ----
    def _warn(self, op: str, key: str, exc: BaseException) -> None:
        log.warning("cache Redis lỗi khi %s %r: %s: %s — bỏ qua cache", op, key, type(exc).__name__, exc)

    @staticmethod
    def is_nocache(key: str) -> bool:
        return key.startswith(NOCACHE_PREFIX)

    def version(self, coll: str) -> int:
        """Phiên bản hiện tại của collection; 0 nếu chưa có (key thiếu → coi như 0).

        Redis lỗi → ``VERSION_UNAVAILABLE`` (-1) kèm log cảnh báo (không ném lỗi).
        """
        try:
            raw = self.redis.get(version_key(coll))
        except Exception as e:  # noqa: BLE001 - Redis lỗi: bỏ qua cache
            self._warn("đọc phiên bản", version_key(coll), e)
            return VERSION_UNAVAILABLE
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def bump_version(self, coll: str) -> int:
        """Tăng phiên bản của collection (INCR) → vô hiệu cache cũ. Trả phiên bản mới."""
        return int(self.redis.incr(version_key(coll)))

    def versioned_key(self, prefix: str, coll: str, *rest: Any) -> str:
        """Key đầy đủ có kèm phiên bản: ``<prefix>:v{ver}:<rest>``."""
        tail = ":".join(str(r) for r in rest)
        ver = self.version(coll)
        key = f"{prefix}:v{ver}" if ver != VERSION_UNAVAILABLE else f"{NOCACHE_PREFIX}{prefix}"
        return f"{key}:{tail}" if tail else key

    # ------------------------------------------------------------ đọc/ghi -------
    def get(self, key: str) -> Any:
        """Đọc giá trị; đếm hit/miss theo loại key. Trả ``None`` nếu không có hoặc Redis lỗi."""
        if self.is_nocache(key):
            return None
        try:
            raw = self.redis.get(key)
        except Exception as e:  # noqa: BLE001
            self._warn("đọc", key, e)
            return None
        value = decode_value(raw)
        kind = CacheStats.kind_of(key)
        if value is None:
            self._inc("misses", kind)
            return None
        self._inc("hits", kind)
        return value

    def get_stale(self, key: str) -> Any:
        """Đọc bản ``stale:`` (giá trị cũ, dùng khi không tính lại được)."""
        if self.is_nocache(key):
            return None
        try:
            raw = self.redis.get(f"stale:{key}")
        except Exception as e:  # noqa: BLE001
            self._warn("đọc", f"stale:{key}", e)
            return None
        value = decode_value(raw)
        if value is not None:
            self._inc("stale_hits", CacheStats.kind_of(key))
        return value

    def set(self, key: str, value: Any, ttl_s: int | None = None) -> int:
        """Ghi giá trị kèm TTL có jitter; đồng thời ghi bản ``stale:`` sống lâu hơn.

        Trả TTL đã đặt; 0 nếu không ghi (key ``nocache:`` hoặc Redis lỗi — có log cảnh báo).
        """
        if self.is_nocache(key):
            return 0
        ttl = int(ttl_s if ttl_s is not None else self.ttl_s.get(self._kind_ttl(key), 60))
        ttl = max(1, int(ttl * (1 + random.uniform(-self.jitter, self.jitter))))
        raw = encode_value(value, self.compress_threshold_bytes)
        try:
            self.redis.set(key, raw, ex=ttl)
            self.redis.set(f"stale:{key}", raw, ex=max(1, ttl * self.stale_factor))
        except Exception as e:  # noqa: BLE001
            self._warn("ghi", key, e)
            return 0
        return ttl

    def delete(self, *keys: str) -> int:
        """Xoá key (kèm bản ``stale:``)."""
        n = 0
        for k in keys:
            n += int(self.redis.delete(k) or 0)
            self.redis.delete(f"stale:{k}")
        return n

    def delete_pattern(self, pattern: str) -> int:
        """Xoá mọi key khớp mẫu (dùng cho ``DELETE /admin/cache``); dùng SCAN, không dùng KEYS."""
        n = 0
        cursor = 0
        while True:
            cursor, batch = self.redis.scan(cursor=cursor, match=pattern, count=500)
            if batch:
                n += int(self.redis.delete(*batch) or 0)
                for k in batch:
                    self.redis.delete(f"stale:{k}")
            if cursor == 0:
                break
        return n

    # ------------------------------------------------------------ write-through -
    def write_through(self, key: str, value: Any, ttl_s: int | None = None) -> int:
        """Ghi thẳng xuống cache (write-through) — dùng cho dự báo sau khi job ghi MongoDB."""
        return self.set(key, value, ttl_s)

    def set_pointer(self, pointer: str, target: str, ttl_s: int | None = None) -> int:
        """Đổi con trỏ ``latest`` (ví dụ ``fc:PHU_AN:latest`` → ``fc:PHU_AN:B_KTTV:2026-11-24``)."""
        return self.set(pointer, target, ttl_s)

    # ------------------------------------------------------------ chống stampede -
    def acquire_rebuild_lock(self, key: str) -> str | None:
        """Cố lấy khoá dựng lại ``lock:rebuild:{key}``. Trả token nếu thành công, ``None`` nếu bận."""
        import uuid
        token = uuid.uuid4().hex
        ok = self.redis.set(f"lock:rebuild:{key}", token, nx=True, px=self.rebuild_lock_ttl_ms)
        return token if ok else None

    def release_rebuild_lock(self, key: str, token: str) -> bool:
        """Nhả khoá dựng lại (Lua so token: chỉ người giữ mới xoá được)."""
        script = ("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) "
                  "else return 0 end")
        try:
            return bool(self.redis.eval(script, 1, f"lock:rebuild:{key}", token))
        except Exception:  # noqa: BLE001
            return False

    def get_or_compute(self, key: str, kind: str, compute: Callable[[], Any], *, ttl_s: int | None = None,
                       max_wait: int | None = None) -> Any:
        """Cache-aside có chống stampede.

        - Đọc cache → có thì trả ngay.
        - Thử lấy ``lock:rebuild:{key}``: được thì tính, ghi cache, trả.
        - Không được khoá: đọc bản ``stale:``; có thì trả bản cũ; không có thì chờ (tối đa
          ``max_wait`` lần, mỗi lần 50 ms) rồi tự tính.
        - Redis lỗi (hoặc key ``nocache:``) → gọi thẳng ``compute()``, không chờ, không ghi cache.
        """
        if self.is_nocache(key):
            return compute()
        try:
            raw = self.redis.get(key)
        except Exception as e:  # noqa: BLE001 - Redis chết: bỏ qua cache, tính trực tiếp
            self._warn("đọc", key, e)
            return compute()
        value = decode_value(raw)
        if value is not None:
            self._inc("hits", CacheStats.kind_of(key))
            return value
        self._inc("misses", CacheStats.kind_of(key))
        try:
            token = self.acquire_rebuild_lock(key)
        except Exception as e:  # noqa: BLE001
            self._warn("lấy khoá dựng lại", key, e)
            return compute()
        if token is not None:
            try:
                value = compute()
                self.set(key, value, ttl_s if ttl_s is not None else self.ttl_s.get(kind))
                self._inc("rebuilds", CacheStats.kind_of(key))
                return value
            finally:
                self.release_rebuild_lock(key, token)
        stale = self.get_stale(key)
        if stale is not None:
            return stale
        limit = self.max_wait if max_wait is None else max_wait
        for _ in range(limit):
            time.sleep(0.05)
            value = self.get(key)
            if value is not None:
                return value
            stale = self.get_stale(key)
            if stale is not None:
                return stale
        # hết chờ: tự tính (chấp nhận tính lại nhiều lần hơn để không treo request)
        value = compute()
        self.set(key, value, ttl_s if ttl_s is not None else self.ttl_s.get(kind))
        return value

    # ------------------------------------------------------------ thống kê -------
    def _kind_ttl(self, key: str) -> str:
        """Loại của key dùng để tra TTL mặc định trong ``ttl_s``."""
        return CacheStats.kind_of(key)

    def _inc(self, field_name: str, kind: str) -> None:
        try:
            self.redis.hincrby(STATS_KEY, f"{field_name}:{kind}", 1)
        except Exception:  # noqa: BLE001 - thống kê không được làm hỏng request
            pass

    def stats(self) -> dict[str, dict[str, int]]:
        """Đọc bộ đếm hit/miss theo loại key."""
        out: dict[str, dict[str, int]] = {"hits": {}, "misses": {}, "stale_hits": {}, "rebuilds": {}}
        try:
            raw = self.redis.hgetall(STATS_KEY) or {}
        except Exception:  # noqa: BLE001
            return out
        for k, v in raw.items():
            if isinstance(k, bytes):
                k = k.decode("utf-8")
            name, _, kind = k.partition(":")
            out.setdefault(name, {})[kind or "unknown"] = int(v)
        return out

    def reset_stats(self) -> None:
        try:
            self.redis.delete(STATS_KEY)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ chẩn đoán -----
    def ping(self) -> bool:
        """Kiểm tra kết nối Redis (không ném lỗi)."""
        try:
            return bool(self.redis.ping())
        except Exception:  # noqa: BLE001
            return False

    def info(self) -> dict:
        """Thông tin nhẹ về cache: số khoá đang có, TTL mặc định theo loại, cấu hình nén."""
        info: dict = {
            "ttl_s": dict(self.ttl_s),
            "jitter": self.jitter,
            "stale_factor": self.stale_factor,
            "compress_threshold_bytes": self.compress_threshold_bytes,
            "max_wait": self.max_wait,
            "rebuild_lock_ttl_ms": self.rebuild_lock_ttl_ms,
            "keys": {},
        }
        for kind, ttl in self.ttl_s.items():
            info["keys"][kind] = {"ttl_s": ttl}
        try:
            info["redis_keys"] = int(self.redis.dbsize() or 0)
            raw = self.redis.info("memory") or {}
            info["redis_memory"] = {k: raw.get(k) for k in
                                    ("used_memory_human", "used_memory_peak_human", "maxmemory_human")}
        except Exception:  # noqa: BLE001 - chẩn đoán không được làm hỏng request
            info["redis_keys"] = None
            info["redis_memory"] = None
        return info


class AsyncCache:
    """Bọc ``Cache`` cho FastAPI: các lệnh Redis chạy trong thread để không chặn event loop."""

    def __init__(self, cache: Cache):
        self._c = cache

    async def _run(self, fn: Callable[[], Any]) -> Any:
        return await asyncio.to_thread(fn)

    async def get(self, key: str) -> Any:
        return await self._run(lambda: self._c.get(key))

    async def get_stale(self, key: str) -> Any:
        return await self._run(lambda: self._c.get_stale(key))

    async def set(self, key: str, value: Any, ttl_s: int | None = None) -> int:
        return await self._run(lambda: self._c.set(key, value, ttl_s))

    async def delete(self, *keys: str) -> int:
        return await self._run(lambda: self._c.delete(*keys))

    async def delete_pattern(self, pattern: str) -> int:
        return await self._run(lambda: self._c.delete_pattern(pattern))

    async def version(self, coll: str) -> int:
        return await self._run(lambda: self._c.version(coll))

    async def bump_version(self, coll: str) -> int:
        return await self._run(lambda: self._c.bump_version(coll))

    async def versioned_key(self, prefix: str, coll: str, *rest: Any) -> str:
        return await self._run(lambda: self._c.versioned_key(prefix, coll, *rest))

    async def write_through(self, key: str, value: Any, ttl_s: int | None = None) -> int:
        return await self._run(lambda: self._c.write_through(key, value, ttl_s))

    async def set_pointer(self, pointer: str, target: str, ttl_s: int | None = None) -> int:
        return await self._run(lambda: self._c.set_pointer(pointer, target, ttl_s))

    async def get_or_compute(self, key: str, kind: str, compute: Callable[[], Any], *,
                            ttl_s: int | None = None, max_wait: int | None = None) -> Any:
        """Bản async của ``Cache.get_or_compute`` (chạy trong thread, cùng cách xử lý khi Redis lỗi)."""
        return await self._run(lambda: self._c.get_or_compute(key, kind, compute, ttl_s=ttl_s,
                                                              max_wait=max_wait))

    async def stats(self) -> dict[str, dict[str, int]]:
        return await self._run(self._c.stats)

    async def reset_stats(self) -> None:
        await self._run(self._c.reset_stats)

    async def ping(self) -> bool:
        return await self._run(self._c.ping)

    async def info(self) -> dict:
        return await self._run(self._c.info)


def build_cache(redis_client: Any, cfg: Any = None) -> Cache:
    """Tạo ``Cache`` từ cấu hình ``vngis.config`` (``cfg.cache``)."""
    if cfg is None:
        from .config import get_config
        cfg = get_config()
    c = cfg.cache
    return Cache(redis_client, ttl_s=c.ttl_s, jitter=c.jitter, stale_factor=c.stale_factor,
                 compress_threshold_bytes=c.compress_threshold_bytes, max_wait=c.max_wait,
                 rebuild_lock_ttl_ms=c.rebuild_lock_ttl_ms)


ComputeFn = Callable[[], Awaitable[Any]]
