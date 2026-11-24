"""Khóa phân tán dùng chung cho API, worker và job (đề cương 5.6, mục GĐ7.5).

Hai backend, chọn bằng ``LOCK_BACKEND``:

- ``redlock`` (mặc định): Redlock trên N node Redis **độc lập**; khóa thành công khi đa số
  (N//2 + 1) node chấp nhận và thời gian còn hiệu lực > 0.
- ``single``: một node Redis (dùng để so sánh ở E17/E19, và khi cụm chỉ có một node khóa).

Cả hai dùng chung:
- **Lua release/extend**: so token trong script, chỉ người giữ khóa mới xoá/gia hạn được (nguyên tử).
- **Watchdog**: gia hạn mỗi ``TTL/3``; gia hạn thất bại → đặt cờ ``lost`` để job ngừng ghi.
- **Fencing token** lấy từ MongoDB (``next_fence``), vì Redlock không tự cung cấp số tăng dần
  đáng tin cậy. Mọi lệnh ghi được bảo vệ phải kèm điều kiện ``last_fence < fence``.

    from vngis.db import get_db, redlock
    from vngis.locks import LockGuard
    guard = LockGuard(redlock(), db=get_db(), resource="reload:bulletins", ttl_ms=120_000)
    with guard:
        guard.write({"last_fence": {"$lt": guard.fence}}, {"last_fence": guard.fence, ...})
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import redis
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

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
    #: Fencing token (từ MongoDB). 0 nếu không lấy được.
    fence: int = 0
    #: Đã mất quyền giữ khóa (watchdog gia hạn thất bại) → phải ngừng ghi.
    lost: bool = False
    #: TTL đầy đủ của khoá (ms), dùng cho nhịp watchdog.
    ttl_ms: int = 0
    #: Tài nguyên mà khoá bảo vệ (dùng để tra `lock_fences`).
    resource: str = ""

    def mark_lost(self) -> None:
        self.lost = True


class Redlock:
    """Redlock trên N node Redis độc lập."""

    def __init__(self, nodes: list[tuple[str, int]], timeout_s: float = 0.05):
        self.clients = [redis.Redis(host=h, port=p, socket_timeout=timeout_s, socket_connect_timeout=timeout_s)
                        for h, p in nodes]
        self.quorum = len(self.clients) // 2 + 1
        self.backend = "redlock"

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
            return Lock(key, token, votes, validity, ttl_ms=ttl_ms), votes
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

    def list_locks(self, limit: int = 200, pattern: str = "lock:*") -> list[dict]:
        """Liệt kê các khoá đang giữ trên node đầu tiên (quét bằng SCAN).

        Chỉ quét một node là đủ để hiển thị cho quản trị; quyền quyết định vẫn dựa trên
        số phiếu của đa số node.
        """
        if not self.clients:
            return []
        out: list[dict] = []
        try:
            cursor = 0
            while True:
                cursor, batch = self.clients[0].scan(cursor=cursor, match=pattern, count=200)
                for k in batch:
                    if isinstance(k, bytes):
                        k = k.decode("utf-8")
                    try:
                        ttl = int(self.clients[0].pttl(k))
                    except redis.RedisError:
                        ttl = -1
                    out.append({"key": k, "ttl_ms": ttl})
                    if len(out) >= limit:
                        return out
                if cursor == 0:
                    break
        except redis.RedisError:
            return out
        return out

    def close(self) -> None:
        for c in self.clients:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


class SingleLock:
    """Khóa trên MỘT node Redis. Cùng ngữ nghĩa với ``Redlock`` để so sánh công bằng (E17/E19)."""

    backend = "single"

    def __init__(self, nodes: list[tuple[str, int]], timeout_s: float = 0.05):
        if not nodes:
            raise ValueError("SingleLock cần ít nhất một node Redis")
        self.client = redis.Redis(host=nodes[0][0], port=nodes[0][1],
                                  socket_timeout=timeout_s, socket_connect_timeout=timeout_s)
        self.clients = [self.client]

    def acquire(self, key: str, ttl_ms: int) -> tuple[Lock | None, int]:
        token = uuid.uuid4().hex
        try:
            ok = bool(self.client.set(key, token, nx=True, px=ttl_ms))
        except redis.RedisError:
            return None, 0
        if ok:
            return Lock(key, token, 1, float(ttl_ms), ttl_ms=ttl_ms), 1
        return None, 0

    def release(self, lock: Lock) -> None:
        try:
            self.client.eval(RELEASE, 1, lock.key, lock.token)
        except redis.RedisError:
            pass

    def extend(self, lock: Lock, ttl_ms: int) -> bool:
        try:
            return bool(self.client.eval(EXTEND, 1, lock.key, lock.token, ttl_ms))
        except redis.RedisError:
            return False

    def ping(self) -> dict[str, bool]:
        name = str(self.client.connection_pool.connection_kwargs["host"])
        try:
            return {name: bool(self.client.ping())}
        except redis.RedisError:
            return {name: False}

    def list_locks(self, limit: int = 200, pattern: str = "lock:*") -> list[dict]:
        """Liệt kê các khoá đang giữ (quét bằng SCAN) — dùng cho `GET /admin/locks`."""
        out: list[dict] = []
        try:
            cursor = 0
            while True:
                cursor, batch = self.client.scan(cursor=cursor, match=pattern, count=200)
                for k in batch:
                    if isinstance(k, bytes):
                        k = k.decode("utf-8")
                    try:
                        ttl = int(self.client.pttl(k))
                    except redis.RedisError:
                        ttl = -1
                    out.append({"key": k, "ttl_ms": ttl})
                    if len(out) >= limit:
                        return out
                if cursor == 0:
                    break
        except redis.RedisError:
            return out
        return out

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass


def build_lock(backend: str, nodes: list[tuple[str, int]], timeout_s: float = 0.05) -> Redlock | SingleLock:
    """Tạo khoá theo tên backend (``single`` | ``redlock``)."""
    if backend == "single":
        return SingleLock(nodes, timeout_s)
    if backend == "redlock":
        return Redlock(nodes, timeout_s)
    raise ValueError(f"backend khóa không hợp lệ: {backend!r} (cần 'single' hoặc 'redlock')")


def next_fence(db: Database, resource: str) -> int:
    """Số tăng dần cho mỗi lần lấy khóa (fencing token), lưu ở collection lock_fences.

    Dùng `find_one_and_update` với `upsert=True` để việc tăng bộ đếm là nguyên tử ở tầng
    CSDL (hai worker cùng lấy khoá vẫn nhận hai fence khác nhau).

    Giá trị trả về PHẢI lấy từ kết quả của chính lệnh `find_one_and_update` (ReturnDocument.AFTER):
    đọc lại bằng `find_one` sau đó KHÔNG an toàn — hai worker cùng tăng rồi cùng đọc sẽ nhận cùng
    một fence. Chỉ khi driver không trả document (một số bản `mongomock` trả `None` lúc vừa upsert)
    mới đọc lại; trường hợp đó chỉ có trong unit test.
    """
    doc = db.lock_fences.find_one_and_update(
        {"_id": resource}, {"$inc": {"fence": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    if not doc or "fence" not in doc:
        doc = db.lock_fences.find_one({"_id": resource}) or {}
    if "fence" not in doc:
        raise RuntimeError(f"không tăng được fencing token cho {resource!r}")
    return int(doc["fence"])


# ------------------------------------------------------------------ watchdog ------
class Watchdog:
    """Gia hạn khoá mỗi ``TTL/3`` ở thread riêng.

    Gia hạn thất bại (mất đa số node, hoặc token đã bị đổi) → đặt cờ ``lock.lost = True``.
    Job phải kiểm tra cờ này trước mỗi lệnh ghi và dừng nếu đã mất.
    """

    def __init__(self, backend: Redlock | SingleLock, lock: Lock, ttl_ms: int | None = None):
        self.backend = backend
        self.lock = lock
        self.ttl_ms = ttl_ms or lock.ttl_ms or 30_000
        self.interval_s = max(0.05, self.ttl_ms / 3000)   # TTL/3 tính bằng giây (ms / 1000 / 3)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.extends = 0
        self.failures = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=f"watchdog-{self.lock.key}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            if self.lock.lost:
                return
            try:
                ok = self.backend.extend(self.lock, self.ttl_ms)
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                self.extends += 1
            else:
                self.failures += 1
                self.lock.mark_lost()
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


# ------------------------------------------------------------------ guard --------
class LockGuard:
    """Giữ khoá trong một khối lệnh và gắn fencing token.

    Dùng như context manager::

        with LockGuard(backend, db, "reload:bulletins", ttl_ms=120_000) as g:
            g.write({"last_fence": {"$lt": g.fence}}, {"last_fence": g.fence, "state": "running"})

    ``write`` kiểm tra ``last_fence < fence`` (điều kiện fencing) và ném ``FenceRejected`` nếu
    không khớp, tức là một tiến trình khác đã ghi với fence lớn hơn → tiến trình này đã lỗi thời.
    """

    def __init__(self, backend: Redlock | SingleLock, db: Database | None = None, *, resource: str,
                 key: str | None = None, ttl_ms: int = 30_000, use_fence: bool = True):
        self.backend = backend
        self.db = db
        self.resource = resource
        self.key = key or f"lock:{resource}"
        self.ttl_ms = ttl_ms
        self.use_fence = use_fence
        self.lock: Lock | None = None
        self.watchdog: Watchdog | None = None
        self.votes = 0

    # -------------------------------------------------------------- vòng đời -----
    def acquire(self) -> Lock:
        """Lấy khoá; ném ``LockBusy`` nếu bận. Lấy fencing token từ MongoDB nếu có ``db``.

        Lấy fence là một lệnh MongoDB có thể chậm (failover, mạng); nếu nó mất lâu hơn thời gian hiệu lực
        của khoá thì khoá có thể ĐÃ hết hạn và tiến trình khác đã lấy được — trả fence lúc đó là sai.
        Vì vậy sau ``next_fence`` phải xác nhận vẫn giữ khoá: thời gian đã trôi (tính từ trước lệnh lấy khoá)
        còn nhỏ hơn ``validity_ms``, hoặc gia hạn (``extend``) thành công. Không xác nhận được → nhả khoá,
        ném ``LockLost``.
        """
        t0 = time.monotonic()
        lock, votes = self.backend.acquire(self.key, self.ttl_ms)
        self.votes = votes
        if lock is None:
            raise LockBusy(f"khóa '{self.key}' đang được giữ (votes={votes})")
        lock.resource = self.resource
        if self.use_fence and self.db is not None:
            try:
                lock.fence = next_fence(self.db, self.resource)
            except Exception:
                self.backend.release(lock)
                raise
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms >= lock.validity_ms and not self._extend_once(lock):
                self.backend.release(lock)
                raise LockLost(f"khóa '{self.key}' có thể đã hết hạn trong lúc lấy fencing token "
                               f"({elapsed_ms:.0f} ms ≥ validity {lock.validity_ms:.0f} ms) và gia hạn thất bại")
        self.lock = lock
        self.watchdog = Watchdog(self.backend, lock, self.ttl_ms)
        self.watchdog.start()
        return lock

    def _extend_once(self, lock: Lock) -> bool:
        try:
            return bool(self.backend.extend(lock, self.ttl_ms))
        except Exception:  # noqa: BLE001 - lỗi mạng = không xác nhận được
            return False

    def release(self) -> None:
        """Nhả khoá và dừng watchdog."""
        if self.watchdog is not None:
            self.watchdog.stop()
            self.watchdog = None
        if self.lock is not None:
            self.backend.release(self.lock)
            self.lock = None

    def __enter__(self) -> "LockGuard":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

    # -------------------------------------------------------------- kiểm tra -----
    @property
    def fence(self) -> int:
        return self.lock.fence if self.lock else 0

    @property
    def lost(self) -> bool:
        """Đã mất khoá (watchdog thất bại) → không được ghi nữa."""
        return bool(self.lock and self.lock.lost)

    def check(self) -> None:
        """Ném ``LockLost`` nếu đã mất khoá."""
        if self.lost:
            raise LockLost(f"đã mất khóa '{self.key}'; dừng ghi để không ghi đè tiến trình khác")

    def write(self, fence_filter: dict, update: dict, collection: str = "job_state",
              doc_id: str | None = None) -> Any:
        """Ghi có điều kiện fencing ``last_fence < fence``.

        `fence_filter` là phần điều kiện khác (nếu có); điều kiện fencing được tự thêm vào.

        **Không dùng `upsert=True`**: nếu bản ghi đã tồn tại với `last_fence >= fence` thì
        `upsert` sẽ cố chèn bản ghi mới và **bỏ qua** điều kiện fencing (đây chính là lỗi
        khiến tiến trình đã lỗi thời ghi đè được lên tiến trình mới). Thay vào đó:
        cập nhật không upsert; nếu không khớp thì thử chèn, và nếu trùng `_id` thì bị từ chối.
        """
        self.check()
        if self.db is None:
            raise RuntimeError("LockGuard cần db để ghi có điều kiện fencing")
        flt = dict(fence_filter)
        flt["last_fence"] = {"$lt": self.fence}
        doc_id = doc_id if doc_id is not None else self.resource
        key = {"_id": doc_id}
        payload = {**update, "last_fence": self.fence}
        res = self.db[collection].find_one_and_update(
            {**key, **flt}, {"$set": payload}, upsert=False, return_document=ReturnDocument.AFTER)
        if res is None:
            # Chưa có bản ghi → thử chèn bản mới (chỉ thành công nếu `_id` chưa tồn tại).
            # KHÔNG đọc lại bằng `find_one` sau khi chèn: giữa hai lệnh, tiến trình có fence lớn hơn có thể
            # đã ghi đè, và ta sẽ trả về (hoặc so) document của người khác. Document vừa chèn chính là kết quả.
            doc = {**key, **payload}
            try:
                self.db[collection].insert_one(dict(doc))
            except DuplicateKeyError:
                raise FenceRejected(
                    f"ghi bị từ chối do fencing (fence={self.fence}); bản ghi đã tồn tại với "
                    f"last_fence >= {self.fence}") from None
            return doc
        if res.get("last_fence", -1) != self.fence:
            raise FenceRejected(f"ghi bị từ chối do fencing (fence={self.fence}); "
                                f"tiến trình khác đã ghi với fence lớn hơn")
        return res


class LockError(RuntimeError):
    """Lỗi khóa chung."""


class LockBusy(LockError):
    """Khóa đang được giữ bởi tiến trình khác."""


class LockLost(LockError):
    """Đã mất khóa (watchdog gia hạn thất bại) → phải dừng ghi."""


class FenceRejected(LockError):
    """Lệnh ghi bị từ chối do điều kiện fencing token."""


@dataclass
class LockHandle:
    """Kết quả thô của một lần lấy khoá (dùng khi không cần context manager)."""
    lock: Lock | None
    votes: int
    backend: str = ""
    meta: dict = field(default_factory=dict)


def try_acquire(backend: Redlock | SingleLock, key: str, ttl_ms: int) -> LockHandle:
    """Thử lấy khoá, không ném lỗi; trả về ``LockHandle``."""
    lock, votes = backend.acquire(key, ttl_ms)
    return LockHandle(lock, votes, getattr(backend, "backend", ""))


def make_backend(cfg: Any = None) -> Redlock | SingleLock:
    """Tạo backend khoá theo cấu hình (``redis.lock.backend`` và ``redis.lock.nodes``)."""
    if cfg is None:
        from .config import get_config
        cfg = get_config()
    lc = cfg.redis.lock
    nodes = parse_nodes(",".join(lc.nodes))
    return build_lock(lc.backend, nodes, lc.timeout_s)


AcquireFn = Callable[[str, int], tuple[Lock | None, int]]
