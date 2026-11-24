"""Tiện ích dùng chung cho các router: chuyển đổi dữ liệu, ngày giờ, địa giới.

Quy tắc quan trọng của dự án (xem `docs/decisions/0001`, `0003`):

- **Thời gian**: MongoDB lưu BSON Date theo UTC; dữ liệu gốc là giờ Việt Nam (UTC+7).
  Chuỗi ngày trả về API dùng định dạng ``YYYY-MM-DD`` theo **giờ Việt Nam**.
- **Địa giới**: mọi truy vấn địa giới phải kèm ``era`` (``before_2025_07`` / ``from_2025_07``).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from fastapi import HTTPException, status

from vngis.admin import BEFORE, ERAS, FROM, VN_TZ, era_of

__all__ = ["VN_TZ", "BEFORE", "FROM", "ERAS", "era_of", "clean", "to_vn_date", "to_vn_datetime",
           "parse_day", "parse_dt", "check_era", "HttpError", "obj_id", "page", "acquire_guard",
           "guard_check", "qc_http_error"]

#: Lỗi HTTP kèm mã trạng thái nghiệp vụ.
HttpError = HTTPException


def clean(doc: Any) -> Any:
    """Chuyển document MongoDB thành JSON an toàn (bỏ ``_id`` ObjectId, giữ nguyên datetime)."""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [clean(d) for d in doc]
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "_id" and isinstance(v, ObjectId):
                continue
            out[k] = clean(v)
        return out
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.isoformat()
    if isinstance(doc, date):
        return doc.isoformat()
    return doc


def to_vn_date(dt: datetime | str | None) -> str | None:
    """Chuỗi ngày ``YYYY-MM-DD`` theo giờ Việt Nam của một Date UTC."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ).strftime("%Y-%m-%d")


def to_vn_datetime(dt: datetime | str | None) -> str | None:
    """Chuỗi ISO 8601 theo giờ Việt Nam.

    Chấp nhận cả `datetime` lẫn chuỗi ISO (trường hợp document đã đi qua `clean()` nên
    datetime đã thành chuỗi) — nếu không thì `dt.tzinfo` ném `AttributeError`.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ).isoformat()


def parse_day(s: str | None, *, field: str = "from") -> datetime | None:
    """Parse chuỗi ngày ``YYYY-MM-DD`` (giờ Việt Nam) thành Date UTC (00:00 giờ VN)."""
    if not s:
        return None
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{field} phải có dạng YYYY-MM-DD, nhận {s!r}") from None
    return datetime(d.year, d.month, d.day, tzinfo=VN_TZ).astimezone(timezone.utc)


def parse_dt(s: str | None, *, field: str = "ts") -> datetime | None:
    """Parse chuỗi ISO 8601 thành Date UTC; không có timezone thì coi là giờ Việt Nam."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{field} phải là chuỗi ISO 8601, nhận {s!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(timezone.utc)


def check_era(era: str) -> str:
    """Kiểm tra `era` hợp lệ; sai → 422."""
    if era not in ERAS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"era phải là một trong {list(ERAS)}, nhận {era!r}")
    return era


def obj_id(value: str, *, field: str = "id") -> ObjectId:
    """Parse ObjectId; sai → 422."""
    try:
        return ObjectId(value)
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{field} không phải ObjectId hợp lệ: {value!r}") from None


def page(items: list, limit: int, offset: int) -> dict:
    """Bọc kết quả phân trang."""
    total = len(items)
    window = items[offset:offset + limit]
    return {"total": total, "count": len(window), "limit": limit, "offset": offset, "items": window}


def day_bounds(d_from: datetime | None, d_to: datetime | None) -> dict:
    """Khoảng thời gian mặc định: 30 ngày gần nhất tính từ hiện tại (giờ VN)."""
    end = d_to or datetime.now(timezone.utc)
    start = d_from or (end - timedelta(days=30))
    return {"from": start, "to": end}


# ------------------------------------------------------------------ khoá + QC --------
def acquire_guard(guard: Any, *, busy_detail: str) -> None:
    """`guard.acquire()`; bận → 423, mất khoá ngay lúc lấy fence → 503."""
    from vngis.locks import LockBusy, LockLost

    try:
        guard.acquire()
    except LockBusy as e:
        raise HTTPException(status.HTTP_423_LOCKED, detail=f"{busy_detail}: {e}") from None
    except LockLost as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e),
                            headers={"Retry-After": "1"}) from None


def guard_check(guard: Any) -> None:
    """Gọi NGAY trước mỗi lệnh ghi khi đang giữ `LockGuard`: đã mất khoá (watchdog gia hạn thất bại)
    thì dừng, trả 503 — không ghi khi tiến trình khác có thể đang giữ khoá."""
    from vngis.locks import LockLost

    try:
        guard.check()
    except LockLost as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e),
                            headers={"Retry-After": "1"}) from None


def qc_http_error(e: Exception) -> HTTPException:
    """Đổi lỗi của `vngis.qc` thành HTTP: không có → 404, xung đột → 409, dữ liệu sai → 422."""
    from vngis import qc

    if isinstance(e, qc.VersionConflict):
        return HTTPException(status.HTTP_409_CONFLICT,
                             detail={"error": "xung đột phiên bản", "flag_id": e.flag_id,
                                     "expected": e.expected, "current": e.current})
    if isinstance(e, qc.FieldConflict):
        return HTTPException(status.HTTP_409_CONFLICT,
                             detail={"error": "giá trị hiện tại khác giá trị mong đợi", "field": e.field,
                                     "expected": clean(e.expected), "current": clean(e.current)})
    if isinstance(e, qc.FlagExists):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
    if isinstance(e, qc.FlagNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    if isinstance(e, ValueError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    if isinstance(e, qc.AuditStateError):
        return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    raise e
