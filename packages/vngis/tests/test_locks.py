"""Test Redlock bằng client Redis giả (không cần Redis thật)."""
import pytest
import redis

from vngis import locks
from vngis.locks import EXTEND, RELEASE, Lock, Redlock, next_fence, parse_nodes


class FakeRedis:
    """Một node Redis tối giản: SET NX PX, script RELEASE/EXTEND, PING. ``down=True`` giả lập node chết."""

    def __init__(self, host="fake", down=False, taken=False):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.down = down
        self.taken = taken          # khóa đã bị tiến trình khác giữ trên node này
        self.connection_pool = type("Pool", (), {"connection_kwargs": {"host": host}})()

    def _check(self):
        if self.down:
            raise redis.ConnectionError("node down")

    def set(self, key, value, nx=False, px=None):
        self._check()
        if self.taken or (nx and key in self.store):
            return None
        self.store[key] = value
        self.ttl[key] = px
        return True

    def eval(self, script, numkeys, key, token, *args):
        self._check()
        if self.store.get(key) != token:
            return 0
        if script == RELEASE:
            del self.store[key]
            return 1
        if script == EXTEND:
            self.ttl[key] = int(args[0])
            return 1
        raise AssertionError("script lạ")

    def ping(self):
        self._check()
        return True


def make(*nodes: FakeRedis) -> Redlock:
    rl = Redlock([("localhost", 6379 + i) for i in range(len(nodes))])   # không kết nối khi khởi tạo
    rl.clients = list(nodes)
    return rl


# ---------------------------------------------------------------- parse_nodes ------
def test_parse_nodes():
    assert parse_nodes("host1:6379,host2:6380") == [("host1", 6379), ("host2", 6380)]


def test_parse_nodes_khoang_trang_va_mot_node():
    assert parse_nodes(" redis-lock-1:6379 , redis-lock-2:6379") == [("redis-lock-1", 6379), ("redis-lock-2", 6379)]
    assert parse_nodes("localhost:6391") == [("localhost", 6391)]


def test_parse_nodes_ipv6_lay_dau_hai_cham_cuoi():
    assert parse_nodes("::1:6379") == [("::1", 6379)]


@pytest.mark.parametrize("bad", ["host", "host:abc", ""])
def test_parse_nodes_loi(bad):
    with pytest.raises(ValueError):
        parse_nodes(bad)


# ---------------------------------------------------------------- quorum -----------
@pytest.mark.parametrize("n, q", [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3)])
def test_quorum(n, q):
    assert Redlock([("localhost", 6379 + i) for i in range(n)]).quorum == q


def test_acquire_du_da_so():
    a, b, c = FakeRedis("a"), FakeRedis("b", down=True), FakeRedis("c")
    lock, votes = make(a, b, c).acquire("job:x", ttl_ms=10_000)
    assert votes == 2 and lock is not None
    assert lock.votes == 2 and 0 < lock.validity_ms < 10_000
    assert a.store["job:x"] == lock.token == c.store["job:x"]
    assert a.ttl["job:x"] == 10_000


def test_acquire_thieu_da_so_thi_nha_khoa_da_lay():
    a, b, c = FakeRedis("a"), FakeRedis("b", taken=True), FakeRedis("c", down=True)
    lock, votes = make(a, b, c).acquire("job:x", ttl_ms=10_000)
    assert lock is None and votes == 1
    assert "job:x" not in a.store          # node đã cấp phiếu được giải phóng ngay


def test_acquire_het_hieu_luc_thi_that_bai(monkeypatch):
    t = iter([0.0, 5.0])                   # giả lập lấy khóa mất 5 s > TTL 1 s
    monkeypatch.setattr(locks.time, "monotonic", lambda: next(t))
    a, b, c = FakeRedis(), FakeRedis(), FakeRedis()
    lock, votes = make(a, b, c).acquire("k", ttl_ms=1000)
    assert lock is None and votes == 3
    assert not a.store and not b.store and not c.store


def test_hai_tien_trinh_khong_cung_giu_khoa():
    nodes = [FakeRedis(), FakeRedis(), FakeRedis()]
    first, _ = make(*nodes).acquire("k", 10_000)
    second, votes = make(*nodes).acquire("k", 10_000)
    assert first is not None and second is None and votes == 0


def test_release_chi_xoa_dung_token():
    nodes = [FakeRedis(), FakeRedis(), FakeRedis()]
    rl = make(*nodes)
    lock, _ = rl.acquire("k", 10_000)
    rl.release(Lock("k", "token-khac", 3, 0))
    assert all("k" in n.store for n in nodes)
    rl.release(lock)
    assert all("k" not in n.store for n in nodes)


def test_extend_can_da_so():
    a, b, c = FakeRedis(), FakeRedis(), FakeRedis()
    rl = make(a, b, c)
    lock, _ = rl.acquire("k", 10_000)
    assert rl.extend(lock, 20_000) is True and a.ttl["k"] == 20_000
    del a.store["k"]
    b.down = True                          # chỉ còn c giữ token → dưới đa số
    assert rl.extend(lock, 20_000) is False


def test_ping():
    rl = make(FakeRedis("r1"), FakeRedis("r2", down=True))
    assert rl.ping() == {"r1": True, "r2": False}


# ---------------------------------------------------------------- fencing token ----
class FakeFences:
    def __init__(self):
        self.docs = {}
        self.calls = []

    def find_one_and_update(self, flt, update, upsert, return_document):
        self.calls.append((flt, update, upsert))
        doc = self.docs.setdefault(flt["_id"], {"_id": flt["_id"], "fence": 0})
        doc["fence"] += update["$inc"]["fence"]
        return dict(doc)


def test_next_fence_tang_dan_theo_tai_nguyen():
    db = type("DB", (), {"lock_fences": FakeFences()})()
    assert [next_fence(db, "reload"), next_fence(db, "reload"), next_fence(db, "other")] == [1, 2, 1]
    assert db.lock_fences.calls[0] == ({"_id": "reload"}, {"$inc": {"fence": 1}}, True)
