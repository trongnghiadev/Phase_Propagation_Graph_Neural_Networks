"use client";

/**
 * Màn hình **Hàng đợi kiểm duyệt** (cờ QC trong `qc_flags`).
 *
 * Endpoint dùng:
 * - `GET /api/v1/review?status=suspect|confirmed_ok|rejected|corrected[&station=]` — cờ QC theo
 *   trạng thái (mặc định `suspect` = chưa quyết định; cờ `retired` bị bỏ).
 * - `POST /api/v1/review/{id}/approve` — ghi quyết định
 *   `{status, reason (bắt buộc), corrected_value (chỉ khi corrected), version}`.
 *   409 khi `version` lệch (người khác vừa quyết định), 422 khi quyết định sai,
 *   423 khi cờ đang được người khác xử lý, 503 khi mất khoá trước khi ghi.
 *
 * Quyết định chỉ ghi vào `qc_flags` (+ `audit_logs`); bước G0 áp vào `tide_extremes` ở lần chạy sau.
 */

import React, { useState } from "react";

import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  Modal,
  inputStyle,
  type Column,
} from "@/components/ui";
import { ApiRequestError, api, toError } from "@/lib/api";
import { formatActor, formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { QcStatus, ReviewItem } from "@/lib/types";

const STATUSES: { key: QcStatus; label: string; color: string }[] = [
  { key: "suspect", label: "Chờ duyệt (suspect)", color: "#f9a825" },
  { key: "confirmed_ok", label: "Xác nhận đúng", color: "#2e7d32" },
  { key: "corrected", label: "Đã hiệu chỉnh", color: "#1565c0" },
  { key: "rejected", label: "Đã loại bỏ", color: "#c62828" },
];

const STATUS_LABEL: Record<string, string> = Object.fromEntries(STATUSES.map((s) => [s.key, s.label]));
const STATUS_COLOR: Record<string, string> = Object.fromEntries(STATUSES.map((s) => [s.key, s.color]));

export default function ReviewQueuePage() {
  const { canApprove } = usePermission();
  const allowed = canApprove(COLLECTIONS.qcFlags);
  const [status, setStatus] = useState<QcStatus>("suspect");
  const [station, setStation] = useState("");
  const [deciding, setDeciding] = useState<ReviewItem | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const queue = useAsync<{ count: number; items: ReviewItem[] }>(
    () => api.reviewQueue({ status, station: station.trim() || undefined, limit: 500 }),
    [status, station],
  );

  const columns: Column<ReviewItem>[] = [
    { key: "station", header: "Trạm", render: (r) => <strong>{r.station ?? "—"}</strong> },
    { key: "ts", header: "Thời điểm", render: (r) => formatDateTime(r.ts) },
    {
      key: "kind",
      header: "Loại",
      render: (r) => (r.kind === "high" ? "Đỉnh" : r.kind === "low" ? "Chân" : "—"),
    },
    {
      key: "value",
      header: "Giá trị (m)",
      align: "right",
      render: (r) => (
        <span>
          {typeof r.value === "number" ? r.value.toFixed(3) : "—"}
          {r.value_mismatch && (
            <span title="Giá trị trong tide_extremes đã khác lúc tạo cờ" style={{ marginLeft: 4 }}>
              <Badge color="#6d4c41">lệch</Badge>
            </span>
          )}
        </span>
      ),
    },
    {
      key: "rules",
      header: "Luật",
      render: (r) => (r.rules && r.rules.length ? r.rules.join(", ") : r.origin ?? "—"),
    },
    { key: "reason", header: "Lý do", render: (r) => r.reason ?? "—" },
    {
      key: "status",
      header: "Trạng thái",
      render: (r) => (
        <Badge color={STATUS_COLOR[r.status] ?? "#546e7a"}>
          {STATUS_LABEL[r.status] ?? r.status}
          {r.status === "corrected" && typeof r.corrected_value === "number"
            ? ` → ${r.corrected_value.toFixed(3)}`
            : ""}
        </Badge>
      ),
    },
    { key: "version", header: "Phiên bản", align: "right" },
    {
      key: "decided_by",
      header: "Người quyết định",
      render: (r) => (r.decided_by ? `${formatActor(r.decided_by, r.decided_by_label)} · ${formatDateTime(r.decided_at)}` : "—"),
    },
    {
      key: "action",
      header: "",
      render: (r) =>
        allowed ? (
          <Button
            variant={r.status === "suspect" ? "primary" : "default"}
            onClick={() => {
              setOk(null);
              setDeciding(r);
            }}
          >
            {r.status === "suspect" ? "Quyết định" : "Đổi quyết định"}
          </Button>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>Chỉ đọc</span>
        ),
    },
  ];

  return (
    <div>
      <h1>Hàng đợi kiểm duyệt</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Cờ QC do job kiểm tra chất lượng hoặc người kiểm tra tạo. Người duyệt cần quyền{" "}
        <code>APPROVE</code> trên <code>qc_flags</code>. Quyết định được ghi vào nhật ký và áp
        vào số liệu triều ở lần chạy G0 kế tiếp.
      </p>

      {queue.error && <ErrorNotice error={queue.error} onRetry={queue.reload} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}
      {!allowed && (
        <InfoNotice kind="warn">
          Tài khoản của bạn không có quyền <code>APPROVE</code> nên chỉ xem được hàng đợi.
        </InfoNotice>
      )}

      <div
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        {STATUSES.map((s) => (
          <Button
            key={s.key}
            variant={status === s.key ? "primary" : "default"}
            onClick={() => setStatus(s.key)}
          >
            {s.label}
          </Button>
        ))}
        <input
          value={station}
          onChange={(e) => setStation(e.target.value)}
          placeholder="lọc theo mã trạm"
          style={{ ...inputStyle, width: 180, marginLeft: 8 }}
        />
        <Button onClick={queue.reload}>Tải lại</Button>
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#607d8b" }}>
          {queue.data?.count ?? 0} mục
        </div>
      </div>

      <section
        style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 6, padding: 14 }}
      >
        <DataTable
          columns={columns}
          rows={queue.data?.items ?? []}
          rowKey={(r) => r.id}
          loading={queue.loading}
          empty="Không có cờ QC nào ở trạng thái này."
          maxHeight={560}
        />
      </section>

      {deciding && (
        <DecisionDialog
          item={deciding}
          onClose={() => setDeciding(null)}
          onConflict={() => {
            setDeciding(null);
            queue.reload();
          }}
          onDone={(msg) => {
            setDeciding(null);
            setOk(msg);
            queue.reload();
          }}
        />
      )}
    </div>
  );
}

/** Hộp thoại ghi quyết định cho một cờ QC (gửi kèm `version` đã xem). */
function DecisionDialog({
  item,
  onClose,
  onConflict,
  onDone,
}: {
  item: ReviewItem;
  onClose: () => void;
  onConflict: () => void;
  onDone: (msg: string) => void;
}) {
  const [decision, setDecision] = useState<QcStatus>(
    item.status === "suspect" ? "confirmed_ok" : item.status,
  );
  const [corrected, setCorrected] = useState(
    typeof item.corrected_value === "number" ? String(item.corrected_value) : "",
  );
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);

  const correctedNum = corrected.trim() === "" ? NaN : Number(corrected);
  const needsValue = decision === "corrected";
  const valid = reason.trim().length > 0 && (!needsValue || Number.isFinite(correctedNum));
  const conflict = err instanceof ApiRequestError && err.status === 409;

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const res = await api.approveReview(item.id, {
        status: decision,
        reason: reason.trim(),
        corrected_value: needsValue ? correctedNum : undefined,
        version: item.version,
      });
      onDone(
        `Đã ghi quyết định "${STATUS_LABEL[decision] ?? decision}" cho cờ ${item.id} ` +
          `(phiên bản ${res.version ?? "?"}).`,
      );
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open title={`Quyết định cờ QC — ${item.station ?? ""}`} onClose={onClose}>
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {conflict && (
        <div style={{ marginBottom: 10 }}>
          <Button onClick={onConflict}>Tải lại hàng đợi để xem quyết định mới</Button>
        </div>
      )}
      <table style={{ fontSize: 13, width: "100%", marginBottom: 12 }}>
        <tbody>
          <Row label="Mã cờ" value={<code style={{ fontSize: 11 }}>{item.id}</code>} />
          <Row label="Thời điểm" value={`${formatDateTime(item.ts)} (${item.kind ?? "—"})`} />
          <Row
            label="Giá trị gốc"
            value={typeof item.value === "number" ? `${item.value.toFixed(3)} m` : "—"}
          />
          <Row label="Luật / lý do" value={`${(item.rules ?? []).join(", ") || "—"} · ${item.reason ?? ""}`} />
          <Row label="Trạng thái hiện tại" value={STATUS_LABEL[item.status] ?? item.status} />
          <Row label="Phiên bản đã xem" value={item.version} />
        </tbody>
      </table>
      {item.value_mismatch && (
        <InfoNotice kind="warn">
          Giá trị trong <code>tide_extremes</code> đã khác lúc tạo cờ — hãy đối chiếu bản tin gốc
          trước khi quyết định.
        </InfoNotice>
      )}

      <label style={labelStyle}>Quyết định</label>
      <select
        value={decision}
        onChange={(e) => setDecision(e.target.value as QcStatus)}
        style={inputStyle}
      >
        <option value="confirmed_ok">Xác nhận giá trị đúng (confirmed_ok)</option>
        <option value="corrected">Hiệu chỉnh giá trị (corrected)</option>
        <option value="rejected">Loại bỏ giá trị (rejected)</option>
        {item.status !== "suspect" && <option value="suspect">Bỏ quyết định cũ (suspect)</option>}
      </select>

      {needsValue && (
        <>
          <label style={labelStyle}>Giá trị hiệu chỉnh (m, bắt buộc)</label>
          <input
            type="number"
            step="0.001"
            value={corrected}
            onChange={(e) => setCorrected(e.target.value)}
            style={inputStyle}
          />
        </>
      )}

      <label style={labelStyle}>Lý do (bắt buộc)</label>
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={3}
        maxLength={1000}
        placeholder="ví dụ: đã đối chiếu bản tin PDF ngày 12/01"
        style={{ ...inputStyle, fontFamily: "inherit" }}
      />
      {!reason.trim() && (
        <div style={{ fontSize: 12, color: "#ef6c00", marginTop: 4 }}>Cần nhập lý do quyết định.</div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <Button
          variant={decision === "rejected" ? "danger" : "primary"}
          disabled={busy || !valid}
          onClick={() => void submit()}
        >
          {busy ? "Đang ghi…" : "Ghi quyết định"}
        </Button>
        <Button onClick={onClose}>Hủy</Button>
      </div>
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <tr>
      <td style={{ padding: "3px 10px 3px 0", color: "#607d8b", width: 150, verticalAlign: "top" }}>
        {label}
      </td>
      <td style={{ padding: "3px 0", verticalAlign: "top" }}>{value}</td>
    </tr>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 13,
  fontWeight: 600,
  margin: "10px 0 4px",
};
