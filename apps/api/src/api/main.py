"""Ứng dụng FastAPI của `apps/api` (đề cương 5.3–5.8, mục GĐ7).

Cấu trúc:

- `main.py`: khởi tạo ứng dụng, middleware `request_id`, handler lỗi, gắn router.
- `router/`: các router theo nhóm nghiệp vụ, tất cả dưới tiền tố **`/api/v1`**.
- `auth/`: JWT, argon2, blacklist, `users`/`roles`.
- `permissions.py`: Bitmask 12 bit, `require(flag, collection)`.
- `deps.py`: dependency (người dùng hiện tại, quyền, giới hạn tần suất).

Mọi đường dẫn nghiệp vụ có tiền tố `/api/v1`; `/health`, `/health/live` và `/docs` để ở
gốc để Nginx và Docker healthcheck dùng được (xem `apps/api/README.md`).

    uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.state import configure_logging, state

log = logging.getLogger("api")

API_PREFIX = "/api/v1"


# ------------------------------------------------------------------ middleware ---
def _register_cors(target: FastAPI) -> None:
    """CORS có `allow_credentials` cho đúng các origin trong `auth.cors_origins` (không dùng `*`).

    Danh sách rỗng (web cùng origin qua Nginx hoặc proxy dev) → không gắn CORS: trình duyệt chặn mọi
    request chéo origin có header tuỳ biến/cookie. Header `X-Requested-With` (chống CSRF) phải nằm trong
    `allow_headers` để preflight của web hợp lệ.
    """
    try:
        auth = state.config.auth
    except Exception as e:  # noqa: BLE001 - thiếu cấu hình thì không bật CORS
        log.warning("không đọc được cấu hình CORS: %s", e)
        return
    origins = list(auth.cors_origins)
    if not origins:
        return
    from fastapi.middleware.cors import CORSMiddleware
    target.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", auth.csrf_header, "X-Request-Id"],
        expose_headers=["X-Request-Id", "X-Instance", "Retry-After", "ETag", "X-SHA256"],
        max_age=600,
    )


def _register_middleware(target: FastAPI) -> None:
    """Gắn middleware gắn `request_id` (Nginx gửi `X-Request-Id`) + tên instance."""

    @target.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        adapter = logging.LoggerAdapter(log, {"request_id": rid})
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - log rồi để handler lỗi xử lý
            adapter.exception("lỗi chưa xử lý: %s %s", request.method, request.url.path)
            raise
        dt_ms = (time.monotonic() - t0) * 1000
        response.headers["X-Request-Id"] = rid
        response.headers["X-Instance"] = state.instance
        adapter.info("%s %s -> %s (%.1f ms)", request.method, request.url.path,
                     response.status_code, dt_ms)
        return response


# ------------------------------------------------------------------ handlers -----
def _register_handlers(target: FastAPI) -> None:
    """Gắn handler lỗi trả về JSON thống nhất kèm `request_id`."""

    @target.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        body = {"error": {"status": exc.status_code, "detail": exc.detail,
                          "request_id": getattr(request.state, "request_id", None),
                          "instance": state.instance}}
        headers = getattr(exc, "headers", None)
        return JSONResponse(status_code=exc.status_code, content=body, headers=headers)

    @target.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            content={"error": {"status": 422, "detail": exc.errors(),
                                               "request_id": getattr(request.state, "request_id", None),
                                               "instance": state.instance}})

    @target.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        logging.getLogger("api").exception("lỗi 500 request_id=%s", rid)
        return JSONResponse(status_code=500,
                            content={"error": {"status": 500, "detail": "lỗi máy chủ nội bộ",
                                               "request_id": rid, "instance": state.instance}})


# ------------------------------------------------------------------ health -------
def _register_health(target: FastAPI) -> None:
    """Gắn các endpoint kiểm tra sống và thông tin hệ thống."""

    @target.get("/health/live", tags=["system"])
    def health_live():
        """Tiến trình còn sống (healthcheck của Docker)."""
        return {"instance": state.instance, "ok": True}

    @target.get("/health", tags=["system"])
    def health():
        """Trạng thái MongoDB, Redis cache (Sentinel) và các node khóa."""
        out: dict = {"instance": state.instance, "ok": True}
        try:
            db = state.db
            st = db.client.admin.command("replSetGetStatus")
            out["mongo"] = {"set": st["set"],
                            "members": {m["name"]: m["stateStr"] for m in st["members"]}}
        except Exception as e:  # noqa: BLE001 - báo lỗi thay vì sập endpoint
            out["mongo"] = {"error": str(e)[:200]}
            out["ok"] = False
        try:
            rc = state.config.redis.cache
            if rc.mode == "sentinel":
                from vngis.db import redis_sentinel
                host, port = redis_sentinel(state.config).discover_master(rc.master)
                out["redis_cache"] = {"master": f"{host}:{port}", "mode": "sentinel"}
            else:
                out["redis_cache"] = {"mode": "single", "ping": state.redis.ping()}
        except Exception as e:  # noqa: BLE001
            out["redis_cache"] = {"error": str(e)[:200]}
            out["ok"] = False
        try:
            out["lock_nodes"] = state.lock.ping()
            out["lock_backend"] = getattr(state.lock, "backend", "?")
        except Exception as e:  # noqa: BLE001
            out["lock_nodes"] = {"error": str(e)[:200]}
        return out

    @target.get(f"{API_PREFIX}/system/info", tags=["system"])
    def system_info():
        """Thông tin phiên bản và cấu hình (không lộ bí mật)."""
        cfg = state.config
        return {
            "instance": state.instance,
            "api_prefix": API_PREFIX,
            "env": cfg.env,
            "mongo_db": cfg.mongo.db,
            "redis_cache_mode": cfg.redis.cache.mode,
            "lock_backend": cfg.redis.lock.backend,
            "auth_required": cfg.auth.required,
        }


# ------------------------------------------------------------------ routers ------
def _include_routers(target: FastAPI) -> None:
    """Gắn các router nghiệp vụ (tiền tố `/api/v1`)."""
    from api.router import ROUTERS

    for r in ROUTERS:
        target.include_router(r, prefix=API_PREFIX)


# ------------------------------------------------------------------ events -------
def _register_events(target: FastAPI) -> None:
    """Gắn hook khởi động/dừng (tạo bảng vai trò, đóng khoá)."""

    @target.on_event("startup")
    async def on_startup() -> None:  # pragma: no cover - chạy khi app khởi động
        configure_logging(state.config.app.log_level, instance=state.instance)
        # Kiểm tra khoá ký JWT ngay lúc khởi động: thiếu/yếu ở môi trường không phải dev/test →
        # JwtConfigError, uvicorn dừng với thông báo rõ (không đợi tới request đăng nhập đầu tiên).
        _ = state.jwt
        log.info("API khởi động: instance=%s env=%s api_prefix=%s",
                 state.instance, state.config.env, API_PREFIX)
        try:
            state.repo.ensure_roles()
        except Exception as e:  # noqa: BLE001 - không có MongoDB thì API vẫn khởi động
            log.warning("không tạo được bảng vai trò: %s", e)
        try:
            state.repo.ensure_indexes()   # `users.account_code` unique (partial) — idempotent, như migration 0008
        except Exception as e:  # noqa: BLE001 - trùng mã cũ / thiếu quyền: báo, không chặn khởi động
            log.warning("không tạo được index của users: %s", e)

    @target.on_event("shutdown")
    async def on_shutdown() -> None:  # pragma: no cover
        log.info("API dừng: instance=%s", state.instance)
        try:
            state.lock.close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ ứng dụng -----
app = FastAPI(
    title="gis-dist API",
    version="1.0.0",
    description=("Hệ thống quản lý dữ liệu phân tán tích hợp dự báo đỉnh mực nước bằng GNN "
                 "lan truyền pha — sông Đồng Nai – Sài Gòn – Vàm Cỏ. Mọi endpoint nghiệp vụ nằm "
                 f"dưới tiền tố {API_PREFIX}."),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

_register_middleware(app)
_register_cors(app)
_register_handlers(app)
_register_health(app)
_include_routers(app)
_register_events(app)


def create_app() -> FastAPI:
    """Tạo một instance ứng dụng mới (dùng cho test và khi chạy nhiều tiến trình).

    Mỗi ứng dụng có định danh riêng nên log không lẫn giữa các test.
    """
    new_app = FastAPI(
        title=app.title,
        version=app.version,
        description=app.description,
        docs_url=app.docs_url,
        redoc_url=app.redoc_url,
        openapi_url=app.openapi_url,
    )
    _register_middleware(new_app)
    _register_cors(new_app)
    _register_handlers(new_app)
    _register_health(new_app)
    _include_routers(new_app)
    _register_events(new_app)
    return new_app
