"""Băm mật khẩu bằng **argon2** (đề cương 5.7, mục GĐ7.2).

Dùng `argon2-cffi` (PasswordHasher). Mật khẩu KHÔNG BAO GIỜ được lưu dạng rõ; chỉ lưu chuỗi
băm ``$argon2id$...`` trong `users.password_hash`.

Nếu `argon2-cffi` không cài, module ném `Argon2Unavailable` khi khởi tạo — API không đổi
thuật toán băm âm thầm (đề cương yêu cầu argon2).

    from api.auth.password import hash_password, verify_password
    h = hash_password("mật khẩu")
    verify_password(h, "mật khẩu")   # True
"""
from __future__ import annotations

import os
import secrets

__all__ = ["Argon2Unavailable", "hash_password", "verify_password", "needs_rehash", "generate_password",
           "dummy_hash"]

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError
    _HAS_ARGON2 = True
except ImportError:  # pragma: no cover - phụ thuộc bắt buộc, cài trong apps/api/pyproject.toml
    _HAS_ARGON2 = False
    PasswordHasher = None  # type: ignore[assignment]

    class InvalidHashError(Exception):  # noqa: D101
        pass

    class VerifyMismatchError(Exception):  # noqa: D101
        pass


class Argon2Unavailable(RuntimeError):
    """Thiếu `argon2-cffi` — không được phép thay bằng thuật toán khác."""


#: Tham số argon2id: m = 64 MiB, t = 3 lượt, p = 4 (khuyến nghị RFC 9106 cho mô hình dịch vụ).
_hasher: object | None = None

#: Cho phép hạ tham số argon2 **chỉ trong test** (biến môi trường `VNGIS_ARGON2_FAST=1`).
#: Mật khẩu trong test không phải bí mật thật, nên giảm `memory_cost` giúp test chạy nhanh.
#: TUYỆT ĐỐI không bật ở môi trường thật — cơ chế này chỉ dành cho `pytest`.
_FAST = os.getenv("VNGIS_ARGON2_FAST", "").strip().lower() in {"1", "true", "yes", "on"}


def _get_hasher():
    global _hasher
    if not _HAS_ARGON2:
        raise Argon2Unavailable("cài 'argon2-cffi' để băm mật khẩu (bắt buộc theo đề cương)")
    if _hasher is None:
        if _FAST:
            _hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1,
                                     hash_len=32, salt_len=16)
        else:
            _hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4,
                                     hash_len=32, salt_len=16)
    return _hasher


def hash_password(password: str) -> str:
    """Băm mật khẩu bằng argon2id; trả chuỗi ``$argon2id$...`` để lưu vào MongoDB."""
    if not password:
        raise ValueError("mật khẩu rỗng")
    return str(_get_hasher().hash(password))


def verify_password(stored_hash: str, password: str) -> bool:
    """Kiểm tra mật khẩu với chuỗi băm đã lưu. Sai định dạng hash → ``False`` (không ném)."""
    if not stored_hash or not password:
        return False
    try:
        return bool(_get_hasher().verify(stored_hash, password))
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


_dummy: str | None = None


def dummy_hash() -> str:
    """Hash argon2 của một mật khẩu ngẫu nhiên (cùng tham số với hash thật), sinh một lần mỗi tiến trình.

    Đăng nhập với tài khoản không tồn tại vẫn chạy `verify_password` trên hash này, để thời gian phản hồi
    không tiết lộ tài khoản nào có thật.
    """
    global _dummy
    if _dummy is None:
        _dummy = hash_password(secrets.token_urlsafe(32))
    return _dummy


def needs_rehash(stored_hash: str) -> bool:
    """Chuỗi băm cũ không còn khớp tham số hiện tại → nên băm lại khi người dùng đăng nhập."""
    try:
        return bool(_get_hasher().check_needs_rehash(stored_hash))
    except (InvalidHashError, ValueError):
        return True


def generate_password(n: int = 20) -> str:
    """Sinh mật khẩu ngẫu nhiên (dùng cho file seed tài khoản mẫu).

    Mật khẩu sinh ra KHÔNG được ghi vào báo cáo hay commit; chỉ lưu dạng băm trong seed.
    """
    if n < 12:
        raise ValueError("mật khẩu ngẫu nhiên cần ít nhất 12 ký tự")
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))
