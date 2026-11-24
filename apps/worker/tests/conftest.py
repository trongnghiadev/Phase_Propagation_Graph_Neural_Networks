"""Fixture dùng chung cho test của `apps/worker`.

Worker không phục vụ HTTP nên không có `request_id`; chỉ cần MongoDB giả, Redis giả
(có shim `EVAL` cho các script Lua của `vngis.locks`) và backend khoá.
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

_VNGIS = Path(__file__).resolve().parents[3] / "packages" / "vngis" / "src"
if str(_VNGIS) not in sys.path:
    sys.path.insert(0, str(_VNGIS))

# Bản vá `$bit` cho mongomock (dùng chung với `apps/api/tests/mongomock_bit.py`).
_API_TESTS = Path(__file__).resolve().parents[2] / "api" / "tests"
if str(_API_TESTS) not in sys.path:
    sys.path.insert(0, str(_API_TESTS))


def _install_lua_shim(client: Any) -> None:
    """Cài `EVAL` tối giản cho fakeredis khi không có `lupa`.

    Hỗ trợ đúng hai script của `vngis.locks`: `RELEASE` và `EXTEND`.
    """
    from vngis.locks import EXTEND, RELEASE

    try:
        client.eval("return 1", 0)
        return
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


@pytest.fixture(scope="session", autouse=True)
def _quiet_logging() -> None:
    """Tắt log DEBUG của pymongo (rất ồn và làm test chậm)."""
    import logging
    for name in ("pymongo", "pymongo.topology", "pymongo.connection", "pymongo.server",
                 "pymongo.monitoring", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


@pytest.fixture()
def fake_db() -> Iterator[Any]:
    """MongoDB giả (mongomock, đã bổ sung `$bit`)."""
    import mongomock
    from mongomock_bit import patch_mongomock_bit
    patch_mongomock_bit()
    client = mongomock.MongoClient()
    db = client["vngis_worker_test"]
    yield db
    client.close()


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
def fake_lock(fake_redis: Any) -> Iterator[Any]:
    """Backend khoá đơn node trên Redis giả."""
    from vngis.locks import SingleLock
    lock = SingleLock([("127.0.0.1", 6379)], timeout_s=0.05)
    lock.client = fake_redis
    lock.clients = [fake_redis]
    yield lock
    try:
        lock.close()
    except Exception:  # noqa: BLE001
        pass
