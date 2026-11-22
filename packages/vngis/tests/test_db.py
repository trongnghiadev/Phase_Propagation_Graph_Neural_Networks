"""Tạo client từ cấu hình; không cần MongoDB/Redis thật (client chỉ kết nối ở lệnh đầu tiên)."""
import pytest
import redis

from vngis import db as vdb
from vngis.config import ConfigError, load_config
from vngis.locks import Redlock


@pytest.fixture
def cfgs(repo_config_dir):
    return {e: load_config(env=e, config_dir=repo_config_dir, environ={}) for e in ("dev", "test", "cluster")}


def test_import_khong_ket_noi():
    import importlib
    importlib.reload(vdb)        # không có lỗi kết nối dù không có MongoDB/Redis


def test_kwargs_mot_node(cfgs):
    kw = vdb.mongo_client_kwargs(cfgs["dev"])
    assert kw["directConnection"] is True and kw["serverSelectionTimeoutMS"] == 5000
    assert "w" not in kw and "readConcernLevel" not in kw
    assert vdb.mongo_client_kwargs(cfgs["test"])["serverSelectionTimeoutMS"] == 1000


def test_kwargs_replica_set(cfgs):
    kw = vdb.mongo_client_kwargs(cfgs["cluster"])
    assert "directConnection" not in kw
    assert kw["w"] == "majority" and kw["readConcernLevel"] == "majority"


def test_kwargs_mongos(repo_config_dir):
    env = {"MONGO_URI": "mongodb://localhost:27200", "MONGO_MODE": "mongos"}
    cfg = load_config(env="cluster", config_dir=repo_config_dir, environ=env)
    assert cfg.mongo.topology == "mongos"
    kw = vdb.mongo_client_kwargs(cfg)
    assert "directConnection" not in kw and kw["w"] == "majority"


def test_get_db_va_collection(cfgs):
    client = vdb.mongo_client(cfgs["test"], connect=False)
    try:
        d = vdb.get_db(cfgs["test"], client=client)
        assert d.name == "vietnam_gis_test"
        assert vdb.collection(d, "stations", cfgs["test"]).name == "stations"
        with pytest.raises(KeyError):
            vdb.collection(d, "khong_co", cfgs["test"])
    finally:
        client.close()


def test_redis_mot_node(repo_config_dir):
    cfg = load_config(env="test", config_dir=repo_config_dir, environ={})
    r = vdb.redis_cache(cfg)
    assert isinstance(r, redis.Redis)
    assert r.connection_pool.connection_kwargs["host"] == "localhost"
    with pytest.raises(ConfigError, match="sentinel"):
        vdb.redis_sentinel(cfg)


def test_redis_sentinel(cfgs):
    s = vdb.redis_sentinel(cfgs["cluster"])
    hosts = [c.connection_pool.connection_kwargs["host"] for c in s.sentinels]
    assert hosts == ["sentinel-1", "sentinel-2", "sentinel-3"]


def test_redlock(cfgs):
    rl = vdb.redlock(cfgs["cluster"])
    assert isinstance(rl, Redlock) and rl.quorum == 2 and len(rl.clients) == 3
    with pytest.raises(ConfigError, match="redis.lock.nodes"):
        vdb.redlock(cfgs["test"])
