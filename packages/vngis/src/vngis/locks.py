"""Khóa phân tán dùng chung cho API, worker và job (đề cương 5.6).

Redlock trên N node Redis độc lập: khóa thành công khi đa số (N//2 + 1) node chấp nhận
và thời gian còn hiệu lực > 0. Fencing token lấy từ MongoDB vì Redlock không tự cung cấp
số tăng dần đáng tin cậy.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import redis
from pymongo import ReturnDocument
from pymongo.database import Database

RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"
EXTEND = ("if redis.call('GET', KEYS[1]) == ARGV[1] then "
          "return redis.call('PEXPIRE', KEYS[1], ARGV[2]) else return 0 end")
CLOCK_DRIFT_FACTOR = 0.01   # 1% TTL + 2 ms


def parse_nodes(spec: str) -> list[tuple[str, int]]:
    """'host1:6379,host2:6379' -> [('host1', 6379), ('host2', 6379)]"""
    out = []
    for item in spec.split(","):
        host, port = item.strip().rsplit(":", 1)
        out.append((host, int(port)))
    return out


@dataclass
class Lock:
    key: str
    token: str
    votes: int
    validity_ms: float


class Redlock:
    def __init__(self, nodes: list[tuple[str, int]], timeout_s: float = 0.05):
        self.clients = [redis.Redis(host=h, port=p, socket_timeout=timeout_s, socket_connect_timeout=timeout_s)
                        for h, p in nodes]
        self.quorum = len(self.clients) // 2 + 1

    def acquire(self, key: str, ttl_ms: int) -> tuple[Lock | None, int]:
        """Trả (Lock, số phiếu) khi thành công, (None, số phiếu) khi thất bại."""
        token = uuid.uuid4().hex
        t0 = time.monotonic()
        votes = 0
        for c in self.clients:
            try:
                if c.set(key, token, nx=True, px=ttl_ms):
                    votes += 1
            except redis.RedisError:
                pass
        elapsed_ms = (time.monotonic() - t0) * 1000
        validity = ttl_ms - elapsed_ms - (ttl_ms * CLOCK_DRIFT_FACTOR + 2)
        if votes >= self.quorum and validity > 0:
            return Lock(key, token, votes, validity), votes
        self.release(Lock(key, token, votes, 0))
        return None, votes

    def release(self, lock: Lock) -> None:
        for c in self.clients:
            try:
                c.eval(RELEASE, 1, lock.key, lock.token)
            except redis.RedisError:
                pass

    def extend(self, lock: Lock, ttl_ms: int) -> bool:
        """Gia hạn (watchdog); chỉ thành công khi đa số node còn giữ đúng token."""
        ok = 0
        for c in self.clients:
            try:
                ok += int(bool(c.eval(EXTEND, 1, lock.key, lock.token, ttl_ms)))
            except redis.RedisError:
                pass
        return ok >= self.quorum

    def ping(self) -> dict[str, bool]:
        out = {}
        for c in self.clients:
            name = str(c.connection_pool.connection_kwargs["host"])
            try:
                out[name] = bool(c.ping())
            except redis.RedisError:
                out[name] = False
        return out


def next_fence(db: Database, resource: str) -> int:
    """Số tăng dần cho mỗi lần lấy khóa (fencing token), lưu ở collection lock_fences."""
    doc = db.lock_fences.find_one_and_update(
        {"_id": resource}, {"$inc": {"fence": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    return doc["fence"]
