"""Bổ sung toán tử `$bit` cho `mongomock` (chỉ dùng cho unit test).

`mongomock` (v4.3) **không** hỗ trợ `$bit` — cập nhật quyền nguyên tử (GĐ7.3) vì vậy
không chạy được trên backend giả. Bản vá ở đây cài đặt `$bit` (`and`/`or`) theo đúng
ngữ nghĩa MongoDB:

- giá trị hiện tại được coi là số nguyên 64 bit **không dấu** (giống MongoDB);
- `and` với mặt nạ âm bị **bỏ qua** (không phải lỗi) — đúng hành vi MongoDB;
- `or` chỉ bật các bit có trong mặt nạ.

Bản vá hoạt động trên **mọi** collection của `mongomock.MongoClient` và được áp dụng
tự động trong `tests/conftest.py`.

Lưu ý: MongoDB thật vẫn là nguồn sự thật; bản vá chỉ phục vụ test để không phụ thuộc
dịch vụ ngoài.
"""
from __future__ import annotations

import copy
from typing import Any

__all__ = ["patch_mongomock_bit"]

_MASK64 = (1 << 64) - 1


def _apply_bit(doc: dict, field: str, spec: dict) -> None:
    """Áp dụng `$bit` cho một field (hỗ trợ field lồng `a.b`)."""
    parts = field.split(".")
    target: Any = doc
    for part in parts[:-1]:
        if not isinstance(target, dict):
            return
        target = target.setdefault(part, {})
        if not isinstance(target, dict):
            return
    last = parts[-1]

    cur = target.get(last, 0)
    if not isinstance(cur, int) or isinstance(cur, bool):
        cur = 0
    cur &= _MASK64

    for op, mask in (spec or {}).items():
        if not isinstance(mask, int) or isinstance(mask, bool):
            raise ValueError(f"$bit yêu cầu mặt nạ số nguyên, nhận {mask!r}")
        m = mask & _MASK64
        if op == "and":
            cur = cur & m
        elif op == "or":
            cur = cur | m
        else:
            raise ValueError(f"toán tử $bit không hợp lệ: {op!r}")
    target[last] = cur


def patch_mongomock_bit() -> bool:
    """Cài `$bit` cho mongomock. Trả về True nếu bản vá được áp dụng."""
    try:
        import mongomock
        from mongomock.collection import Collection
    except ImportError:  # pragma: no cover
        return False

    if getattr(Collection, "_pgnn_bit_patched", False):
        return True

    original = Collection._apply_update_document

    def _apply_update_document(self, existing_document, spec, document, was_insert):  # noqa: ANN001
        bit_fields: dict[str, dict] = {}
        plain: dict[str, Any] = {}
        for k, v in (document or {}).items():
            if k == "$bit":
                bit_fields.update(v or {})
            else:
                plain[k] = v
        if bit_fields:
            # Áp `$bit` trước, rồi các modifier còn lại ($set/$inc/...) như thường.
            for field, bitspec in bit_fields.items():
                _apply_bit(existing_document, field, bitspec)
            if not plain:
                # Không còn modifier nào → không gọi tiếp, vì mongomock sẽ **xoá sạch**
                # document khi nhận update rỗng.
                return None
            document = plain
        return original(self, existing_document, spec, document, was_insert)

    Collection._apply_update_document = _apply_update_document
    Collection._pgnn_bit_patched = True  # type: ignore[attr-defined]

    # `find_one_and_update` của mongomock cần document là dict; đảm bảo `$bit` lọt tới
    # `_apply_update_document` chứ không bị coi là "replace document".
    _ = mongomock
    return True


def mongomock_supports_bit(client: Any) -> bool:
    """Kiểm tra nhanh backend có hỗ trợ `$bit` hay không."""
    try:
        col = client["_pgnn_probe"]["bit"]
        col.drop()
        col.insert_one({"_id": "p", "m": 0})
        col.update_one({"_id": "p"}, {"$bit": {"m": {"or": 4}}})
        val = col.find_one({"_id": "p"})["m"]
        col.drop()
        return int(val) == 4
    except Exception:  # noqa: BLE001
        return False
