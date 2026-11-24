/**
 * Lớp gọi API tập trung cho `apps/web`.
 *
 * Nguyên tắc:
 * - Chỉ gọi API qua HTTP (`/api/v1/...`), KHÔNG import từ `packages/` (luật phụ thuộc).
 * - Access token 15 phút, refresh token 7 ngày (xem `apps/api/src/api/auth/jwt.py`).
 * - Khi API trả **401** → thử refresh một lần rồi phát lại yêu cầu; nếu refresh hỏng
 *   thì xoá phiên đăng nhập và báo người dùng.
 * - Khi API trả **409** (xung đột phiên bản) hoặc **423** (đang bị khoá) → ném lỗi
 *   `ApiRequestError` với `status` để giao diện hiện thông báo rõ ràng.
 */

import type { ApiErrorBody, TokenResponse } from "./types";

/** Gốc API. Rỗng = dùng đường dẫn tương đối (cùng origin, qua Nginx). */
export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "");

/** Tiền tố chung của mọi đường dẫn nghiệp vụ. */
export const API_PREFIX = "/api/v1";

/** Lỗi có cấu trúc, mang mã trạng thái HTTP để giao diện xử lý. */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly requestId: string | null;
  readonly instance: string | null;

  constructor(status: number, message: string, detail?: unknown, requestId?: string | null,
              instance?: string | null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.detail = detail;
    this.requestId = requestId ?? null;
    this.instance = instance ?? null;
  }

  /** Người khác đang sửa bản ghi (423) — cần thông báo riêng. */
  get isLocked(): boolean {
    return this.status === 423;
  }

  /** Xung đột phiên bản (409) — dữ liệu đã bị người khác thay đổi. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  /** Không đủ quyền (403). */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  /** Chưa đăng nhập / token hết hạn (401). */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  /** API chưa sẵn sàng (503) — ví dụ chưa có mô hình PhaseGNN đã huấn luyện. */
  get isUnavailable(): boolean {
    return this.status === 503;
  }

  /** Vượt giới hạn tần suất (429). */
  get isRateLimited(): boolean {
    return this.status === 429;
  }
}

/** Không kết nối được API (mạng hoặc API chưa chạy). */
export class ApiOfflineError extends Error {
  constructor(message = "Không kết nối được API") {
    super(message);
    this.name = "ApiOfflineError";
  }
}

/**
 * Chuẩn hoá giá trị bị ném về `Error` để hiển thị an toàn trong JSX
 * (`{state.error && <ErrorNotice error={state.error} />}` không lỗi kiểu `unknown`).
 */
export function toError(err: unknown): Error {
  if (err instanceof Error) return err;
  if (typeof err === "string") return new Error(err);
  try {
    return new Error(JSON.stringify(err));
  } catch {
    return new Error("Lỗi không xác định");
  }
}

// ---------------------------------------------------------------------------
// Quản lý token
// ---------------------------------------------------------------------------

const ACCESS_KEY = "gisdist.access_token";
const REFRESH_KEY = "gisdist.refresh_token";
const USER_KEY = "gisdist.user";

/**
 * Lưu token ở `localStorage`.
 *
 * **Hạn chế bảo mật đã biết:** `localStorage` đọc được bằng JavaScript, nên nếu có
 * XSS thì token bị lấy. Backend hiện **không** đặt cookie httpOnly cho API
 * (`POST /api/v1/auth/login` trả JSON), nên frontend không thể dùng cookie httpOnly.
 * Hệ thống đã giảm rủi ro ở server: access token 15 phút, refresh token 7 ngày,
 * đổi quyền → `perm_ver` tăng → token cũ mất hiệu lực ngay, logout chặn `jti`.
 *
 * Nếu sau này backend hỗ trợ cookie httpOnly (SameSite=Strict) thì nên chuyển sang
 * `credentials: "include"` và bỏ hoàn toàn phần token trong `localStorage`.
 */
export const tokenStore = {
  getAccess(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(ACCESS_KEY);
  },
  getRefresh(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(REFRESH_KEY);
  },
  set(tokens: TokenResponse): void {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(ACCESS_KEY, tokens.access_token);
    window.localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
    window.localStorage.setItem(USER_KEY, JSON.stringify({
      user_id: tokens.user_id,
      username: tokens.username,
      role: tokens.role,
      perm_ver: tokens.perm_ver,
    }));
  },
  setUser(user: unknown): void {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  getUser<T = { user_id: string; username: string; role: string; perm_ver: number }>(): T | null {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  },
  clear(): void {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
    window.localStorage.removeItem(USER_KEY);
  },
};

/** Gọi khi refresh token hỏng → báo cho các component biết cần đăng nhập lại. */
type SessionListener = () => void;
const sessionListeners = new Set<SessionListener>();

export function onSessionExpired(fn: SessionListener): () => void {
  sessionListeners.add(fn);
  return () => sessionListeners.delete(fn);
}

function notifySessionExpired(): void {
  tokenStore.clear();
  sessionListeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* bỏ qua lỗi của listener */
    }
  });
}

// ---------------------------------------------------------------------------
// Xử lý lỗi
// ---------------------------------------------------------------------------

/** Rút thông điệp tiếng Việt từ `detail` của backend (có thể là chuỗi hoặc object). */
export function extractDetail(detail: unknown): string {  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === "object") {
          const rec = d as Record<string, unknown>;
          const loc = Array.isArray(rec.loc) ? rec.loc.join(".") : "";
          return `${loc ? `${loc}: ` : ""}${String(rec.msg ?? "")}`;
        }
        return String(d);
      })
      .join("; ");
  }
  if (typeof detail === "object") {
    const rec = detail as Record<string, unknown>;
    if (typeof rec.error === "string" && typeof rec.reason === "string") {
      return `${rec.error}: ${rec.reason}`;
    }
    if (typeof rec.reason === "string") return rec.reason;
    if (typeof rec.error === "string") return rec.error;
    if (typeof rec.expected === "number" || typeof rec.current === "number") {
      return `xung đột phiên bản (mong đợi ${String(rec.expected)}, hiện tại ${String(rec.current)})`;
    }
    return JSON.stringify(rec);
  }
  return String(detail);
}

/** Thông điệp thân thiện cho các mã lỗi hay gặp. */
export function friendlyError(status: number, detail: unknown): string {
  const raw = extractDetail(detail);
  switch (status) {
    case 401:
      return raw || "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.";
    case 403:
      return raw || "Bạn không có quyền thực hiện thao tác này.";
    case 404:
      return raw || "Không tìm thấy dữ liệu yêu cầu.";
    case 409:
      return raw || "Dữ liệu đã bị người khác thay đổi. Hãy tải lại trang rồi thử lại.";
    case 423:
      return raw || "Bản ghi đang được người khác sửa. Vui lòng thử lại sau.";
    case 429:
      return raw || "Vượt giới hạn tần suất. Vui lòng thử lại sau ít phút.";
    case 503:
      return raw || "Dịch vụ chưa sẵn sàng (503). Thường là chưa có mô hình đã huấn luyện.";
    default:
      return raw || `Lỗi ${status} từ máy chủ.`;
  }
}

// ---------------------------------------------------------------------------
// Gọi API
// ---------------------------------------------------------------------------

export interface ApiOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Bỏ qua header Authorization (dùng cho login/refresh). */
  anonymous?: boolean;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
}

/** Đang chạy refresh → các request 401 khác chờ luôn (tránh refresh song song). */
let refreshInFlight: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const refresh = tokenStore.getRefresh();
  if (!refresh) return false;
  try {
    const res = await fetch(`${API_BASE_URL}${API_PREFIX}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) return false;
    const data = (await res.json()) as TokenResponse;
    tokenStore.set(data);
    return true;
  } catch {
    return false;
  }
}

/** Refresh token (dùng chung cho nhiều request đồng thời). */
export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

function buildUrl(path: string, query?: ApiOptions["query"]): string {
  const url = `${API_BASE_URL}${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/** Gọi API và trả về JSON (hoặc `null` cho 204). */
export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { method = "GET", body, anonymous = false, query, signal } = options;

  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (!anonymous) {
      const token = tokenStore.getAccess();
      if (token) headers.Authorization = `Bearer ${token}`;
    }
    try {
      return await fetch(buildUrl(path, query), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      });
    } catch (err) {
      throw new ApiOfflineError(
        err instanceof Error
          ? `Không kết nối được API (${err.message}). Kiểm tra API có đang chạy không.`
          : "Không kết nối được API.",
      );
    }
  };

  let res = await send();

  // 401 → thử refresh một lần rồi phát lại yêu cầu.
  if (res.status === 401 && !anonymous) {
    const ok = await refreshSession();
    if (ok) {
      res = await send();
    } else {
      notifySessionExpired();
    }
  }

  if (res.status === 204) return null as T;

  const contentType = res.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  let payload: unknown = null;
  if (isJson) {
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }
  }

  if (!res.ok) {
    const body = (payload ?? {}) as ApiErrorBody;
    const err = body.error ?? {};
    const detail = err.detail !== undefined ? err.detail : body.detail;
    throw new ApiRequestError(
      res.status,
      friendlyError(res.status, detail),
      detail,
      err.request_id,
      err.instance,
    );
  }

  return payload as T;
}

/** Tải tệp nhị phân (PDF) — trả `Blob` kèm `ETag`/SHA-256 nếu API có gửi. */
export async function apiFetchBlob(
  path: string,
  options: ApiOptions = {},
): Promise<{ blob: Blob; etag: string | null; sha256: string | null }> {
  const { method = "GET", query, signal } = options;
  const headers: Record<string, string> = {};
  const token = tokenStore.getAccess();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), { method, headers, signal });
  } catch (err) {
    throw new ApiOfflineError(
      err instanceof Error ? `Không kết nối được API (${err.message}).` : "Không kết nối được API.",
    );
  }

  if (res.status === 401) {
    const ok = await refreshSession();
    if (!ok) {
      notifySessionExpired();
      throw new ApiRequestError(401, "Phiên đăng nhập đã hết hạn.");
    }
    const token2 = tokenStore.getAccess();
    res = await fetch(buildUrl(path, query), {
      method,
      headers: token2 ? { ...headers, Authorization: `Bearer ${token2}` } : headers,
      signal,
    });
  }

  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      /* không phải JSON */
    }
    const body = (detail ?? {}) as ApiErrorBody;
    const err = body.error ?? {};
    const d = err.detail !== undefined ? err.detail : body.detail;
    throw new ApiRequestError(res.status, friendlyError(res.status, d), d, err.request_id, err.instance);
  }

  return {
    blob: await res.blob(),
    etag: res.headers.get("etag"),
    sha256: res.headers.get("x-sha256"),
  };
}

// ---------------------------------------------------------------------------
// Hàm gọi API theo nghiệp vụ (đường dẫn khớp 100% với `apps/api/src/api/router/`)
// ---------------------------------------------------------------------------

export const api = {
  // ---- auth ---------------------------------------------------------------
  login: (username: string, password: string) =>
    apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      anonymous: true,
      body: { username, password },
    }),
  logout: (refreshToken: string | null) =>
    apiFetch<{ ok: boolean; revoked: number }>("/auth/logout", {
      method: "POST",
      body: { refresh_token: refreshToken },
    }),
  me: () => apiFetch<import("./types").MeResponse>("/auth/me"),
  roles: () => apiFetch<import("./types").RoleInfo[]>("/auth/roles"),

  // ---- stations -----------------------------------------------------------
  stations: (query?: { kind?: string; q?: string; limit?: number }) =>
    apiFetch<{ total: number; count: number; items: import("./types").Station[] }>("/stations", { query }),
  station: (code: string) =>
    apiFetch<import("./types").Station>(`/stations/${encodeURIComponent(code)}`),
  patchStation: (code: string, body: Record<string, unknown>) =>
    apiFetch<import("./types").Station>(`/stations/${encodeURIComponent(code)}`, {
      method: "PATCH",
      body,
    }),
  stationTides: (
    code: string,
    query?: { from?: string; to?: string; kind?: string; limit?: number },
  ) =>
    apiFetch<{
      station: string;
      count: number;
      from: string | null;
      to: string | null;
      items: import("./types").TideItem[];
    }>(`/stations/${encodeURIComponent(code)}/tides`, { query }),  /** `PATCH /tides/{station}/{ts}` — sửa giá trị / đặt cờ QC (bắt buộc `reason`). */
  patchTide: (station: string, ts: string, body: Record<string, unknown>) =>
    apiFetch<import("./types").TideItem>(
      `/tides/${encodeURIComponent(station)}/${encodeURIComponent(ts)}`,
      { method: "PATCH", body },
    ),

  // ---- forecasts ----------------------------------------------------------
  forecastsLatest: (query?: { station?: string; basin?: string; limit?: number }) =>
    apiFetch<{
      source: string;
      count: number;
      /**
       * Khi `source === "model"` là các bản ghi dự báo chuẩn hoá; khi `source === "sample"`
       * là các dòng CSV thô đọc từ `results/` (nên là `Record<string, unknown>`).
       */
      items: Array<Record<string, unknown>>;
      note?: string;
      file?: string;
    }>("/forecasts/latest", { query }),
  forecastsCompare: (query?: { station?: string; basin?: string; model_version?: string }) =>
    apiFetch<{
      source: string;
      groups: Record<string, Array<Record<string, unknown>>>;
      note?: string;
      file?: string;
    }>("/forecasts/compare", { query }),
  forecastsMap: (query?: { basin?: string; issued_at?: string }) =>
    apiFetch<{
      source: string;
      features: Array<{ properties: Record<string, unknown> }>;
      note?: string;
      file?: string;
    }>("/forecasts/map", { query }),
  propagation: (station: string, limit?: number) =>
    apiFetch<{
      station: string;
      source: string;
      count: number;
      items: Array<{
        issued_at?: string | null;
        peak_ts?: string | null;
        peak_value?: number | null;
        lag_hours?: number | null;
        upstream?: string | null;
        ts?: string | null;
        distance_km?: number | null;
        method?: string | null;
        model_version?: string | null;
      }>;
    }>(`/forecasts/${encodeURIComponent(station)}/propagation`, { query: { limit } }),
  runForecast: (body: Record<string, unknown>) =>
    apiFetch<{ job_id: string; state: string; source?: string; note?: string }>("/forecasts/run", {
      method: "POST",
      body,
    }),
  trainModel: (body: Record<string, unknown>) =>
    apiFetch<{ job_id: string; model_version: string; state: string; note?: string }>("/models/train", {
      method: "POST",
      body,
    }),
  promoteModel: (version: string) =>
    apiFetch<{ model_version: string; state: string; promoted_at: string }>(
      `/models/${encodeURIComponent(version)}/promote`,
      { method: "POST" },
    ),

  // ---- alerts -------------------------------------------------------------
  alerts: (query?: {
    station?: string;
    basin?: string;
    severity?: string;
    status?: string;
    from?: string;
    to?: string;
    limit?: number;
  }) => apiFetch<{ count: number; items: import("./types").AlertItem[] }>("/alerts", { query }),
  ackAlert: (alertId: string, body: { note?: string; status?: string }) =>
    apiFetch<import("./types").AlertItem>(`/alerts/${encodeURIComponent(alertId)}/ack`, {
      method: "POST",
      body,
    }),

  // ---- bulletins ----------------------------------------------------------
  /** `GET /bulletins` — lọc theo ngày (giờ VN), loại bản tin, bản chính. */
  bulletins: (query?: {
    date?: string;
    type?: string;
    is_primary?: boolean;
    limit?: number;
    offset?: number;
  }) =>
    apiFetch<{
      total: number;
      count: number;
      limit: number;
      offset: number;
      items: import("./types").Bulletin[];
    }>("/bulletins", { query }),
  /** `GET /bulletins/{source_file}` — chi tiết bản tin đã parse. */
  bulletin: (sourceFile: string) =>
    apiFetch<import("./types").Bulletin>(`/bulletins/${encodePath(sourceFile)}`),
  /** `GET /bulletins/{source_file}/files` — metadata PDF (SHA-256, kích thước). */
  bulletinPdfMeta: (sourceFile: string) =>
    apiFetch<{ source_file: string; sha256: string; size_bytes: number; content_type: string }>(
      `/bulletins/${encodePath(sourceFile)}/files`,
    ),
  /** `GET /bulletins/{source_file}/pdf` — tải PDF gốc (cần quyền EXPORT). */
  bulletinPdf: (sourceFile: string) =>
    apiFetchBlob(`/bulletins/${encodePath(sourceFile)}/pdf`),
  /** `POST /bulletins/reload` — nạp lại nguyên tử (RUN_JOB). */
  reloadBulletins: () => apiFetch<Record<string, unknown>>("/bulletins/reload", { method: "POST" }),

  // ---- QC / review --------------------------------------------------------
  /**
   * Danh sách cờ QC đang nghi ngờ.
   *
   * API **không** có endpoint riêng `/tides/suspect`; nguồn chính thức là
   * `GET /api/v1/review?status=pending` (cờ QC trong `qc_flags` chưa duyệt).
   */
  qcFlags: (query?: { status?: string; limit?: number }) =>
    apiFetch<{ count: number; items: import("./types").ReviewItem[] }>("/review", { query }),
  /** `GET /review` — hàng đợi duyệt (cờ QC đang `pending`). */
  reviewQueue: (query?: { status?: string; limit?: number }) =>
    apiFetch<{ count: number; items: import("./types").ReviewItem[] }>("/review", { query }),
  /** `POST /review/{id}/approve` — duyệt/từ chối một cờ QC. */
  approveReview: (
    id: string,
    body: {
      decision?: "approve" | "reject";
      reason?: string;
      collection?: string;
      new_value?: number;
      version?: number;
    },
  ) =>
    apiFetch<import("./types").ReviewItem & { applied?: unknown }>(
      `/review/${encodeURIComponent(id)}/approve`,
      { method: "POST", body },
    ),

  // ---- reservoirs ---------------------------------------------------------
  reservoirs: () => apiFetch<{ count: number; items: string[] }>("/reservoirs"),
  reservoirDaily: (
    name: string,
    query?: { from?: string; to?: string; limit?: number },
  ) =>
    apiFetch<{
      reservoir: string;
      count: number;
      from: string | null;
      to: string | null;
      items: import("./types").ReservoirDaily[];
    }>(`/reservoirs/${encodeURIComponent(name)}/daily`, { query }),

  // ---- địa giới theo thời gian -------------------------------------------
  // Luôn kèm `era` theo luật địa giới (ADR 0003).
  /** `GET /admin-units/provinces?at=YYYY-MM-DD` — tỉnh hiệu lực tại ngày. */
  provinces: (at?: string) =>
    apiFetch<{ at: string; era: string; count: number; items: import("./types").Province[] }>(
      "/admin-units/provinces",
      { query: at ? { at } : undefined },
    ),
  /** `GET /admin-units/eras` — hai thời kỳ và mốc hiệu lực. */
  eras: () => apiFetch<{ eras: string[]; before: string; from: string; effective_utc: string }>(
    "/admin-units/eras",
  ),
  /** `GET /admin-units/provinces/{era}/{code}` — chi tiết tỉnh theo thời kỳ. */
  province: (era: string, code: number) =>
    apiFetch<import("./types").Province>(
      `/admin-units/provinces/${encodeURIComponent(era)}/${code}`,
    ),
  /** `GET /admin-units/wards/{era}/{code}/mapping` — ánh xạ xã cũ ↔ mới. */
  wardMapping: (era: string, code: number, limit?: number) =>
    apiFetch<{
      era: string;
      province_code: number;
      wards: import("./types").Ward[];
      mappings: import("./types").WardMapping[];
    }>(`/admin-units/wards/${encodeURIComponent(era)}/${code}/mapping`, { query: { limit } }),

  // ---- features / trace ---------------------------------------------------
  features: (coll: string, query?: { bbox?: string; z?: number; era?: string; limit?: number }) =>
    apiFetch<import("./types").FeatureCollection>(`/features/${encodeURIComponent(coll)}`, { query }),
  trace: (comid: number, limit?: number) =>
    apiFetch<{
      comid: number;
      segments: Array<Record<string, unknown>>;
      upstream_edges: Array<Record<string, unknown>>;
      stations: Array<Record<string, unknown>>;
      count: number;
    }>(`/trace/${comid}`, { query: { limit } }),

  // ---- stats --------------------------------------------------------------
  tideStats: (query?: { station?: string; year?: number; kind?: string }) =>
    apiFetch<import("./types").TideStats>("/stats/tide", { query }),

  // ---- jobs ---------------------------------------------------------------
  jobs: (query?: { state?: string; type?: string; limit?: number }) =>
    apiFetch<{ count: number; items: import("./types").JobItem[] }>("/jobs", { query }),
  job: (id: string) => apiFetch<import("./types").JobItem>(`/jobs/${encodeURIComponent(id)}`),
  createJob: (type: string, body: Record<string, unknown>) =>
    apiFetch<import("./types").JobItem>(`/jobs/${encodeURIComponent(type)}`, {
      method: "POST",
      body,
    }),

  // ---- audit --------------------------------------------------------------
  audit: (query?: { collection?: string; actor?: string; state?: string; limit?: number }) =>
    apiFetch<{ count: number; items: import("./types").AuditItem[] }>("/audit", { query }),
  revertAudit: (id: string, body: { reason: string; version?: number }) =>
    apiFetch<Record<string, unknown>>(`/audit/${encodeURIComponent(id)}/revert`, {
      method: "POST",
      body,
    }),

  // ---- users --------------------------------------------------------------
  users: (query?: { q?: string; limit?: number }) =>
    apiFetch<{ count: number; items: import("./types").UserItem[] }>("/users", { query }),
  updateUserPerm: (
    id: string,
    body: {
      grant?: string[];
      revoke?: string[];
      collections?: Record<string, string[]>;
      version?: number;
      reason?: string;
    },
  ) => apiFetch<import("./types").UserItem>(`/users/${encodeURIComponent(id)}/perm`, {
    method: "PUT",
    body,
  }),
  setUserActive: (id: string, body: { active: boolean; reason?: string }) =>
    apiFetch<import("./types").UserItem>(`/users/${encodeURIComponent(id)}/active`, {
      method: "PATCH",
      body,
    }),

  // ---- admin --------------------------------------------------------------
  cacheInfo: () =>
    apiFetch<{ stats: Record<string, unknown>; info: Record<string, unknown> }>("/admin/cache"),
  purgeCache: (body: { collection?: string; prefix?: string; bump_version?: boolean }) =>
    apiFetch<{ deleted_keys: number; bumped_version: number | null; collection?: string }>(
      "/admin/cache",
      { method: "DELETE", body },
    ),
  locks: (limit?: number) =>
    apiFetch<{
      count: number;
      backend: string;
      items: Array<Record<string, unknown>>;
    }>("/admin/locks", { query: { limit } }),
  adminHealth: () => apiFetch<Record<string, unknown>>("/admin/health"),
  systemInfo: () => apiFetch<Record<string, unknown>>("/system/info"),
};

/** Mã hoá `source_file` (chứa dấu `/`) để giữ nguyên đường dẫn (backend dùng `:path`). */
function encodePath(sourceFile: string): string {
  return sourceFile
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
}

/**
 * Tải một tệp nhị phân và trả về `objectURL` để nhúng vào `<iframe>`/`<a download>`.
 *
 * Người gọi **phải** `URL.revokeObjectURL(url)` khi huỷ để không rò rỉ bộ nhớ.
 */
export async function fetchBlobUrl(path: string): Promise<string> {
  const { blob } = await apiFetchBlob(path);
  return URL.createObjectURL(blob);
}
