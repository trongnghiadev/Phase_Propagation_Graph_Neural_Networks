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
 * - `GET /api/v1/jobs?type=train` — theo dõi tiến trình huấn luyện.
 *
 * Lưu ý: API **không** có endpoint liệt kê mô hình (`GET /models`). Danh sách phiên bản
 * ở đây được suy ra từ các job `train` đã giao — nếu chưa có job nào thì danh sách rỗng.
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
import { api, toError } from "@/lib/api";
import { formatActor, formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { JobItem } from "@/lib/types";

export default function ModelsPage() {
  const { canManageModel, canRunForecast } = usePermission();
  const [version, setVersion] = useState("");
  const [epochs, setEpochs] = useState("50");
  const [lr, setLr] = useState("0.001");
  const [station, setStation] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [promoting, setPromoting] = useState<string | null>(null);

  /** Các job huấn luyện đã giao — suy ra danh sách phiên bản mô hình. */
  const trainJobs = useAsync<{ items: JobItem[] }>(() => api.jobs({ type: "train", limit: 200 }), []);

  /** Các job dự báo để tham chiếu phiên bản mô hình đang phục vụ. */
  const forecastJobs = useAsync<{ items: JobItem[] }>(
    () => api.jobs({ type: "forecast_run", limit: 50 }),
    [],
  );

  /** Phiên bản mô hình đã biết (từ job train) kèm trạng thái. */
  const modelVersions = useMemo(() => {
    const map = new Map<string, { version: string; state: string; created: string | null }>();
    (trainJobs.data?.items ?? []).forEach((j) => {
      const v = j.model_version ?? (j.params?.model_version as string | undefined);
      if (!v) return;
      map.set(v, {
        version: v,
        state: j.state ?? "unknown",
        created: j.created_at ?? null,
      });
    });
    return [...map.values()].sort((a, b) => (b.created ?? "").localeCompare(a.created ?? ""));
  }, [trainJobs.data]);

  /** Phiên bản đang được dùng để dự báo (job dự báo gần nhất). */
  const activeVersion = useMemo(() => {
    const jobs = forecastJobs.data?.items ?? [];
    for (const j of jobs) {
      const v = j.params?.model_version as string | undefined;
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
        model_version: version.trim() || undefined,
        epochs: Number(epochs) || 50,
        learning_rate: Number(lr) || 0.001,
        station: station.trim() || undefined,
      });
      setOk(
        `Đã giao huấn luyện phiên bản ${res.model_version} (job ${res.job_id}, trạng thái ${res.state}). ${
          res.note ?? ""
        }`,
      );
      trainJobs.reload();
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
      trainJobs.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setPromoting(null);
    }
  }

  const versionColumns: Column<{ version: string; state: string; created: string | null }>[] = [
    { key: "version", header: "Phiên bản", render: (m) => <code>{m.version}</code> },
    {
      key: "state",
      header: "Trạng thái",
      render: (m) => (
        <Badge
          color={
            m.state === "done"
              ? "#2e7d32"
              : m.state === "failed"
                ? "#c62828"
                : m.state === "running"
                  ? "#1565c0"
                  : "#78909c"
          }
        >
          {m.state}
        </Badge>
      ),
    },
    { key: "created", header: "Tạo lúc", render: (m) => formatDateTime(m.created) },
    {
      key: "action",
      header: "",
      render: (m) =>
        canManageModel(COLLECTIONS.models) ? (
          <Button
            variant="primary"
            disabled={promoting === m.version}
            onClick={() => void promote(m.version)}
          >
            {promoting === m.version ? "Đang phát hành…" : "Phát hành (promote)"}
          </Button>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>Chỉ đọc</span>
        ),
    },
  ];

  const jobColumns: Column<JobItem>[] = [
    { key: "id", header: "Job", render: (j) => <code style={{ fontSize: 11 }}>{j.id}</code> },
    { key: "state", header: "Trạng thái", render: (j) => <JobStateBadge state={j.state} /> },
    {
      key: "model_version",
      header: "Phiên bản",
      render: (j) => j.model_version ?? (j.params?.model_version as string) ?? "—",
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
              <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 3 }}>
                Phiên bản (để trống = tự sinh)
              </label>
              <input
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="ví dụ: v20261124"
                style={{ ...inputStyle, marginBottom: 10 }}
              />

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

              <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 3 }}>
                Trạm (tuỳ chọn)
              </label>
              <input
                value={station}
                onChange={(e) => setStation(e.target.value)}
                placeholder="ví dụ: VUONGTAU"
                style={{ ...inputStyle, marginBottom: 12 }}
              />

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
          <InfoNotice kind="info">
            API chưa có endpoint liệt kê mô hình; danh sách này suy ra từ các job huấn
            luyện đã giao.
          </InfoNotice>
          <DataTable
            columns={versionColumns}
            rows={modelVersions}
            rowKey={(m) => m.version}
            loading={trainJobs.loading}
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
