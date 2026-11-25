/**
 * Kiểu dữ liệu phản chiếu JSON trả về từ `apps/api` (tiền tố `/api/v1`).
 *
 * Quy ước chung của repo (xem `apps/api/README.md`):
 * - Mọi đường dẫn nghiệp vụ nằm dưới `/api/v1`.
 * - Thời gian trả về là ISO 8601 theo **giờ Việt Nam** (UTC+7).
 * - Lỗi trả về dạng `{ error: { status, detail, request_id, instance } }`.
 */

/** Lỗi trả về từ API (handler lỗi thống nhất của FastAPI). */
export interface ApiErrorBody {
  error?: {
    status?: number;
    detail?: unknown;
    request_id?: string | null;
    instance?: string | null;
  };
  detail?: unknown;
}

/** Cặp token trả về từ `POST /api/v1/auth/login`. */
export interface TokenResponse {
  access_token: string;
  /** `null` với trình duyệt: refresh token nằm trong cookie httpOnly (JavaScript không đọc được). */
  refresh_token: string | null;
  token_type: string;
  expires_in: number;
  user_id: string;
  username: string;
  role: string;
  perm_ver: number;
}

/** `GET /api/v1/auth/me` — người dùng hiện tại kèm quyền theo collection. */
export interface MeResponse {
  user_id: string;
  username: string;
  /** Mã tài khoản ổn định (dùng trong `audit_logs.actor`). */
  account_code?: string | null;
  display_name: string;
  role: string;
  roles: string[];
  active: boolean;
  perm_ver: number;
  /** Bảng quyền theo collection: `{collection: mask}`. `*` là quyền mặc định. */
  perm: Record<string, number>;
  /** Cùng nội dung `perm` nhưng ở dạng danh sách tên quyền. */
  perm_flags?: Record<string, string[]>;
  collection_perm?: Record<string, number>;
}

/** `GET /api/v1/auth/roles` — bảng 8 vai trò mặc định. */
export interface RoleInfo {
  name: string;
  mask: number;
  perm: string[];
  description: string;
}

/** Danh mục trạm (`stations`). */
export interface Station {
  code: string;
  name: string;
  kind?: string;
  /** GeoJSON Point dạng `[lon, lat]`. */
  location?: [number, number] | null;
  location_meta?: Record<string, unknown> | null;
  river?: string | null;
  aliases?: string[] | null;
  datum?: string | null;
  merit_comid?: number | null;
  admin?: Record<string, unknown> | null;
  admin_history?: Record<string, unknown>[] | null;
  note?: string | null;
  /** Ngưỡng báo động theo thời gian: `[{from, to, level_1_m, level_2_m, level_3_m}]`. */
  alarm_history?: AlarmSegment[] | null;
  version?: number;
}

export interface AlarmSegment {
  from?: string | null;
  to?: string | null;
  level_1_m?: number | null;
  level_2_m?: number | null;
  level_3_m?: number | null;
}

/** Một phần tử triều trong `tide_extremes` (đỉnh hoặc chân). */
export interface TideItem {
  /** Mã trạm (API trả trong `meta.station`, frontend chuẩn hoá về `station`). */
  station?: string;
  ts: string | null;
  kind: "high" | "low" | string;
  value: number | null;
  /** Cờ QC đã áp bởi G0: `ok`, `suspect`, `corrected`, `rejected`, ... */
  qc?: string | null;
  source_file?: string | null;
  /** Cờ QC (`qc_flags`) của điểm này nếu có — `null` khi chưa có cờ. */
  flag_id?: string | null;
  /** `version` hiện tại của cờ: gửi kèm `PATCH /tides/...` (không cần chờ 428). */
  flag_version?: number | null;
  flag_status?: QcStatus | string | null;
  flag_retired?: boolean | null;
  /** Người quyết định cờ (`user:<account_code>:<role>`) và nhãn hiển thị (API tra `users` khi đọc). */
  flag_decided_by?: string | null;
  flag_decided_by_label?: ActorLabel | null;
  flag_decided_at?: string | null;
}

/** Bản tin KTTV (phần tử của `hydromet_bulletins`). */
export interface Bulletin {
  source_file: string;
  /** Ngày phát hành (giờ Việt Nam) — `YYYY-MM-DD`. */
  bulletin_date?: string | null;
  bulletin_type?: string | null;
  bulletin_no?: string | number | null;
  year?: number | null;
  month?: number | null;
  source?: { is_primary?: boolean; [k: string]: unknown };
  parse?: {
    warnings?: string[] | null;
    /** Các dòng dữ liệu đã parse từ bản tin. */
    rows?: Array<Record<string, unknown>>;
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

/** Dự báo (`forecasts`). */
export interface ForecastItem {
  station?: string | null;
  basin?: string | null;
  issued_at?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  peak_ts?: string | null;
  peak_value?: number | null;
  model_version?: string | null;
  source?: string;
  lag_hours?: number | null;
  upstream?: string | null;
}

export interface ForecastCompareGroup {
  issued_at: string | null;
  peak_ts: string | null;
  peak_value: number | null;
  model_version: string | null;
  source?: string;
  [k: string]: unknown;
}

/** Cảnh báo (`alerts`). */
export interface AlertItem {
  alert_id: string;
  station?: string | null;
  basin?: string | null;
  type?: string | null;
  severity?: string | null;
  issued_at?: string | null;
  valid_to?: string | null;
  message?: string | null;
  status?: string | null;
  ack_by?: string | null;
  ack_by_label?: ActorLabel | null;
  acked_at?: string | null;
  era?: string | null;
}

/** Trạng thái của cờ QC trong `qc_flags` (`vngis.qc_rules.STATUSES`). */
export type QcStatus = "suspect" | "confirmed_ok" | "rejected" | "corrected";

/** Mục duyệt QC (`qc_flags`) — trả về từ `GET /api/v1/review`. */
export interface ReviewItem {
  /** Khoá `_id` của cờ QC (dùng cho `POST /review/{id}/approve`). */
  id: string;
  collection?: string | null;
  station?: string | null;
  /** `high` | `low`. */
  kind?: string | null;
  ts?: string | null;
  /** Giá trị gốc lúc tạo cờ. */
  value?: number | null;
  status: QcStatus;
  corrected_value?: number | null;
  /** Các luật QC đã kích hoạt cờ (R1/R2/R3...). */
  rules?: string[];
  /** `auto` (job QC) | `manual` (người kiểm tra). */
  origin?: string | null;
  reason?: string | null;
  /** Phiên bản cờ — gửi lại khi quyết định (khoá lạc quan). */
  version: number;
  /** Giá trị hiện tại trong `tide_extremes` đã khác giá trị lúc tạo cờ. */
  value_mismatch?: boolean;
  decided_by?: string | null;
  decided_by_label?: ActorLabel | null;
  decided_at?: string | null;
  created_at?: string | null;
}

/** Thân `POST /api/v1/review/{id}/approve`. */
export interface ReviewDecision {
  status: QcStatus;
  /** Bắt buộc, không rỗng (ghi vào `audit_logs`). */
  reason: string;
  /** Chỉ khi `status = "corrected"`. */
  corrected_value?: number;
  version: number;
}

/** Thân `PATCH /api/v1/tides/{station}/{ts}`. */
export interface TideDecision {
  status: "rejected" | "corrected" | "confirmed_ok";
  corrected_value?: number;
  kind?: "high" | "low";
  reason: string;
  /** Bắt buộc khi cờ đã tồn tại (thiếu → 428, lệch → 409). */
  version?: number;
}

/** Kết quả `PATCH /api/v1/tides/{station}/{ts}` (cờ QC sau quyết định). */
export interface TidePatchResult {
  flag_id: string;
  flag_created: boolean;
  status?: QcStatus;
  version?: number;
  decided_by?: string | null;
  decided_by_label?: ActorLabel | null;
  note?: string;
  [k: string]: unknown;
}

/** Bản ghi nhật ký (`audit_logs`). */
/** Nhãn hiển thị của người thực hiện (API tra `users` khi đọc; không lưu trong nhật ký). */
export interface ActorLabel {
  actor_id?: string | null;
  user_id?: string | null;
  username?: string | null;
  display_name?: string | null;
  role?: string | null;
  /** Bản ghi cũ lưu tên đăng nhập thay vì định danh ổn định. */
  legacy?: boolean;
}

export interface AuditItem {
  id: string | null;
  at?: string | null;
  /** `user:<account_code>:<role>` (bản ghi mới) hoặc tên đăng nhập (bản ghi cũ). */
  actor?: string | null;
  actor_label?: ActorLabel | null;
  reverted_by_label?: ActorLabel | null;
  action?: string | null;
  source?: string | null;
  collection?: string | null;
  doc_id?: string | null;
  target?: Record<string, unknown> | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  reason?: string | null;
  state?: string | null;
  reverted_at?: string | null;
  reverted_by?: string | null;
  revert_reason?: string | null;
  revert_audit_id?: string | null;
}

/** Tác vụ nền (`jobs`). */
export interface JobItem {
  id: string | null;
  type?: string | null;
  state?: string | null;
  params?: Record<string, unknown> | null;
  priority?: number | null;
  requested_by?: string | null;
  requested_by_label?: ActorLabel | null;
  worker?: string | null;
  fence?: number | null;
  heartbeat?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  message?: string | null;
  error?: string | null;
  /** Chỉ có với job `train` — phiên bản mô hình (số nguyên; job cũ có thể là chuỗi). */
  model_version?: number | string | null;
}

/** Trạng thái huấn luyện của bản ghi `models` (worker `train`). */
export type TrainState = "queued" | "running" | "succeeded" | "failed" | "skipped";

/** Phiên bản mô hình trong `GET /api/v1/models` (registry `models`, `version` số nguyên). */
export interface ModelItem {
  id: string;
  version: number;
  name?: string | null;
  variant?: string | null;
  status?: "staging" | "production" | "archived" | string | null;
  /** Thiếu ở bản ghi cũ (đăng ký trước khi có worker `train`). */
  train_state?: TrainState | string | null;
  train_reason?: string | null;
  train_error?: string | null;
  job_id?: string | null;
  metrics?: Record<string, number | null> | null;
  requested_by?: string | null;
  requested_by_label?: ActorLabel | null;
  created_at?: string | null;
  train_finished_at?: string | null;
  promoted_at?: string | null;
  promoted_by?: string | null;
  promoted_by_label?: ActorLabel | null;
  /** Bản production đang phục vụ (lượt promote mới nhất). */
  serving?: boolean;
  /** API cho phép promote: đã huấn luyện xong và chưa phục vụ. */
  promotable?: boolean;
}

/** Người dùng trong `GET /api/v1/users`. */
export interface UserItem {
  _id?: string;
  user_id?: string;
  username: string;
  display_name?: string;
  role?: string;
  roles?: string[];
  active?: boolean;
  perm_ver?: number;
  /** Dạng `{collection: [tên quyền]}`. */
  perm?: Record<string, string[]>;
  perm_default?: string[];
  created_at?: string | null;
  last_login?: string | null;
}

/** Tỉnh theo thời kỳ (`province_timeline`). */
export interface Province {
  era: string;
  code: number;
  name: string;
  name_norm?: string;
  aliases_norm?: string[];
  valid_from?: string | null;
  valid_to?: string | null;
  successor?: { code: number; name?: string } | null;
  [k: string]: unknown;
}

/** Xã (`admin_units`, level = "ward"). */
export interface Ward {
  code: number;
  name: string;
  province_code?: number;
  successor_ward_codes?: number[] | null;
  predecessor_ward_codes?: number[] | null;
}

/** Ánh xạ xã cũ ↔ mới (`admin_unit_mappings`). */
export interface WardMapping {
  old?: { ward_code?: number; ward_name?: string; province_code?: number };
  new?: { ward_code?: number; ward_name?: string; province_code?: number };
  [k: string]: unknown;
}

/** Số liệu hồ chứa theo ngày (`reservoir_daily`). */
export interface ReservoirDaily {
  reservoir?: string;
  date?: string | null;
  water_level_m?: number | null;
  inflow_m3s?: number | null;
  outflow_m3s?: number | null;
  spillway_m3s?: number | null;
  turbine_m3s?: number | null;
  storage_hectom3?: number | null;
  rain_mm?: number | null;
  province?: { era?: string; code?: number; note?: string; provisional?: boolean } | null;
  [k: string]: unknown;
}

/** Thống kê đỉnh triều (`GET /api/v1/stats/tide`). */
export interface TideStats {
  kind: string;
  year: number | null;
  n_documents: number;
  stations: Array<{
    station: string | null;
    n: number;
    n_value: number;
    median: number | null;
    mean: number | null;
    min: number | null;
    max: number | null;
    max_ts: string | null;
    exceed: { level_1: number; level_2: number; level_3: number };
  }>;
}

/** GeoJSON FeatureCollection. */
export interface FeatureCollection {
  type?: "FeatureCollection";
  collection?: string;
  era?: string | null;
  count?: number;
  features: Array<{
    type: "Feature";
    geometry: { type: string; coordinates?: unknown } | null;
    properties: Record<string, unknown>;
  }>;
  source?: string;
  note?: string;
}
