"""Tạo kết nối MongoDB và Redis từ cấu hình (``vngis.config``).

Import module này KHÔNG kết nối gì; chỉ khi gọi hàm mới tạo client. Bản thân ``MongoClient``,
``redis.Redis`` và ``Sentinel`` cũng chỉ mở kết nối ở lệnh đầu tiên.

    from vngis.db import get_db, collection, redis_cache, redlock
    db = get_db()                                   # cấu hình theo APP_ENV
    stations = collection(db, "stations")
    r = redis_cache()                               # master qua Sentinel hoặc một node
    lock = redlock()                                # vngis.locks.Redlock trên các node khóa
"""
from __future__ import annotations

from typing import Any

import redis
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from redis.sentinel import Sentinel

from .cache import Cache, build_cache
from .config import Config, ConfigError, get_config
from .locks import Redlock, build_lock, parse_nodes


def _cfg(cfg: Config | None) -> Config:
    return cfg if cfg is not None else get_config()


# ---------------------------------------------------------------- MongoDB ---------
def mongo_client_kwargs(cfg: Config | None = None) -> dict[str, Any]:
    """Tham số truyền cho ``MongoClient`` theo kiểu kết nối (một node / replica set / mongos)."""
    m = _cfg(cfg).mongo
    kw: dict[str, Any] = {"appname": m.app_name, "serverSelectionTimeoutMS": m.server_selection_timeout_ms}
    topo = m.topology
    if topo == "single" and not m.uri.startswith("mongodb+srv://") and "directConnection" not in m.uri \
            and "," not in m.uri.split("/")[2]:
        # Một node: nối thẳng, không dò replica set (tránh lỗi tên host nội bộ khi node là replica set 1 thành viên).
        kw["directConnection"] = True
    if m.write_concern is not None:
        w = m.write_concern
        kw["w"] = int(w) if w.isdigit() else w
    if m.read_concern is not None:
        kw["readConcernLevel"] = m.read_concern
    return kw


def mongo_client(cfg: Config | None = None, **overrides: Any) -> MongoClient:
    """``MongoClient`` từ cấu hình; ``overrides`` ghi đè tham số (ví dụ ``tz_aware=True``)."""
    c = _cfg(cfg)
    return MongoClient(c.mongo.uri, **{**mongo_client_kwargs(c), **overrides})


def get_db(cfg: Config | None = None, client: MongoClient | None = None) -> Database:
    """Database ``mongo.db`` (mặc định ``vietnam_gis``)."""
    c = _cfg(cfg)
    return (client or mongo_client(c))[c.mongo.db]


def collection(db: Database, name: str, cfg: Config | None = None) -> Collection:
    """Collection theo tên logic trong ``mongo.collections`` (ví dụ ``"stations"``)."""
    cols = _cfg(cfg).mongo.collections
    if name not in type(cols).model_fields:
        raise KeyError(f"collection '{name}' không có trong mongo.collections")
    return db[getattr(cols, name)]


# ---------------------------------------------------------------- Redis -----------
def redis_sentinel(cfg: Config | None = None) -> Sentinel:
    """``Sentinel`` cho Redis cache; cần ``redis.cache.mode = sentinel``."""
    rc = _cfg(cfg).redis.cache
    if rc.mode != "sentinel":
        raise ConfigError("redis.cache.mode không phải sentinel (đặt REDIS_SENTINELS hoặc dùng APP_ENV=cluster)")
    return Sentinel(parse_nodes(",".join(rc.sentinels)), socket_timeout=rc.socket_timeout_s)


def redis_cache(cfg: Config | None = None, readonly: bool = False) -> redis.Redis:
    """Client Redis cache: master (hoặc replica khi ``readonly``) qua Sentinel, hoặc một node theo ``url``."""
    rc = _cfg(cfg).redis.cache
    if rc.mode == "sentinel":
        s = redis_sentinel(cfg)
        return (s.slave_for if readonly else s.master_for)(rc.master, socket_timeout=rc.socket_timeout_s)
    return redis.Redis.from_url(rc.url, socket_timeout=rc.socket_timeout_s)


def redlock(cfg: Config | None = None) -> Redlock:
    """Khoá phân tán theo cấu hình: ``redlock`` (N node) hoặc ``single`` (một node)."""
    lc = _cfg(cfg).redis.lock
    if not lc.nodes:
        raise ConfigError("redis.lock.nodes rỗng: đặt REDIS_LOCK_NODES hoặc khai báo trong config/env/*.yaml")
    return build_lock(lc.backend, parse_nodes(",".join(lc.nodes)), lc.timeout_s)


def cache(cfg: Config | None = None, readonly: bool = False) -> "Cache":
    """Cache Redis theo cấu hình (``vngis.cache.Cache``) trên master Sentinel hoặc một node."""
    return build_cache(redis_cache(cfg, readonly=readonly), cfg)
