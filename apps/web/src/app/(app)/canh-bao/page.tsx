"use client";

/**
 * Màn hình **Cảnh báo**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/alerts` — danh sách cảnh báo (lọc theo trạm, mức, trạng thái).
 * - `GET /api/v1/alerts/{id}` — chi tiết.
 * - `POST /api/v1/alerts/{id}/ack` — xác nhận cảnh báo (cần quyền UPDATE).
 */

import React, { useState } from "react";

import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  Modal,
  type Column,
} from "@/components/ui";
import { api, toError } from "@/lib/api";
import { formatActor, formatDateTime, severityLabel, statusColor, statusLabel } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { AlertItem } from "@/lib/types";

export default function AlertsPage() {
  const { canUpdate } = usePermission();
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [station, setStation] = useState("");
  const [detail, setDetail] = useState<AlertItem | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [ackError, setAckError] = useState<Error | null>(null);
  const [ackDone, setAckDone] = useState<string | null>(null);

  const alerts = useAsync<{ items: AlertItem[] }>(
    () =>
      api.alerts({
        severity: severity || undefined,
        status: status || undefined,
        station: station || undefined,
        limit: 500,
      }),
    [severity, status, station],
  );

  const canAck = canUpdate(COLLECTIONS.alerts);

  async function ack(alertId: string, newStatus: string) {
    setBusy(alertId);
    setAckError(null);
    try {
      await api.ackAlert(alertId, { note, status: newStatus });
      setAckDone(alertId);
      setDetail(null);
      setNote("");
      alerts.reload();
    } catch (err) {
      setAckError(toError(err));
    } finally {
      setBusy(null);
    }
  }

  const columns: Column<AlertItem>[] = [
    {
      key: "alert_id",
      header: "Mã",
      render: (r) => (
        <button
          type="button"
          onClick={() => setDetail(r)}
          style={{ background: "none", border: "none", color: "#0d47a1", cursor: "pointer", padding: 0, fontSize: 13 }}
        >
          {r.alert_id}
        </button>
      ),
    },
    { key: "station", header: "Trạm", render: (r) => r.station ?? "—" },
    { key: "basin", header: "Lưu vực", render: (r) => r.basin ?? "—" },
    {
      key: "severity",
      header: "Mức",
      render: (r) => (
        <Badge
          color={
            r.severity === "3" ? "#c62828" : r.severity === "2" ? "#ef6c00" : "#f9a825"
          }
        >
          {severityLabel(r.severity)}
        </Badge>
      ),
    },
    { key: "type", header: "Loại", render: (r) => r.type ?? "—" },
    { key: "message", header: "Nội dung" },
    { key: "issued_at", header: "Phát hành", render: (r) => formatDateTime(r.issued_at) },
    { key: "valid_to", header: "Hiệu lực đến", render: (r) => formatDateTime(r.valid_to) },
    {
      key: "status",
      header: "Trạng thái",
      render: (r) => <Badge color={statusColor(r.status)}>{statusLabel(r.status)}</Badge>,
    },
    { key: "ack_by", header: "Người xác nhận", render: (r) => formatActor(r.ack_by, r.ack_by_label) },
    { key: "era", header: "Thời kỳ", render: (r) => r.era ?? "—" },
    ...(canAck
      ? [
          {
            key: "action",
            header: "Thao tác",
            render: (r: AlertItem) => (
              <Button
                variant="primary"
                disabled={busy === r.alert_id}
                onClick={() => void ack(r.alert_id, "acknowledged")}
              >
                {busy === r.alert_id ? "Đang xác nhận…" : "Xác nhận"}
              </Button>
            ),
          },
        ]
      : []),
  ];

  return (
    <div>
      <h1>Cảnh báo</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Cảnh báo vượt cấp báo động do worker sinh; người dùng có quyền{" "}
        <code>UPDATE</code> trên collection <code>alerts</code> mới xác nhận được.
      </p>

      {alerts.error && <ErrorNotice error={alerts.error} onRetry={alerts.reload} />}
      {ackError && <ErrorNotice error={ackError} onDismiss={() => setAckError(null)} />}
      {ackDone && (
        <InfoNotice kind="success">Đã xác nhận cảnh báo {ackDone}.</InfoNotice>
      )}
      {!canAck && (
        <InfoNotice kind="warn">
          Tài khoản của bạn chỉ có quyền đọc, nên không hiện nút xác nhận. Backend cũng sẽ
          trả 403 nếu gọi thủ công.
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
          gap: 14,
          flexWrap: "wrap",
          alignItems: "flex-end",
        }}
      >
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Mức</div>
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            style={sel}
          >
            <option value="">Tất cả</option>
            <option value="1">BĐ I</option>
            <option value="2">BĐ II</option>
            <option value="3">BĐ III</option>
          </select>
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Trạng thái</div>
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={sel}>
            <option value="">Tất cả</option>
            <option value="pending">Chờ xử lý</option>
            <option value="acknowledged">Đã xác nhận</option>
            <option value="resolved">Đã xử lý</option>
          </select>
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Trạm</div>
          <input
            value={station}
            onChange={(e) => setStation(e.target.value)}
            placeholder="mã trạm"
            style={{ ...sel, minWidth: 120 }}
          />
        </div>
      </div>

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
        }}
      >
        <DataTable
          columns={columns}
          rows={alerts.data?.items ?? []}
          rowKey={(r) => r.alert_id}
          loading={alerts.loading}
          empty="Không có cảnh báo nào."
          maxHeight={560}
        />
      </section>

      <Modal
        open={Boolean(detail)}
        title={`Cảnh báo ${detail?.alert_id ?? ""}`}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <>
            <table style={{ fontSize: 13, width: "100%", marginBottom: 12 }}>
              <tbody>
                <Row label="Trạm" value={detail.station ?? "—"} />
                <Row label="Lưu vực" value={detail.basin ?? "—"} />
                <Row label="Mức" value={severityLabel(detail.severity)} />
                <Row label="Loại" value={detail.type ?? "—"} />
                <Row label="Nội dung" value={detail.message ?? "—"} />
                <Row label="Phát hành" value={formatDateTime(detail.issued_at)} />
                <Row label="Hiệu lực đến" value={formatDateTime(detail.valid_to)} />
                <Row label="Thời kỳ địa giới" value={detail.era ?? "—"} />
                <Row label="Trạng thái" value={statusLabel(detail.status)} />
                <Row label="Người xác nhận" value={formatActor(detail.ack_by, detail.ack_by_label)} />
              </tbody>
            </table>

            {canAck && (
              <>
                <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                  Ghi chú xác nhận
                </label>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={3}
                  style={{ width: "100%", padding: 8, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
                />
                <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                  <Button
                    variant="primary"
                    disabled={busy === detail.alert_id}
                    onClick={() => void ack(detail.alert_id, "acknowledged")}
                  >
                    Xác nhận
                  </Button>
                  <Button
                    disabled={busy === detail.alert_id}
                    onClick={() => void ack(detail.alert_id, "resolved")}
                  >
                    Đánh dấu đã xử lý
                  </Button>
                </div>
              </>
            )}
          </>
        )}
      </Modal>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <tr>
      <td style={{ padding: "3px 8px 3px 0", color: "#607d8b", width: 150, verticalAlign: "top" }}>
        {label}
      </td>
      <td style={{ padding: "3px 0", verticalAlign: "top" }}>{value}</td>
    </tr>
  );
}

const sel: React.CSSProperties = {
  padding: 6,
  borderRadius: 4,
  border: "1px solid #cfd8dc",
  fontSize: 13,
  background: "#fff",
  minWidth: 130,
};
