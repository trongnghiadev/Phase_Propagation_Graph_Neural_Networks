"""Chuẩn hóa tên tiếng Việt để so khớp (tên tỉnh, xã, trạm).

Hiện các script trong pipelines/ingest/ vẫn có bản sao riêng của các hàm này (chạy độc lập,
chưa cài workspace). Khi chuyển sang `uv sync`, các script sẽ import từ đây.
"""
from __future__ import annotations

import unicodedata

ADMIN_PREFIXES = ["thành phố trung ương", "thành phố", "tỉnh", "thị xã", "thị trấn", "quận", "huyện",
                  "phường", "xã", "đặc khu"]


def strip_accents(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm_name(s: str, prefixes: list[str] = ADMIN_PREFIXES) -> str:
    """'Thành phố Hồ Chí Minh' -> 'ho chi minh'; 'Tỉnh Bà Rịa - Vũng Tàu' -> 'ba ria vung tau'."""
    s = " ".join(s.strip().lower().split())
    for p in prefixes:
        if s.startswith(p + " "):
            s = s[len(p) + 1:]
            break
    s = strip_accents(s)
    return " ".join(s.replace("-", " ").split())


def slug(name: str) -> str:
    """'Thủ Dầu Một' -> 'THU_DAU_MOT' (mã trạm)."""
    s = strip_accents(name)
    return "".join(c if c.isalnum() else "_" for c in s).strip("_").upper()
