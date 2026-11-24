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
  refresh_token: string;
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
  /** Cờ QC: `suspect`, `corrected`, `rejected`, `manual`, ... */
  qc?: string | null;
  source_file?: string | null;
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
  acked_at?: string | null;
  era?: string | null;
}

/** Mục duyệt QC (`qc_flags`) — trả về từ `GET /api/v1/review`. */
export interface ReviewItem {
  /** Khoá `_id` của cờ QC (dùng cho `POST /review/{id}/approve`). */
  id: string;
  collection?: string | null;
  station?: string | null;
  reason?: string | null;
  severity?: string | null;
  /** `pending` | `approved` | `rejected`. */
  state?: string | null;
  version: number;
  created_at?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
}

/** Bản ghi nhật ký (`audit_logs`). */
export interface AuditItem {
  id: string | null;
  at?: string | null;
  actor?: string | null;
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
  version?: number;
}

/** Tác vụ nền (`jobs`). */
export interface JobItem {
  id: string | null;
  type?: string | null;
  state?: string | null;
  params?: Record<string, unknown> | null;
  priority?: number | null;
  requested_by?: string | null;
  worker?: string | null;
  fence?: number | null;
  heartbeat?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  message?: string | null;
  error?: string | null;
  /** Chỉ có với job `train` — phiên bản mô hình được huấn luyện. */
  model_version?: string | null;
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
