"use client";

/**
 * Màn hình **Mô hình**.
 *
 * Endpoint dùng:
 * - `POST /api/v1/models/train` — yêu cầu huấn luyện (cần quyền `MANAGE_MODEL`).
 *   API chỉ giao job cho `apps/worker` vì `apps/api` không được import `phasegnn`.
 * - `POST /api/v1/models/{version}/promote` — chuyển phiên bản thành bản đang phục vụ.
 *   Bản đang promote trước đó chuyển sang `archived`; cache `ver:models` và
 *   `ver:forecasts` được tăng để dự báo cũ mất hiệu lực.
 * - `GET /api/v1/models` — danh sách phiên bản (registry `models`) kèm `train_state`/`train_reason`,
 *   `promotable` (đã huấn luyện xong, chưa phục vụ) và `serving` (bản production đang phục vụ).
 * - `GET /api/v1/jobs?type=train` — theo dõi tiến trình huấn luyện.
 *
 * Nút promote chỉ bật cho bản `train_state=succeeded` (hoặc bản ghi cũ không có `train_state`) và
 * chưa phục vụ. Lỗi promote: 409 (`promote_superseded` — lượt promote khác mới hơn đã thắng; hoặc bản
 * chưa huấn luyện xong) và 423 (đang có lượt promote khác) được báo rõ, **không tự thử lại**.
 *
 * Phiên bản là **số nguyên** do API cấp nguyên tử (registry `models.version`, `status`
 * `staging|production|archived`); người dùng không tự đặt số.
 */

import React, { useMemo, useState } from "react";

import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  inputStyle,
  type Column,
} from "@/components/ui";
import { JobStateBadge } from "@/components/JobStateBadge";
import { api, ApiRequestError, toError } from "@/lib/api";
import { formatActor, formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { JobItem, ModelItem } from "@/lib/types";

/** Phiên bản mô hình (int của registry; job cũ có thể còn chuỗi) → chuỗi hiển thị/khóa. */
function versionKey(v: unknown): string | null {
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  if (typeof v === "string" && v.trim()) return v.trim();
  return null;
}

/** Nhãn + màu cho `models.train_state`. */
const TRAIN_STATE: Record<string, { label: string; color: string }> = {
  queued: { label: "chờ huấn luyện", color: "#78909c" },
  running: { label: "đang huấn luyện", color: "#1565c0" },
  succeeded: { label: "đã huấn luyện", color: "#2e7d32" },
  failed: { label: "lỗi", color: "#c62828" },
  skipped: { label: "bỏ qua", color: "#ef6c00" },
};

/** Nhãn + màu cho `models.status`. */
const MODEL_STATUS: Record<string, { label: string; color: string }> = {
  staging: { label: "staging", color: "#78909c" },
  production: { label: "production", color: "#2e7d32" },
  archived: { label: "archived", color: "#90a4ae" },
};

/** Vì sao bản này chưa promote được (hiện thay cho nút). */
function notPromotableReason(m: ModelItem): string {
  if (m.serving) return "Đang phục vụ";
  const ts = m.train_state;
  if (ts === "queued" || ts === "running") return "Chưa huấn luyện xong";
  if (ts === "failed") return "Huấn luyện lỗi";
  if (ts === "skipped") return "Huấn luyện bị bỏ qua";
  return "Không promote được";
}

/** Thông điệp rõ cho lỗi promote (409/423); `null` → dùng thông điệp chung. */
function promoteErrorMessage(e: unknown, version: string): string | null {
  if (!(e instanceof ApiRequestError)) return null;
  const d = e.detail && typeof e.detail === "object" ? (e.detail as Record<string, unknown>) : {};
  if (e.status === 423) {
    return (
      `Đang có lượt promote khác chạy (khoá models:promote) — phiên bản ${version} chưa được phát hành. ` +
      "Đợi lượt kia xong, tải lại danh sách rồi quyết định lại."
    );
  }
  if (e.status === 409 && d.error === "promote_superseded") {
    return (
      `Một lượt promote mới hơn đã thắng — phiên bản ${version} không được phục vụ. ` +
      "Tải lại danh sách để xem bản đang phục vụ."
    );
  }
  if (e.status === 409 && d.error === "model_not_trained") {
    const ts = typeof d.train_state === "string" ? d.train_state : "?";
    return `Phiên bản ${version} chưa huấn luyện xong (train_state=${ts}); chỉ promote được bản đã huấn luyện.`;
  }
  return null;
}

export default function ModelsPage() {
  const { canManageModel, canRunForecast } = usePermission();
  const [epochs, setEpochs] = useState("50");
  const [lr, setLr] = useState("0.001");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [promoting, setPromoting] = useState<string | null>(null);

  /** Các job huấn luyện đã giao. */
  const trainJobs = useAsync<{ items: JobItem[] }>(() => api.jobs({ type: "train", limit: 200 }), []);

  /** Phiên bản mô hình (registry `models`). */
  const models = useAsync<{ items: ModelItem[] }>(() => api.models({ limit: 200 }), []);

  /** Các job dự báo để tham chiếu phiên bản mô hình đang phục vụ. */
  const forecastJobs = useAsync<{ items: JobItem[] }>(
    () => api.jobs({ type: "forecast_run", limit: 50 }),
    [],
  );

  /** Phiên bản đang được dùng để dự báo (job dự báo gần nhất). */
  const activeVersion = useMemo(() => {
    const jobs = forecastJobs.data?.items ?? [];
    for (const j of jobs) {
      const v = versionKey(j.params?.model_version);
      if (v) return v;
    }
    return null;
  }, [forecastJobs.data]);

  async function train() {
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      const res = await api.trainModel({
        epochs: Number(epochs) || 50,
        learning_rate: Number(lr) || 0.001,
        // Không gửi `station`: trình huấn luyện chưa hỗ trợ (worker bỏ qua, ghi `params_ignored`).
      });
      const job = res.job_id ? `job ${res.job_id}, ` : "";
      setOk(
        res.created === false
          ? `Phiên bản ${res.model_version} đã có (${job}trạng thái ${res.state}). ${res.note ?? ""}`
          : `Đã giao huấn luyện phiên bản ${res.model_version} (${job}trạng thái ${res.state}). ${res.note ?? ""}`,
      );
      trainJobs.reload();
      models.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  async function promote(v: string) {
    setPromoting(v);
    setErr(null);
    setOk(null);
    try {
      const res = await api.promoteModel(v);
      setOk(
        `Đã phát hành mô hình ${res.model_version} (${formatDateTime(res.promoted_at)}, ${formatActor(
          res.promoted_by,
          res.promoted_by_label,
        )}).`,
      );
    } catch (e) {
      // Không tự thử lại: 409/423 cần người dùng xem lại danh sách rồi quyết định.
      const msg = promoteErrorMessage(e, v);
      setErr(msg ? new Error(msg) : toError(e));
    } finally {
      setPromoting(null);
      models.reload();
    }
  }

  const versionColumns: Column<ModelItem>[] = [
    {
      key: "version",
      header: "Phiên bản",
      render: (m) => (
        <>
          <code>{m.version}</code>
          {m.serving && (
            <span style={{ marginLeft: 6 }}>
              <Badge color="#2e7d32">đang phục vụ</Badge>
            </span>
          )}
        </>
      ),
    },
    {
      key: "status",
      header: "Registry",
      render: (m) => {
        const st = MODEL_STATUS[m.status ?? ""] ?? { label: m.status ?? "—", color: "#78909c" };
        return <Badge color={st.color}>{st.label}</Badge>;
      },
    },
    {
      key: "train_state",
      header: "Huấn luyện",
      render: (m) => {
        if (!m.train_state) return <span style={{ color: "#90a4ae" }}>—</span>;
        const ts = TRAIN_STATE[m.train_state] ?? { label: m.train_state, color: "#78909c" };
        const why = m.train_reason || m.train_error;
        return (
          <div>
            <Badge color={ts.color}>{ts.label}</Badge>
            {why && (
              <div style={{ fontSize: 11, color: "#607d8b", marginTop: 3, maxWidth: 260 }} title={why}>
                {why.length > 120 ? `${why.slice(0, 120)}…` : why}
              </div>
            )}
          </div>
        );
      },
    },
    {
      key: "metrics",
      header: "MAE đỉnh (val)",
      render: (m) => {
        const v = m.metrics?.val_mae_peak;
        return typeof v === "number" ? `${v.toFixed(3)} m` : "—";
      },
    },
    { key: "created", header: "Tạo lúc", render: (m) => formatDateTime(m.created_at ?? null) },
    {
      key: "action",
      header: "",
      render: (m) => {
        const key = String(m.version);
        if (!canManageModel(COLLECTIONS.models)) {
          return <span style={{ fontSize: 12, color: "#90a4ae" }}>Chỉ đọc</span>;
        }
        if (!m.promotable) {
          return <span style={{ fontSize: 12, color: "#90a4ae" }}>{notPromotableReason(m)}</span>;
        }
        return (
          <Button variant="primary" disabled={promoting !== null} onClick={() => void promote(key)}>
            {promoting === key ? "Đang phát hành…" : "Phát hành (promote)"}
          </Button>
        );
      },
    },
  ];

  const jobColumns: Column<JobItem>[] = [
    { key: "id", header: "Job", render: (j) => <code style={{ fontSize: 11 }}>{j.id}</code> },
    { key: "state", header: "Trạng thái", render: (j) => <JobStateBadge state={j.state} /> },
    {
      key: "model_version",
      header: "Phiên bản",
      render: (j) => versionKey(j.model_version) ?? versionKey(j.params?.model_version) ?? "—",
    },
    { key: "requested_by", header: "Người giao", render: (j) => formatActor(j.requested_by, j.requested_by_label) },
    { key: "worker", header: "Worker", render: (j) => j.worker ?? "—" },
    { key: "created_at", header: "Tạo lúc", render: (j) => formatDateTime(j.created_at) },
    {
      key: "heartbeat",
      header: "Heartbeat",
      render: (j) => (j.heartbeat ? formatDateTime(j.heartbeat) : "—"),
    },
  ];

  return (
    <div>
      <h1>Mô hình</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Huấn luyện và phát hành phiên bản PhaseGNN. Theo luật phụ thuộc,{" "}
        <code>apps/api</code> không import <code>phasegnn</code> nên lệnh huấn luyện được giao
        cho <code>apps/worker</code> thực thi.
      </p>

      {trainJobs.error && <ErrorNotice error={trainJobs.error} onRetry={trainJobs.reload} />}
      {models.error && <ErrorNotice error={models.error} onRetry={models.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}

      {activeVersion && (
        <InfoNotice kind="info">
          Phiên bản mô hình đang được dùng cho dự báo gần nhất:{" "}
          <code>{activeVersion}</code>
        </InfoNotice>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 2fr",
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
          <h2 style={{ marginTop: 0 }}>Huấn luyện mô hình</h2>
          {!canManageModel(COLLECTIONS.models) ? (
            <InfoNotice kind="warn">
              Bạn cần quyền <code>MANAGE_MODEL</code> trên collection <code>models</code>.
            </InfoNotice>
          ) : (
            <>
              <p style={{ fontSize: 12, color: "#607d8b", marginTop: 0 }}>
                Phiên bản do hệ thống cấp (số nguyên tăng dần), bản ghi mới ở trạng thái <code>staging</code>.
              </p>

              <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 3 }}>
                Số epoch
              </label>
              <input
                type="number"
                value={epochs}
                onChange={(e) => setEpochs(e.target.value)}
                style={{ ...inputStyle, marginBottom: 10 }}
              />

              <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 3 }}>
                Learning rate
              </label>
              <input
                type="number"
                step="0.0001"
                value={lr}
                onChange={(e) => setLr(e.target.value)}
                style={{ ...inputStyle, marginBottom: 10 }}
              />

              <p style={{ fontSize: 12, color: "#607d8b", marginTop: 0, marginBottom: 12 }}>
                Huấn luyện trên toàn bộ các trạm (chưa hỗ trợ chọn riêng một trạm).
              </p>

              <Button variant="primary" disabled={busy} onClick={() => void train()}>
                {busy ? "Đang giao…" : "Giao huấn luyện"}
              </Button>
            </>
          )}
        </section>

        <section
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
          }}
        >
          <h2 style={{ marginTop: 0 }}>Phiên bản mô hình</h2>
          <p style={{ fontSize: 12, color: "#607d8b", marginTop: 0 }}>
            Chỉ phát hành được bản đã huấn luyện xong (<code>train_state=succeeded</code>).{" "}
            <Button onClick={() => models.reload()}>Tải lại</Button>
          </p>
          <DataTable
            columns={versionColumns}
            rows={models.data?.items ?? []}
            rowKey={(m) => m.id}
            loading={models.loading}
            empty="Chưa có phiên bản nào được huấn luyện."
            maxHeight={260}
          />

          <h2 style={{ marginTop: 18 }}>Chạy dự báo</h2>
          {!canRunForecast(COLLECTIONS.forecasts) ? (
            <InfoNotice kind="warn">
              Bạn cần quyền <code>RUN_FORECAST</code> trên collection <code>forecasts</code>.
            </InfoNotice>
          ) : (
            <RunForecastBox />
          )}
        </section>
      </div>

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Job huấn luyện</h2>
        <DataTable
          columns={jobColumns}
          rows={trainJobs.data?.items ?? []}
          rowKey={(j) => j.id ?? ""}
          loading={trainJobs.loading}
          empty="Chưa có job huấn luyện nào."
          maxHeight={360}
        />
      </section>
    </div>
  );
}

/** Hộp chạy dự báo nhanh. */
function RunForecastBox() {
  const [station, setStation] = useState("");
  const [horizon, setHorizon] = useState("72");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      const res = await api.runForecast({
        station: station.trim() || undefined,
        horizon_h: Number(horizon) || 72,
      });
      setOk(
        `Đã giao dự báo (job ${res.job_id}, trạng thái ${res.state}).${
          res.note ? ` ${res.note}` : ""
        }`,
      );
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Trạm</div>
          <input
            value={station}
            onChange={(e) => setStation(e.target.value)}
            placeholder="để trống = toàn lưu vực"
            style={{ ...inputStyle, width: 180 }}
          />
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Chân trời (giờ)</div>
          <input
            type="number"
            value={horizon}
            onChange={(e) => setHorizon(e.target.value)}
            style={{ ...inputStyle, width: 110 }}
          />
        </div>
        <Button variant="primary" disabled={busy} onClick={() => void run()}>
          {busy ? "Đang giao…" : "Chạy dự báo"}
        </Button>
      </div>
    </>
  );
}
