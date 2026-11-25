/**
 * Tiện ích định dạng và quy ước hiển thị dùng chung cho các màn hình.
 */

/**
 * Hiển thị người thực hiện. Nhật ký lưu định danh ổn định `user:<account_code>:<role>` (không lưu
 * tên); API gắn thêm nhãn (`actor_label`, `requested_by_label`) khi đọc. Có nhãn → "tên (vai trò)";
 * không có nhãn → rút gọn định danh ("acc_… (vai trò)"); giá trị cũ (tên đăng nhập, `cli`) giữ nguyên.
 */
export function formatActor(
  actor: string | null | undefined,
  label?: { display_name?: string | null; username?: string | null; role?: string | null } | null,
): string {
  if (!actor) return "—";
  const m = /^user:(acc_[0-9a-f]+)(?::([A-Za-z0-9_.-]+))?$/.exec(actor);
  const role = label?.role || m?.[2] || "";
  const name = label?.display_name || label?.username || (m ? m[1] : actor);
  return role ? `${name} (${role})` : name;
}

/** Ngày theo giờ Việt Nam (API trả ISO 8601 kèm múi giờ +07:00). */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  return d.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

/** Ngày hôm nay theo giờ Việt Nam, dạng `YYYY-MM-DD`. */
export function todayVN(): string {
  const now = new Date();
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Ngày trước đó N ngày theo giờ Việt Nam, dạng `YYYY-MM-DD`. */
export function daysAgoVN(days: number): string {
  const d = new Date(Date.now() - days * 86_400_000);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Số có 2 chữ số thập phân (bỏ `null`). */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

/** Số có dấu phân cách nghìn. */
export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("vi-VN");
}

/**
 * Mức vượt báo động của một giá trị so với ngưỡng theo thời gian.
 * Ngưỡng lấy từ `stations.alarm_history` (mục 2.3 / K9 của đề cương) — KHÔNG
 * dùng ngưỡng cố định.
 */
export type ExceedLevel = 0 | 1 | 2 | 3;

export interface AlarmThresholds {
  level_1_m: number | null;
  level_2_m: number | null;
  level_3_m: number | null;
}

/** Chọn đoạn `alarm_history` có hiệu lực tại thời điểm `when` (ISO hoặc Date). */
export function thresholdsAt(
  history: Array<Record<string, unknown>> | null | undefined,
  when: string | Date = new Date(),
): AlarmThresholds {
  const empty: AlarmThresholds = { level_1_m: null, level_2_m: null, level_3_m: null };
  if (!history || history.length === 0) return empty;
  const t = when instanceof Date ? when.getTime() : new Date(when).getTime();
  for (const seg of history) {
    const from = seg.from ? new Date(String(seg.from)).getTime() : Number.NEGATIVE_INFINITY;
    const to = seg.to ? new Date(String(seg.to)).getTime() : Number.POSITIVE_INFINITY;
    if (Number.isNaN(from) || Number.isNaN(to)) continue;
    if (t >= from && t <= to) {
      return {
        level_1_m: (seg.level_1_m as number | null) ?? null,
        level_2_m: (seg.level_2_m as number | null) ?? null,
        level_3_m: (seg.level_3_m as number | null) ?? null,
      };
    }
  }
  return empty;
}

/** Xếp cấp vượt báo động (0 = không vượt, 3 = vượt cấp III). */
export function exceedLevel(value: number | null | undefined, th: AlarmThresholds): ExceedLevel {
  if (value === null || value === undefined || Number.isNaN(value)) return 0;
  if (th.level_3_m !== null && value >= th.level_3_m) return 3;
  if (th.level_2_m !== null && value >= th.level_2_m) return 2;
  if (th.level_1_m !== null && value >= th.level_1_m) return 1;
  return 0;
}

/** Nhãn + màu cho từng cấp vượt báo động. */
export const EXCEED_META: Record<ExceedLevel, { label: string; color: string; short: string }> = {
  0: { label: "Dưới ngưỡng BĐ I", color: "#2e7d32", short: "BĐ <I" },
  1: { label: "Vượt BĐ I", color: "#f9a825", short: "BĐ I" },
  2: { label: "Vượt BĐ II", color: "#ef6c00", short: "BĐ II" },
  3: { label: "Vượt BĐ III", color: "#c62828", short: "BĐ III" },
};

/** Rút gọn chuỗi dài cho bảng. */
export function truncate(s: string | null | undefined, max = 60): string {
  if (!s) return "—";
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/**
 * Kiểm tra tọa độ trạm hợp lệ: mảng GeoJSON Point `[lon, lat]` có ít nhất 2 phần tử
 * và hai phần tử đầu là số hữu hạn. Dùng để tránh `.toFixed` / `Number(...)` lỗi khi
 * `location` rỗng, thiếu phần tử hoặc chứa giá trị không phải số.
 */
export function hasStationLocation(
  location: readonly number[] | null | undefined,
): location is [number, number] {
  return (
    Array.isArray(location) &&
    location.length >= 2 &&
    typeof location[0] === "number" &&
    typeof location[1] === "number" &&
    Number.isFinite(location[0]) &&
    Number.isFinite(location[1])
  );
}

/** Tên hiển thị của trạng job / QC / cảnh báo (tiếng Việt). */
export function statusLabel(state: string | null | undefined): string {
  const map: Record<string, string> = {
    pending: "Chờ xử lý",
    approved: "Đã duyệt",
    rejected: "Bị từ chối",
    suspect: "Nghi ngờ",
    confirmed_ok: "Đã đối chiếu OK",
    corrected: "Đã sửa",
    retired: "Đã thu hồi",
    queued: "Đang chờ",
    running: "Đang chạy",
    succeeded: "Thành công",
    failed: "Thất bại",
    acknowledged: "Đã xác nhận",
    resolved: "Đã xử lý",
    active: "Đang hoạt động",
    inactive: "Đã khoá",
    applied: "Đã áp dụng",
    reverted: "Đã hoàn tác",
    pending_rollback: "Chờ hoàn tác",
    promoted: "Đang phục vụ",
    archived: "Lưu trữ",
    staging: "Chờ phát hành",
  };
  const key = (state ?? "").toLowerCase();
  return map[key] ?? (state || "—");
}

/** Màu badge theo trạng thái. */
export function statusColor(state: string | null | undefined): string {
  const key = (state ?? "").toLowerCase();
  if (["failed", "rejected", "retired"].includes(key)) return "#c62828";
  if (["pending", "queued", "suspect", "running"].includes(key)) return "#f9a825";
  if (["approved", "succeeded", "acknowledged", "resolved", "applied", "confirmed_ok"].includes(key))
    return "#2e7d32";
  if (["corrected", "promoted", "active"].includes(key)) return "#1565c0";
  return "#546e7a";
}

/** Nhãn tiếng Việt của cấp báo động. */
export function severityLabel(sev: string | null | undefined): string {
  const key = (sev ?? "").toLowerCase();
  if (key === "1" || key === "level_1" || key === "i") return "BĐ I";
  if (key === "2" || key === "level_2" || key === "ii") return "BĐ II";
  if (key === "3" || key === "level_3" || key === "iii") return "BĐ III";
  return sev || "—";
}
