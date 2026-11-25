"""Bản tin KTTV: danh sách, chi tiết, PDF gốc, nạp lại (đề cương 5.8).

- `GET /bulletins?date=&type=` — READ, cache-aside theo phiên bản `ver:hydromet_bulletins`.
- `GET /bulletins/{source_file}` — READ, cache-aside (TTL 7 ngày, gần như bất biến).
- `GET /bulletins/{source_file}/pdf` — EXPORT; trả PDF trực tiếp từ MongoDB kèm
  `ETag` = SHA-256 và `Cache-Control: private` (cần quyền nên proxy dùng chung không được lưu).
  **PDF không đưa vào Redis** (xem đề cương 5.5).
- `GET /bulletins/{source_file}/files` — READ; metadata PDF (không có nội dung).
- `POST /bulletins/reload` — RUN_JOB; nạp lại nguyên tử qua collection tạm
  (`hydromet_bulletins__staging`), kiểm tra, `renameCollection(dropTarget=True)`, tăng
  `ver:hydromet_bulletins`. Toàn bộ chạy dưới khoá `lock:reload:bulletins` có watchdog.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.deps import UserContext, rate_limit, require
from api.permissions import P
from api.router.common import acquire_guard, clean, guard_check, parse_day, to_vn_date
from api.state import state

log = logging.getLogger("api.bulletins")

router = APIRouter(tags=["bulletins"])

BULLETINS = "hydromet_bulletins"
BULLETIN_FILES = "hydromet_bulletin_files"
#: PDF cần xác thực → `private`; nội dung theo SHA-256 nên không đổi (`immutable`).
PDF_CACHE_CONTROL = "private, max-age=604800, immutable"


@router.get("/bulletins", dependencies=[Depends(require(P.READ, BULLETINS)),
                                        Depends(rate_limit(scope="bulletins"))])
async def list_bulletins(date: str | None = None, type: str | None = None, is_primary: bool | None = None,
                         limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """Danh sách bản tin (lọc theo ngày và loại). Có cache theo phiên bản dữ liệu."""
    db = state.db
    q: dict[str, Any] = {}
    if date:
        # Khoảng trong ngày theo giờ Việt Nam: 00:00:00 → 23:59:59.999 giờ VN
        from datetime import timedelta

        from vngis.admin import VN_TZ
        d = parse_day(date, field="date")
        start = d.astimezone(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        q["bulletin_date"] = {"$gte": start, "$lt": start + timedelta(days=1)}
    if type:
        q["bulletin_type"] = type
    if is_primary is not None:
        q["source.is_primary"] = is_primary

    cache = state.cache
    key = cache.versioned_key("bul", BULLETINS, date or "all", type or "all",
                              str(is_primary), limit, offset)

    def compute() -> list:
        cur = db[BULLETINS].find(q, {"_id": 0, "source_file": 1, "bulletin_date": 1, "bulletin_type": 1,
                                     "source.is_primary": 1, "bulletin_no": 1, "year": 1, "month": 1,
                                     "parse.warnings": 1}).sort("bulletin_date", -1)
        docs = list(cur.skip(offset).limit(limit))
        return clean(docs)

    items = await _get_or_compute(cache, key, "bulletin", compute)
    total = db[BULLETINS].count_documents(q)
    return {"total": total, "count": len(items), "limit": limit, "offset": offset, "items": items}


async def _get_or_compute(cache, key: str, kind: str, compute):
    """Gọi `Cache.get_or_compute` bằng thread để không chặn event loop."""
    import asyncio
    return await asyncio.to_thread(cache.get_or_compute, key, kind, compute)


def _content_disposition(source_file: str) -> str:
    """`inline` kèm tên file: bản ASCII (header HTTP chỉ latin-1) + `filename*` UTF-8 (RFC 6266/5987)."""
    from urllib.parse import quote

    name = source_file.rsplit("/", 1)[-1] or "bulletin.pdf"
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name)
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


# Thứ tự quan trọng: `{source_file:path}` khớp cả chuỗi có "/pdf", "/files" ở cuối, nên hai route này
# PHẢI được khai báo trước route chi tiết, nếu không chúng không bao giờ được gọi.
@router.get("/bulletins/{source_file:path}/pdf", dependencies=[Depends(require(P.EXPORT, BULLETIN_FILES))])
async def get_bulletin_pdf(source_file: str):
    """Tải PDF gốc kèm `ETag` (SHA-256) và `Cache-Control: private` (dữ liệu cần đăng nhập + quyền EXPORT:
    proxy/CDN dùng chung KHÔNG được lưu; chỉ trình duyệt của người đã xác thực được cache).

    PDF **không** được đưa vào Redis: trả thẳng từ MongoDB (`hydromet_bulletin_files`).
    """
    db = state.db
    doc = db[BULLETIN_FILES].find_one({"source_file": source_file},
                                       {"_id": 0, "data": 1, "sha256": 1, "content_type": 1,
                                        "size_bytes": 1, "source_file": 1})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có PDF của {source_file!r}")
    data = doc.get("data")
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="bản tin này không có PDF gốc")
    sha = str(doc.get("sha256") or "")
    headers = {"ETag": f'"{sha}"', "Cache-Control": PDF_CACHE_CONTROL, "X-SHA256": sha,
               "Content-Disposition": _content_disposition(source_file)}
    return Response(content=bytes(data), media_type=doc.get("content_type", "application/pdf"),
                    headers=headers)


@router.get("/bulletins/{source_file:path}/files", dependencies=[Depends(require(P.READ, BULLETIN_FILES))])
async def bulletin_files_meta(source_file: str):
    """Metadata PDF (kích thước, SHA-256) — cache-aside, không chứa nội dung PDF."""
    db = state.db
    cache = state.cache
    key = cache.versioned_key("pdfmeta", BULLETIN_FILES, source_file)

    def compute():
        doc = db[BULLETIN_FILES].find_one({"source_file": source_file},
                                         {"_id": 0, "source_file": 1, "sha256": 1,
                                          "size_bytes": 1, "content_type": 1, "uploaded_at": 1})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có PDF của {source_file!r}")
        return clean(doc)

    return await _get_or_compute(cache, key, "pdf_meta", compute)


@router.get("/bulletins/{source_file:path}", dependencies=[Depends(require(P.READ, BULLETINS))])
async def get_bulletin(source_file: str):
    """Chi tiết một bản tin (đã parse). Cache-aside, TTL 7 ngày."""
    db = state.db
    cache = state.cache
    key = cache.versioned_key("bul", BULLETINS, source_file)

    def compute():
        doc = db[BULLETINS].find_one({"source_file": source_file})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"không có bản tin {source_file!r}")
        return clean(doc)

    return await _get_or_compute(cache, key, "bulletin", compute)


@router.post("/bulletins/reload", dependencies=[Depends(require(P.RUN_JOB, BULLETINS)),
                                                Depends(rate_limit(scope="job"))])
async def reload_bulletins(user: UserContext = Depends(require(P.RUN_JOB, BULLETINS))):
    """Nạp lại toàn bộ bản tin theo quy trình nguyên tử (staging → kiểm tra → rename).

    Giữ khoá `lock:reload:bulletins` có watchdog; tăng `ver:hydromet_bulletins` sau khi thay.
    Nếu không lấy được khoá → 423.
    """
    from vngis.atomic_reload import LOCK_RESOURCE
    from vngis.locks import LockGuard

    # Cùng tài nguyên với worker (ingest/reload bản tin) ⇒ cùng khoá Redis và cùng bộ đếm fence.
    guard = LockGuard(state.lock, db=state.db, resource=LOCK_RESOURCE, ttl_ms=120_000)
    acquire_guard(guard, busy_detail="đang có lượt nạp lại khác chạy")
    try:
        # Kiểm tra trước khi ghi; `reload_bulletins_atomic` kiểm tra lại ngay trước sao lưu và rename.
        guard_check(guard)
        result = await _reload_atomic(user, guard)
        return result
    finally:
        guard.release()


async def _reload_atomic(user: UserContext, guard=None) -> dict:
    """Thực hiện nạp lại nguyên tử (dùng `vngis.atomic_reload`)."""
    from vngis.atomic_reload import ReloadError
    from vngis.locks import LockLost

    try:
        return await _reload_atomic_call(user, guard)
    except LockLost as e:
        # Mất khoá trước bước sao lưu/rename: bản chính giữ nguyên, staging đã dọn.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e),
                            headers={"Retry-After": "1"}) from None
    except ReloadError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from None


async def _reload_atomic_call(user: UserContext, guard=None) -> dict:
    import asyncio
    from vngis.atomic_reload import reload_bulletins_atomic
    return await asyncio.to_thread(reload_bulletins_atomic, state.db, actor=user.actor, guard=guard)
