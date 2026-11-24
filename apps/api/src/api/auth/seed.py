"""Tạo tài khoản mẫu cho 8 vai trò mặc định (GĐ7.2).

**Quy tắc bảo mật (quan trọng):**
- Mật khẩu được **sinh ngẫu nhiên** bằng `secrets`; chỉ lưu **hash argon2** vào MongoDB.
- Mật khẩu rõ chỉ in ra **một lần** khi chạy script (dùng cho bootstrap), và **không** được
  ghi vào báo cáo, log, hay commit vào repo công khai.
- Nếu biến môi trường `SEED_PASSWORD` được đặt, dùng mật khẩu đó cho MỌI tài khoản
  (tiện cho môi trường CI / test nội bộ); mặc định là mật khẩu ngẫu nhiên.

Cách dùng::

    python -m api.auth.seed --db mongodb://localhost:27017/vngis
    python -m api.auth.seed --reset          # tạo lại (xoá tài khoản cũ của 8 vai trò)
    python -m api.auth.seed --print-passwords  # in mật khẩu (chỉ dùng bootstrap)

Tài khoản mẫu có tên dạng ``<role>_seed`` (viewer, analyst, editor, reviewer, data_engineer,
hydrologist, forecast_officer, admin) — không dùng tên/email thật của ai.
"""
from __future__ import annotations

import argparse
import os
import sys

from api.auth.password import generate_password, hash_password
from api.auth.users import UserRepo
from api.permissions import ROLES

#: Tên tài khoản mẫu theo vai trò (không chứa thông tin cá nhân).
SEED_USERNAMES: dict[str, str] = {
    "viewer": "viewer_seed",
    "analyst": "analyst_seed",
    "editor": "editor_seed",
    "reviewer": "reviewer_seed",
    "data_engineer": "data_engineer_seed",
    "hydrologist": "hydrologist_seed",
    "forecast_officer": "forecast_officer_seed",
    "admin": "admin_seed",
}

#: Mô tả vai trò (tiếng Việt) — dùng cho `display_name`.
DISPLAY_NAMES: dict[str, str] = {
    "viewer": "Tài khoản xem (mẫu)",
    "analyst": "Tài khoản phân tích (mẫu)",
    "editor": "Tài khoản biên tập (mẫu)",
    "reviewer": "Tài khoản duyệt QC (mẫu)",
    "data_engineer": "Tài khoản kỹ sư dữ liệu (mẫu)",
    "hydrologist": "Tài khoản thủy văn (mẫu)",
    "forecast_officer": "Tài khoản dự báo (mẫu)",
    "admin": "Tài khoản quản trị (mẫu)",
}


def seed_roles(repo: UserRepo) -> int:
    """Đảm bảo bảng `roles` có đủ 8 vai trò mặc định."""
    return repo.ensure_roles()


def seed_users(repo: UserRepo, *, reset: bool = False,
               fixed_password: str | None = None) -> dict[str, str | None]:
    """Tạo tài khoản mẫu cho 8 vai trò.

    Trả về `{username: password_rõ}` — `password_rõ` là `None` nếu tài khoản đã tồn tại
    (không đổi mật khẩu) hoặc nếu không được yêu cầu in.
    """
    out: dict[str, str | None] = {}
    for role, username in SEED_USERNAMES.items():
        if role not in ROLES:
            raise ValueError(f"vai trò {role!r} không có trong bảng quyền")
        if reset:
            repo.users.delete_one({"_id": username})
        if repo.get_user_by_username(username) is not None:
            out[username] = None
            continue
        pwd = fixed_password or generate_password(20)
        repo.create_user(username=username, password_hash=hash_password(pwd), role=role,
                         display_name=DISPLAY_NAMES.get(role, username))
        out[username] = pwd
    return out


def main(argv: list[str] | None = None) -> int:
    """Điểm vào CLI. Mã thoát: 0 = thành công, 1 = lỗi."""
    ap = argparse.ArgumentParser(description="Tạo bảng vai trò và tài khoản mẫu")
    ap.add_argument("--db", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017/vngis"),
                    help="chuỗi kết nối MongoDB")
    ap.add_argument("--reset", action="store_true",
                    help="xoá và tạo lại tài khoản mẫu (giữ nguyên vai trò)")
    ap.add_argument("--print-passwords", action="store_true",
                    help="in mật khẩu rõ ra stdout (chỉ dùng khi bootstrap)")
    args = ap.parse_args(argv)

    try:
        from pymongo import MongoClient
        client = MongoClient(args.db, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        db = client.get_default_database()
    except Exception as e:  # noqa: BLE001
        print(f"không kết nối được MongoDB: {e}", file=sys.stderr)
        return 1

    repo = UserRepo(db)
    n_roles = seed_roles(repo)
    fixed = os.getenv("SEED_PASSWORD") or None
    created = seed_users(repo, reset=args.reset, fixed_password=fixed)

    print(f"đã bảo đảm {n_roles} vai trò trong collection `roles`")
    for username, pwd in created.items():
        if pwd is None:
            print(f"  - {username}: đã tồn tại, giữ nguyên mật khẩu")
        elif args.print_passwords:
            print(f"  - {username}: {pwd}")
        else:
            print(f"  - {username}: đã tạo (mật khẩu ngẫu nhiên, không in)")
    if not args.print_passwords:
        print("\nDùng --print-passwords nếu cần lấy mật khẩu (chỉ hiển thị một lần).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
