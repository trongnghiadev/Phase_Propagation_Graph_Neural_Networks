"""Người dùng, vai trò và bảng quyền theo collection (đề cương 5.4, mục GĐ7.2–7.3).

Hai collection MongoDB:

- **`users`**: `{_id, username, account_code, password_hash, role, roles, perm: {coll: mask}, perm_ver,
  active, failed_logins, locked_until, created_at, updated_at}`.

`account_code` (dạng `acc_<12 hex>`, ngẫu nhiên, không suy ra được từ tên) là định danh ổn định dùng
để ghi người thực hiện vào `audit_logs.actor`, `jobs.requested_by`, … (`actor_id`): không lưu tên đăng
nhập vào nhật ký. Tài khoản cũ chưa có mã được cấp mã ở lần đầu cần (`ensure_account_code`).
- **`roles`**: `{_id, name, mask, perm: {coll: mask}, description}` — bảng vai trò mặc định.

Quyền thực tế của một người dùng = `perm` của họ (theo collection) **cộng** quyền được cấp
thêm theo từng collection. Bảng `perm` được cache trong Redis ở `perm:{user_id}` với TTL 5 phút;
đổi quyền → tăng `perm_ver`, xoá cache, tăng `ver:users` để vô hiệu cache khác.

Cập nhật quyền: `grant`/`revoke` dùng `$bit` (nguyên tử). Ghi đè mask (`set_collection_perm`,
`update_perms`) là MỘT lệnh `find_one_and_update` có điều kiện `perm_ver` (compare-and-set): hai request
đổi quyền đồng thời thì một bên thắng, bên kia nhận `PermissionChanged` (API trả 409), không mất thay đổi.
Tên collection trong `perm` được kiểm tra (`validate_perm_collection`): không chứa `.`/`$`, phải nằm
trong `KNOWN_PERM_COLLECTIONS`.

    from vngis.db import get_db
    from api.auth.users import UserRepo
    repo = UserRepo(get_db())
    repo.set_collection_perm(user_id, "tide_extremes", 4)      # thêm UPDATE
    repo.revoke_collection_perm(user_id, "tide_extremes", 4)   # thu hồi UPDATE
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.database import Database

from api.permissions import (P, PERM_ALL, ROLE_MASKS, ROLES, flag_names, has, mask_of,
                             validate_mask, validate_perm_collection)

__all__ = ["UserRepo", "PERM_CACHE_PREFIX", "USER_COLLECTION", "ROLE_COLLECTION", "perm_cache_key",
           "PermissionChanged", "UserNotFound", "InvalidRole", "new_account_code", "actor_id",
           "parse_actor", "ACTOR_PREFIX", "USERS_INDEXES", "MISSING_ACCOUNT_CODE"]

PERM_CACHE_PREFIX = "perm:"
USER_COLLECTION = "users"
ROLE_COLLECTION = "roles"


#: Index của `users`. `account_code` unique nhưng chỉ trên document có mã dạng chuỗi (partial) — tài khoản cũ
#: chưa có mã (thiếu trường / ``null``) không va nhau. Trùng định nghĩa `migrations/mongo/0008_users_account_code.py`
#: (test kiểm tra). API tạo idempotent khi khởi động (`UserRepo.ensure_indexes`).
USERS_INDEXES: list[dict] = [
    {"name": "account_code_unique", "key": {"account_code": 1}, "unique": True,
     "partialFilterExpression": {"account_code": {"$type": "string"}}},
]

#: Bộ lọc "chưa có mã tài khoản": thiếu trường, ``null``, chuỗi rỗng hoặc không phải chuỗi.
MISSING_ACCOUNT_CODE: dict = {"$or": [{"account_code": {"$in": [None, ""]}},
                                      {"account_code": {"$not": {"$type": "string"}}}]}


def perm_cache_key(user_id: str) -> str:
    """Khoá cache quyền trong Redis: ``perm:{user_id}``."""
    return f"{PERM_CACHE_PREFIX}{user_id}"


#: Tiền tố của định danh người thực hiện là tài khoản: ``user:<account_code>:<role>``.
ACTOR_PREFIX = "user:"
_ACTOR_RE = re.compile(r"^user:(acc_[0-9a-f]{6,32})(?::([A-Za-z0-9_.-]+))?$")


def new_account_code() -> str:
    """Mã tài khoản ngẫu nhiên, ổn định suốt đời tài khoản, không chứa tên người."""
    return f"acc_{secrets.token_hex(6)}"


def actor_id(account_code: str, role: str = "") -> str:
    """Định danh người thực hiện ghi vào nhật ký: ``user:<account_code>[:<role>]``.

    Vai trò là vai trò TẠI THỜI ĐIỂM thao tác; lọc theo người dùng chỉ dùng phần ``user:<account_code>``.
    """
    return f"{ACTOR_PREFIX}{account_code}" + (f":{role}" if role else "")


def parse_actor(actor: Any) -> tuple[str, str] | None:
    """``user:<account_code>[:<role>]`` → ``(account_code, role)``; giá trị cũ (tên đăng nhập, `cli`, …) → None."""
    if not isinstance(actor, str):
        return None
    m = _ACTOR_RE.match(actor)
    return (m.group(1), m.group(2) or "") if m else None


class UserNotFound(LookupError):
    """Không có người dùng đó."""


class InvalidRole(ValueError):
    """Tên vai trò không có trong bảng vai trò."""


class PermissionChanged(RuntimeError):
    """Quyền đã bị đổi giữa lúc đọc (perm_ver không khớp) → cần đọc lại."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserRepo:
    """Truy cập `users` và `roles`; quản lý cache quyền và `perm_ver`."""

    def __init__(self, db: Database, cache=None, *, perm_ttl_s: int = 300):
        self.db = db
        self.users = db[USER_COLLECTION]
        self.roles = db[ROLE_COLLECTION]
        self.cache = cache
        self.perm_ttl_s = perm_ttl_s

    # ------------------------------------------------------------ vai trò ------
    def ensure_roles(self) -> int:
        """Đảm bảo bảng `roles` có đủ 8 vai trò mặc định (idempotent). Trả số vai trò ghi."""
        n = 0
        for name, info in ROLES.items():
            self.roles.update_one({"_id": name}, {"$set": {"name": name, "mask": info["mask"],
                                                          "perm": {"*": info["mask"]},
                                                          "description": info["description"]},
                                   "$setOnInsert": {"created_at": _now()}}, upsert=True)
            n += 1
        return n

    def list_roles(self) -> list[dict]:
        """Danh sách vai trò kèm quyền."""
        out = []
        for doc in self.roles.find({"_id": {"$exists": True}}).sort("_id", ASCENDING):
            out.append({"name": doc.get("name", doc["_id"]), "mask": int(doc.get("mask", 0)),
                        "perm": doc.get("perm", {}), "description": doc.get("description", "")})
        return out

    def role_mask(self, role: str) -> int:
        """Mask của vai trò; ném `InvalidRole` nếu không có."""
        if role not in ROLE_MASKS:
            raise InvalidRole(f"vai trò không hợp lệ: {role!r} (có: {sorted(ROLE_MASKS)})")
        doc = self.roles.find_one({"_id": role})
        if doc and "mask" in doc:
            return int(doc["mask"])
        return int(ROLE_MASKS[role])

    # ------------------------------------------------------------ người dùng ----
    def create_user(self, username: str, password_hash: str, *, role: str = "viewer",
                    perm: dict[str, int] | None = None, user_id: str | None = None,
                    display_name: str = "", active: bool = True, roles: list[str] | None = None,
                    email: str | None = None, station: str | None = None) -> dict:
        """Tạo người dùng. `perm` (nếu có) là bảng quyền theo collection; mặc định lấy theo vai trò."""
        role_list = list(roles) if roles else [role]
        if not role_list:
            role_list = [role]
        for r in role_list:
            if r not in ROLE_MASKS:
                raise InvalidRole(f"vai trò không hợp lệ: {r!r}")
        mask = self.role_mask(role_list[0])
        for r in role_list[1:]:
            mask |= self.role_mask(r)
        perms = dict(perm) if perm else {"*": mask}
        doc = {
            "_id": user_id or username,
            "username": username,
            "account_code": new_account_code(),
            "display_name": display_name or username,
            "password_hash": password_hash,
            "role": role_list[0],
            "roles": role_list,
            "perm": perms,
            "perm_ver": 1,
            "active": active,
            "failed_logins": 0,
            "locked_until": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if email:
            doc["email"] = email
        if station:
            doc["station"] = station
        self.users.insert_one(doc)
        self._invalidate(user_id or username)
        return {k: v for k, v in doc.items() if k != "password_hash"}

    def get_user(self, user_id: str) -> dict | None:
        return self.users.find_one({"_id": user_id})

    def get_user_by_username(self, username: str) -> dict | None:
        return self.users.find_one({"username": username})

    def ensure_indexes(self) -> None:
        """Index của `users` (idempotent; gọi khi API khởi động). Định nghĩa trùng migration `0008`."""
        for ix in USERS_INDEXES:
            self.users.create_index(list(ix["key"].items()), name=ix["name"], unique=bool(ix.get("unique")),
                                    partialFilterExpression=ix["partialFilterExpression"])

    def ensure_account_code(self, user_id: str, doc: dict | None = None) -> str:
        """Mã tài khoản của `user_id`; tài khoản cũ chưa có thì cấp (nguyên tử, chỉ ghi khi còn thiếu).

        "Chưa có" = thiếu trường, ``null``, chuỗi rỗng hoặc không phải chuỗi (bộ lọc `MISSING_ACCOUNT_CODE`).
        Trùng mã (index unique `account_code_unique`, xác suất rất nhỏ) → thử mã khác.
        """
        doc = doc if doc is not None else self.users.find_one({"_id": user_id}, {"account_code": 1})
        if doc is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        code = doc.get("account_code")
        if isinstance(code, str) and code:
            return code
        for _ in range(5):
            try:
                self.users.update_one({"_id": user_id, **MISSING_ACCOUNT_CODE},
                                      {"$set": {"account_code": new_account_code()}})
                break
            except DuplicateKeyError:
                continue
        cur = self.users.find_one({"_id": user_id}, {"account_code": 1}) or {}
        code = cur.get("account_code")
        if not (isinstance(code, str) and code):
            raise UserNotFound(f"không cấp được account_code cho {user_id!r}")
        return code

    def get_user_by_account_code(self, code: str) -> dict | None:
        return self.users.find_one({"account_code": code})

    def actor_labels(self, actors: list[Any]) -> dict[str, dict]:
        """Nhãn hiển thị cho danh sách actor (chỉ trả khi đọc, KHÔNG ghi vào nhật ký).

        ``user:<code>[:<role>]`` → `{username, display_name, role, user_id}` của tài khoản hiện tại; giá trị
        cũ (tên đăng nhập) → tra theo `username` để vẫn đọc được bản ghi cũ; không khớp → không có nhãn.
        """
        codes: dict[str, str] = {}
        legacy: set[str] = set()
        for a in {x for x in actors if isinstance(x, str) and x}:
            parsed = parse_actor(a)
            if parsed:
                codes[a] = parsed[0]
            else:
                legacy.add(a)
        out: dict[str, dict] = {}
        proj = {"username": 1, "display_name": 1, "role": 1, "account_code": 1}
        by_code = {d.get("account_code"): d for d in
                   self.users.find({"account_code": {"$in": sorted(set(codes.values()))}}, proj)} if codes else {}
        for a, code in codes.items():
            role = parse_actor(a)[1]  # type: ignore[index]
            d = by_code.get(code)
            out[a] = {"actor_id": f"{ACTOR_PREFIX}{code}", "role": role or (d or {}).get("role"),
                      "username": (d or {}).get("username"), "user_id": (d or {}).get("_id"),
                      "display_name": (d or {}).get("display_name") or (d or {}).get("username"),
                      "legacy": False}
        if legacy:
            for d in self.users.find({"username": {"$in": sorted(legacy)}}, proj):
                out[d["username"]] = {"actor_id": f"{ACTOR_PREFIX}{d['account_code']}"
                                      if d.get("account_code") else None,
                                      "role": d.get("role"), "username": d.get("username"), "user_id": d["_id"],
                                      "display_name": d.get("display_name") or d.get("username"),
                                      "legacy": True}
        return out

    def list_users(self, limit: int = 200) -> list[dict]:
        out = []
        for d in self.users.find({"_id": {"$exists": True}}).sort("username", ASCENDING).limit(limit):
            out.append({"user_id": d["_id"], "username": d.get("username"), "display_name": d.get("display_name"),
                        "role": d.get("role"), "roles": d.get("roles", []), "active": bool(d.get("active", True)),
                        "perm_ver": int(d.get("perm_ver", 1)),
                        "perm": {k: int(v) for k, v in (d.get("perm") or {}).items()}})
        return out

    def set_active(self, user_id: str, active: bool) -> dict:
        """Bật/tắt tài khoản (quản trị người dùng)."""
        res = self.users.find_one_and_update({"_id": user_id}, {"$set": {"active": active, "updated_at": _now()}},
                                             return_document=ReturnDocument.AFTER)
        if res is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        self._invalidate(user_id)
        return res

    # ------------------------------------------------------------ quyền --------
    def effective_perm(self, user_id: str) -> dict[str, int]:
        """Bảng quyền theo collection của người dùng (đọc thẳng từ MongoDB, không qua cache).

        Nếu bản ghi **chưa** khai báo `perm["*"]`, thì suy ra quyền mặc định từ vai trò
        (gộp nhiều vai trò bằng OR). Nếu `perm["*"]` **đã** được khai báo thì lấy nguyên
        giá trị đó — không gộp lại vai trò, vì nếu gộp thì mọi thao tác thu hồi quyền
        (dùng `$bit`) trên `*` sẽ bị vô hiệu hoá.
        """
        doc = self.users.find_one({"_id": user_id}, {"perm": 1, "perm_ver": 1, "role": 1, "roles": 1})
        if doc is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        perm = {k: int(v) for k, v in (doc.get("perm") or {}).items()}
        if "*" not in perm:
            extra = 0
            for role in doc.get("roles") or [doc.get("role")]:
                if role in ROLE_MASKS:
                    extra |= int(ROLE_MASKS[role])
            if extra:
                perm["*"] = extra
        return perm

    def perm_ver(self, user_id: str) -> int:
        doc = self.users.find_one({"_id": user_id}, {"perm_ver": 1})
        if doc is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        return int(doc.get("perm_ver", 1))

    def get_perm_cached(self, user_id: str) -> dict[str, int]:
        """Bảng quyền, lấy từ cache Redis `perm:{user_id}` (TTL 5 phút) nếu có.

        Trả về dict kèm hai khoá đặc biệt: ``__ver`` (perm_ver) và ``__cached`` (True/False).
        """
        if self.cache is not None:
            raw = self.cache.get(perm_cache_key(user_id))
            if raw is not None:
                if isinstance(raw, dict):
                    return {**raw, "__cached": True}
        perm = self.effective_perm(user_id)
        ver = self.perm_ver(user_id)
        out = {**perm, "__ver": ver, "__cached": False}
        if self.cache is not None:
            self.cache.set(perm_cache_key(user_id), out, self.perm_ttl_s)
        return out

    def mask_for(self, user_id: str, collection: str) -> int:
        """Mask quyền của người dùng cho một collection."""
        perm = self.get_perm_cached(user_id)
        clean = {k: v for k, v in perm.items() if not k.startswith("__")}
        return mask_of(clean, collection)

    def has_perm(self, user_id: str, collection: str, required: int) -> bool:
        """Người dùng có đủ quyền `required` trên `collection` hay không."""
        return has(self.mask_for(user_id, collection), required)

    # ------------------------------------------------------------ đổi quyền ----
    def set_collection_perm(self, user_id: str, collection: str, mask: int, *,
                            expected_perm_ver: int | None = None) -> dict:
        """Đặt (ghi đè) mask quyền của một collection bằng MỘT lệnh update có điều kiện `perm_ver`.

        `expected_perm_ver` bỏ trống → dùng `perm_ver` vừa đọc (vẫn là compare-and-set: nếu ai đó đổi quyền
        giữa lúc đọc và ghi thì lệnh không khớp → `PermissionChanged`, không ghi đè thay đổi của họ).

        Giá trị 0 được **lưu là 0** (không `$unset`): xoá hẳn field thì `mask_of` quay về `perm["*"]` và
        quyền bị thu hồi sẽ "tự quay lại".
        """
        validate_mask(mask)
        validate_perm_collection(collection)
        return self.update_perms(user_id, {collection: int(mask)}, expected_perm_ver=expected_perm_ver)

    def update_perms(self, user_id: str, masks: dict[str, int], *,
                     expected_perm_ver: int | None = None) -> dict:
        """Ghi đè nhiều mask (`{collection | "*": mask}`) trong MỘT lệnh có điều kiện `perm_ver`, tăng `perm_ver`.

        Lỗi: `UserNotFound`, `PermissionChanged` (perm_ver hiện tại ≠ mong đợi), `ValueError` (mask/tên sai).
        """
        if not masks:
            raise ValueError("không có quyền nào để ghi")
        sets: dict[str, Any] = {}
        for coll, m in masks.items():
            validate_perm_collection(coll)
            sets[f"perm.{coll}"] = validate_mask(m)
        if expected_perm_ver is None:
            expected_perm_ver = self.perm_ver(user_id)
        sets["updated_at"] = _now()
        res = self.users.find_one_and_update({"_id": user_id, "perm_ver": int(expected_perm_ver)},
                                             {"$set": sets, "$inc": {"perm_ver": 1}},
                                             return_document=ReturnDocument.AFTER)
        if res is None:
            cur = self.users.find_one({"_id": user_id}, {"perm_ver": 1})
            if cur is None:
                raise UserNotFound(f"không có người dùng {user_id!r}")
            raise PermissionChanged(f"perm_ver của {user_id!r} là {cur.get('perm_ver', 1)}, "
                                    f"mong đợi {expected_perm_ver}")
        self._invalidate(user_id)
        return res

    def _perm_field(self, user_id: str, collection: str) -> str:
        """Chọn field chứa quyền để thao tác: `perm.{collection}` nếu đã khai báo riêng,
        ngược lại `perm.*` (quyền mặc định áp cho mọi collection chưa khai báo).

        Nhờ vậy `revoke(user, "stations", READ)` khi chưa có `perm.stations` sẽ xoá bit READ
        khỏi `perm.*` — đúng như ý nghĩa "thu hồi quyền đọc trên collection stations",
        thay vì tạo một override ở `perm.stations` chỉ chứa các bit còn lại.
        """
        doc = self.users.find_one({"_id": user_id}, {"perm": 1}) or {}
        perm = doc.get("perm") or {}
        return f"perm.{collection}" if collection in perm else "perm.*"

    def grant(self, user_id: str, collection: str, flag: int) -> dict:
        """CấP thêm một quyền (`$bit or`) — nguyên tử."""
        validate_mask(flag)
        field = self._perm_field(user_id, collection)
        res = self.users.find_one_and_update({"_id": user_id},
                                             {"$bit": {field: {"or": int(flag)}},
                                              "$inc": {"perm_ver": 1}, "$set": {"updated_at": _now()}},
                                             return_document=ReturnDocument.AFTER)
        if res is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        self._invalidate(user_id)
        return res

    def revoke(self, user_id: str, collection: str, flag: int) -> dict:
        """Thu hồi một quyền (`$bit and`) — nguyên tử.

        **Lưu ý kỹ thuật:** `$bit and` của MongoDB dùng số nguyên 64 bit **không dấu**,
        nên phải truyền mặt nạ dương. Với bitmask 12 bit, mặt nạ xoá bit `flag` là
        ``PERM_ALL ^ flag`` (= 4095 ^ flag), **không phải** ``~flag`` (số âm — sẽ bị bỏ qua).
        """
        validate_mask(flag)
        field = self._perm_field(user_id, collection)
        clear_mask = PERM_ALL ^ int(flag)
        res = self.users.find_one_and_update({"_id": user_id},
                                             {"$bit": {field: {"and": clear_mask}},
                                              "$inc": {"perm_ver": 1}, "$set": {"updated_at": _now()}},
                                             return_document=ReturnDocument.AFTER)
        if res is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        self._invalidate(user_id)
        return res

    def apply_bits(self, user_id: str, *, grant: int = 0, revoke: int = 0,
                   expected_version: int | None = None) -> dict | None:
        """Bật/tắt quyền trên mask mặc định `"*"` bằng `$bit` — nguyên tử.

        Dùng cho `PUT /users/{id}/perm`: hai request đổi quyền đồng thời không mất thay đổi
        của nhau (vì `$bit` là thao tác nguyên tử ở tầng CSDL, không phải đọc–ghi–ghi).

        `expected_version` → thêm điều kiện `perm_ver` để phát hiện xung đột (409).
        Trả về `None` nếu không khớp điều kiện (người dùng không tồn tại hoặc xung đột phiên bản).

        **Lưu ý:** `$bit and` cần mặt nạ **dương** (`PERM_ALL ^ flag`), không phải `~flag`.
        """
        validate_mask(grant)
        validate_mask(revoke)
        if not grant and not revoke:
            return self.get_user(user_id)
        flt: dict[str, Any] = {"_id": user_id}
        if expected_version is not None:
            flt["perm_ver"] = int(expected_version)
        if grant and revoke:
            # Vừa bật vừa tắt: hai thao tác `$bit` tuần tự, cùng điều kiện phiên bản.
            # Mặt nạ `and` là số dương (`PERM_ALL ^ revoke`); số âm sẽ bị MongoDB bỏ qua.
            res = self.users.find_one_and_update(flt, {"$bit": {"perm.*": {"or": int(grant)}}},
                                                 return_document=ReturnDocument.AFTER)
            if res is None:
                return None
            res = self.users.find_one_and_update(
                flt, {"$bit": {"perm.*": {"and": PERM_ALL ^ int(revoke)}},
                      "$inc": {"perm_ver": 1}, "$set": {"updated_at": _now()}},
                return_document=ReturnDocument.AFTER)
            if res is None:
                return None
            self._invalidate(user_id)
            return res
        if grant:
            bit = {"or": int(grant)}
        else:
            bit = {"and": PERM_ALL ^ int(revoke)}
        res = self.users.find_one_and_update(flt, {"$bit": {"perm.*": bit},
                                                   "$inc": {"perm_ver": 1},
                                                   "$set": {"updated_at": _now()}},
                                             return_document=ReturnDocument.AFTER)
        if res is None:
            return None
        self._invalidate(user_id)
        return res

    def set_role(self, user_id: str, role: str) -> dict:
        """Đổi vai trò chính (kèm quyền mặc định theo vai trò), tăng `perm_ver`."""
        if role not in ROLE_MASKS:
            raise InvalidRole(f"vai trò không hợp lệ: {role!r}")
        mask = self.role_mask(role)
        res = self.users.find_one_and_update({"_id": user_id},
                                             {"$set": {"role": role, "roles": [role], "perm": {"*": mask},
                                                       "updated_at": _now()},
                                              "$inc": {"perm_ver": 1}},
                                             return_document=ReturnDocument.AFTER)
        if res is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        self._invalidate(user_id)
        return res

    def set_password(self, user_id: str, password_hash: str) -> dict:
        """Đổi mật khẩu (lưu hash), xoá số lần đăng nhập sai."""
        res = self.users.find_one_and_update({"_id": user_id},
                                             {"$set": {"password_hash": password_hash, "failed_logins": 0,
                                                       "locked_until": None, "updated_at": _now()},
                                              "$inc": {"perm_ver": 1}},
                                             return_document=ReturnDocument.AFTER)
        if res is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        self._invalidate(user_id)
        return res

    def revoke_all_sessions(self, user_id: str) -> int | None:
        """Thu hồi MỌI phiên của người dùng: tăng `perm_ver` → mọi access/refresh token đã phát
        (mang `perm_ver` cũ) bị từ chối ở lần dùng tiếp theo. Trả `perm_ver` mới (None nếu không có user)."""
        res = self.users.find_one_and_update({"_id": user_id},
                                             {"$inc": {"perm_ver": 1}, "$set": {"updated_at": _now()}},
                                             return_document=ReturnDocument.AFTER, projection={"perm_ver": 1})
        self._invalidate(user_id)
        return None if res is None else int(res.get("perm_ver", 1))

    # ------------------------------------------------------------ khóa tài khoản
    def register_failure(self, user_id: str, *, max_failures: int = 10, lock_minutes: int = 15) -> dict:
        """Ghi nhận đăng nhập sai; quá ngưỡng thì khoá tạm."""
        doc = self.users.find_one_and_update({"_id": user_id}, {"$inc": {"failed_logins": 1}},
                                             return_document=ReturnDocument.AFTER)
        if doc is None:
            raise UserNotFound(f"không có người dùng {user_id!r}")
        if int(doc.get("failed_logins", 0)) >= max_failures:
            self.users.update_one({"_id": user_id}, {"$set": {"locked_until": _now() + timedelta(
                minutes=lock_minutes)}})
            doc["locked_until"] = _now() + timedelta(minutes=lock_minutes)
        return doc

    def reset_failures(self, user_id: str) -> None:
        self.users.update_one({"_id": user_id}, {"$set": {"failed_logins": 0, "locked_until": None}})

    def is_locked(self, doc: dict) -> bool:
        lu = doc.get("locked_until")
        if not lu:
            return False
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        return lu > _now()

    # ------------------------------------------------------------ cache -------
    def _invalidate(self, user_id: str) -> None:
        """Xoá cache quyền và tăng phiên bản cache người dùng."""
        if self.cache is not None:
            try:
                self.cache.delete(perm_cache_key(user_id))
                self.cache.bump_version("users")
            except Exception:  # noqa: BLE001 - cache lỗi không được làm hỏng việc đổi quyền
                pass

    def public_user(self, doc: dict) -> dict:
        """Thông tin người dùng an toàn để trả về client (không có hash)."""
        perm = {k: int(v) for k, v in (doc.get("perm") or {}).items()}
        return {"user_id": doc["_id"], "username": doc.get("username"),
                "account_code": doc.get("account_code"),
                "display_name": doc.get("display_name", doc.get("username")), "role": doc.get("role"),
                "roles": doc.get("roles", []), "active": bool(doc.get("active", True)),
                "perm_ver": int(doc.get("perm_ver", 1)), "perm": perm,
                "perm_flags": {coll: flag_names(mask) for coll, mask in perm.items()}}


def permission_matrix(perms: dict[str, int]) -> dict[str, list[str]]:
    """Chuyển bảng quyền theo collection thành danh sách tên quyền (để trả về client)."""
    return {coll: flag_names(mask) for coll, mask in perms.items()}
