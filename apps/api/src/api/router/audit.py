"""Nhật ký thay đổi và hoàn tác (`GET /api/v1/audit`, `POST /api/v1/audit/{id}/revert`).

`audit_logs` lưu lại mọi thay đổi (nguồn `api`, `cli`, `pipeline`) với `state` ∈ `pending` | `applied`
(validator `vngis.qc_rules.AUDIT_LOGS_VALIDATOR`; không có trạng thái "reverted").

Hoàn tác (`revert`) KHÔNG ghi thẳng `before` vào collection tuỳ ý. Chỉ các loại audit trong danh sách
trắng `REVERTIBLE`, mỗi loại đi qua hàm ghi có kiểm toán của `vngis.qc` với điều kiện "chưa ai sửa tiếp":

- `qc_decision` (`qc_flags`) → `qc.decide(status/corrected_value cũ, expected_version = version sau
  thay đổi)`; cần quyền APPROVE trên `qc_flags`.
- `bulletin_metadata` (`hydromet_bulletins`) → `qc.update_bulletin_fields({field: (giá trị sau, giá trị
  trước)})` (điều kiện giá trị cũ); cần quyền UPDATE trên `hydromet_bulletins`.
- Loại khác (`qc_flag_create`, `qc_flag_retire`, `api_update`, `user_perm`, …): chưa có cách hoàn tác an
  toàn → 422.

Mỗi lần hoàn tác tạo một bản ghi `audit_logs` MỚI (do `vngis.qc` ghi, `source: "api_revert"`, `reason` nêu
id audit gốc). Bản ghi gốc chỉ được gắn thêm `reverted_at` / `reverted_by` / `revert_reason` /
`revert_audit_id` (không đổi `state`). Chặn hoàn tác lặp: đánh dấu `revert_claim` nguyên tử
(`update_one` có điều kiện chưa có `reverted_at` và `revert_claim`) trước khi ghi → lần thứ hai 409.

Quyền: `VIEW_AUDIT` trên `audit_logs` để xem/hoàn tác, CỘNG quyền ghi trên collection đích như trên.

Người thực hiện (`actor`, `reverted_by`): bản ghi mới là ``user:<account_code>:<role>`` (không chứa tên);
bản ghi cũ có thể còn tên đăng nhập. Khi đọc, API gắn thêm `actor_label` (tra `users`, không lưu vào
nhật ký). Lọc `?actor=` nhận ``user:<account_code>`` (mọi vai trò), id đầy đủ, hoặc tên đăng nhập (khớp cả
bản ghi cũ ghi tên lẫn bản ghi mới của tài khoản đó).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from bson import ObjectId

from api.auth.users import ACTOR_PREFIX, parse_actor
from api.deps import UserContext, require, rate_limit
from api.permissions import P, has
from api.router.common import clean, qc_http_error, to_vn_datetime
from api.state import state

log = logging.getLogger("api.audit")

router = APIRouter(tags=["audit"])

AUDIT_LOGS = "audit_logs"

#: Loại audit được phép hoàn tác: action → (collection đích bắt buộc, quyền cần trên collection đó).
REVERTIBLE: dict[str, tuple[str, int]] = {
    "qc_decision": ("qc_flags", int(P.APPROVE)),
    "bulletin_metadata": ("hydromet_bulletins", int(P.UPDATE)),
}


def _is_objectid(s: str) -> bool:
    """Chuỗi có phải ObjectId hợp lệ của MongoDB không."""
    try:
        ObjectId(s)
        return True
    except Exception:  # noqa: BLE001
        return False


class RevertRequest(BaseModel):
    """Yêu cầu hoàn tác một thay đổi đã ghi."""
    reason: str = Field(min_length=1, max_length=1000,
                        description="Lý do hoàn tác (bắt buộc, ghi vào audit_logs)")


@router.get("/audit", dependencies=[Depends(require(P.VIEW_AUDIT, AUDIT_LOGS))])
async def list_audit(collection: str | None = None, actor: str | None = None,
                     audit_state: str | None = Query(None, alias="state"),
                     from_: str | None = Query(None, alias="from"), to: str | None = None,
                     limit: int = Query(200, ge=1, le=2000)):
    """Danh sách bản ghi nhật ký thay đổi."""
    from api.router.common import parse_day

    db = state.db
    flt: dict[str, Any] = {}
    if collection:
        flt["collection"] = collection
    if actor:
        flt.update(actor_filter("actor", actor))
    if audit_state:
        flt["state"] = audit_state
    if from_ or to:
        d_to = parse_day(to, field="to") or datetime.now(timezone.utc)
        d_from = parse_day(from_, field="from") or d_to
        flt["at"] = {"$gte": d_from, "$lte": d_to}
    docs = list(db[AUDIT_LOGS].find(flt).sort([("at", -1)]).limit(limit))
    labels = _labels([d.get(f) for d in docs for f in ("actor", "reverted_by")])
    items = []
    for d in docs:
        out = clean(d)
        # `clean` bỏ `_id` nếu là ObjectId → phải lấy trước khi làm sạch.
        out["id"] = str(d.get("_id")) if d.get("_id") is not None else None
        out.pop("_id", None)
        out["at"] = to_vn_datetime(out.get("at"))
        out["reverted_at"] = to_vn_datetime(out.get("reverted_at"))
        out["actor_label"] = labels.get(out.get("actor"))
        out["reverted_by_label"] = labels.get(out.get("reverted_by"))
        items.append(out)
    return {"count": len(items), "items": items}


def actor_filter(field: str, actor: str) -> dict[str, Any]:
    """Điều kiện lọc theo người thực hiện, đọc được cả bản ghi cũ (tên) lẫn mới (``user:<code>:<role>``)."""
    import re as _re

    actor = actor.strip()
    parsed = parse_actor(actor)
    if parsed is not None:
        code, role = parsed
        if role:
            return {field: actor}
        return {field: {"$regex": f"^{_re.escape(ACTOR_PREFIX + code)}(:|$)"}}
    user = state.repo.get_user_by_username(actor)
    if user is None or not user.get("account_code"):
        return {field: actor}       # tên cũ / actor hệ thống (`cli`, `g0`, …): khớp đúng
    pattern = f"^{_re.escape(ACTOR_PREFIX + str(user['account_code']))}(:|$)"
    return {"$or": [{field: actor}, {field: {"$regex": pattern}}]}


def _labels(actors: list[Any]) -> dict[str, dict]:
    """Nhãn hiển thị của actor (`api.router.common.actor_labels`); chỉ khi đọc."""
    from api.router.common import actor_labels
    return actor_labels(actors)


@router.get("/audit/{audit_id}", dependencies=[Depends(require(P.VIEW_AUDIT, AUDIT_LOGS))])
async def get_audit(audit_id: str):
    """Chi tiết một bản ghi nhật ký."""
    doc = state.db[AUDIT_LOGS].find_one({"_id": ObjectId(audit_id)}) if _is_objectid(audit_id) \
        else state.db[AUDIT_LOGS].find_one({"_id": audit_id})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có bản ghi nhật ký {audit_id!r}")
    out = clean(doc)
    out["id"] = str(doc.get("_id"))
    out.pop("_id", None)
    out["at"] = to_vn_datetime(out.get("at"))
    labels = _labels([doc.get("actor"), doc.get("reverted_by")])
    out["actor_label"] = labels.get(out.get("actor"))
    out["reverted_by_label"] = labels.get(out.get("reverted_by"))
    return out


def _aware(v: Any) -> Any:
    """datetime không múi giờ (pymongo mặc định) → UTC có múi giờ (yêu cầu của `update_bulletin_fields`)."""
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


def _find_new_audit(db, action: str, doc_id: str, at: datetime, actor: str) -> str | None:
    d = db[AUDIT_LOGS].find_one({"action": action, "doc_id": doc_id, "at": at, "actor": actor,
                                 "source": "api_revert"}, {"_id": 1})
    return str(d["_id"]) if d else None


def _revert_qc_decision(db, doc: dict, reason: str, actor: str, now: datetime) -> dict:
    from vngis import qc

    before, after = doc.get("before") or {}, doc.get("after") or {}
    if "status" not in before or after.get("version") is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="audit qc_decision thiếu before.status hoặc after.version: không hoàn tác được")
    return qc.decide(db, str(doc["doc_id"]), before["status"], reason=reason, decided_by=actor,
                     expected_version=int(after["version"]), corrected_value=before.get("corrected_value"),
                     source="api_revert", now=now)


def _revert_bulletin(db, doc: dict, reason: str, actor: str, now: datetime) -> dict:
    from vngis import qc

    before, after = doc.get("before") or {}, doc.get("after") or {}
    if not after or set(before) != set(after):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="audit bulletin_metadata thiếu before/after: không hoàn tác được")
    changes = {f: (_aware(after[f]), _aware(before[f])) for f in after}
    return qc.update_bulletin_fields(db, str(doc["doc_id"]), changes, reason=reason, actor=actor,
                                     source="api_revert", now=now)


_REVERTERS = {"qc_decision": _revert_qc_decision, "bulletin_metadata": _revert_bulletin}


@router.post("/audit/{audit_id}/revert", dependencies=[Depends(rate_limit(scope="write"))])
def revert_audit(audit_id: str, body: RevertRequest,
                 user: UserContext = Depends(require(P.VIEW_AUDIT, AUDIT_LOGS))):
    """Hoàn tác một thay đổi đã ghi (xem docstring module).

    - 404 nếu không có bản ghi nhật ký; 403 nếu thiếu quyền ghi trên collection đích;
    - 422 nếu loại audit không hoàn tác được an toàn (không thuộc danh sách trắng, hoặc thiếu dữ liệu);
    - 409 nếu đã hoàn tác (hoặc đang hoàn tác), audit chưa `applied`, hoặc dữ liệu đã bị sửa tiếp sau
      thay đổi này (điều kiện version / giá trị cũ không khớp).
    """
    from vngis import qc

    db = state.db
    key = ObjectId(audit_id) if _is_objectid(audit_id) else audit_id
    doc = db[AUDIT_LOGS].find_one({"_id": key})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có bản ghi nhật ký {audit_id!r}")
    action = doc.get("action")
    if action not in REVERTIBLE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"error": "loại thay đổi này không hoàn tác được qua API", "action": action,
                                    "revertible": sorted(REVERTIBLE)})
    coll, flag = REVERTIBLE[action]
    if doc.get("collection") != coll:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"audit {action} phải thuộc collection {coll!r}, nhận {doc.get('collection')!r}")
    if not has(user.mask_for(coll), flag):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail={"error": "thiếu quyền", "required": P(flag).name, "collection": coll,
                                    "your_mask": user.mask_for(coll)})
    if doc.get("reverted_at") is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="bản ghi này đã được hoàn tác trước đó")
    if doc.get("state") != "applied":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"chỉ hoàn tác được thay đổi đã áp dụng (state={doc.get('state')!r})")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    reason = f"hoàn tác audit {audit_id}: {body.reason.strip()}"
    # Đánh dấu nguyên tử: chỉ một request được hoàn tác bản ghi này.
    claim = {"by": user.actor, "at": now}
    got = db[AUDIT_LOGS].update_one({"_id": doc["_id"], "state": "applied", "reverted_at": None,
                                     "revert_claim": None}, {"$set": {"revert_claim": claim}})
    if got.matched_count != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="bản ghi này đã hoặc đang được hoàn tác")
    try:
        result = _REVERTERS[action](db, doc, reason, user.actor, now)
    except HTTPException:
        db[AUDIT_LOGS].update_one({"_id": doc["_id"], "revert_claim": claim}, {"$unset": {"revert_claim": ""}})
        raise
    except (qc.VersionConflict, qc.FieldConflict, qc.FlagNotFound, ValueError) as e:
        # Chắc chắn KHÔNG ghi gì (điều kiện không khớp / dữ liệu sai) → gỡ đánh dấu.
        db[AUDIT_LOGS].update_one({"_id": doc["_id"], "revert_claim": claim}, {"$unset": {"revert_claim": ""}})
        raise qc_http_error(e) from None
    # Lỗi khác (mạng, AuditStateError…): GIỮ `revert_claim` để không hoàn tác hai lần; cần kiểm tra tay.
    new_id = _find_new_audit(db, action, str(doc["doc_id"]), now, user.actor)
    db[AUDIT_LOGS].update_one({"_id": doc["_id"], "revert_claim": claim},
                              {"$set": {"reverted_at": now, "reverted_by": user.actor,
                                        "revert_reason": body.reason.strip(), "revert_audit_id": new_id},
                               "$unset": {"revert_claim": ""}})
    try:
        state.cache.bump_version(coll)
    except Exception as e:  # noqa: BLE001
        log.warning("không bump ver:%s: %s", coll, e)
    return {"reverted": True, "audit_id": audit_id, "revert_audit_id": new_id, "action": action,
            "collection": coll, "doc_id": doc.get("doc_id"), "reverted_at": to_vn_datetime(now),
            "result": clean(result)}
