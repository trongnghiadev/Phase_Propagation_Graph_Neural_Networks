"""Phân quyền dạng Bitmask 12 bit (đề cương 5.4, mục GĐ7.3).

Bitmask 12 bit: mỗi bit là một quyền. Kiểm tra quyền là phép AND nên O(1); lưu gọn trong
một số nguyên; cập nhật nguyên tử bằng ``$bit`` của MongoDB.

Bảng vai trò mặc định (đề cương 5.4.2) — 8 vai trò, giá trị bitmask:

======== ============================ ========
Vai trò  Quyền                        Bitmask
======== ============================ ========
viewer   READ                                  1
analyst  READ \\| EXPORT                       17
editor   READ \\| CREATE \\| UPDATE \\| EXPORT    23
reviewer editor \\| APPROVE \\| VIEW_AUDIT      183
data_engineer reviewer \\| DELETE \\| RUN_JOB    255
hydrologist READ \\| EXPORT \\| VIEW_AUDIT \\| RUN_FORECAST   1169
forecast_officer hydrologist \\| APPROVE \\| MANAGE_MODEL 3249
admin    tất cả 12 bit                     4095
======== ============================ ========

Quyền theo collection: ``{"hydromet_bulletins": 1, "hydromet_bulletin_files": 17, ...}``;
collection không khai báo thì dùng giá trị của ``"*"`` (mặc định: chỉ đọc).

    from api.permissions import P, require, has
    @router.get("/bulletins", dependencies=[Depends(require(P.READ, "hydromet_bulletins"))])
    ...
"""
from __future__ import annotations

from enum import IntFlag
from typing import Any

__all__ = ["P", "ROLES", "ROLE_MASKS", "has", "mask_of", "role_mask", "PERM_ALL", "MAX_BITS",
           "flag_names", "mask_from_names", "validate_mask", "KNOWN_PERM_COLLECTIONS",
           "validate_perm_collection"]


class P(IntFlag):
    """12 quyền của hệ thống (mỗi bit một quyền)."""
    READ = 1
    CREATE = 2
    UPDATE = 4
    DELETE = 8
    EXPORT = 16
    APPROVE = 32
    RUN_JOB = 64
    VIEW_AUDIT = 128
    MANAGE_USERS = 256
    MANAGE_CACHE = 512
    RUN_FORECAST = 1024
    MANAGE_MODEL = 2048


#: 12 bit dùng hết.
MAX_BITS = 12
#: Toàn bộ quyền (tất cả 12 bit) = 4095.
PERM_ALL = int(P.READ | P.CREATE | P.UPDATE | P.DELETE | P.EXPORT | P.APPROVE | P.RUN_JOB | P.VIEW_AUDIT
               | P.MANAGE_USERS | P.MANAGE_CACHE | P.RUN_FORECAST | P.MANAGE_MODEL)

#: Bảng vai trò mặc định (đề cương 5.4.2). Tên vai trò là khoá, giá trị là bitmask.
ROLE_MASKS: dict[str, int] = {
    "viewer": 1,
    "analyst": 17,
    "editor": 23,
    "reviewer": 183,
    "data_engineer": 255,
    "hydrologist": 1169,
    "forecast_officer": 3249,
    "admin": 4095,
}

#: Mô tả quyền của từng vai trò (để hiển thị trong API quản trị).
ROLES: dict[str, dict[str, Any]] = {
    "viewer": {"mask": 1, "perm": ["READ"],
               "description": "Chỉ xem dữ liệu, bản đồ, dự báo, bản tin"},
    "analyst": {"mask": 17, "perm": ["READ", "EXPORT"],
                "description": "Xem và xuất dữ liệu (GeoJSON/CSV, tải PDF)"},
    "editor": {"mask": 23, "perm": ["READ", "CREATE", "UPDATE", "EXPORT"],
               "description": "Nhập liệu, sửa QC, sửa thuộc tính"},
    "reviewer": {"mask": 183, "perm": ["READ", "CREATE", "UPDATE", "EXPORT", "APPROVE", "VIEW_AUDIT"],
                 "description": "Duyệt thay đổi, xem nhật ký"},
    "data_engineer": {"mask": 255, "perm": ["READ", "CREATE", "UPDATE", "EXPORT", "APPROVE", "RUN_JOB",
                                           "VIEW_AUDIT"],
                      "description": "Chạy job, nạp lại bản tin, xoá mềm"},
    "hydrologist": {"mask": 1169, "perm": ["READ", "EXPORT", "VIEW_AUDIT", "RUN_FORECAST"],
                    "description": "Chạy dự báo, huấn luyện, xem nhật ký"},
    "forecast_officer": {"mask": 3249, "perm": ["READ", "EXPORT", "VIEW_AUDIT", "RUN_FORECAST", "APPROVE",
                                                "MANAGE_MODEL"],
                         "description": "Duyệt và phát hành mô hình, phát cảnh báo"},
    "admin": {"mask": 4095, "perm": [f.name for f in P],
              "description": "Toàn quyền, gồm quản lý người dùng và cache"},
}


def has(mask: int, required: int) -> bool:
    """Kiểm tra mask có đủ TẤT CẢ bit của `required` hay không."""
    return (int(mask) & int(required)) == int(required)


def mask_of(perm: dict[str, int] | dict[str, Any], collection: str) -> int:
    """Mask quyền của một collection trong bản đồ quyền theo collection.

    Collection không khai báo → dùng giá trị ``"*"``; cũng không có ``"*"`` → 0 (không quyền gì).

    **Lưu ý:** giá trị ``0`` khai báo tường minh (collection đã bị thu hồi toàn bộ quyền)
    vẫn được tôn trọng — không quay về ``"*"``.
    """
    if collection in perm:
        return int(perm[collection])
    if "*" in perm:
        return int(perm["*"])
    return 0


def role_mask(role: str) -> int:
    """Bitmask của một vai trò; ``KeyError`` nếu không có vai trò đó."""
    return ROLE_MASKS[role]


def flag_names(mask: int) -> list[str]:
    """Danh sách tên quyền có trong một mask."""
    return [f.name for f in P if int(mask) & int(f)]


def mask_from_names(names: list[str] | tuple[str, ...]) -> int:
    """Chuyển danh sách **tên** quyền thành mask.

    Ném `ValueError` nếu có tên không hợp lệ (giúp API trả 422 thay vì 500).
    """
    out = 0
    for n in names or ():
        key = str(n).strip().upper()
        if not hasattr(P, key):
            raise ValueError(f"quyền không hợp lệ: {n!r} (hợp lệ: {[f.name for f in P]})")
        out |= int(getattr(P, key))
    return out


def _known_collections() -> frozenset[str]:
    """Tên được phép làm khoá trong `users.perm`: `"*"`, collection logic của `vngis.config.Collections`,
    và các phạm vi quyền không phải collection mà router dùng (`cache`, `locks`, `system`, `rivers`)."""
    names = {"*", "cache", "locks", "system", "rivers"}
    try:
        from vngis.config import Collections
        names |= set(Collections.model_fields)
    except Exception:  # noqa: BLE001 - thiếu vngis (không xảy ra khi cài đủ) → danh sách tối thiểu
        names |= {"hydromet_bulletins", "hydromet_bulletin_files", "stations", "admin_units",
                  "admin_unit_mappings", "province_timeline", "users", "roles", "forecasts", "alerts",
                  "models", "jobs", "tide_extremes", "reservoir_daily", "qc_flags", "audit_logs"}
    return frozenset(names)


#: Collection (khoá của `users.perm`) được phép cấp quyền.
KNOWN_PERM_COLLECTIONS: frozenset[str] = _known_collections()


def validate_perm_collection(name: str) -> str:
    """Tên collection hợp lệ để làm khoá trong `perm` — chặn tiêm đường dẫn (`perm.a.b`, `$set`…)."""
    if not isinstance(name, str) or not name or "." in name or "$" in name or "\x00" in name:
        raise ValueError(f"tên collection không hợp lệ: {name!r} (không được rỗng, chứa '.' hoặc '$')")
    if name not in KNOWN_PERM_COLLECTIONS:
        raise ValueError(f"collection không có trong danh sách đã biết: {name!r}")
    return name


def validate_mask(mask: int) -> int:
    """Kiểm tra mask chỉ dùng 12 bit đã định nghĩa; trả mask đã chuẩn hoá."""
    m = int(mask)
    if m < 0:
        raise ValueError("bitmask không được âm")
    if m & ~PERM_ALL:
        raise ValueError(f"bitmask {m} dùng bit ngoài 12 bit đã định nghĩa")
    return m
