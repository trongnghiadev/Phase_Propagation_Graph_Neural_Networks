"use client";

/**
 * Màn hình **Tác vụ nền (Jobs)**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/jobs?state=&type=` — danh sách tác vụ.
 * - `GET /api/v1/jobs/{id}` — chi tiết một tác vụ (heartbeat, fencing token).
 * - `POST /api/v1/jobs/{job_type}` — giao tác vụ mới cho worker (`apps/worker` thực thi,
 *   khoá phân tán bảo đảm mỗi lượt chỉ một instance chạy).
 *
 * Loại tác vụ được API chấp nhận (whitelist trong `apps/api/src/api/router/jobs.py`):
 * `forecast_run`, `train`, `reload_bulletins`, `ingest_bulletins`, `cache_warm`,
 * `build_features`, `qc_review`.
 */

import React, { useState } from "react";

import {
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
import { COLLECTIONS } from "@/lib/permissions";
import { JobStateBadge } from "@/components/JobStateBadge";
import type { JobItem } from "@/lib/types";

/** Các loại tác vụ API cho phép giao. */
const JOB_TYPES = [
  "forecast_run",
  "train",
  "reload_bulletins",
  "ingest_bulletins",
  "cache_warm",
  "build_features",
  "qc_review",
];

const JOB_TYPE_LABELS: Record<string, string> = {
  forecast_run: "Chạy dự báo",
  train: "Huấn luyện mô hình",
  reload_bulletins: "Nạp lại bản tin",
  ingest_bulletins: "Thu thập bản tin",
  cache_warm: "Làm nóng cache",
  build_features: "Dựng đặc trưng",
  qc_review: "Rà soát QC",
};

export default function JobsPage() {
  const { canRunJob } = usePermission();
  const [state, setState] = useState("");
  const [type, setType] = useState("");
  const [detail, setDetail] = useState<JobItem | null>(null);
  const [newType, setNewType] = useState("forecast_run");
  const [params, setParams] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const jobs = useAsync<{ count: number; items: JobItem[] }>(
    () => api.jobs({ state: state || undefined, type: type || undefined, limit: 300 }),
    [state, type],
  );

  async function submit() {
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      let parsed: Record<string, unknown> = {};
      try {
        parsed = params.trim() ? (JSON.parse(params) as Record<string, unknown>) : {};
      } catch {
        throw new Error("Tham số phải là JSON hợp lệ, ví dụ: {\"station\": \"VUONGTAU\"}");
      }
      const job = await api.createJob(newType, { params: parsed, priority: 0 });
      setOk(`Đã giao tác vụ ${job.id ?? newType} (trạng thái: ${job.state ?? "queued"}).`);
      jobs.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<JobItem>[] = [
    { key: "id", header: "Mã", render: (j) => <code style={{ fontSize: 11 }}>{j.id}</code> },
    {
      key: "type",
      header: "Loại",
      render: (j) => JOB_TYPE_LABELS[j.type ?? ""] ?? j.type ?? "—",
    },
    {
      key: "state",
      header: "Trạng thái",
      render: (j) => <JobStateBadge state={j.state} />,
    },
    { key: "requested_by", header: "Người giao", render: (j) => formatActor(j.requested_by, j.requested_by_label) },
    { key: "worker", header: "Worker", render: (j) => j.worker ?? "—" },
    { key: "fence", header: "Fence", align: "right", render: (j) => j.fence ?? "—" },
    {
      key: "heartbeat",
      header: "Heartbeat",
      render: (j) => (j.heartbeat ? formatDateTime(j.heartbeat) : "—"),
    },
    { key: "created_at", header: "Tạo lúc", render: (j) => formatDateTime(j.created_at) },
    {
      key: "action",
      header: "",
      render: (j) => (
        <Button onClick={() => setDetail(j)}>Chi tiết</Button>
      ),
    },
  ];

  return (
    <div>
      <h1>Tác vụ nền</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        <code>apps/api</code> chỉ <b>giao</b> việc; <code>apps/worker</code> (APScheduler +
        khoá phân tán) mới thực thi. Mỗi tác vụ có <code>fence</code> (fencing token) và{" "}
        <code>heartbeat</code> để phát hiện worker chết.
      </p>

      {jobs.error && <ErrorNotice error={jobs.error} onRetry={jobs.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "2fr 1fr",
          gap: 16,
          marginBottom: 16,
        }}
      >
        <section
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
          }}
        >
          <div style={{ display: "flex", gap: 14, alignItems: "flex-end", marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Trạng thái</div>
              <select value={state} onChange={(e) => setState(e.target.value)} style={sel}>
                <option value="">Tất cả</option>
                <option value="queued">queued</option>
                <option value="running">running</option>
                <option value="done">done</option>
                <option value="failed">failed</option>
              </select>
            </div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Loại</div>
              <select value={type} onChange={(e) => setType(e.target.value)} style={sel}>
                <option value="">Tất cả</option>
                {JOB_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {JOB_TYPE_LABELS[t] ?? t}
                  </option>
                ))}
              </select>
            </div>
            <Button onClick={jobs.reload}>Tải lại</Button>
            <div style={{ marginLeft: "auto", fontSize: 12, color: "#607d8b" }}>
              {jobs.data?.count ?? 0} tác vụ
            </div>
          </div>

          <DataTable
            columns={columns}
            rows={jobs.data?.items ?? []}
            rowKey={(j) => j.id ?? ""}
            loading={jobs.loading}
            empty="Không có tác vụ nào."
            maxHeight={480}
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
          <h2 style={{ marginTop: 0 }}>Giao tác vụ mới</h2>
          {!canRunJob(COLLECTIONS.jobs) ? (
            <InfoNotice kind="warn">
              Bạn cần quyền <code>RUN_JOB</code> trên collection <code>jobs</code> để giao tác
              vụ.
            </InfoNotice>
          ) : (
            <>
              <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 3 }}>
                Loại tác vụ
              </label>
              <select
                value={newType}
                onChange={(e) => setNewType(e.target.value)}
                style={{ ...sel, width: "100%", marginBottom: 10 }}
              >
                {JOB_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {JOB_TYPE_LABELS[t] ?? t} ({t})
                  </option>
                ))}
              </select>

              <label
                style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 3 }}
              >
                Tham số (JSON)
              </label>
              <textarea
                value={params}
                onChange={(e) => setParams(e.target.value)}
                rows={5}
                placeholder={'{"station": "VUONGTAU", "horizon_h": 72}'}
                style={{
                  width: "100%",
                  padding: 8,
                  borderRadius: 4,
                  border: "1px solid #cfd8dc",
                  fontSize: 13,
                  fontFamily: "ui-monospace, monospace",
                }}
              />

              <div style={{ marginTop: 10 }}>
                <Button variant="primary" disabled={busy} onClick={() => void submit()}>
                  {busy ? "Đang giao…" : "Giao tác vụ"}
                </Button>
              </div>
            </>
          )}
        </section>
      </div>

      <Modal
        open={Boolean(detail)}
        title={`Tác vụ — ${detail?.id ?? ""}`}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <table style={{ fontSize: 13, width: "100%" }}>
            <tbody>
              <Row label="Mã" value={detail.id ?? "—"} />
              <Row label="Loại" value={detail.type ?? "—"} />
              <Row label="Trạng thái" value={<JobStateBadge state={detail.state} />} />
              <Row label="Người giao" value={formatActor(detail.requested_by, detail.requested_by_label)} />
              <Row label="Worker" value={detail.worker ?? "—"} />
              <Row label="Fencing token" value={detail.fence ?? "—"} />
              <Row label="Heartbeat" value={formatDateTime(detail.heartbeat)} />
              <Row label="Tạo lúc" value={formatDateTime(detail.created_at)} />
              <Row label="Bắt đầu" value={formatDateTime(detail.started_at)} />
              <Row label="Xong" value={formatDateTime(detail.finished_at)} />
              <Row
                label="Tham số"
                value={<code style={{ fontSize: 11 }}>{JSON.stringify(detail.params ?? {})}</code>}
              />
              {detail.message && <Row label="Thông báo" value={detail.message} />}
              {detail.error && <Row label="Lỗi" value={detail.error} />}
            </tbody>
          </table>
        )}
      </Modal>
    </div>
  );
}

/** Dòng bảng dạng nhãn / giá trị. */
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
