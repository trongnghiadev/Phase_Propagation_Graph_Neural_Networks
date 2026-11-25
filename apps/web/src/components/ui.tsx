"use client";

/**
 * Các component giao diện dùng chung: bảng, badge, hộp thông báo, hộp thoại,
 * biểu đồ SVG tự vẽ (không phụ thuộc thư viện biểu đồ ngoài).
 */

import React, { useEffect, useState } from "react";

import { ApiOfflineError, ApiRequestError } from "@/lib/api";

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------

export function Badge({
  children,
  color = "#546e7a",
  title,
}: {
  children: React.ReactNode;
  color?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 10,
        background: `${color}1a`,
        color,
        border: `1px solid ${color}55`,
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Thông báo lỗi / trạng thái
// ---------------------------------------------------------------------------

export type NoticeKind = "error" | "warn" | "info" | "success";

const PALETTE: Record<NoticeKind, { bg: string; border: string; fg: string }> = {
  error: { bg: "#fdecea", border: "#c62828", fg: "#8e0000" },
  warn: { bg: "#fff8e1", border: "#f9a825", fg: "#7a4b00" },
  info: { bg: "#e3f2fd", border: "#1565c0", fg: "#0d47a1" },
  success: { bg: "#e8f5e9", border: "#2e7d32", fg: "#1b5e20" },
};

/**
 * Hiển thị thông báo lỗi. Đặc biệt xử lý:
 * - **423**: người khác đang sửa bản ghi.
 * - **409**: xung đột phiên bản (dữ liệu đã đổi).
 * - **503**: API chưa sẵn sàng (chưa có mô hình đã huấn luyện).
 * - Lỗi mạng: API không chạy → nói rõ thay vì để trang trắng.
 */
export function ErrorNotice({
  error,
  onRetry,
  onDismiss,
  title: titleOverride,
}: {
  error: unknown;
  onRetry?: () => void;
  onDismiss?: () => void;
  /** Tiêu đề riêng; khi có thì bỏ gợi ý mặc định theo mã lỗi (thông điệp đã đủ rõ). */
  title?: string;
}) {
  if (!error) return null;

  const isOffline = error instanceof ApiOfflineError;
  const status = error instanceof ApiRequestError ? error.status : null;

  let title = "Lỗi";
  let kind: NoticeKind = "error";
  let hint = "";

  if (isOffline) {
    title = "Không kết nối được API";
    kind = "warn";
    hint =
      "Máy chủ API có thể chưa chạy hoặc mất kết nối. Hãy kiểm tra cụm Docker " +
      "(docker compose -f infra/compose/docker-compose.yml --profile ha up -d) " +
      "hoặc đổi NEXT_PUBLIC_API_BASE_URL trong .env.local.";
  } else if (status === 423) {
    title = "Đang có người khác sửa";
    hint = "Bản ghi này đang bị khoá. Vui lòng chờ hoặc tải lại trang rồi thử lại.";
  } else if (status === 409) {
    title = "Xung đột phiên bản";
    hint =
      "Dữ liệu đã bị thay đổi (hoặc thao tác đã được thực hiện) kể từ lúc bạn mở. " +
      "Hãy tải lại để có dữ liệu mới rồi quyết định lại.";
  } else if (status === 422) {
    title = "Dữ liệu không hợp lệ";
    hint = "Kiểm tra lại các trường đã nhập; thao tác này có thể không được hỗ trợ cho loại bản ghi này.";
  } else if (status === 428) {
    title = "Cần phiên bản của bản ghi";
    hint = "Bản ghi đã có quyết định trước đó. Xem lại trạng thái hiện tại rồi gửi lại kèm phiên bản.";
  } else if (status === 503) {
    title = "Dịch vụ tạm thời không sẵn sàng";
    const ra = error instanceof ApiRequestError ? error.retryAfter : null;
    hint =
      "Máy chủ chưa xử lý được yêu cầu (Redis/khoá phân tán gián đoạn, mất khoá trước khi ghi, " +
      "hoặc dữ liệu/mô hình chưa sẵn sàng). Không có thay đổi nào được ghi." +
      (ra ? ` Thử lại sau khoảng ${ra} giây.` : " Vui lòng thử lại sau ít giây.");
  } else if (status === 403) {
    title = "Không đủ quyền";
    hint = "Tài khoản của bạn không có quyền thao tác này. Liên hệ quản trị viên.";
  } else if (status === 429) {
    title = "Vượt giới hạn tần suất";
    hint = "Bạn gọi API quá nhanh. Vui lòng thử lại sau ít phút.";
  } else if (status === 401) {
    title = "Phiên đăng nhập hết hạn";
    hint = "Vui lòng đăng nhập lại.";
  }

  if (titleOverride) {
    title = titleOverride;
    hint = "";
  }

  const message = error instanceof Error ? error.message : String(error);
  const requestId = error instanceof ApiRequestError ? error.requestId : null;
  const p = PALETTE[kind];

  return (
    <div
      role="alert"
      style={{
        background: p.bg,
        border: `1px solid ${p.border}`,
        borderLeft: `4px solid ${p.border}`,
        borderRadius: 6,
        padding: "10px 14px",
        color: p.fg,
        margin: "12px 0",
        fontSize: 14,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <strong>{title}</strong>
        <span style={{ display: "flex", gap: 8 }}>
          {onRetry && (
            <button type="button" onClick={onRetry} style={linkBtn}>
              Thử lại
            </button>
          )}
          {onDismiss && (
            <button type="button" onClick={onDismiss} style={linkBtn}>
              Đóng
            </button>
          )}
        </span>
      </div>
      <div style={{ marginTop: 4 }}>{message}</div>
      {hint && <div style={{ marginTop: 6, opacity: 0.85, fontSize: 13 }}>{hint}</div>}
      {requestId && (
        <div style={{ marginTop: 6, fontSize: 12, opacity: 0.7 }}>
          request_id: {requestId}
        </div>
      )}
    </div>
  );
}

const linkBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "inherit",
  cursor: "pointer",
  textDecoration: "underline",
  fontSize: 13,
  padding: 0,
};

/** Thông báo trạng thái không phải lỗi (ví dụ "chưa có dữ liệu"). */
export function InfoNotice({
  children,
  kind = "info",
}: {
  children: React.ReactNode;
  kind?: NoticeKind;
}) {
  const p = PALETTE[kind];
  return (
    <div
      style={{
        background: p.bg,
        border: `1px solid ${p.border}`,
        borderRadius: 6,
        padding: "10px 14px",
        color: p.fg,
        margin: "12px 0",
        fontSize: 14,
      }}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bảng
// ---------------------------------------------------------------------------

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  width?: string;
  align?: "left" | "right" | "center";
}
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty = "Không có dữ liệu.",
  loading = false,
  onRowClick,
  maxHeight,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  empty?: string;
  loading?: boolean;
  onRowClick?: (row: T) => void;
  maxHeight?: number;
}) {
  if (loading) {
    return <div style={emptyStyle}>Đang tải dữ liệu…</div>;
  }
  if (!rows.length) {
    return <div style={emptyStyle}>{empty}</div>;
  }
  return (
    <div style={{ overflow: "auto", maxHeight }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                style={{ ...thStyle, width: c.width, textAlign: c.align ?? "left" }}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              style={{ cursor: onRowClick ? "pointer" : "default" }}
              onMouseEnter={(e) => {
                if (onRowClick) e.currentTarget.style.background = "#eef3fb";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
              }}
            >
              {columns.map((c) => (
                <td key={c.key} style={{ ...tdStyle, textAlign: c.align ?? "left" }}>
                  {c.render
                    ? c.render(row)
                    : String((row as unknown as Record<string, unknown>)[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
  background: "#fff",
};

const thStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderBottom: "2px solid #d7dee8",
  background: "#f5f7fa",
  fontWeight: 700,
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "7px 10px",
  borderBottom: "1px solid #eceff3",
  verticalAlign: "top",
};

const emptyStyle: React.CSSProperties = {
  padding: 24,
  textAlign: "center",
  color: "#78909c",
  background: "#fafbfc",
  border: "1px dashed #cfd8dc",
  borderRadius: 6,
  margin: "12px 0",
  fontSize: 14,
};

// ---------------------------------------------------------------------------
// Hộp thoại
// ---------------------------------------------------------------------------

export function Modal({
  open,
  title,
  onClose,
  children,
  width = 720,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        padding: "32px 16px",
        zIndex: 1200,
        overflow: "auto",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#fff",
          borderRadius: 8,
          width: "100%",
          maxWidth: width,
          boxShadow: "0 12px 40px rgba(0,0,0,0.3)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "12px 16px",
            borderBottom: "1px solid #e3e8ee",
          }}
        >
          <strong>{title}</strong>
          <button type="button" onClick={onClose} style={closeBtn} aria-label="Đóng">
            ✕
          </button>
        </div>
        <div style={{ padding: 16 }}>{children}</div>
      </div>
    </div>
  );
}

const closeBtn: React.CSSProperties = {
  border: "none",
  background: "transparent",
  fontSize: 16,
  cursor: "pointer",
  color: "#607d8b",
};

// ---------------------------------------------------------------------------
// Biểu đồ SVG tự vẽ (tránh thêm thư viện)
// ---------------------------------------------------------------------------

export interface Series {
  name: string;
  color: string;
  points: Array<{ x: string | number; y: number | null }>;
  /** Vẽ vùng tô (khoảng tin cậy) thay vì đường. */
  fill?: boolean;
  dashed?: boolean;
}

/** Biểu đồ đường SVG: trục X theo chuỗi ngày, trục Y theo giá trị số. */
export function LineChart({
  series,
  height = 260,
  yLabel,
  title,
  markers,
}: {
  series: Series[];
  height?: number;
  yLabel?: string;
  title?: string;
  /** Vạch ngang đánh dấu (ví dụ ngưỡng báo động). */
  markers?: Array<{ value: number; color: string; label: string }>;
}) {
  const W = 900;
  const H = height;
  const PAD = { l: 56, r: 16, t: title ? 28 : 12, b: 40 };
  const all = series.flatMap((s) => s.points.filter((p) => p.y !== null));
  if (!all.length) {
    return <div style={emptyStyle}>Chưa có số liệu để vẽ biểu đồ.</div>;
  }
  const xs = series.flatMap((s) => s.points.map((p) => p.x));
  const xLabels = [...new Set(xs.map((x) => String(x)))];
  const yVals = all.map((p) => p.y as number);
  let yMin = Math.min(...yVals);
  let yMax = Math.max(...yVals);
  if (markers?.length) {
    yMin = Math.min(yMin, ...markers.map((m) => m.value));
    yMax = Math.max(yMax, ...markers.map((m) => m.value));
  }
  const pad = (yMax - yMin) * 0.12 || 0.5;
  yMin -= pad;
  yMax += pad;

  const xOf = (x: string | number) => {
    // `indexOf` trả -1 khi nhãn không có trong `xLabels` ⇒ toạ độ âm (vẽ ra ngoài khung).
    const i = Math.max(xLabels.indexOf(String(x)), 0);
    const n = Math.max(xLabels.length - 1, 1);
    return PAD.l + (i / n) * (W - PAD.l - PAD.r);
  };
  const yOf = (y: number) =>
    PAD.t + (1 - (y - yMin) / (yMax - yMin || 1)) * (H - PAD.t - PAD.b);

  const ticks = 5;
  const yTicks = Array.from({ length: ticks + 1 }, (_, i) => yMin + ((yMax - yMin) * i) / ticks);
  const step = Math.max(1, Math.ceil(xLabels.length / 8));

  return (
    <div>
      {title && (
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{title}</div>
      )}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", height: "auto", background: "#fff" }}
        role="img"
        aria-label={title ?? "Biểu đồ"}
      >
        {yTicks.map((t, i) => (
          <g key={`y${i}`}>
            <line
              x1={PAD.l}
              x2={W - PAD.r}
              y1={yOf(t)}
              y2={yOf(t)}
              stroke="#eceff3"
              strokeWidth={1}
            />
            <text x={PAD.l - 8} y={yOf(t) + 4} fontSize={11} fill="#607d8b" textAnchor="end">
              {t.toFixed(2)}
            </text>
          </g>
        ))}
        {xLabels.map((x, i) =>
          i % step === 0 ? (
            <text
              key={`x${i}`}
              x={xOf(x)}
              y={H - PAD.b + 16}
              fontSize={11}
              fill="#607d8b"
              textAnchor="middle"
            >
              {String(x).slice(5)}
            </text>
          ) : null,
        )}
        <line x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={H - PAD.b} stroke="#b0bec5" />
        <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="#b0bec5" />
        {markers?.map((m, i) => (
          <g key={`m${i}`}>
            <line
              x1={PAD.l}
              x2={W - PAD.r}
              y1={yOf(m.value)}
              y2={yOf(m.value)}
              stroke={m.color}
              strokeDasharray="5 4"
              strokeWidth={1.2}
            />
            <text x={W - PAD.r - 4} y={yOf(m.value) - 4} fontSize={11} fill={m.color} textAnchor="end">
              {m.label}
            </text>
          </g>
        ))}
        {series.map((s) => {
          const segs: string[] = [];
          let cur: string[] = [];
          s.points.forEach((p, idx) => {
            if (p.y === null) {
              if (cur.length) segs.push(cur.join(" "));
              cur = [];
              return;
            }
            const cmd =
              idx === 0 || s.points[idx - 1]?.y === null ? "M" : "L";
            cur.push(`${cmd}${xOf(p.x).toFixed(1)},${yOf(p.y).toFixed(1)}`);
          });
          if (cur.length) segs.push(cur.join(" "));

          const areas: string[] = [];
          if (s.fill) {
            const valid = s.points.filter((p) => p.y !== null) as Array<{
              x: string | number;
              y: number;
            }>;
            if (valid.length > 1) {
              const top = valid
                .map((p, i) => `${i === 0 ? "M" : "L"}${xOf(p.x).toFixed(1)},${yOf(p.y).toFixed(1)}`)
                .join(" ");
              const bot = [...valid]
                .reverse()
                .map((p) => `L${xOf(p.x).toFixed(1)},${yOf(p.y).toFixed(1)}`)
                .join(" ");
              areas.push(`${top} ${bot} Z`);
            }
          }
          return (
            <g key={s.name}>
              {areas.map((d, i) => (
                <path key={`a${i}`} d={d} fill={s.color} fillOpacity={0.18} stroke="none" />
              ))}
              {segs.map((d, i) => (
                <path
                  key={`s${i}`}
                  d={d}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={2}
                  strokeDasharray={s.dashed ? "6 4" : undefined}
                />
              ))}
            </g>
          );
        })}
      </svg>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, marginTop: 4 }}>
        {series.map((s) => (
          <span key={s.name} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span
              style={{
                width: 14,
                height: 3,
                background: s.color,
                display: "inline-block",
                borderTop: s.dashed ? "3px dashed " : undefined,
              }}
            />
            {s.name}
          </span>
        ))}
        {yLabel && <span style={{ color: "#78909c" }}>Đơn vị: {yLabel}</span>}
      </div>
    </div>
  );
}

/** Biểu đồ cột cho so sánh (ví dụ sai số theo trạm / theo chân trời). */
export function BarChart({
  data,
  height = 240,
  yLabel,
  color = "#1565c0",
  title,
}: {
  data: Array<{ label: string; value: number | null }>;
  height?: number;
  yLabel?: string;
  color?: string;
  title?: string;
}) {
  const valid = data.filter((d) => d.value !== null);
  if (!valid.length) {
    return <div style={emptyStyle}>Chưa có số liệu để vẽ biểu đồ.</div>;
  }
  const W = 900;
  const H = height;
  const PAD = { l: 56, r: 16, t: title ? 28 : 12, b: 52 };
  const max = Math.max(...valid.map((d) => d.value as number), 0);
  const min = Math.min(...valid.map((d) => d.value as number), 0);
  const span = max - min || 1;
  const n = Math.max(data.length, 1);
  const slot = (W - PAD.l - PAD.r) / n;
  const barW = Math.max(6, slot * 0.62);
  const zeroY = PAD.t + ((max - 0) / span) * (H - PAD.t - PAD.b);

  return (
    <div>
      {title && (
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>{title}</div>
      )}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", height: "auto", background: "#fff" }}
        role="img"
        aria-label={title ?? "Biểu đồ cột"}
      >
        <line x1={PAD.l} x2={W - PAD.r} y1={zeroY} y2={zeroY} stroke="#78909c" strokeWidth={1} />
        {data.map((d, i) => {
          const x = PAD.l + i * slot + (slot - barW) / 2;
          const v = d.value;
          if (v === null) return null;
          const y = v >= 0 ? zeroY - (v / span) * (H - PAD.t - PAD.b) : zeroY;
          const h = Math.max(1, Math.abs((v / span) * (H - PAD.t - PAD.b)));
          return (
            <g key={`${d.label}-${i}`}>
              <rect x={x} y={y} width={barW} height={h} fill={color} rx={2}>
                <title>{`${d.label}: ${v.toFixed(3)}`}</title>
              </rect>
              <text
                x={x + barW / 2}
                y={H - PAD.b + 14}
                fontSize={10}
                fill="#607d8b"
                textAnchor="middle"
                transform={`rotate(-25 ${x + barW / 2} ${H - PAD.b + 14})`}
              >
                {d.label}
              </text>
            </g>
          );
        })}
        <text x={PAD.l - 8} y={PAD.t + 4} fontSize={11} fill="#607d8b" textAnchor="end">
          {max.toFixed(2)}
        </text>
        <text x={PAD.l - 8} y={zeroY + 4} fontSize={11} fill="#607d8b" textAnchor="end">
          0
        </text>
        <text x={PAD.l - 8} y={H - PAD.b} fontSize={11} fill="#607d8b" textAnchor="end">
          {min.toFixed(2)}
        </text>
      </svg>
      {yLabel && <div style={{ fontSize: 12, color: "#78909c" }}>Đơn vị: {yLabel}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Form
// ---------------------------------------------------------------------------

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <span style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 3 }}>
        {label}
      </span>
      {children}
      {hint && <span style={{ fontSize: 12, color: "#78909c" }}>{hint}</span>}
    </label>
  );
}

export const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "7px 9px",
  border: "1px solid #cfd8dc",
  borderRadius: 4,
  fontSize: 14,
  background: "#fff",
};

export function Button({
  children,
  onClick,
  type = "button",
  variant = "default",
  disabled = false,
  title,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  type?: "button" | "submit";
  variant?: "default" | "primary" | "danger";
  disabled?: boolean;
  title?: string;
}) {
  const styles: Record<string, React.CSSProperties> = {
    default: { background: "#fff", color: "#37474f", border: "1px solid #b0bec5" },
    primary: { background: "#1565c0", color: "#fff", border: "1px solid #1565c0" },
    danger: { background: "#c62828", color: "#fff", border: "1px solid #c62828" },
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        ...styles[variant],
        padding: "6px 12px",
        borderRadius: 4,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
        fontSize: 13,
        fontWeight: 600,
      }}
    >
      {children}
    </button>
  );
}

/** Hộp chọn ngày. */
export function DateInput({
  value,
  onChange,
  label,
  max,
  min,
}: {
  value: string;
  onChange: (v: string) => void;
  label?: string;
  max?: string;
  min?: string;
}) {
  return (
    <Field label={label ?? "Ngày"}>
      <input
        type="date"
        value={value}
        max={max}
        min={min}
        onChange={(e) => onChange(e.target.value)}
        style={inputStyle}
      />
    </Field>
  );
}

/** Trạng thái nút khi đang tải PDF. */
export function useBusy() {
  const [busy, setBusy] = useState<string | null>(null);
  const run = async <T,>(key: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(key);
    try {
      return await fn();
    } finally {
      setBusy(null);
    }
  };
  return { busy, run };
}
