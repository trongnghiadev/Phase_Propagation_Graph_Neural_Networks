"use client";

/**
 * Màn hình **Hàng đợi kiểm duyệt**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/review?status=pending` — các cờ QC đang chờ duyệt.
 * - `GET /api/v1/review?status=approved|rejected` — lịch sử duyệt.
 * - `POST /api/v1/review/{id}/approve` — duyệt (`decision="approve"`) hoặc từ chối
 *   (`decision="reject"`, bắt buộc `reason`).
 *
 * Ghi chú: `GET /review` mặc định đã lọc `state = "pending"`; truyền `status` rỗng
 * sẽ không lấy được toàn bộ lịch sử — muốn xem lịch sử phải chỉ định trạng thái.
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
import { formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { ReviewItem } from "@/lib/types";

const STATES = [
  { key: "pending", label: "Chờ duyệt" },
  { key: "approved", label: "Đã duyệt" },
  { key: "rejected", label: "Đã từ chối" },
];

export default function ReviewQueuePage() {
  const { canApprove } = usePermission();
  const [state, setState] = useState("pending");
  const [decide, setDecide] = useState<{ item: ReviewItem; decision: "approve" | "reject" } | null>(
    null,
  );
  const [reason, setReason] = useState("");
  const [newValue, setNewValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const queue = useAsync<{ count: number; items: ReviewItem[] }>(
    () => api.reviewQueue({ status: state, limit: 500 }),
    [state],
  );

  async function submit() {
    if (!decide) return;
    setBusy(true);
    setErr(null);
    try {
      await api.approveReview(decide.item.id, {
        decision: decide.decision,
        reason,
        collection: decide.item.collection ?? undefined,
        new_value: newValue.trim() ? Number(newValue) : undefined,
        version: decide.item.version,
      });
      setOk(
        decide.decision === "approve"
          ? `Đã duyệt cờ QC ${decide.item.id}.`
          : `Đã từ chối cờ QC ${decide.item.id}.`,
      );
      setDecide(null);
      setReason("");
      setNewValue("");
      queue.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<ReviewItem>[] = [
    { key: "id", header: "Mã", render: (r) => <code style={{ fontSize: 11 }}>{r.id}</code> },
    { key: "station", header: "Trạm", render: (r) => r.station ?? "—" },
    { key: "collection", header: "Collection", render: (r) => r.collection ?? "—" },
    {
      key: "severity",
      header: "Mức",
      render: (r) => (
        <Badge
          color={
            r.severity === "high" || r.severity === "3"
              ? "#c62828"
              : r.severity === "medium" || r.severity === "2"
                ? "#ef6c00"
                : "#f9a825"
          }
        >
          {r.severity ?? "—"}
        </Badge>
      ),
    },
    { key: "reason", header: "Lý do nghi ngờ" },
    { key: "created_at", header: "Tạo lúc", render: (r) => formatDateTime(r.created_at) },
    { key: "version", header: "Phiên bản", align: "right" },
    { key: "reviewed_by", header: "Người duyệt", render: (r) => r.reviewed_by ?? "—" },
    {
      key: "action",
      header: "",
      render: (r) =>
        canApprove(COLLECTIONS.qcFlags) && r.state === "pending" ? (
          <div style={{ display: "flex", gap: 6 }}>
            <Button
              variant="primary"
              onClick={() => {
                setDecide({ item: r, decision: "approve" });
                setReason("");
                setNewValue("");
              }}
            >
              Duyệt
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setDecide({ item: r, decision: "reject" });
                setReason("");
              }}
            >
              Từ chối
            </Button>
          </div>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>
            {r.state === "pending" ? "Chỉ đọc" : (r.state ?? "—")}
          </span>
        ),
    },
  ];

  return (
    <div>
      <h1>Hàng đợi kiểm duyệt</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Các cờ QC do bước G0 sinh ra đang chờ người rà soát. Người duyệt cần có quyền{" "}
        <code>APPROVE</code> trên collection <code>qc_flags</code>.
      </p>

      {queue.error && <ErrorNotice error={queue.error} onRetry={queue.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}
      {!canApprove(COLLECTIONS.qcFlags) && (
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
        }}
      >
        {STATES.map((s) => (
          <Button
            key={s.key}
            variant={state === s.key ? "primary" : "default"}
            onClick={() => setState(s.key)}
          >
            {s.label}
          </Button>
        ))}
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#607d8b" }}>
          {queue.data?.count ?? 0} mục
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
          rows={queue.data?.items ?? []}
          rowKey={(r) => r.id}
          loading={queue.loading}
          empty="Không có mục nào trong trạng thái này."
          maxHeight={560}
        />
      </section>

      <Modal
        open={Boolean(decide)}
        title={
          decide?.decision === "approve"
            ? `Duyệt cờ QC — ${decide.item.id}`
            : `Từ chối cờ QC — ${decide?.item.id ?? ""}`
        }
        onClose={() => setDecide(null)}
      >
        {decide && (
          <>
            <table style={{ fontSize: 13, width: "100%", marginBottom: 12 }}>
              <tbody>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b", width: 140 }}>Trạm</td>
                  <td style={{ padding: "3px 0" }}>{decide.item.station ?? "—"}</td>
                </tr>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b" }}>Collection</td>
                  <td style={{ padding: "3px 0" }}>{decide.item.collection ?? "—"}</td>
                </tr>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b" }}>Lý do</td>
                  <td style={{ padding: "3px 0" }}>{decide.item.reason ?? "—"}</td>
                </tr>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b" }}>Phiên bản</td>
                  <td style={{ padding: "3px 0" }}>{decide.item.version}</td>
                </tr>
              </tbody>
            </table>

            <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              Lý do quyết định {decide.decision === "reject" ? "(bắt buộc)" : ""}
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder={
                decide.decision === "approve"
                  ? "ví dụ: đã đối chiếu bản tin PDF ngày 12/01"
                  : "bắt buộc nêu lý do từ chối"
              }
              style={{
                width: "100%",
                padding: 8,
                borderRadius: 4,
                border: "1px solid #cfd8dc",
                fontSize: 13,
              }}
            />

            {decide.decision === "approve" && (
              <>
                <label
                  style={{ display: "block", fontSize: 13, fontWeight: 600, margin: "10px 0 4px" }}
                >
                  Giá trị sửa đề xuất (tuỳ chọn)
                </label>
                <input
                  type="number"
                  step="0.001"
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  placeholder="để trống nếu không sửa giá trị"
                  style={{
                    width: "100%",
                    padding: 8,
                    borderRadius: 4,
                    border: "1px solid #cfd8dc",
                    fontSize: 13,
                  }}
                />
                <div style={{ fontSize: 12, color: "#78909c", marginTop: 4 }}>
                  Nếu có giá trị, API sẽ ghi vào bản ghi gốc theo điều kiện lọc của cờ QC
                  (có fencing token để tránh ghi đè bản ghi đang bị sửa).
                </div>
              </>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <Button
                variant={decide.decision === "approve" ? "primary" : "danger"}
                disabled={busy || (decide.decision === "reject" && !reason.trim())}
                onClick={() => void submit()}
              >
                {busy ? "Đang xử lý…" : decide.decision === "approve" ? "Duyệt" : "Từ chối"}
              </Button>
              <Button onClick={() => setDecide(null)}>Hủy</Button>
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
