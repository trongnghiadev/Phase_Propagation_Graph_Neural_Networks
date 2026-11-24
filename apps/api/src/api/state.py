"""Trạng thái dùng chung của tiến trình API: cấu hình, MongoDB, Redis cache, khóa, logger.

Tất cả đối tượng được tạo lazily (khi endpoint đầu tiên cần) để:
- import module không kết nối gì (giống `vngis.db`);
- test có thể thay thế bằng `override_state()`.

    from api.state import state
    state.db          # Database của MongoDB
    state.cache       # vngis.cache.Cache
    state.lock        # vngis.locks.Redlock | SingleLock
    state.instance    # tên instance (biến môi trường INSTANCE)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

__all__ = ["AppState", "state", "configure_logging", "get_logger", "SensitiveDriverLogFilter",
           "DRIVER_LOGGERS"]

#: Logger của driver MongoDB. Từ pymongo 4.8, `pymongo.command` ở mức DEBUG in NGUYÊN lệnh và reply
#: (kể cả document `users` có `password_hash`) → luôn giữ các logger này ở mức tối thiểu INFO.
DRIVER_LOGGERS = ("pymongo", "pymongo.command", "pymongo.connection", "pymongo.serverSelection",
                  "pymongo.topology", "pymongo.monitoring")
_SENSITIVE_MARKERS = ("password_hash", '"users"', "'users'", "users.", "$argon2")


class SensitiveDriverLogFilter(logging.Filter):
    """Chặn mọi dòng log của driver MongoDB nhắc tới collection `users` hoặc hash mật khẩu.

    Lớp phòng thủ thứ hai (lớp thứ nhất là giữ `pymongo.*` ≥ INFO): kể cả khi ai đó hạ mức log của
    driver xuống DEBUG, reply của `users` vẫn không bao giờ ra log.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("pymongo"):
            return True
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 - không định dạng được thì không in
            return False
        return not any(m in text for m in _SENSITIVE_MARKERS)


def configure_logging(level: str = "INFO", *, instance: str = "api") -> logging.Logger:
    """Cấu hình log có `request_id` và tên instance trong mọi dòng log.

    Định dạng: ``<giờ> <mức> [<instance>] [request_id=<id>] <nội dung>``.
    `request_id` do middleware gắn vào `logging.LogRecord` qua `LoggerAdapter`/filter.

    `log_level: DEBUG` chỉ áp cho code của dự án; logger `pymongo.*` luôn ≥ INFO (tắt command
    monitoring ở mức DEBUG) và mọi handler gắn `SensitiveDriverLogFilter`.
    """
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        h = logging.StreamHandler()
        h.setFormatter(_Formatter(instance))
        root.addHandler(h)
    else:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler):
                h.setFormatter(_Formatter(instance))
    for h in root.handlers:
        if not any(isinstance(f, SensitiveDriverLogFilter) for f in h.filters):
            h.addFilter(SensitiveDriverLogFilter())
    floor = max(logging.INFO, logging.getLevelName(level) if isinstance(level, str) else int(level))
    for name in DRIVER_LOGGERS:
        lg = logging.getLogger(name)
        lg.setLevel(floor)
        if not any(isinstance(f, SensitiveDriverLogFilter) for f in lg.filters):
            lg.addFilter(SensitiveDriverLogFilter())
    return logging.getLogger("api")


class _Formatter(logging.Formatter):
    """Bổ sung tên instance và `request_id` vào mọi dòng log."""

    def __init__(self, instance: str):
        super().__init__()
        self.instance = instance

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None)
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        base = f"{ts} {record.levelname:<7} [{self.instance}]"
        if rid:
            base += f" [request_id={rid}]"
        return f"{base} {record.getMessage()}"


def get_logger(name: str = "api") -> logging.Logger:
    return logging.getLogger(name)


@dataclass
class AppState:
    """Giữ các tài nguyên dùng chung của tiến trình."""

    instance: str = field(default_factory=lambda: os.getenv("INSTANCE", "api"))
    cfg: Any = None
    _db: Any = None
    _cache: Any = None
    _lock: Any = None
    _redis: Any = None
    _jwt: Any = None
    _repo: Any = None
    _blacklist: Any = None

    # -------------------------------------------------------------- cấu hình ----
    @property
    def config(self):
        if self.cfg is None:
            from vngis.config import get_config
            self.cfg = get_config()
        return self.cfg

    # -------------------------------------------------------------- MongoDB ----
    @property
    def db(self):
        if self._db is None:
            from vngis.db import get_db
            self._db = get_db(self.config)
        return self._db

    # -------------------------------------------------------------- Redis ------
    @property
    def redis(self):
        """Client Redis cache (master qua Sentinel hoặc một node)."""
        if self._redis is None:
            from vngis.db import redis_cache
            self._redis = redis_cache(self.config)
        return self._redis

    @property
    def cache(self):
        """Cache (``vngis.cache.Cache``) trên client Redis cache."""
        if self._cache is None:
            from vngis.cache import build_cache
            self._cache = build_cache(self.redis, self.config)
        return self._cache

    @property
    def lock(self):
        """Backend khóa phân tán theo ``LOCK_BACKEND``."""
        if self._lock is None:
            from vngis.db import redlock
            self._lock = redlock(self.config)
        return self._lock

    # -------------------------------------------------------------- xác thực ----
    @property
    def jwt(self):
        if self._jwt is None:
            from api.auth.jwt import JwtManager
            a = self.config.auth
            self._jwt = JwtManager(issuer=a.issuer, access_ttl_min=a.access_ttl_min,
                                   refresh_ttl_days=a.refresh_ttl_days, secret_env=a.secret_env,
                                   app_env=self.config.env)
        return self._jwt

    @property
    def repo(self):
        """``UserRepo`` (users/roles + cache quyền)."""
        if self._repo is None:
            from api.auth.users import UserRepo
            self._repo = UserRepo(self.db, cache=self.cache, perm_ttl_s=self.config.auth.perm_ttl_s)
        return self._repo

    @property
    def blacklist(self):
        if self._blacklist is None:
            from api.auth.blacklist import TokenBlacklist
            self._blacklist = TokenBlacklist(self.redis)
        return self._blacklist

    # -------------------------------------------------------------- ghi đè (test)
    def override(self, **kwargs: Any) -> None:
        """Thay thế tài nguyên (dùng trong test): ``override(db=..., cache=..., lock=...)``."""
        mapping = {"db": "_db", "cache": "_cache", "lock": "_lock", "redis": "_redis",
                   "jwt": "_jwt", "repo": "_repo", "blacklist": "_blacklist", "cfg": "cfg"}
        for k, v in kwargs.items():
            if k not in mapping:
                raise KeyError(f"không ghi đè được {k!r}")
            setattr(self, mapping[k], v)

    def reset(self) -> None:
        """Xoá mọi tài nguyên đã tạo (giữ cấu hình)."""
        self._db = self._cache = self._lock = self._redis = None
        self._jwt = self._repo = self._blacklist = None


#: Trạng thái dùng chung của tiến trình.
state = AppState()
