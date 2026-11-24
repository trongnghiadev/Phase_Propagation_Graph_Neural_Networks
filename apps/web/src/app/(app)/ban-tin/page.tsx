"use client";

/**
 * Màn hình **Bản tin**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/bulletins` — danh sách bản tin đã parse (lọc theo ngày, loại).
 * - `GET /api/v1/bulletins/{source_file}` — chi tiết bản tin (JSON đã parse).
 * - `GET /api/v1/bulletins/{source_file}/pdf` — PDF gốc (cần quyền `EXPORT` trên
 *   collection `hydromet_bulletin_files`).
 * - `GET /api/v1/bulletins/{source_file}/files` — metadata PDF (SHA-256, kích thước).
 * - `POST /api/v1/bulletins/reload` — nạp lại nguyên tử (cần quyền `RUN_JOB`).
 *
 * Bố cục: cột trái = số liệu đã parse, cột phải = PDF gốc trong `<iframe>`.
 */

import React, { useEffect, useMemo, useState } from "react";

import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  type Column,
} from "@/components/ui";
import { api, apiFetchBlob, toError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { Bulletin } from "@/lib/types";

/** Số dòng đã parse — trường `parse.rows` (mảng object) của bản tin. */
interface BulletinRow {
  [k: string]: unknown;
}

export default function BulletinsPage() {
  const { canRunJob, canExport, canRead } = usePermission();
  const [date, setDate] = useState("");
  const [type, setType] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfMeta, setPdfMeta] = useState<{ sha256: string; size_bytes: number } | null>(null);
  const [pdfError, setPdfError] = useState<Error | null>(null);
  const [reloadMsg, setReloadMsg] = useState<string | null>(null);
  const [reloadErr, setReloadErr] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  const list = useAsync<{ items: Bulletin[]; total: number }>(
    () =>
      api.bulletins({
        date: date || undefined,
        type: type || undefined,
        limit: 300,
      }),
    [date, type],
  );

  const detail = useAsync<Bulletin>(
    () => (selected ? api.bulletin(selected) : Promise.resolve({} as never)),
    [selected],
  );

  // Tải PDF gốc (cần EXPORT) → tạo object URL cho iframe.
  useEffect(() => {
    let url: string | null = null;
    setPdfUrl(null);
    setPdfMeta(null);
    setPdfError(null);
    if (!selected || !canExport(COLLECTIONS.bulletinFiles)) return;
    let cancelled = false;
    apiFetchBlob(`/bulletins/${encodePathForUrl(selected)}/pdf`)
      .then(({ blob, sha256 }) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setPdfUrl(url);
        setPdfMeta({ sha256: sha256 ?? "", size_bytes: blob.size });
      })
      .catch((err) => {
        if (!cancelled) setPdfError(toError(err));
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [selected, canExport]);

  async function reload() {
    setBusy(true);
    setReloadErr(null);
    setReloadMsg(null);
    try {
      const res = await api.reloadBulletins();
      setReloadMsg(
        `Đã yêu cầu nạp lại bản tin. ${
          typeof res?.loaded === "number" ? `${res.loaded} bản tin.` : ""
        }${typeof res?.errors === "object" && res?.errors ? ` Có lỗi: ${JSON.stringify(res.errors).slice(0, 300)}` : ""}`,
      );
      list.reload();
    } catch (err) {
      setReloadErr(toError(err));
    } finally {
      setBusy(false);
    }
  }

  const rows = useMemo<BulletinRow[]>(() => {
    const raw = detail.data?.parse?.rows;
    return Array.isArray(raw) ? (raw as BulletinRow[]) : [];
  }, [detail.data]);

  const warnings = detail.data?.parse?.warnings ?? [];

  const columns: Column<Bulletin>[] = [
    { key: "source_file", header: "Tệp nguồn" },
    {
      key: "bulletin_date",
      header: "Ngày",
      render: (r) => formatDateTime(r.bulletin_date),
    },
    { key: "bulletin_type", header: "Loại", render: (r) => r.bulletin_type ?? "—" },
    { key: "bulletin_no", header: "Số", render: (r) => r.bulletin_no ?? "—" },
    {
      key: "source",
      header: "Bản chính",
      render: (r) =>
        r.source?.is_primary ? <Badge color="#2e7d32">Chính</Badge> : <Badge color="#78909c">Phụ</Badge>,
    },
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
        <Button variant="primary" onClick={() => setSelected(r.source_file)}>
          Xem
        </Button>
      ),
    },
  ];

  return (
    <div>
      <h1>Bản tin</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Số liệu đã parse từ bản tin PDF (nguồn: khai thác bản tin thủy văn) đặt cạnh bản
        gốc để đối chiếu. Số cảnh báo khi parse cho biết bản tin có cần rà soát QC hay không.
      </p>

      {list.error && <ErrorNotice error={list.error} onRetry={list.reload} />}
      {reloadErr && <ErrorNotice error={reloadErr} onDismiss={() => setReloadErr(null)} />}
      {reloadMsg && <InfoNotice kind="success">{reloadMsg}</InfoNotice>}

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Ngày</div>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                style={sel}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Loại bản tin</div>
              <input
                value={type}
                onChange={(e) => setType(e.target.value)}
                placeholder="ví dụ: muc_du_lieu"
                style={{ ...sel, minWidth: 160 }}
              />
            </div>
          </div>
          {canRunJob(COLLECTIONS.bulletins) && (
            <Button variant="primary" disabled={busy} onClick={() => void reload()}>
              {busy ? "Đang nạp lại…" : "Nạp lại bản tin"}
            </Button>
          )}
        </div>

        <div style={{ marginTop: 12 }}>
          <DataTable
            columns={columns}
            rows={list.data?.items ?? []}
            rowKey={(r) => r.source_file}
            loading={list.loading}
            empty="Chưa có bản tin nào."
            maxHeight={300}
          />
        </div>
      </section>

      {selected && (
        <section
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
          }}
        >
          <div
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}
          >
            <h2 style={{ margin: 0, wordBreak: "break-all" }}>{selected}</h2>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {canExport(COLLECTIONS.bulletinFiles) && pdfUrl && (
                <a className="btn" href={pdfUrl} download={selected.split("/").pop()}>
                  Tải PDF
                </a>
              )}
              <Button onClick={() => setSelected(null)}>Đóng</Button>
            </div>
          </div>

          {detail.error && <ErrorNotice error={detail.error} onRetry={detail.reload} />}

          {warnings.length > 0 && (
            <InfoNotice kind="warn">
              <strong>Parse gặp {warnings.length} cảnh báo:</strong>
              <ul style={{ margin: "6px 0 0 18px", padding: 0 }}>
                {warnings.slice(0, 10).map((w, i) => (
                  <li key={i}>{String(w)}</li>
                ))}
              </ul>
            </InfoNotice>
          )}

          {pdfMeta?.sha256 && (
            <div style={{ fontSize: 11, color: "#78909c", marginTop: 6, wordBreak: "break-all" }}>
              SHA-256: {pdfMeta.sha256} · {Math.round(pdfMeta.size_bytes / 1024)} KB
            </div>
          )}

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 14,
              marginTop: 12,
            }}
          >
            <div>
              <h3 style={{ marginTop: 0 }}>Số liệu đã parse ({rows.length} dòng)</h3>
              <div style={{ overflow: "auto", maxHeight: 520, border: "1px solid var(--line)" }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      {rows.length > 0 &&
                        Object.keys(rows[0]).map((k) => <th key={k}>{k}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i}>
                        {Object.entries(r).map(([k, v]) => (
                          <td key={k}>
                            {typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length === 0 && !detail.loading && (
                  <div style={{ padding: 12, color: "#607d8b", fontSize: 13 }}>
                    Không có dòng dữ liệu đã parse (trường <code>parse.rows</code>).
                  </div>
                )}
              </div>
            </div>

            <div>
              <h3 style={{ marginTop: 0 }}>Bản gốc (PDF)</h3>
              {!canExport(COLLECTIONS.bulletinFiles) ? (
                <InfoNotice kind="warn">
                  Tài khoản của bạn không có quyền <code>EXPORT</code> trên{" "}
                  <code>hydromet_bulletin_files</code> nên không tải được PDF gốc.
                </InfoNotice>
              ) : (
                <>
                  {pdfError && <ErrorNotice error={pdfError} />}
                  {pdfUrl ? (
                    <iframe
                      src={pdfUrl}
                      title={selected}
                      style={{ width: "100%", height: 520, border: "1px solid var(--line)" }}
                    />
                  ) : (
                    <div
                      style={{
                        height: 520,
                        border: "1px dashed var(--line)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: "#607d8b",
                        fontSize: 13,
                      }}
                    >
                      {pdfError ? "Không tải được PDF" : "Đang tải PDF…"}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {!canRead(COLLECTIONS.bulletins) && (
        <InfoNotice kind="warn">Bạn không có quyền đọc bản tin.</InfoNotice>
      )}
    </div>
  );
}

/** Mã hoá `source_file` (có dấu `/`) — backend dùng đường dẫn kiểu `:path`. */
function encodePathForUrl(sourceFile: string): string {
  return sourceFile
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
}

const sel: React.CSSProperties = {
  padding: 6,
  borderRadius: 4,
  border: "1px solid #cfd8dc",
  fontSize: 13,
  background: "#fff",
};
