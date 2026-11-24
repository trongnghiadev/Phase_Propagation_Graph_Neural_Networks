/**
 * Bitmask 12 bit của backend (`apps/api/src/api/permissions.py`).
 *
 * Backend luôn kiểm tra quyền thật ở mọi endpoint (`require(flag, collection)`).
 * Frontend chỉ dùng bitmask để **ẩn/hiện nút** cho khỏi gây 403 — đây là tiện ích
 * giao diện, KHÔNG phải cơ chế bảo mật.
 *
 * Bảng vai trò mặc định (đề cương 5.4.2):
 *   viewer=1 · analyst=17 · editor=23 · reviewer=183
 *   data_engineer=255 · hydrologist=1169 · forecast_officer=3249 · admin=4095
 */

/** Tên quyền — thứ tự khớp với `IntFlag P` của backend. */
export const PERM_FLAGS = {
  READ: 1,
  CREATE: 2,
  UPDATE: 4,
  DELETE: 8,
  EXPORT: 16,
  APPROVE: 32,
  RUN_JOB: 64,
  VIEW_AUDIT: 128,
  MANAGE_USERS: 256,
  MANAGE_CACHE: 512,
  RUN_FORECAST: 1024,
  MANAGE_MODEL: 2048,
} as const;

export type PermFlagName = keyof typeof PERM_FLAGS;

/** Toàn bộ 12 bit = 4095. */
export const PERM_ALL = 4095;

/** Nhãn tiếng Việt của từng quyền (dùng cho bảng quyền trong màn hình Người dùng). */
export const PERM_FLAG_LABELS: Record<PermFlagName, string> = {
  READ: "Đọc",
  CREATE: "Tạo",
  UPDATE: "Sửa",
  DELETE: "Xoá",
  EXPORT: "Xuất / tải PDF",
  APPROVE: "Duyệt",
  RUN_JOB: "Chạy job",
  VIEW_AUDIT: "Xem nhật ký",
  MANAGE_USERS: "Quản lý người dùng",
  MANAGE_CACHE: "Quản lý cache & khoá",
  RUN_FORECAST: "Chạy dự báo",
  MANAGE_MODEL: "Quản lý mô hình",
};

/**
 * Collection được backend dùng làm khoá quyền. Tên collection phải khớp CHÍNH XÁC
 * với chuỗi trong `require(flag, "<collection>")` của từng router, nếu không quyền
 * sẽ được suy ra từ `perm["*"]` (quyền mặc định) — vẫn đúng, nhưng kém chính xác.
 */
export const COLLECTIONS = {
  bulletins: "hydromet_bulletins",
  bulletinFiles: "hydromet_bulletin_files",
  stations: "stations",
  tides: "tide_extremes",
  qcFlags: "qc_flags",
  forecasts: "forecasts",
  models: "models",
  alerts: "alerts",
  adminUnits: "admin_units",
  provinceTimeline: "province_timeline",
  adminUnitMappings: "admin_unit_mappings",
  reservoirs: "reservoir_daily",
  jobs: "jobs",
  audit: "audit_logs",
  users: "users",
  cache: "cache",
  locks: "locks",
  system: "system",
  rivers: "rivers",
  reviewQueue: "review_queue",
} as const;

/** Bảng quyền theo collection của người dùng hiện tại. */
export type PermTable = Record<string, number>;

/** Kiểm tra mask có đủ TẤT CẢ bit của `required` hay không (giống `api.permissions.has`). */
export function has(mask: number, required: number): boolean {
  return (mask & required) === required;
}

/**
 * Mask quyền của một collection.
 *
 * Collection không khai báo → dùng `perm["*"]` (mặc định); không có `*` nữa → 0.
 * Giữ nguyên giá trị `0` đã khai báo tường minh (collection bị thu hồi toàn bộ quyền).
 */
export function maskOf(perm: PermTable | undefined | null, collection: string): number {
  if (!perm) return 0;
  if (collection in perm) return Number(perm[collection]) || 0;
  if ("*" in perm) return Number(perm["*"]) || 0;
  return 0;
}

/** Người dùng có quyền `flag` trên `collection` hay không. */
export function can(
  perm: PermTable | undefined | null,
  collection: string,
  flag: number,
): boolean {
  return has(maskOf(perm, collection), flag);
}

/** Danh sách tên quyền có trong một mask. */
export function flagNames(mask: number): PermFlagName[] {
  return (Object.keys(PERM_FLAGS) as PermFlagName[]).filter((n) => (mask & PERM_FLAGS[n]) !== 0);
}

/** Chuyển danh sách tên quyền thành mask (đối chiếu trùng tên backend). */
export function maskFromNames(names: string[]): number {
  let out = 0;
  for (const raw of names || []) {
    const key = raw.trim().toUpperCase() as PermFlagName;
    if (key in PERM_FLAGS) out |= PERM_FLAGS[key];
  }
  return out;
}

/** Bảng vai trò mặc định — dùng khi hiển thị trợ giúp (không thay thế API `/auth/roles`). */
export const DEFAULT_ROLE_MASKS: Record<string, number> = {
  viewer: 1,
  analyst: 17,
  editor: 23,
  reviewer: 183,
  data_engineer: 255,
  hydrologist: 1169,
  forecast_officer: 3249,
  admin: 4095,
};
