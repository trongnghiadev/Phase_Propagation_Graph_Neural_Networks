/**
 * Lớp gọi API tập trung cho `apps/web`.
 *
 * Nguyên tắc:
 * - Chỉ gọi API qua HTTP (`/api/v1/...`), KHÔNG import từ `packages/` (luật phụ thuộc).
 * - Access token 15 phút chỉ giữ trong BỘ NHỚ của trang (không localStorage/sessionStorage).
 * - Refresh token 7 ngày nằm trong cookie `httpOnly; SameSite=Strict; Path=/api/v1/auth/{refresh,logout}`
 *   do API đặt — JavaScript không đọc được. Mọi request gửi `credentials: "include"` và header
 *   `X-Requested-With: XMLHttpRequest` (API dùng header này để chọn chế độ cookie và chống CSRF).
 * - Tải trang → gọi `POST /auth/refresh` (cookie) để lấy access token mới (`restoreSession`).
 * - Khi API trả **401** → thử refresh một lần rồi phát lại yêu cầu; nếu refresh hỏng
 *   thì xoá phiên đăng nhập và báo người dùng.
 * - Khi API trả **409** (xung đột phiên bản) hoặc **423** (đang bị khoá) → ném lỗi
 *   `ApiRequestError` với `status` để giao diện hiện thông báo rõ ràng.
 */

import type { ActorLabel, ApiErrorBody, TokenResponse } from "./types";

/** Gốc API. Rỗng = dùng đường dẫn tương đối (cùng origin, qua Nginx). */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_PROXY === "1"
    ? "" // proxy dev của Next.js (next.config.ts → rewrites) → gọi cùng origin
    : (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "");

/** Tiền tố chung của mọi đường dẫn nghiệp vụ. */
export const API_PREFIX = "/api/v1";

/** Lỗi có cấu trúc, mang mã trạng thái HTTP để giao diện xử lý. */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly requestId: string | null;
  readonly instance: string | null;
  /** Số giây nên chờ trước khi thử lại (header `Retry-After` của 429/503). */
  readonly retryAfter: number | null;

  constructor(status: number, message: string, detail?: unknown, requestId?: string | null,
              instance?: string | null, retryAfter?: number | null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.detail = detail;
    this.requestId = requestId ?? null;
    this.instance = instance ?? null;
    this.retryAfter = retryAfter ?? null;
  }

  /** Dữ liệu không hợp lệ hoặc thao tác không hỗ trợ (422). */
  get isInvalid(): boolean {
    return this.status === 422;
  }

  /** Thiếu điều kiện tiên quyết (428) — ví dụ chưa gửi `version` khi cờ QC đã tồn tại. */
  get isPreconditionRequired(): boolean {
    return this.status === 428;
  }

  /** Phiên bản hiện tại mà API báo trong `detail.current` (409/428). */
  get currentVersion(): number | null {
    const d = this.detail;
    if (d && typeof d === "object" && !Array.isArray(d)) {
      const cur = (d as Record<string, unknown>).current;
      if (typeof cur === "number") return cur;
    }
    return null;
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

/** Header tuỳ biến bắt buộc (chống CSRF, khớp `auth.csrf_header` của API). */
const CSRF_HEADER = { "X-Requested-With": "XMLHttpRequest" } as const;

export interface SessionUser {
  user_id: string;
  username: string;
  role: string;
  perm_ver: number;
}

let accessToken: string | null = null;
let sessionUser: SessionUser | null = null;

/**
 * Access token chỉ nằm trong biến của module (mất khi tải lại trang → lấy lại bằng refresh cookie).
 * Không dùng `localStorage`: XSS không đọc được token lưu lâu dài; refresh token là cookie httpOnly.
 */
export const tokenStore = {
  getAccess(): string | null {
    return accessToken;
  },
  set(tokens: TokenResponse): void {
    accessToken = tokens.access_token;
    sessionUser = {
      user_id: tokens.user_id,
      username: tokens.username,
      role: tokens.role,
      perm_ver: tokens.perm_ver,
    };
    authChannel();
  },
  getUser(): SessionUser | null {
    return sessionUser;
  },
  clear(): void {
    accessToken = null;
    sessionUser = null;
  },
};

/** Báo các tab khác khi đăng xuất (mỗi tab giữ access token riêng trong bộ nhớ). */
const AUTH_CHANNEL = "gisdist-auth";
let channel: BroadcastChannel | null = null;
function authChannel(): BroadcastChannel | null {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") return null;
  if (!channel) {
    channel = new BroadcastChannel(AUTH_CHANNEL);
    channel.onmessage = (ev: MessageEvent) => {
      if (ev.data === "logout") notifySessionExpired();
    };
  }
  return channel;
}

/** Gọi khi refresh token hỏng → báo cho các component biết cần đăng nhập lại. */
type SessionListener = () => void;
const sessionListeners = new Set<SessionListener>();

export function onSessionExpired(fn: SessionListener): () => void {
  sessionListeners.add(fn);
  return () => sessionListeners.delete(fn);
}

/** Báo các tab khác đã đăng xuất (chúng xoá access token trong bộ nhớ). */
export function broadcastLogout(): void {
  try {
    authChannel()?.postMessage("logout");
  } catch {
    /* bỏ qua */
  }
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
export function extractDetail(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // Lỗi kiểm tra dữ liệu của FastAPI: [{loc, msg, type}]
    return detail
      .map((d) => {
        if (d && typeof d === "object") {
          const rec = d as Record<string, unknown>;
          const loc = Array.isArray(rec.loc) ? rec.loc.filter((x) => x !== "body").join(".") : "";
          return `${loc ? `${loc}: ` : ""}${String(rec.msg ?? "")}`;
        }
        return String(d);
      })
      .join("; ");
  }
  if (typeof detail === "object") {
    const rec = detail as Record<string, unknown>;
    const parts: string[] = [];
    if (typeof rec.error === "string") parts.push(rec.error);
    if (typeof rec.message === "string") parts.push(rec.message);
    if (typeof rec.reason === "string") parts.push(rec.reason);
    if (typeof rec.expected === "number" || typeof rec.current === "number") {
      const fmt = (v: unknown) => (v === undefined || v === null ? "—" : String(v));
      parts.push(`phiên bản bạn gửi ${fmt(rec.expected)}, hiện tại ${fmt(rec.current)}`);
    }
    if (typeof rec.action === "string") parts.push(`loại: ${rec.action}`);
    if (parts.length) return parts.join(" — ");
    return JSON.stringify(rec);
  }
  return String(detail);
}

/** Thông điệp tiếng Việt cho các mã lỗi hay gặp, kèm chi tiết của API nếu có. */
export function friendlyError(status: number, detail: unknown, retryAfter?: number | null): string {
  const raw = extractDetail(detail);
  const withRaw = (base: string) => (raw ? `${base} Chi tiết: ${raw}.` : base);
  const wait = retryAfter ? ` Thử lại sau khoảng ${retryAfter} giây.` : " Vui lòng thử lại sau ít giây.";
  switch (status) {
    case 400:
      return withRaw("Yêu cầu không hợp lệ.");
    case 401:
      return raw || "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.";
    case 403:
      return withRaw("Bạn không có quyền thực hiện thao tác này.");
    case 404:
      return withRaw("Không tìm thấy dữ liệu yêu cầu.");
    case 409:
      return withRaw(
        "Xung đột dữ liệu: bản ghi đã bị người khác thay đổi hoặc thao tác đã được thực hiện trước đó. " +
          "Hãy tải lại để xem dữ liệu mới nhất rồi quyết định lại.",
      );
    case 422:
      return withRaw("Dữ liệu gửi lên không hợp lệ hoặc thao tác không được hỗ trợ.");
    case 423:
      return withRaw("Bản ghi đang được người khác xử lý (đang bị khoá). Vui lòng thử lại sau ít phút.");
    case 428:
      return withRaw(
        "Thiếu phiên bản (version) của bản ghi: dữ liệu đã có quyết định trước đó, hãy xem lại rồi gửi lại.",
      );
    case 429:
      return withRaw(`Bạn thao tác quá nhanh (vượt giới hạn tần suất).${wait}`);
    case 503:
      return withRaw(`Dịch vụ tạm thời không sẵn sàng (Redis, khoá phân tán hoặc dữ liệu chưa sẵn sàng).${wait}`);
    default:
      return withRaw(`Lỗi ${status} từ máy chủ.`);
  }
}

/** Đọc header `Retry-After` (giây hoặc ngày giờ HTTP). */
function parseRetryAfter(res: Response): number | null {
  const v = res.headers.get("retry-after");
  if (!v) return null;
  const n = Number(v);
  if (Number.isFinite(n) && n >= 0) return Math.ceil(n);
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : Math.max(0, Math.ceil((t - Date.now()) / 1000));
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

/**
 * Refresh token chỉ dùng được MỘT lần; API coi việc dùng lại là dấu hiệu bị đánh cắp và thu hồi
 * mọi phiên. Các tab cùng chia sẻ MỘT cookie refresh nên phải tuần tự hoá refresh giữa các tab
 * (Web Locks API): tab vào sau gửi cookie MỚI mà trình duyệt vừa nhận từ tab trước.
 * Trong cùng tab: nếu access token đã đổi trong lúc chờ khoá (lời gọi khác vừa làm mới) thì dùng luôn.
 */
async function doRefresh(): Promise<boolean> {
  const before = tokenStore.getAccess();
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (!locks) return doRefreshUnlocked();
  return locks.request("gisdist-refresh", async () => {
    const now = tokenStore.getAccess();
    if (now && now !== before) return true;
    return doRefreshUnlocked();
  });
}

async function doRefreshUnlocked(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}${API_PREFIX}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { ...CSRF_HEADER },
    });
    if (!res.ok) return false;
    const data = (await res.json()) as TokenResponse;
    tokenStore.set(data);
    return true;
  } catch {
    return false;
  }
}

/**
 * Khôi phục phiên khi tải trang: đổi cookie refresh lấy access token (giữ trong bộ nhớ).
 * `false` = chưa đăng nhập / cookie hết hạn.
 */
export function restoreSession(): Promise<boolean> {
  if (tokenStore.getAccess()) return Promise.resolve(true);
  return refreshSession();
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
    const headers: Record<string, string> = { ...CSRF_HEADER };
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
        // Cookie refresh chỉ có Path=/api/v1/auth/{refresh,logout}: các endpoint khác không nhận cookie.
        credentials: "include",
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
    const retryAfter = parseRetryAfter(res);
    throw new ApiRequestError(
      res.status,
      friendlyError(res.status, detail, retryAfter),
      detail,
      err.request_id,
      err.instance,
      retryAfter,
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
  const headers: Record<string, string> = { ...CSRF_HEADER };
  const token = tokenStore.getAccess();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), { method, headers, credentials: "include", signal });
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
    try {
      res = await fetch(buildUrl(path, query), {
        method,
        headers: token2 ? { ...headers, Authorization: `Bearer ${token2}` } : headers,
        credentials: "include",
        signal,
      });
    } catch (err) {
      throw new ApiOfflineError(
        err instanceof Error ? `Không kết nối được API (${err.message}).` : "Không kết nối được API.",
      );
    }
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
    const retryAfter = parseRetryAfter(res);
    throw new ApiRequestError(res.status, friendlyError(res.status, d, retryAfter), d, err.request_id,
      err.instance, retryAfter);
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
  /** Thu hồi access token (nếu còn) + refresh token trong cookie; API xoá cookie. */
  logout: () =>
    apiFetch<{ ok: boolean; revoked: number }>("/auth/logout", {
      method: "POST",
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
    }>(`/stations/${encodeURIComponent(code)}/tides`, { query }),
  /**
   * `PATCH /tides/{station}/{ts}` — quyết định QC cho một đỉnh/chân triều (ghi vào `qc_flags`;
   * G0 áp vào `tide_extremes` ở lần chạy sau). `reason` bắt buộc; `version` bắt buộc khi cờ đã
   * tồn tại (thiếu → 428, lệch → 409).
   */
  patchTide: (station: string, ts: string, body: import("./types").TideDecision) =>
    apiFetch<import("./types").TidePatchResult>(
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
    apiFetch<{
      job_id?: string;
      model_version: number;
      status?: string;
      state: string;
      note?: string;
      /** `false`: phiên bản đã có, không giao việc mới. */
      created?: boolean;
      retry?: boolean;
    }>("/models/train", {
      method: "POST",
      body,
    }),
  /** `GET /models` — phiên bản mô hình kèm `train_state`/`train_reason`, `promotable`, `serving`. */
  models: (query?: { limit?: number }) =>
    apiFetch<{ items: import("./types").ModelItem[]; count: number }>("/models", { query }),
  promoteModel: (version: string) =>
    apiFetch<{
      model_version: number | string;
      status: string;
      state: string;
      promoted_at: string;
      promoted_by?: string | null;
      promoted_by_label?: ActorLabel | null;
    }>(
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
   * `GET /review?status=` — cờ QC trong `qc_flags` theo `status`
   * (`suspect` mặc định | `confirmed_ok` | `rejected` | `corrected`), bỏ cờ `retired`.
   */
  reviewQueue: (query?: { status?: import("./types").QcStatus; station?: string; limit?: number }) =>
    apiFetch<{ count: number; items: import("./types").ReviewItem[] }>("/review", { query }),
  /**
   * `POST /review/{id}/approve` — ghi quyết định cho một cờ QC.
   * `version` = phiên bản cờ người duyệt đã xem (409 nếu lệch); `reason` bắt buộc;
   * `corrected_value` chỉ gửi khi `status = "corrected"`.
   */
  approveReview: (id: string, body: import("./types").ReviewDecision) =>
    apiFetch<import("./types").ReviewItem & { flag?: Record<string, unknown> }>(
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
  /** `POST /audit/{id}/revert` — chỉ `qc_decision` và `bulletin_metadata`; loại khác 422, lặp lại 409. */
  revertAudit: (id: string, body: { reason: string }) =>
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
      /** `perm_ver` đã xem (khoá lạc quan, 409 nếu lệch). */
      perm_ver: number;
      /** Bắt buộc (ghi vào `audit_logs`). */
      reason: string;
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
