"""Địa giới hành chính theo hai thời kỳ (ADR 0003).

Từ 01/07/2025 00:00 giờ Việt Nam (= 2025-06-30T17:00:00Z) cả nước còn 34 tỉnh. Cùng một mã có thể là
hai đơn vị KHÁC NHAU ở hai thời kỳ (tỉnh 80: Long An → Tây Ninh), nên mọi truy vấn ở đây đều kèm ``era``.

Dữ liệu tra cứu: collection ``province_timeline`` (pipelines/ingest/admin_units/create_admin_units.py),
mỗi tỉnh một bản ghi ``{era, code, name, name_norm, aliases_norm?, valid_from, valid_to, successor?}``;
khoảng hiệu lực ``valid_from <= t < valid_to`` (null = không giới hạn phía đó).

Module chỉ dùng thư viện chuẩn khi chạy (pymongo chỉ để chú thích kiểu). Quy tắc gán tỉnh từ nhãn trong bản tin
nằm ở ``vngis.province_labels``.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING

from .text import norm_name

if TYPE_CHECKING:            # chỉ để chú thích kiểu: module này không cần pymongo khi chạy (dùng được trong Spark)
    from pymongo.database import Database

BEFORE = "before_2025_07"
FROM = "from_2025_07"
ERAS = (BEFORE, FROM)
EFFECTIVE_UTC = datetime(2025, 6, 30, 17, 0, tzinfo=timezone.utc)   # 01/07/2025 00:00 giờ Việt Nam
VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
TIMELINE = "province_timeline"


def to_utc(dt: datetime) -> datetime:
    """Datetime aware theo UTC. Naive được coi là UTC (đúng với Date đọc từ pymongo mặc định)."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def era_of(dt: datetime | date) -> str:
    """Thời kỳ địa giới của thời điểm ``dt``.

    - ``datetime`` naive: coi là UTC; aware: đổi về UTC.
    - ``date`` (không giờ): ngày lịch Việt Nam, tức 00:00 giờ Việt Nam của ngày đó.
    """
    return FROM if _instant(dt) >= EFFECTIVE_UTC else BEFORE


def _instant(dt: datetime | date) -> datetime:
    if isinstance(dt, datetime):
        return to_utc(dt)
    if isinstance(dt, date):
        return datetime.combine(dt, time(0), tzinfo=VN_TZ).astimezone(timezone.utc)
    raise TypeError(f"cần datetime hoặc date, nhận {type(dt).__name__}")


def _check_era(era: str) -> None:
    if era not in ERAS:
        raise ValueError(f"era phải là một trong {ERAS}, nhận {era!r}")


def find_province(db: Database, era: str, label: str | None, collection: str = TIMELINE) -> dict | None:
    """Tìm tỉnh theo tên/nhãn trong một thời kỳ, so khớp qua ``name_norm`` hoặc ``aliases_norm``.

    ``find_province(db, BEFORE, "Tỉnh Long An")`` → bản ghi Long An (mã 80) thời kỳ cũ; ``None`` nếu không thấy.
    """
    _check_era(era)
    if not label or not label.strip():
        return None
    n = norm_name(label)
    return db[collection].find_one({"era": era, "$or": [{"name_norm": n}, {"aliases_norm": n}]}, {"_id": 0})


def province_at(db: Database, dt: datetime | date, collection: str = TIMELINE) -> list[dict]:
    """Các tỉnh có hiệu lực tại thời điểm ``dt`` (63 tỉnh trước mốc, 34 tỉnh từ mốc), sắp theo mã."""
    t = _instant(dt)
    era = FROM if t >= EFFECTIVE_UTC else BEFORE
    q = {"era": era, "$and": [
        {"$or": [{"valid_from": None}, {"valid_from": {"$lte": t}}]},
        {"$or": [{"valid_to": None}, {"valid_to": {"$gt": t}}]},
    ]}
    return list(db[collection].find(q, {"_id": 0}).sort("code", 1))


def successor_of(db: Database, old_code: int, collection: str = TIMELINE) -> dict | None:
    """Tỉnh mới (thời kỳ ``from_2025_07``) kế thừa phần lớn tỉnh cũ mã ``old_code``.

    Lấy theo ``successor.code`` của bản ghi cũ (tỉnh mới nhận nhiều xã/phường nhất của tỉnh cũ).
    ``None`` nếu không có tỉnh cũ mã đó.
    """
    old = db[collection].find_one({"era": BEFORE, "code": int(old_code)}, {"_id": 0, "successor": 1})
    if not old or not old.get("successor"):
        return None
    return db[collection].find_one({"era": FROM, "code": old["successor"]["code"]}, {"_id": 0})
