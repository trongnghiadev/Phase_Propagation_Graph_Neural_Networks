"use client";

/**
 * Màn hình **QC số liệu**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/review?status=pending` — danh sách cờ QC đang chờ (nguồn chính thức
 *   của “suspect”; API không có endpoint riêng `/tides/suspect`).
 * - `GET /api/v1/stations/{code}/tides` — chuỗi đỉnh/chân triều của một trạm.
 * - `PATCH /api/v1/tides/{station}/{ts}` — sửa giá trị / đặt cờ QC kèm `reason` bắt buộc.
 * - `POST /api/v1/review/{id}/approve` — duyệt (hoặc từ chối) một cờ QC.
 * - `GET /api/v1/bulletins` — bản tin để mở PDF đối chiếu.
 */

import React, { useEffect, useMemo, useState } from "react";

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
import type { Bulletin, ReviewItem, TideItem } from "@/lib/types";

/** Một bản ghi triều cần sửa QC. */
interface EditTarget extends TideItem {
  /** Mã trạm — bắt buộc để gọi `PATCH /tides/{station}/{ts}`. */
  station: string;
  /** Khóa cờ QC tương ứng trong `qc_flags` (nếu có). */
  flagId?: string | null;
}

export default function QcPage() {
  const { canUpdate, canApprove } = usePermission();
  const [station, setStation] = useState("");
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [newValue, setNewValue] = useState("");
  const [newFlag, setNewFlag] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  // Danh sách cờ QC đang chờ duyệt (collection `qc_flags`).
  const flags = useAsync<{ items: ReviewItem[] }>(
    () => api.qcFlags({ status: "pending", limit: 500 }),
    [],
  );
  // Bản tin để mở PDF đối chiếu.
  const bulletins = useAsync<{ items: Bulletin[] }>(() => api.bulletins({ limit: 100 }), []);
  // Thực đo của trạm đang chọn (để sửa giá trị đỉnh/chân).
  const tides = useAsync<{
    station: string;
    count: number;
    items: TideItem[];
  }>(
    () =>
      station
        ? api.stationTides(station, { limit: 4000 })
        : Promise.resolve({ station: "", count: 0, items: [] as TideItem[] }),
    [station],
  );
  // Hàng đợi duyệt (dùng chung nguồn `qc_flags` nhưng hiện mọi trạng thái).
  const reviews = useAsync<{ items: ReviewItem[] }>(() => api.reviewQueue({ limit: 300 }), []);

  /** Ghép cờ QC với bản ghi triều tương ứng (theo trạm + thời điểm). */
  const flagByStationTs = useMemo(() => {
    const map = new Map<string, ReviewItem>();
    (flags.data?.items ?? []).forEach((f) => {
      if (f.station) map.set(f.station, f);
    });
    return map;
  }, [flags.data]);

  useEffect(() => {
    if (editing) {
      setNewValue(
        editing.value !== null && editing.value !== undefined ? String(editing.value) : "",
      );
      setNewFlag(editing.qc ?? "");
      setReason("");
      setErr(null);
    }
  }, [editing]);

  async function save() {
    if (!editing) return;
    setBusy(true);
    setErr(null);
    try {
      await api.patchTide(editing.station, editing.ts as string, {
        value: newValue === "" ? null : Number(newValue),
        qc: newFlag || null,
        reason,
      });
      setOk(`Đã cập nhật QC cho ${editing.station} @ ${formatDateTime(editing.ts)}.`);
      setEditing(null);
      tides.reload();
      flags.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  async function decide(item: ReviewItem, decision: "approve" | "reject", note: string) {
    setBusy(true);
    setErr(null);
    try {
      await api.approveReview(item.id, {
        decision,
        reason: note,
        collection: item.collection ?? undefined,
        version: item.version,
      });
      setOk(decision === "approve" ? `Đã duyệt cờ QC ${item.id}.` : `Đã từ chối cờ QC ${item.id}.`);
      flags.reload();
      reviews.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  /** Các bản ghi triều đang có cờ QC (lấy từ `qc` của `tide_extremes`). */
  const suspectRows = useMemo(() => {
    const out: EditTarget[] = [];
    // API `/stations/{code}/tides` không lặp lại mã trạm trong từng phần tử —
    // chuẩn hoá thêm ở đây để thao tác sửa có đủ định danh.
    const code = station || tides.data?.station || "";
    (tides.data?.items ?? []).forEach((t) => {
      if (t.qc && t.qc !== "ok") {
        out.push({
          ...t,
          station: t.station ?? code,
          flagId: flagByStationTs.get(t.station ?? code)?.id ?? null,
        });
      }
    });
    return out;
  }, [tides.data, flagByStationTs, station]);

  const tideColumns: Column<EditTarget>[] = [
    { key: "station", header: "Trạm", render: (r) => <strong>{r.station}</strong> },
    { key: "ts", header: "Thời điểm", render: (r) => formatDateTime(r.ts) },
    {
      key: "value",
      header: "Mực nước (m)",
      align: "right",
      render: (r) => (r.value === null || r.value === undefined ? "—" : r.value.toFixed(3)),
    },
    {
      key: "kind",
      header: "Loại",
      render: (r) => (r.kind === "high" ? "Triều cao" : r.kind === "low" ? "Triều thấp" : "—"),
    },
    {
      key: "qc",
      header: "Cờ QC",
      render: (r) =>
        r.qc ? <Badge color="#ef6c00">{r.qc}</Badge> : <Badge color="#2e7d32">OK</Badge>,
    },
    { key: "source_file", header: "Bản tin", render: (r) => r.source_file ?? "—" },
    {
      key: "action",
      header: "",
      render: (r) =>
        canUpdate(COLLECTIONS.tides) ? (
          <Button variant="primary" onClick={() => setEditing(r)}>
            Sửa / gắn cờ
          </Button>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>Chỉ đọc</span>
        ),
    },
  ];

  const flagColumns: Column<ReviewItem>[] = [
    { key: "id", header: "Mã cờ", render: (r) => <code style={{ fontSize: 11 }}>{r.id}</code> },
    { key: "station", header: "Trạm", render: (r) => r.station ?? "—" },
    { key: "collection", header: "Collection", render: (r) => r.collection ?? "—" },
    {
      key: "severity",
      header: "Mức",
      render: (r) => (
        <Badge color={r.severity === "high" ? "#c62828" : r.severity === "medium" ? "#ef6c00" : "#f9a825"}>
          {r.severity ?? "—"}
        </Badge>
      ),
    },
    { key: "reason", header: "Lý do nghi ngờ" },
    { key: "created_at", header: "Tạo lúc", render: (r) => formatDateTime(r.created_at) },
    { key: "version", header: "Phiên bản", align: "right" },
    {
      key: "action",
      header: "",
      render: (r) =>
        canApprove(COLLECTIONS.qcFlags) ? (
          <div style={{ display: "flex", gap: 6 }}>
            <Button variant="primary" disabled={busy} onClick={() => void decide(r, "approve", "Đã đối chiếu bản tin PDF")}>
              Duyệt
            </Button>
            <Button disabled={busy} onClick={() => void decide(r, "reject", "Không khớp bản tin gốc")}>
              Từ chối
            </Button>
          </div>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>—</span>
        ),
    },
  ];

  return (
    <div>
      <h1>Kiểm soát chất lượng (QC)</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Sửa giá trị mực nước hoặc gắn cờ QC kèm lý do; mọi thay đổi được ghi vào{" "}
        <code>audit_logs</code> và có thể hoàn tác tại màn hình Nhật ký.
      </p>

      {flags.error && <ErrorNotice error={flags.error} onRetry={flags.reload} />}
      {reviews.error && <ErrorNotice error={reviews.error} onRetry={reviews.reload} />}
      {tides.error && <ErrorNotice error={tides.error} onRetry={tides.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Cờ QC đang chờ duyệt ({flags.data?.items.length ?? 0})</h2>
        <DataTable
          columns={flagColumns}
          rows={flags.data?.items ?? []}
          rowKey={(r) => r.id}
          loading={flags.loading}
          empty="Không có cờ QC nào đang chờ."
          maxHeight={340}
        />
      </section>

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Sửa giá trị / gắn cờ QC</h2>
        <div style={{ display: "flex", gap: 14, alignItems: "flex-end", marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
              Trạm (để tải chuỗi triều)
            </div>
            <input
              value={station}
              onChange={(e) => setStation(e.target.value)}
              placeholder="mã trạm, ví dụ: VUONGTAU"
              style={{ padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
            />
          </div>
          <Button onClick={tides.reload}>Tải lại</Button>
          <div style={{ fontSize: 12, color: "#607d8b" }}>
            {suspectRows.length} bản ghi đang có cờ QC
          </div>
        </div>
        <DataTable
          columns={tideColumns}
          rows={suspectRows}
          rowKey={(r, i) => `${r.station}-${r.ts}-${i}`}
          loading={tides.loading}
          empty={
            station
              ? "Trạm này không có bản ghi nào gắn cờ QC."
              : "Chọn một trạm để xem các bản ghi cần rà soát."
          }
          maxHeight={340}
        />
      </section>

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Bản tin để đối chiếu PDF</h2>
        <DataTable
          columns={[
            { key: "source_file", header: "Tệp nguồn" },
            { key: "bulletin_date", header: "Ngày", render: (r) => formatDateTime(r.bulletin_date) },
            { key: "bulletin_type", header: "Loại", render: (r) => r.bulletin_type ?? "—" },
            {
              key: "warnings",
              header: "Cảnh báo parse",
              render: (r) => {
                const n = r.parse?.warnings?.length ?? 0;
                return n > 0 ? <Badge color="#ef6c00">{n}</Badge> : <Badge color="#2e7d32">0</Badge>;
              },
            },
            {
              key: "action",
              header: "",
              render: (r) => (
                <a
                  className="btn"
                  href={`/ban-tin?q=${encodeURIComponent(r.source_file)}`}
                  target="_blank"
                >
                  Mở ở màn hình Bản tin
                </a>
              ),
            },
          ]}
          rows={(bulletins.data?.items ?? []).filter((b) => (b.parse?.warnings?.length ?? 0) > 0)}
          rowKey={(r) => r.source_file}
          loading={bulletins.loading}
          empty="Không có bản tin nào có cảnh báo parse."
          maxHeight={280}
        />
      </section>

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Hàng đợi kiểm duyệt (mọi trạng thái)</h2>
        <DataTable
          columns={[
            { key: "id", header: "Mã", render: (r) => <code style={{ fontSize: 11 }}>{r.id}</code> },
            { key: "station", header: "Trạm", render: (r) => r.station ?? "—" },
            { key: "collection", header: "Collection", render: (r) => r.collection ?? "—" },
            { key: "reason", header: "Lý do", render: (r) => r.reason ?? "—" },
            {
              key: "state",
              header: "Trạng thái",
              render: (r) => (
                <Badge
                  color={
                    r.state === "approved"
                      ? "#2e7d32"
                      : r.state === "rejected"
                        ? "#c62828"
                        : "#f9a825"
                  }
                >
                  {r.state ?? "—"}
                </Badge>
              ),
            },
            { key: "reviewed_by", header: "Người duyệt", render: (r) => r.reviewed_by ?? "—" },
            { key: "reviewed_at", header: "Lúc duyệt", render: (r) => formatDateTime(r.reviewed_at) },
          ]}
          rows={reviews.data?.items ?? []}
          rowKey={(r) => r.id}
          loading={reviews.loading}
          empty="Không có hồ sơ nào."
          maxHeight={320}
        />
      </section>

      <Modal
        open={Boolean(editing)}
        title={`Sửa QC — ${editing?.station ?? ""} @ ${editing ? formatDateTime(editing.ts) : ""}`}
        onClose={() => setEditing(null)}
      >
        {editing && (
          <>
            <div style={{ fontSize: 13, marginBottom: 10, color: "#607d8b" }}>
              Giá trị hiện tại:{" "}
              <strong>
                {editing.value === null || editing.value === undefined
                  ? "—"
                  : editing.value.toFixed(3)}{" "}
                m
              </strong>
              {editing.source_file ? ` · bản tin: ${editing.source_file}` : ""}
            </div>
            <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              Giá trị mới (m)
            </label>
            <input
              type="number"
              step="0.001"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              style={{ width: "100%", padding: 8, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
            />

            <label style={{ display: "block", fontSize: 13, fontWeight: 600, margin: "10px 0 4px" }}>
              Cờ QC
            </label>
            <select
              value={newFlag}
              onChange={(e) => setNewFlag(e.target.value)}
              style={{ width: "100%", padding: 8, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
            >
              <option value="">— Bỏ cờ (đạt) —</option>
              <option value="suspect">suspect — nghi ngờ</option>
              <option value="corrected">corrected — đã hiệu chỉnh</option>
              <option value="rejected">rejected — loại bỏ</option>
              <option value="manual">manual — nhập tay</option>
            </select>

            <label style={{ display: "block", fontSize: 13, fontWeight: 600, margin: "10px 0 4px" }}>
              Lý do (bắt buộc)
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="Ví dụ: đối chiếu bản tin PDF ngày 12/01, giá trị bị lệch 0.12 m"
              style={{ width: "100%", padding: 8, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
            />

            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <Button variant="primary" disabled={busy || !reason.trim()} onClick={() => void save()}>
                {busy ? "Đang lưu…" : "Lưu thay đổi"}
              </Button>
              <Button onClick={() => setEditing(null)}>Hủy</Button>
            </div>
            {reason.trim() === "" && (
              <div style={{ fontSize: 12, color: "#ef6c00", marginTop: 6 }}>
                API yêu cầu <code>reason</code> không rỗng (<code>min_length=1</code>).
              </div>
            )}
          </>
        )}
      </Modal>
    </div>
  );
}
