"""Xác thực và phân quyền của `apps/api`.

- `password`: băm mật khẩu argon2.
- `jwt`: phát hành access (15 phút) + refresh (7 ngày), chứa `user_id` và `perm_ver`.
- `blacklist`: chặn token khi đăng xuất.
- `users`: `users`/`roles` trong MongoDB, cache quyền `perm:{user_id}`.
"""
from __future__ import annotations

from .blacklist import TokenBlacklist
from .jwt import JwtManager, TokenPair, blacklist_key
from .password import Argon2Unavailable, generate_password, hash_password, needs_rehash, verify_password
from .users import InvalidRole, PermissionChanged, UserNotFound, UserRepo, perm_cache_key

__all__ = ["TokenBlacklist", "JwtManager", "TokenPair", "blacklist_key", "Argon2Unavailable",
           "generate_password", "hash_password", "needs_rehash", "verify_password", "UserRepo",
           "InvalidRole", "PermissionChanged", "UserNotFound", "perm_cache_key"]
