"""Các router của API, tất cả dưới tiền tố `/api/v1` (xem `apps/api/README.md`).

Mỗi router đúng một nhóm nghiệp vụ; `ROUTERS` là danh sách được `main.py` gắn vào app.

| Router | Nhóm |
|---|---|
| `auth` | đăng nhập / làm mới token / đăng xuất |
| `bulletins` | bản tin (danh sách, chi tiết, PDF gốc, nạp lại) |
| `stations` | danh mục trạm, triều của trạm, sửa giá trị đo |
| `admin_units` | địa giới hành chính theo hai thời kỳ |
| `reservoirs` | số liệu hồ chứa |
| `stats` | thống kê đỉnh triều / vượt báo động |
| `forecasts` | dự báo (mới nhất, so sánh, bản đồ, lan truyền), chạy dự báo, mô hình |
| `alerts` | cảnh báo và xác nhận |
| `features` | đặc trưng và lần dòng chảy trên đồ thị |
| `review` | hàng đợi kiểm duyệt (QC, gán trạm) |
| `jobs` | chạy job và theo dõi |
| `audit` | nhật ký thay đổi và hoàn tác |
| `users` | quản trị người dùng và quyền |
| `admin` | quản trị hệ thống (cache, khóa) |
"""
from __future__ import annotations

from fastapi import APIRouter

from api.router import (admin, admin_units, alerts, audit, auth, bulletins, features, forecasts,
                        jobs, reservoirs, review, stations, stats, users)

#: Danh sách router được gắn vào app (thứ tự không quan trọng).
ROUTERS: list[APIRouter] = [
    auth.router,
    bulletins.router,
    stations.router,
    admin_units.router,
    reservoirs.router,
    stats.router,
    forecasts.router,
    alerts.router,
    features.router,
    review.router,
    jobs.router,
    audit.router,
    users.router,
    admin.router,
]

__all__ = ["ROUTERS"]
