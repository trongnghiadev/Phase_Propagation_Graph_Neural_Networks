"use client";

/**
 * Màn hình **Nhật ký thay đổi**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/audit?collection=&actor=&state=` — danh sách bản ghi nhật ký
 *   (cần quyền `VIEW_AUDIT`).
 * - `GET /api/v1/audit/{id}` — chi tiết một bản ghi.
 * - `POST /api/v1/audit/{id}/revert` — hoàn tác (bắt buộc `reason`). Chỉ hai loại:
 *   `qc_decision` (đưa cờ QC về quyết định trước) và `bulletin_metadata` (khôi phục trường bản tin);
 *   loại khác → 422. Đã/đang hoàn tác, chưa `applied` hoặc dữ liệu đã bị sửa tiếp → 409.
 *   Cần `VIEW_AUDIT` và quyền ghi trên collection đích (thiếu → 403).
 *   Hoàn tác tạo một bản ghi nhật ký MỚI; bản ghi gốc chỉ được gắn `reverted_at`.
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
import { formatActor, formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS, PERM_FLAGS } from "@/lib/permissions";
import type { AuditItem } from "@/lib/types";

/**
 * Loại thay đổi API cho phép hoàn tác và quyền ghi cần có trên collection đích
 * (`REVERTIBLE` trong `apps/api/src/api/router/audit.py`): `qc_decision` cần APPROVE trên `qc_flags`,
 * `bulletin_metadata` cần UPDATE trên `hydromet_bulletins`.
 */
const REVERTIBLE: Record<string, { collection: string; flag: number }> = {
  qc_decision: { collection: "qc_flags", flag: PERM_FLAGS.APPROVE },
  bulletin_metadata: { collection: "hydromet_bulletins", flag: PERM_FLAGS.UPDATE },
};

/** Bản ghi có thể hoàn tác qua API không (kiểm tra thật vẫn ở API). */
function canRevertItem(a: AuditItem): boolean {
  const rule = a.action ? REVERTIBLE[a.action] : undefined;
  return Boolean(a.id && rule && a.collection === rule.collection && a.state === "applied" && !a.reverted_at);
}

export default function AuditPage() {
  const { can, canViewAudit } = usePermission();
  /** Người dùng có quyền ghi cần thiết trên collection đích của bản ghi (API kiểm tra lại, thiếu → 403). */
  const hasRevertPermission = (a: AuditItem): boolean => {
    const rule = a.action ? REVERTIBLE[a.action] : undefined;
    return Boolean(rule && can(rule.collection, rule.flag));
  };
  const [collection, setCollection] = useState("");
  const [actor, setActor] = useState("");
  const [state, setState] = useState("");
  const [detail, setDetail] = useState<AuditItem | null>(null);
  const [reverting, setReverting] = useState<AuditItem | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const audit = useAsync<{ count: number; items: AuditItem[] }>(
    () =>
      api.audit({
        collection: collection || undefined,
        actor: actor || undefined,
        state: state || undefined,
        limit: 300,
      }),
    [collection, actor, state],
  );

  async function revert() {
    if (!reverting) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await api.revertAudit(reverting.id ?? "", { reason: reason.trim() });
      const newId = typeof res.revert_audit_id === "string" ? res.revert_audit_id : null;
      setOk(
        `Đã hoàn tác bản ghi nhật ký ${reverting.id}` +
          (newId ? ` (bản ghi hoàn tác mới: ${newId}).` : "."),
      );
      setReverting(null);
      setReason("");
      audit.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<AuditItem>[] = [
    {
      key: "at",
      header: "Thời điểm",
      render: (a) => formatDateTime(a.at),
    },
    { key: "actor", header: "Người thực hiện", render: (a) => formatActor(a.actor, a.actor_label) },
    { key: "action", header: "Hành động", render: (a) => a.action ?? "—" },
    { key: "source", header: "Nguồn", render: (a) => a.source ?? "—" },
    { key: "collection", header: "Collection", render: (a) => a.collection ?? "—" },
    { key: "doc_id", header: "Tài liệu", render: (a) => a.doc_id ?? "—" },
    { key: "reason", header: "Lý do", render: (a) => a.reason ?? "—" },
    {
      key: "state",
      header: "Trạng thái",
      render: (a) => (
        <span style={{ display: "inline-flex", gap: 4 }}>
          <Badge color={a.state === "applied" ? "#2e7d32" : "#f9a825"}>{a.state ?? "—"}</Badge>
          {a.reverted_at && (
            <Badge color="#6d4c41" title={`hoàn tác lúc ${formatDateTime(a.reverted_at)}`}>
              đã hoàn tác
            </Badge>
          )}
        </span>
      ),
    },
    {
      key: "action2",
      header: "",
      render: (a) => (
        <div style={{ display: "flex", gap: 6 }}>
          <Button onClick={() => setDetail(a)}>Xem</Button>
          {canViewAudit(COLLECTIONS.audit) && canRevertItem(a) && hasRevertPermission(a) && (
            <Button
              variant="danger"
              onClick={() => {
                setOk(null);
                setErr(null);
                setReason("");
                setReverting(a);
              }}
            >
              Hoàn tác
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <h1>Nhật ký thay đổi</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Mọi thay đổi dữ liệu (từ API hoặc pipeline) đều được ghi vào{" "}
        <code>audit_logs</code>. Chỉ hoàn tác được quyết định QC (<code>qc_decision</code>) và sửa
        metadata bản tin (<code>bulletin_metadata</code>) đã áp dụng.
      </p>

      {audit.error && <ErrorNotice error={audit.error} onRetry={audit.reload} />}
      {err && !reverting && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}
      {!canViewAudit(COLLECTIONS.audit) && (
        <InfoNotice kind="warn">
          Bạn cần quyền <code>VIEW_AUDIT</code> để xem nhật ký. Backend sẽ trả 403 nếu
          gọi thủ công.
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
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Collection</div>
          <input
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            placeholder="ví dụ: stations"
            style={sel}
          />
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Người thực hiện</div>
          <input
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            placeholder="tên đăng nhập hoặc user:acc_…"
            style={sel}
          />
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Trạng thái</div>
          <select value={state} onChange={(e) => setState(e.target.value)} style={sel}>
            <option value="">Tất cả</option>
            <option value="pending">pending</option>
            <option value="applied">applied</option>
          </select>
        </div>
        <Button onClick={audit.reload}>Tải lại</Button>
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#607d8b" }}>
          {audit.data?.count ?? 0} bản ghi
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
          rows={audit.data?.items ?? []}
          rowKey={(a, i) => a.id ?? `${a.at}-${i}`}
          loading={audit.loading}
          empty="Không có bản ghi nhật ký nào."
          maxHeight={560}
        />
      </section>

      <Modal
        open={Boolean(detail)}
        title={`Bản ghi nhật ký — ${detail?.id ?? ""}`}
        onClose={() => setDetail(null)}
        width={860}
      >
        {detail && (
          <>
            <table style={{ fontSize: 13, width: "100%", marginBottom: 14 }}>
              <tbody>
                <Row label="Thời điểm" value={formatDateTime(detail.at)} />
                <Row label="Người thực hiện" value={formatActor(detail.actor, detail.actor_label)} />
                <Row label="Định danh" value={detail.actor ?? "—"} />
                <Row label="Hành động" value={detail.action ?? "—"} />
                <Row label="Nguồn" value={detail.source ?? "—"} />
                <Row label="Collection" value={detail.collection ?? "—"} />
                <Row label="Tài liệu" value={detail.doc_id ?? "—"} />
                <Row label="Lý do" value={detail.reason ?? "—"} />
                <Row label="Trạng thái" value={detail.state ?? "—"} />
                {detail.reverted_at && (
                  <>
                    <Row label="Hoàn tác lúc" value={formatDateTime(detail.reverted_at)} />
                    <Row label="Người hoàn tác" value={formatActor(detail.reverted_by, detail.reverted_by_label)} />
                    <Row label="Lý do hoàn tác" value={detail.revert_reason ?? "—"} />
                    <Row label="Bản ghi hoàn tác" value={detail.revert_audit_id ?? "—"} />
                  </>
                )}
              </tbody>
            </table>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <h3 style={{ marginTop: 0, fontSize: 13 }}>Giá trị trước (before)</h3>
                <pre style={preStyle}>{JSON.stringify(detail.before ?? null, null, 2)}</pre>
              </div>
              <div>
                <h3 style={{ marginTop: 0, fontSize: 13 }}>Giá trị sau (after)</h3>
                <pre style={preStyle}>{JSON.stringify(detail.after ?? null, null, 2)}</pre>
              </div>
            </div>

            {canRevertItem(detail) && !hasRevertPermission(detail) && (
              <InfoNotice kind="info">
                Bạn không có quyền hoàn tác bản ghi này (cần{" "}
                {detail.action === "qc_decision" ? "APPROVE trên qc_flags" : "UPDATE trên hydromet_bulletins"}).
              </InfoNotice>
            )}
            {!canRevertItem(detail) && (
              <InfoNotice kind="info">
                {detail.reverted_at
                  ? "Bản ghi này đã được hoàn tác."
                  : detail.action && REVERTIBLE[detail.action]
                    ? "Chỉ hoàn tác được thay đổi đã áp dụng (state = applied)."
                    : "Loại thay đổi này không hoàn tác được qua API (chỉ qc_decision và bulletin_metadata)."}
              </InfoNotice>
            )}
          </>
        )}
      </Modal>

      <Modal
        open={Boolean(reverting)}
        title={`Hoàn tác — ${reverting?.id ?? ""}`}
        onClose={() => setReverting(null)}
      >
        {reverting && (
          <>
            {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
            <div style={{ fontSize: 13, marginBottom: 10, color: "#607d8b" }}>
              {reverting.action === "qc_decision"
                ? "Sẽ đưa cờ QC về quyết định trước thay đổi này"
                : "Sẽ khôi phục giá trị trước thay đổi này của bản tin"}{" "}
              <code>{reverting.doc_id}</code> (collection <code>{reverting.collection}</code>). Nếu
              dữ liệu đã bị sửa tiếp sau thay đổi này, API sẽ từ chối (409).
            </div>
            <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              Lý do hoàn tác (bắt buộc)
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              maxLength={1000}
              placeholder="ví dụ: duyệt nhầm cờ QC"
              style={{
                width: "100%",
                padding: 8,
                borderRadius: 4,
                border: "1px solid #cfd8dc",
                fontSize: 13,
              }}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <Button variant="danger" disabled={busy || !reason.trim()} onClick={() => void revert()}>
                {busy ? "Đang hoàn tác…" : "Hoàn tác"}
              </Button>
              <Button onClick={() => setReverting(null)}>Hủy</Button>
            </div>
          </>
        )}
      </Modal>
    </div>
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

const sel: React.CSSProperties = {
  padding: 6,
  borderRadius: 4,
  border: "1px solid #cfd8dc",
  fontSize: 13,
  background: "#fff",
  minWidth: 160,
};

const preStyle: React.CSSProperties = {
  background: "#fafbfc",
  border: "1px solid var(--line)",
  borderRadius: 4,
  padding: 8,
  fontSize: 11,
  maxHeight: 260,
  overflow: "auto",
  margin: 0,
};
