"""Chuẩn hóa tên tiếng Việt để so khớp (tên tỉnh, xã, trạm).

Bản DUY NHẤT của hàm chuẩn hóa tên trong repo: script nạp dữ liệu (pipelines/ingest/) và job Spark
(pipelines/spark/) đều import từ đây. Module chỉ dùng thư viện chuẩn (chạy được trong image Spark
không có pymongo/pydantic/pyyaml).
"""
from __future__ import annotations

import unicodedata

ADMIN_PREFIXES = ["thành phố trung ương", "thành phố", "tỉnh", "thị xã", "thị trấn", "quận", "huyện",
                  "phường", "xã", "đặc khu"]


def strip_accents(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _nfc_lower(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s).lower().split())


def norm_name(s: str, prefixes: list[str] = ADMIN_PREFIXES) -> str:
    """Khóa so khớp tên: dạng NFC, chữ thường, bỏ MỘT tiền tố cấp hành chính, bỏ dấu, '-' → khoảng trắng,
    gộp mọi khoảng trắng liên tiếp thành một.

    'Thành phố Hồ Chí Minh' -> 'ho chi minh'; 'Tỉnh Bà Rịa - Vũng Tàu' -> 'ba ria vung tau';
    'Thị xã Thủ Dầu Một' -> 'thu dau mot'. Chuỗi rỗng/None -> ''.
    """
    if not s:
        return ""
    s = _nfc_lower(s)          # NFC trước khi so tiền tố: chữ có dấu dạng tổ hợp (NFD) vẫn khớp 'tỉnh'
    for p in prefixes:
        p = _nfc_lower(p)
        if s.startswith(p + " "):
            s = s[len(p) + 1:]
            break
    s = strip_accents(s)
    return " ".join(s.replace("-", " ").split())


def slug(name: str) -> str:
    """'Thủ Dầu Một' -> 'THU_DAU_MOT' (mã trạm)."""
    s = strip_accents(unicodedata.normalize("NFC", name))
    return "".join(c if c.isalnum() else "_" for c in s).strip("_").upper()
