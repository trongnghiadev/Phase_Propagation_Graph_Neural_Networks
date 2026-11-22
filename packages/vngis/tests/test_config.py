from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from vngis import admin
from vngis.config import ConfigError, deep_merge, find_config_dir, load_config

DEFAULT = {
    "config_version": 1,
    "app": {"timezone": "Asia/Ho_Chi_Minh"},
    "mongo": {
        "uri": "mongodb://localhost:27017", "db": "vietnam_gis",
        "collections": {k: k for k in ["hydromet_bulletins", "hydromet_bulletin_files", "merit_river_reaches",
                                       "waterways_segments", "stations", "admin_units", "admin_unit_mappings",
                                       "province_timeline", "lock_fences"]},
    },
    "redis": {"cache": {"mode": "single", "url": "redis://localhost:6379/0"}, "lock": {"nodes": []}},
    "admin": {"effective_utc": "2025-06-30T17:00:00Z", "eras": ["before_2025_07", "from_2025_07"]},
    "stations": {"snap_radius_m": 3000, "basin_root_comid": 44045960},
    "cache": {"ttl_s": {"stats": 86400}},
}


def write_config(tmp_path: Path, default=None, envs=None) -> Path:
    d = tmp_path / "config"
    (d / "env").mkdir(parents=True)
    (d / "default.yaml").write_text(yaml.safe_dump(DEFAULT if default is None else default, allow_unicode=True),
                                    encoding="utf-8")
    for name, content in (envs if envs is not None else {"dev": {}}).items():
        (d / "env" / f"{name}.yaml").write_text(content if isinstance(content, str) else
                                                 yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")
    return d


# ---------------------------------------------------------------- cấu hình thật của repo ----
@pytest.mark.parametrize("env", ["dev", "test", "cluster"])
def test_cau_hinh_repo_hop_le(repo_config_dir, env):
    cfg = load_config(env=env, config_dir=repo_config_dir, environ={})
    assert cfg.config_version == 1 and cfg.env == env
    assert cfg.mongo.collections.province_timeline == "province_timeline"
    assert cfg.stations.snap_radius_m == 3000 and cfg.stations.basin_root_comid == 44045960
    assert cfg.admin.effective_utc == admin.EFFECTIVE_UTC
    assert tuple(cfg.admin.eras) == admin.ERAS


def test_cau_hinh_repo_theo_moi_truong(repo_config_dir):
    dev = load_config(env="dev", config_dir=repo_config_dir, environ={})
    test = load_config(env="test", config_dir=repo_config_dir, environ={})
    cl = load_config(env="cluster", config_dir=repo_config_dir, environ={})
    assert dev.mongo.uri == "mongodb://localhost:27017" and dev.mongo.db == "vietnam_gis"
    assert test.mongo.db != "vietnam_gis" and test.scheduler.enabled is False
    assert cl.mongo.uri == "mongodb://mongo1:27101,mongo2:27102,mongo3:27103/?replicaSet=rs0"
    assert cl.mongo.topology == "replica_set"
    assert cl.redis.cache.mode == "sentinel" and len(cl.redis.cache.sentinels) == 3
    assert cl.redis.lock.nodes == ["redis-lock-1:6379", "redis-lock-2:6379", "redis-lock-3:6379"]
    # khóa không ghi trong env vẫn lấy từ mặc định
    assert cl.cache.ttl_s == dev.cache.ttl_s and cl.mongo.db == "vietnam_gis"


def test_stations_yaml_doc_duoc(repo_config_dir):
    cat = yaml.safe_load((repo_config_dir / "stations.yaml").read_text(encoding="utf-8"))
    assert len(cat["water_level"]) == 9 and len(cat["reservoirs"]) == 7 and len(cat["rain"]) == 10
    for sec in ("water_level", "reservoirs", "rain"):
        for name, c in cat[sec].items():
            assert c is None or (set(c) == {"lon", "lat", "confidence"} and 104 < c["lon"] < 109
                                 and 8 < c["lat"] < 13), name
    assert set(cat["station_province"]) <= set(cat["water_level"]) | set(cat["rain"])


def test_find_config_dir_tu_thu_muc_con(tmp_path):
    d = write_config(tmp_path)
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_config_dir(start=sub, environ={}) == d.resolve()
    assert find_config_dir(environ={"VNGIS_CONFIG_DIR": str(d)}) == d.resolve()


def test_find_config_dir_bien_moi_truong_sai(tmp_path):
    with pytest.raises(ConfigError, match="VNGIS_CONFIG_DIR"):
        find_config_dir(environ={"VNGIS_CONFIG_DIR": str(tmp_path)})


# ---------------------------------------------------------------- gộp ----------------
def test_deep_merge():
    a = {"x": {"y": 1, "z": [1, 2]}, "k": 1}
    out = deep_merge(a, {"x": {"z": [3]}, "n": 2})
    assert out == {"x": {"y": 1, "z": [3]}, "k": 1, "n": 2}
    assert a["x"]["z"] == [1, 2]            # không sửa bản gốc


def test_gop_default_env_va_bien_moi_truong(tmp_path):
    d = write_config(tmp_path, envs={"dev": {"mongo": {"db": "db_dev"}, "app": {"debug": True}}})
    cfg = load_config(config_dir=d, environ={"APP_ENV": "dev"})
    assert cfg.mongo.db == "db_dev" and cfg.app.debug is True
    assert cfg.mongo.uri == "mongodb://localhost:27017"           # từ default

    env = {"APP_ENV": "dev", "MONGO_URI": "mongodb://h1:1,h2:2/?replicaSet=rs0", "MONGO_DB": "db_env",
           "REDIS_SENTINELS": "s1:26379, s2:26379", "REDIS_LOCK_NODES": "l1:6379,l2:6379,l3:6379"}
    cfg = load_config(config_dir=d, environ=env)
    assert cfg.mongo.db == "db_env"                                 # biến môi trường thắng env file
    assert cfg.mongo.topology == "replica_set"
    assert cfg.redis.cache.mode == "sentinel" and cfg.redis.cache.sentinels == ["s1:26379", "s2:26379"]
    assert cfg.redis.lock.nodes == ["l1:6379", "l2:6379", "l3:6379"]


def test_bien_moi_truong_rong_bi_bo_qua(tmp_path):
    d = write_config(tmp_path)
    cfg = load_config(config_dir=d, environ={"MONGO_DB": "  ", "REDIS_SENTINELS": ""})
    assert cfg.mongo.db == "vietnam_gis" and cfg.redis.cache.mode == "single"


def test_app_env_mac_dinh_la_dev(tmp_path):
    d = write_config(tmp_path, envs={"dev": {"mongo": {"db": "x"}}})
    assert load_config(config_dir=d, environ={}).env == "dev"


def test_effective_utc_doi_ve_utc(tmp_path):
    cfg_d = dict(DEFAULT, admin={"effective_utc": "2025-07-01T00:00:00+07:00", "eras": ["a", "b"]})
    cfg = load_config(config_dir=write_config(tmp_path, default=cfg_d), environ={})
    assert cfg.admin.effective_utc == datetime(2025, 6, 30, 17, tzinfo=timezone.utc)
    assert cfg.admin.effective_utc.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------- lỗi ----------------
def test_loi_thieu_default(tmp_path):
    (tmp_path / "env").mkdir()
    with pytest.raises(ConfigError, match="default.yaml"):
        load_config(config_dir=tmp_path, environ={})


def test_loi_thieu_file_env(tmp_path):
    d = write_config(tmp_path)
    with pytest.raises(ConfigError, match="APP_ENV=staging"):
        load_config(config_dir=d, environ={"APP_ENV": "staging"})


def test_loi_ten_env_khong_hop_le(tmp_path):
    with pytest.raises(ConfigError, match="không hợp lệ"):
        load_config(config_dir=write_config(tmp_path), environ={"APP_ENV": "../dev"})


def test_loi_thieu_khoa_bat_buoc(tmp_path):
    bad = {k: v for k, v in DEFAULT.items() if k != "stations"}
    bad["mongo"] = {k: v for k, v in DEFAULT["mongo"].items() if k != "uri"}
    with pytest.raises(ConfigError) as e:
        load_config(config_dir=write_config(tmp_path, default=bad), environ={})
    assert "stations: thiếu khóa bắt buộc" in str(e.value)
    assert "mongo.uri: thiếu khóa bắt buộc" in str(e.value)


def test_loi_khoa_la(tmp_path):
    d = write_config(tmp_path, envs={"dev": {"mongo": {"dbname": "x"}}})
    with pytest.raises(ConfigError, match="mongo.dbname"):
        load_config(config_dir=d, environ={})


@pytest.mark.parametrize("env_yaml, msg", [
    ({"config_version": 2}, "config_version"),
    ({"mongo": {"uri": "localhost:27017"}}, "mongo.uri"),
    ({"mongo": {"mode": "replica_set"}}, "replicaSet"),
    ({"redis": {"cache": {"mode": "sentinel"}}}, "sentinels"),
    ({"redis": {"lock": {"nodes": ["redis-lock-1"]}}}, "host:port"),
    ({"stations": {"snap_radius_m": -1}}, "stations.snap_radius_m"),
    ({"admin": {"effective_utc": "2025-06-30T17:00:00"}}, "admin.effective_utc"),   # thiếu múi giờ
    ("mongo: [không, phải, mapping", "YAML không hợp lệ"),
    ("- danh sách", "mapping"),
])
def test_loi_gia_tri_sai(tmp_path, env_yaml, msg):
    d = write_config(tmp_path, envs={"dev": env_yaml})
    with pytest.raises(ConfigError, match=msg):
        load_config(config_dir=d, environ={})


def test_loi_tu_bien_moi_truong(tmp_path):
    with pytest.raises(ConfigError, match="mongo.uri"):
        load_config(config_dir=write_config(tmp_path), environ={"MONGO_URI": "http://x"})


# ---------------------------------------------------------------- None trong file env ----
def test_deep_merge_none_khong_xoa_mapping():
    base = {"redis": {"cache": {"mode": "single"}}, "mongo": {"write_concern": "majority"}}
    out = deep_merge(base, {"redis": None, "mongo": {"write_concern": None}})
    assert out["redis"] == {"cache": {"mode": "single"}}       # mapping mặc định giữ nguyên
    assert out["mongo"]["write_concern"] is None               # None đè giá trị thường vẫn có hiệu lực
    assert deep_merge({}, {"x": None}) == {"x": None}


def test_khoa_rong_trong_file_env_giu_mac_dinh(tmp_path):
    d = write_config(tmp_path, envs={"dev": "redis:\nmongo:\n  db: x\ncache:\n"})
    cfg = load_config(config_dir=d, environ={})
    assert cfg.redis.cache.url == "redis://localhost:6379/0" and cfg.mongo.db == "x"
    assert cfg.cache.ttl_s == {"stats": 86400}


def test_max_label_lag_days(repo_config_dir, tmp_path):
    assert load_config(env="dev", config_dir=repo_config_dir, environ={}).admin.max_label_lag_days == 31
    d = write_config(tmp_path, envs={"dev": {"admin": {"max_label_lag_days": -1}}})
    with pytest.raises(ConfigError, match="admin.max_label_lag_days"):
        load_config(config_dir=d, environ={})


# ---------------------------------------------------------------- stations.yaml ----------
def test_load_stations_catalog_repo(repo_config_dir):
    from vngis.config import load_stations_catalog
    cat = load_stations_catalog(repo_config_dir)
    assert list(cat.water_level)[0] == "Vũng Tàu"                 # giữ thứ tự khai báo
    assert cat.reservoirs["Sông Hỏa"] is None
    assert cat.station_province["Tân An"].from_2025_07 == "Tây Ninh"


@pytest.mark.parametrize("mutate, msg", [
    (lambda c: c.pop("reservoirs"), "reservoirs: thiếu khóa bắt buộc"),
    (lambda c: c["water_level"]["Phú An"].pop("lat"), "water_level.Phú An.lat: thiếu khóa bắt buộc"),
    (lambda c: c["water_level"]["Phú An"].update(conf="x"), "water_level.Phú An.conf"),
    (lambda c: c["rain"]["Củ Chi"].update(confidence="rất cao"), "rain.Củ Chi.confidence"),
    (lambda c: c["station_province"]["Tân An"].pop("from_2025_07"), "station_province.Tân An.from_2025_07: thiếu"),
    (lambda c: c["station_province"].update({"Không Có": {"before_2025_07": "a", "from_2025_07": "b"}}), "Không Có"),
])
def test_load_stations_catalog_loi_ro(repo_config_dir, tmp_path, mutate, msg):
    from vngis.config import load_stations_catalog
    cat = yaml.safe_load((repo_config_dir / "stations.yaml").read_text(encoding="utf-8"))
    mutate(cat)
    (tmp_path / "stations.yaml").write_text(yaml.safe_dump(cat, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match=msg):
        load_stations_catalog(tmp_path)


def test_load_stations_catalog_thieu_file(tmp_path):
    from vngis.config import load_stations_catalog
    with pytest.raises(ConfigError, match="thiếu file"):
        load_stations_catalog(tmp_path)
