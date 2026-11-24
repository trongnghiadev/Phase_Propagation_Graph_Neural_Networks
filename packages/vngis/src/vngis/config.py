"""Đọc và kiểm tra cấu hình hệ thống.

Thứ tự gộp (sau ghi đè trước):
  1. ``config/default.yaml``
  2. ``config/env/{APP_ENV}.yaml``  (APP_ENV mặc định ``dev``; file phải tồn tại)
  3. biến môi trường: ``MONGO_URI``, ``MONGO_DB``, ``MONGO_MODE``, ``REDIS_URL``, ``REDIS_MODE``, ``REDIS_SENTINELS``,
     ``REDIS_SENTINEL_MASTER``, ``REDIS_LOCK_NODES``, ``LOCK_BACKEND``

Thư mục cấu hình: biến ``VNGIS_CONFIG_DIR`` nếu có; nếu không thì đi ngược từ vị trí file này, rồi từ
thư mục hiện hành, tìm thư mục ``config/`` chứa ``default.yaml``.

Sai cấu hình (thiếu file, YAML hỏng, thiếu khóa, khóa lạ, sai kiểu) → ``ConfigError`` với thông báo
liệt kê từng lỗi theo đường dẫn khóa, để dịch vụ dừng ngay khi khởi động.

    from vngis.config import get_config
    cfg = get_config()
    cfg.mongo.db, cfg.mongo.collections.stations, cfg.redis.lock.nodes
"""
from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt,
                      ValidationError, field_validator, model_validator)

SUPPORTED_CONFIG_VERSION = 1
DEFAULT_ENV = "dev"
CONFIG_DIR_ENV = "VNGIS_CONFIG_DIR"

# biến môi trường → đường dẫn khóa trong cấu hình
ENV_VARS: dict[str, tuple[str, ...]] = {
    "MONGO_URI": ("mongo", "uri"),
    "MONGO_DB": ("mongo", "db"),
    "MONGO_MODE": ("mongo", "mode"),
    "REDIS_URL": ("redis", "cache", "url"),
    "REDIS_MODE": ("redis", "cache", "mode"),
    "REDIS_SENTINELS": ("redis", "cache", "sentinels"),
    "REDIS_SENTINEL_MASTER": ("redis", "cache", "master"),
    "REDIS_LOCK_NODES": ("redis", "lock", "nodes"),
    "LOCK_BACKEND": ("redis", "lock", "backend"),
}
_LIST_VARS = {"REDIS_SENTINELS", "REDIS_LOCK_NODES"}
_ENV_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(RuntimeError):
    """Cấu hình sai hoặc thiếu."""


# ---------------------------------------------------------------- mô hình ----------
class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _split_nodes(v: Any) -> Any:
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return v


def _check_nodes(nodes: list[str]) -> list[str]:
    for n in nodes:
        host, sep, port = n.rpartition(":")
        if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"node '{n}' phải có dạng host:port")
    return nodes


class AppConfig(_Model):
    timezone: str
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class Collections(_Model):
    hydromet_bulletins: str
    hydromet_bulletin_files: str
    merit_river_reaches: str
    waterways_segments: str
    stations: str
    admin_units: str
    admin_unit_mappings: str
    province_timeline: str
    lock_fences: str
    # Collection vận hành và nghiệp vụ (GĐ7).
    users: str = "users"
    roles: str = "roles"
    forecasts: str = "forecasts"
    alerts: str = "alerts"
    models: str = "models"
    job_locks: str = "job_locks"
    jobs: str = "jobs"
    graph_edges: str = "graph_edges"
    tide_extremes: str = "tide_extremes"
    official_forecasts: str = "official_forecasts"
    reservoir_daily: str = "reservoir_daily"
    rainfall_daily: str = "rainfall_daily"
    qc_flags: str = "qc_flags"
    audit_logs: str = "audit_logs"


class MongoConfig(_Model):
    uri: str
    db: str = Field(min_length=1)
    mode: Literal["auto", "single", "replica_set", "mongos"] = "auto"
    app_name: str = "vngis"
    server_selection_timeout_ms: PositiveInt = 5000
    write_concern: str | None = None
    read_concern: Literal["local", "available", "majority", "linearizable", "snapshot"] | None = None
    collections: Collections

    @field_validator("uri")
    @classmethod
    def _uri(cls, v: str) -> str:
        if not v.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("URI MongoDB phải bắt đầu bằng mongodb:// hoặc mongodb+srv://")
        return v

    @model_validator(mode="after")
    def _mode_matches_uri(self) -> "MongoConfig":
        if self.mode == "replica_set" and "replicaSet=" not in self.uri:
            raise ValueError("mode = replica_set nhưng URI không có tham số replicaSet=")
        return self

    @property
    def topology(self) -> str:
        """Kiểu kết nối thực tế: single | replica_set | mongos (mode auto suy ra từ URI)."""
        if self.mode != "auto":
            return self.mode
        return "replica_set" if "replicaSet=" in self.uri else "single"


class RedisCacheConfig(_Model):
    mode: Literal["single", "sentinel"] = "single"
    url: str = "redis://localhost:6379/0"
    sentinels: list[str] = []
    master: str = "cache"
    socket_timeout_s: PositiveFloat = 0.5

    @field_validator("sentinels", mode="before")
    @classmethod
    def _split(cls, v: Any) -> Any:
        return _split_nodes(v)

    @field_validator("sentinels")
    @classmethod
    def _nodes(cls, v: list[str]) -> list[str]:
        return _check_nodes(v)

    @model_validator(mode="after")
    def _need_sentinels(self) -> "RedisCacheConfig":
        if self.mode == "sentinel" and not self.sentinels:
            raise ValueError("mode = sentinel nhưng danh sách sentinels rỗng")
        if self.mode == "single" and not self.url.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("url Redis phải bắt đầu bằng redis://, rediss:// hoặc unix://")
        return self


class RedisLockConfig(_Model):
    # `redlock`: N node Redis độc lập (đa số node). `single`: một node (dùng để so sánh ở E17/E19).
    backend: Literal["single", "redlock"] = "redlock"
    nodes: list[str] = []
    timeout_s: PositiveFloat = 0.05
    default_ttl_ms: PositiveInt = 30000
    # Số lần thử lại khi lấy khóa thất bại (backoff ngẫu nhiên).
    max_attempts: PositiveInt = 20
    # Khoá dựng lại cache (chống stampede) dùng node đầu tiên của `nodes`.
    rebuild_lock_ms: PositiveInt = 30000

    @field_validator("nodes", mode="before")
    @classmethod
    def _split(cls, v: Any) -> Any:
        return _split_nodes(v)

    @field_validator("nodes")
    @classmethod
    def _nodes(cls, v: list[str]) -> list[str]:
        return _check_nodes(v)

    @model_validator(mode="after")
    def _need_nodes(self) -> "RedisLockConfig":
        if not self.nodes:
            raise ValueError("redis.lock.nodes rỗng: đặt REDIS_LOCK_NODES hoặc khai báo trong config/env/*.yaml")
        return self


class RedisConfig(_Model):
    cache: RedisCacheConfig
    lock: RedisLockConfig


class AdminConfig(_Model):
    effective_utc: AwareDatetime
    eras: tuple[str, str]
    # nhãn tỉnh cũ còn dùng sau mốc tối đa bao nhiêu ngày thì coi là "đổi tên chậm" (vngis.province_labels)
    max_label_lag_days: int = Field(31, ge=0, le=366)

    @field_validator("effective_utc")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(timezone.utc)

    @field_validator("eras")
    @classmethod
    def _eras(cls, v: tuple[str, str]) -> tuple[str, str]:
        if v[0] == v[1]:
            raise ValueError("hai thời kỳ phải khác nhau")
        return v


class StationsConfig(_Model):
    snap_radius_m: PositiveInt
    basin_root_comid: int
    min_uparea_km2: dict[str, PositiveFloat] = {}


class QcR1Config(_Model):
    p_low: float = Field(ge=0, le=1)
    p_high: float = Field(ge=0, le=1)
    margin: float = Field(ge=0)
    physical_m: dict[str, tuple[float, float]] = {}

    @model_validator(mode="after")
    def _check(self) -> "QcR1Config":
        if self.p_low >= self.p_high:
            raise ValueError("p_low phải nhỏ hơn p_high")
        bad = [k for k, (lo, hi) in self.physical_m.items() if lo >= hi]
        if bad:
            raise ValueError(f"physical_m: min phải nhỏ hơn max ({bad})")
        return self


class QcR2Config(_Model):
    min_gap_m: float = Field(0.0, ge=0)


class QcR3Config(_Model):
    max_ref_gap_h: PositiveFloat
    iqr_k: float = Field(ge=0)
    min_dev_h: float = Field(ge=0)
    min_samples: PositiveInt
    min_ref_peaks_per_day: int = Field(0, ge=0, le=4)


class QcConfig(_Model):
    """Tham số QC bản tin (`vngis.qc_rules`); job Spark dùng bản sao `DEFAULT_PARAMS` (test giữ khớp)."""
    rules_version: PositiveInt
    reference_station: str = Field(min_length=1)
    r1: QcR1Config
    r2: QcR2Config
    r3: QcR3Config
    audit_pending_grace_s: PositiveInt = 600


class CacheConfig(_Model):
    ttl_s: dict[str, PositiveInt]
    jitter: float = Field(0.1, ge=0, le=0.5)
    stale_factor: PositiveInt = 4
    # Ngưỡng giá trị lớn (byte) thì nén zstd + msgpack thay vì JSON thô.
    compress_threshold_bytes: PositiveInt = 4096
    # Số lần chờ tối đa khi chờ một tiến trình khác dựng lại giá trị (chống stampede).
    max_wait: PositiveInt = 20
    # Khoá chống dựng lại trùng: TTL của lock:rebuild:{key}.
    rebuild_lock_ttl_ms: PositiveInt = 30000


class AuthConfig(_Model):
    """Xác thực và phân quyền (đề cương 5.4, mục GĐ7.2–7.3)."""
    # Thời hạn token (phút / ngày).
    access_ttl_min: PositiveInt = 15
    refresh_ttl_days: PositiveInt = 7
    # Khóa ký JWT: lấy từ biến môi trường AUTH_SECRET; nếu thiếu thì sinh tạm (chỉ hợp cho dev).
    secret_env: str = "AUTH_SECRET"
    issuer: str = "gis-dist"
    # TTL cache quyền trong Redis (giây) — 5 phút theo đề cương.
    perm_ttl_s: PositiveInt = 300
    # Số lần đăng nhập sai tối đa trước khi khóa tạm.
    max_failed_logins: PositiveInt = 10
    lockout_minutes: PositiveInt = 15
    # Bật/tắt yêu cầu xác thực (chỉ dùng cho test tự động; KHÔNG bật trong production).
    required: bool = True


class RateLimitConfig(_Model):
    """Giới hạn tần suất (đề cương 5.8: mã 429)."""
    enabled: bool = True
    # Số request cho mỗi cửa sổ.
    requests: PositiveInt = 120
    window_s: PositiveInt = 60
    # Nới riêng cho các endpoint công khai (đăng nhập, health).
    login_requests: PositiveInt = 10
    login_window_s: PositiveInt = 60


class SchedulerConfig(_Model):
    enabled: bool = True
    # Giờ chạy job nạp bản tin mới và dự báo hằng ngày (giờ Việt Nam).
    ingest_hour: int = Field(6, ge=0, le=23)
    forecast_hour: int = Field(8, ge=0, le=23)
    forecast_minute: int = Field(0, ge=0, le=59)
    # TTL khóa job (giây) và chu kỳ ghi heartbeat (giây).
    lock_ttl_s: PositiveInt = 600
    heartbeat_s: PositiveInt = 30


class Config(_Model):
    config_version: int
    env: str
    config_dir: str
    app: AppConfig
    mongo: MongoConfig
    redis: RedisConfig
    admin: AdminConfig
    stations: StationsConfig
    cache: CacheConfig
    qc: QcConfig
    auth: AuthConfig = AuthConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    scheduler: SchedulerConfig = SchedulerConfig()

    @field_validator("config_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != SUPPORTED_CONFIG_VERSION:
            raise ValueError(f"config_version {v} không được hỗ trợ (cần {SUPPORTED_CONFIG_VERSION})")
        return v


# ---------------------------------------------------------------- đọc file --------
def find_config_dir(start: str | os.PathLike | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """Tìm thư mục config/: ``VNGIS_CONFIG_DIR`` → đi ngược từ ``start`` (hoặc file này) → từ thư mục hiện hành."""
    environ = os.environ if environ is None else environ
    explicit = environ.get(CONFIG_DIR_ENV)
    if explicit:
        p = Path(explicit)
        if not (p / "default.yaml").is_file():
            raise ConfigError(f"{CONFIG_DIR_ENV}={explicit}: không có default.yaml trong thư mục này")
        return p.resolve()
    starts = [Path(start)] if start is not None else [Path(__file__), Path.cwd()]
    for s in starts:
        s = s.resolve()
        for d in [s, *s.parents]:
            cand = d / "config"
            if (cand / "default.yaml").is_file():
                return cand
    raise ConfigError(f"không tìm thấy config/default.yaml; đặt biến {CONFIG_DIR_ENV} trỏ tới thư mục config")


def _read_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"thiếu file cấu hình: {path}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML không hợp lệ trong {path}: {e}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: cấp ngoài cùng phải là mapping (khóa: giá trị)")
    return data


def deep_merge(base: Mapping, override: Mapping) -> dict:
    """Gộp đệ quy: mapping gộp theo khóa, giá trị khác (kể cả list) bị thay thế.

    Giá trị ``None`` đè lên một MAPPING thì bị bỏ qua (giữ mapping mặc định): trong YAML, khóa để trống
    (``redis:`` không có khóa con, hoặc khóa con bị comment hết) đọc ra ``None`` và không được xóa cả nhánh
    mặc định. ``None`` đè lên giá trị thường (``write_concern: null``) vẫn có hiệu lực.
    """
    out = copy.deepcopy(dict(base))
    for k, v in override.items():
        if v is None and isinstance(out.get(k), Mapping):
            continue
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _env_overrides(environ: Mapping[str, str]) -> dict:
    out: dict = {}
    for var, path in ENV_VARS.items():
        val = environ.get(var)
        if val is None or val.strip() == "":
            continue
        value: Any = _split_nodes(val) if var in _LIST_VARS else val.strip()
        node = out
        for k in path[:-1]:
            node = node.setdefault(k, {})
        node[path[-1]] = value
    # Có REDIS_SENTINELS mà không chỉ rõ REDIS_MODE → dùng Sentinel
    if environ.get("REDIS_SENTINELS", "").strip() and not environ.get("REDIS_MODE", "").strip():
        out.setdefault("redis", {}).setdefault("cache", {})["mode"] = "sentinel"
    return out


def _format_errors(e: ValidationError) -> str:
    lines = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(gốc)"
        msg = err["msg"]
        if err["type"] == "missing":
            msg = "thiếu khóa bắt buộc"
        elif err["type"] == "extra_forbidden":
            msg = "khóa không được khai báo (gõ sai tên?)"
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)


def load_config(env: str | None = None, config_dir: str | os.PathLike | None = None,
                environ: Mapping[str, str] | None = None) -> Config:
    """Đọc, gộp và kiểm tra cấu hình. ``environ`` mặc định là ``os.environ``."""
    environ = os.environ if environ is None else environ
    env = env or environ.get("APP_ENV") or DEFAULT_ENV
    if not _ENV_NAME.match(env):
        raise ConfigError(f"APP_ENV '{env}' không hợp lệ (chỉ chữ, số, '_' và '-')")
    cdir = Path(config_dir).resolve() if config_dir is not None else find_config_dir(environ=environ)

    data = _read_yaml(cdir / "default.yaml")
    env_file = cdir / "env" / f"{env}.yaml"
    if not env_file.is_file():
        raise ConfigError(f"APP_ENV={env}: thiếu file {env_file}")
    data = deep_merge(data, _read_yaml(env_file))
    data = deep_merge(data, _env_overrides(environ))
    data["env"] = env
    data["config_dir"] = str(cdir)
    try:
        return Config.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"cấu hình không hợp lệ (APP_ENV={env}, {cdir}):\n{_format_errors(e)}") from None


# ---------------------------------------------------------------- danh mục trạm -----
class StationCoord(_Model):
    """Tọa độ ƯỚC LƯỢNG của trạm/hồ (WGS84, trong phạm vi Việt Nam)."""
    lon: float = Field(ge=102, le=110)
    lat: float = Field(ge=8, le=24)
    confidence: Literal["high", "medium", "low", "unknown"]


class StationProvince(_Model):
    """Tên tỉnh của trạm ở hai thời kỳ (ADR 0003)."""
    before_2025_07: str = Field(min_length=1)
    from_2025_07: str = Field(min_length=1)


class StationsCatalog(_Model):
    """``config/stations.yaml``: danh mục trạm cho pipelines/ingest/stations/create_stations.py."""
    config_version: int
    water_level: dict[str, StationCoord | None]
    reservoirs: dict[str, StationCoord | None]
    rain: dict[str, StationCoord | None]
    station_province: dict[str, StationProvince] = {}

    @field_validator("config_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != SUPPORTED_CONFIG_VERSION:
            raise ValueError(f"config_version {v} không được hỗ trợ (cần {SUPPORTED_CONFIG_VERSION})")
        return v

    @model_validator(mode="after")
    def _known_stations(self) -> "StationsCatalog":
        unknown = sorted(set(self.station_province) - set(self.water_level) - set(self.rain))
        if unknown:
            raise ValueError(f"station_province có trạm không khai báo trong water_level/rain: {unknown}")
        return self


def load_stations_catalog(config_dir: str | os.PathLike | None = None,
                          environ: Mapping[str, str] | None = None,
                          filename: str = "stations.yaml") -> StationsCatalog:
    """Đọc và kiểm tra ``config/stations.yaml``; thiếu khóa, khóa lạ, sai kiểu → ``ConfigError`` liệt kê từng lỗi."""
    cdir = Path(config_dir).resolve() if config_dir is not None else find_config_dir(environ=environ)
    path = cdir / filename
    try:
        return StationsCatalog.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise ConfigError(f"danh mục trạm không hợp lệ ({path}):\n{_format_errors(e)}") from None


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Cấu hình của tiến trình (đọc một lần). Gọi ``get_config.cache_clear()`` để đọc lại."""
    return load_config()
