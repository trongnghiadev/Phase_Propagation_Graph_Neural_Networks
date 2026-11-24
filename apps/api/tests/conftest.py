"""Fixture dùng chung cho test của `apps/api`.

Nguyên tắc: test **không** được cần MongoDB/Redis thật. Dùng:
- `mongomock` cho MongoDB;
- `fakeredis` cho Redis.

**Về Lua (`EVAL`):** `fakeredis` chỉ hỗ trợ `EVAL` khi có thêm `lupa` (cần trình biên dịch
C — không có sẵn trên mọi máy). Vì vậy fixture `fake_redis` cài một **shim** `EVAL` chỉ
hỗ trợ đúng hai script mà dự án dùng (`vngis.locks.RELEASE` và `vngis.locks.EXTEND`) —
cùng ngữ nghĩa với Lua: so token, rồi `DEL` / `PEXPIRE`. Khi `lupa` có sẵn, shim không
được cài để kiểm thử vẫn dùng Lua thật.

Các test có nhãn `@pytest.mark.integration` được bỏ qua nếu thiếu `MONGO_URI`/`REDIS_URL`
thật (chạy: `pytest -m integration`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

# Cho phép chạy test mà không cần cài đặt package: thêm `src` vào `sys.path`.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _install_lua_shim(client: Any) -> bool:
    """Cài `EVAL` tối giảu cho fakeredis khi không có `lupa`.

    Trả về True nếu đã cài shim, False nếu client đã hỗ trợ Lua natively.
    """
    from vngis.locks import EXTEND, RELEASE

    try:
        client.eval("return 1", 0)
        return False          # đã hỗ trợ Lua thật → không cài shim
    except Exception:  # noqa: BLE001
        pass

    def _same(cur: Any, token: Any) -> bool:
        if cur is None:
            return False
        want = token.encode() if isinstance(token, str) else token
        return cur == want

    def _eval(script: str, numkeys: int, *args: Any) -> Any:
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if script == RELEASE:
            key = keys[0]
            if _same(client.get(key), argv[0]):
                return int(client.delete(key) or 0)
            return 0
        if script == EXTEND:
            key = keys[0]
            if _same(client.get(key), argv[0]):
                return int(bool(client.pexpire(key, int(argv[1]))))
            return 0
        raise NotImplementedError(f"script Lua chưa được hỗ trợ trong shim: {script!r}")

    client.eval = _eval  # type: ignore[method-assign]
    return True


@pytest.fixture(scope="session", autouse=True)
def _fast_argon2() -> None:
    """Hạ tham số argon2 cho test (mật khẩu test không phải bí mật thật).

    Phải đặt **trước** khi `api.auth.password` khởi tạo hasher, nên dùng biến môi
    trường ở cấp session và xoá cache module.
    """
    os.environ["VNGIS_ARGON2_FAST"] = "1"
    import api.auth.password as pwd_mod
    pwd_mod._hasher = None  # type: ignore[attr-defined]
    yield
    os.environ.pop("VNGIS_ARGON2_FAST", None)


@pytest.fixture(scope="session", autouse=True)
def _quiet_logging() -> None:
    """Tắt log DEBUG của pymongo/uvicorn (rất ồn và làm test chậm)."""
    import logging
    for name in ("pymongo", "pymongo.topology", "pymongo.connection", "pymongo.server",
                 "pymongo.monitoring", "httpx", "httpcore", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def fake_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """MongoDB cho test.

    Mặc định dùng **mongomock** (không phụ thuộc dịch vụ ngoài) đã được bổ sung toán tử
    `$bit` (xem `tests/mongomock_bit.py`) — cần cho cập nhật quyền nguyên tử (GĐ7.3).

    Đặt `MONGO_TEST_URI` để chạy test với MongoDB **thật** (khuyến nghị khi chạy CI
    có sẵn dịch vụ): `MONGO_TEST_URI=mongodb://localhost:27017 pytest`.
    """
    uri = os.getenv("MONGO_TEST_URI", "").strip()
    db_name = os.getenv("MONGO_TEST_DB", "vngis_gd7_test")
    if uri:
        from pymongo import MongoClient
        client = MongoClient(uri, serverSelectionTimeoutMS=3000, tz_aware=True)
        client.admin.command("ping")
        db = client[db_name]
        for coll in list(db.list_collection_names()):
            db[coll].drop()
        yield db
        for coll in list(db.list_collection_names()):
            db[coll].drop()
        client.close()
        return

    import mongomock
    from mongomock_bit import patch_mongomock_bit
    patch_mongomock_bit()
    # mongomock không có lệnh `hello` → `vngis.qc` chạy nhánh một node (audit pending → applied).
    import vngis.qc
    monkeypatch.setattr(vngis.qc, "_supports_txn", lambda db: False)
    client = mongomock.MongoClient(tz_aware=True)
    db = client["vngis_test"]
    yield db
    client.close()


@pytest.fixture()
def requires_bit(fake_db: Any) -> Any:
    """Bỏ qua test nếu backend không hỗ trợ toán tử `$bit`."""
    try:
        fake_db["_bit_probe"].insert_one({"_id": "probe", "m": 0})
        fake_db["_bit_probe"].update_one({"_id": "probe"}, {"$bit": {"m": {"or": 4}}})
        val = fake_db["_bit_probe"].find_one({"_id": "probe"})["m"]
        fake_db["_bit_probe"].drop()
        if int(val) != 4:
            pytest.skip("backend MongoDB không hỗ trợ `$bit` đúng như mongomock")
    except pytest.skip.Exception:  # pragma: no cover
        raise
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"backend MongoDB không hỗ trợ `$bit`: {e}")
    return fake_db


@pytest.fixture()
def fake_redis() -> Iterator[Any]:
    """Redis giả (fakeredis) — có shim `EVAL` nếu không có `lupa`."""
    fakeredis = pytest.importorskip("fakeredis")
    r = fakeredis.FakeStrictRedis()
    _install_lua_shim(r)
    yield r
    try:
        r.close()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def fake_cache(fake_redis: Any):
    """Cache `vngis.cache.Cache` trên Redis giả."""
    from vngis.cache import Cache
    return Cache(fake_redis, ttl_s={"stations": 30, "tide_extremes": 60, "stats": 30,
                                    "features": 120, "admin_units": 300, "reservoir_daily": 120,
                                    "warm": 60, "default": 60},
                jitter=0.0, stale_factor=4, compress_threshold_bytes=512,
                max_wait=3, rebuild_lock_ttl_ms=2000)


@pytest.fixture()
def fake_lock(fake_redis: Any):
    """Backend khoá đơn node trên Redis giả."""
    from vngis.locks import SingleLock
    lock = SingleLock([("127.0.0.1", 6379)], timeout_s=0.05)
    # Gắn client giả để không cần Redis thật.
    lock.client = fake_redis
    lock.clients = [fake_redis]
    yield lock
    try:
        lock.close()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def cfg(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Cấu hình `vngis.config` cho test (không đọc file môi trường thật)."""
    from vngis.config import get_config
    monkeypatch.setenv("AUTH_SECRET", "test-secret-khong-dung-cho-production-0123456789")
    monkeypatch.setenv("LOCK_BACKEND", "single")
    return get_config()


@pytest.fixture()
def client(fake_db: Any, fake_cache: Any, fake_lock: Any, cfg: Any,
           monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """TestClient FastAPI với state được ghi đè bằng các bản giả."""
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.state import state

    monkeypatch.setenv("AUTH_SECRET", "test-secret-khong-dung-cho-production-0123456789")
    state.override(db=fake_db, cache=fake_cache, lock=fake_lock, redis=fake_cache.redis)
    # Tắt giới hạn tần suất mặc định; test 429 sẽ bật lại tường minh.
    # `Config` của `vngis.config` là model **đóng băng** (frozen) → dùng `model_copy`.
    from vngis.config import get_config
    try:
        base = get_config()
        state.override(cfg=base.model_copy(update={
            "rate_limit": base.rate_limit.model_copy(update={"enabled": False})}))
    except Exception:  # noqa: BLE001
        pass
    app = create_app()
    with TestClient(app) as c:
        yield c
    state.reset()
