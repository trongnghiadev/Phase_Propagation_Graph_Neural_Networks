"use client";

/**
 * Màn hình **So sánh với KTTV**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/forecasts/compare` — các lần phát hành dự báo, gom theo trạm.
 * - `GET /api/v1/stations` — danh mục trạm.
 * - `GET /api/v1/stations/{code}/tides` — thực đo để tính sai số.
 * - `GET /api/v1/stats/tide` — thống kê đỉnh triều và số ngày vượt cấp báo động.
 *
 * API **không** có endpoint báo cáo chỉ số so sánh riêng, nên sai số được tính ở phía
 * trình duyệt theo quy ước đề cương 4.5:
 *   ΔH = |peak_mô_hình − peak_thực_đo|, Δt = |giờ đỉnh mô hình − giờ đỉnh thực đo|,
 * ghép đỉnh thực đo trong cửa sổ ±6 giờ quanh đỉnh dự báo.
 */

import React, { useMemo, useState } from "react";

import {
  BarChart,
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  LineChart,
  type Column,
  type Series,
} from "@/components/ui";
import { api } from "@/lib/api";
import { fmtNum, formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import type { Station, TideItem, TideStats } from "@/lib/types";

interface Row {
  station: string;
  issued_at: string;
  peak_ts: string;
  peak_value: number | null;
  model_version: string;
  source: string;
  obs_peak: number | null;
  obs_ts: string | null;
  dh: number | null;
  dt: number | null;
}

export default function CompareKttvPage() {
  const [station, setStation] = useState<string>("");
  const [view, setView] = useState<"lead" | "station" | "period">("station");
  const [year, setYear] = useState<string>("");

  const stations = useAsync<{ items: Station[] }>(() => api.stations({ limit: 1000 }), []);
  const compare = useAsync<{
    source: string;
    groups: Record<string, Array<Record<string, unknown>>>;
    note?: string;
    file?: string;
  }>(() => api.forecastsCompare(), []);
  const tides = useAsync<{ items: TideItem[] }>(
    () =>
      station
        ? api.stationTides(station, { limit: 4000 })
        : Promise.resolve({ items: [] as TideItem[] }),
    [station],
  );
  const stats = useAsync<TideStats>(
    () =>
      api.tideStats({
        station: station || undefined,
        year: year ? Number(year) : undefined,
        kind: "high",
      }),
    [station, year],
  );

  /** Các dòng so sánh: dự báo + thực đo tương ứng (đỉnh cao nhất trong ±6 giờ). */
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    const groups = compare.data?.groups ?? {};
    Object.entries(groups).forEach(([code, list]) => {
      (Array.isArray(list) ? list : []).forEach((g) => {
        const peakTs = String(g.peak_ts ?? "");
        const peakValue = (g.peak_value as number | null) ?? null;
        // Ghép với đỉnh thực đo gần nhất trong ±6 giờ.
        let obs: TideItem | undefined;
        if (station && station === code && tides.data && peakTs) {
          const t0 = new Date(peakTs).getTime();
          let best = Infinity;
          tides.data.items
            .filter((x) => x.kind === "high" && x.value !== null && x.ts)
            .forEach((x) => {
              const dt = Math.abs(new Date(x.ts as string).getTime() - t0);
              if (dt <= 6 * 3600_000 && dt < best) {
                best = dt;
                obs = x;
              }
            });
        }
        out.push({
          station: code,
          issued_at: String(g.issued_at ?? ""),
          peak_ts: peakTs,
          peak_value: peakValue,
          model_version: String(g.model_version ?? "—"),
          source: String(g.source ?? compare.data?.source ?? "model"),
          obs_peak: obs ? obs.value : null,
          obs_ts: obs ? obs.ts : null,
          dh:
            peakValue !== null && obs && obs.value !== null
              ? Math.abs(peakValue - obs.value)
              : null,
          dt:
            obs && obs.ts && peakTs
              ? Math.abs(new Date(peakTs).getTime() - new Date(obs.ts).getTime()) / 3600_000
              : null,
        });
      });
    });
    return out;
  }, [compare.data, tides.data, station]);

  /** Sai số trung bình theo trạm. */
  const byStation = useMemo(() => {
    const acc: Record<string, { n: number; sum: number; sumDt: number; nDt: number }> = {};
    rows.forEach((r) => {
      if (r.dh === null) return;
      const a = (acc[r.station] ??= { n: 0, sum: 0, sumDt: 0, nDt: 0 });
      a.n += 1;
      a.sum += r.dh;
      if (r.dt !== null) {
        a.nDt += 1;
        a.sumDt += r.dt;
      }
    });
    return Object.entries(acc).map(([code, a]) => ({
      label: code,
      value: a.n ? a.sum / a.n : null,
      dt: a.nDt ? a.sumDt / a.nDt : null,
      n: a.n,
    }));
  }, [rows]);

  /** Sai số theo chân trời (số ngày giữa ngày phát hành và ngày đỉnh). */
  const byLead = useMemo(() => {
    const acc: Record<number, { n: number; sum: number }> = {};
    rows.forEach((r) => {
      if (r.dh === null || !r.issued_at || !r.peak_ts) return;
      const lead = Math.round(
        (new Date(r.peak_ts).getTime() - new Date(r.issued_at).getTime()) / 86400_000,
      );
      if (lead < 0 || lead > 10) return;
      const a = (acc[lead] ??= { n: 0, sum: 0 });
      a.n += 1;
      a.sum += r.dh;
    });
    return Object.entries(acc)
      .map(([k, a]) => ({ label: `${k} ngày`, value: a.n ? a.sum / a.n : null, n: a.n }))
      .sort((x, y) => Number(x.label.split(" ")[0]) - Number(y.label.split(" ")[0]));
  }, [rows]);

  /** Sai số theo thời kỳ (quý trong năm). */
  const byPeriod = useMemo(() => {
    const acc: Record<string, { n: number; sum: number }> = {};
    rows.forEach((r) => {
      if (r.dh === null || !r.peak_ts) return;
      const d = new Date(r.peak_ts);
      const q = Math.floor(d.getMonth() / 3) + 1;
      const key = `Q${q}/${d.getFullYear()}`;
      const a = (acc[key] ??= { n: 0, sum: 0 });
      a.n += 1;
      a.sum += r.dh;
    });
    return Object.entries(acc)
      .map(([k, a]) => ({ label: k, value: a.n ? a.sum / a.n : null, n: a.n }))
      .sort((x, y) => x.label.localeCompare(y.label));
  }, [rows]);

  const series = useMemo<Series[]>(() => {
    const byDay = new Map<string, { model: number | null; obs: number | null }>();
    rows.forEach((r) => {
      if (!r.peak_ts) return;
      const day = r.peak_ts.slice(0, 10);
      const cur = byDay.get(day) ?? { model: null, obs: null };
      cur.model = r.peak_value;
      cur.obs = r.obs_peak;
      byDay.set(day, cur);
    });
    const days = [...byDay.keys()].sort();
    return [
      {
        name: "Dự báo mô hình",
        color: "#c62828",
        points: days.map((d) => ({ x: d, y: byDay.get(d)!.model })),
      },
      {
        name: "Thực đo (đỉnh)",
        color: "#1565c0",
        points: days.map((d) => ({ x: d, y: byDay.get(d)!.obs })),
      },
    ];
  }, [rows]);

  const columns: Column<Row>[] = [
    { key: "station", header: "Trạm", render: (r) => <strong>{r.station}</strong> },
    { key: "issued_at", header: "Phát hành", render: (r) => formatDateTime(r.issued_at) },
    { key: "peak_ts", header: "Đỉnh dự báo", render: (r) => formatDateTime(r.peak_ts) },
    {
      key: "peak_value",
      header: "H dự báo (m)",
      align: "right",
      render: (r) => fmtNum(r.peak_value),
    },
    {
      key: "obs_peak",
      header: "H thực đo (m)",
      align: "right",
      render: (r) => fmtNum(r.obs_peak),
    },
    {
      key: "dh",
      header: "ΔH (m)",
      align: "right",
      render: (r) =>
        r.dh === null ? (
          "—"
        ) : (
          <Badge color={r.dh <= 0.2 ? "#2e7d32" : r.dh <= 0.5 ? "#f9a825" : "#c62828"}>
            {r.dh.toFixed(3)}
          </Badge>
        ),
    },
    {
      key: "dt",
      header: "Δt (giờ)",
      align: "right",
      render: (r) => (r.dt === null ? "—" : r.dt.toFixed(2)),
    },
    { key: "model_version", header: "Phiên bản" },
    { key: "source", header: "Nguồn" },
  ];

  const withObs = rows.filter((r) => r.dh !== null);

  return (
    <div>
      <h1>So sánh với dự báo KTTV</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Sai số ΔH = |đỉnh dự báo − đỉnh thực đo|, ghép đỉnh trong ±6 giờ (quy ước đề
        cương 4.5). Chọn một trạm để ghép được thực đo.
      </p>

      {compare.error && <ErrorNotice error={compare.error} onRetry={compare.reload} />}
      {compare.data?.note && <InfoNotice kind="warn">{compare.data.note}</InfoNotice>}

      <div
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
          display: "flex",
          gap: 16,
          flexWrap: "wrap",
          alignItems: "flex-end",
        }}
      >
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
            Trạm (cần chọn để ghép thực đo)
          </div>
          <select
            value={station}
            onChange={(e) => setStation(e.target.value)}
            style={{ padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13, minWidth: 220 }}
          >
            <option value="">— Chưa chọn —</option>
            {(stations.data?.items ?? []).map((s) => (
              <option key={s.code} value={s.code}>
                {s.code} — {s.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Năm thống kê</div>
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            placeholder="VD: 2025"
            style={{ padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13, width: 100 }}
          />
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Cách xem</div>
          <div style={{ display: "flex", gap: 6 }}>
            {(
              [
                ["station", "Theo trạm"],
                ["lead", "Theo chân trời"],
                ["period", "Theo thời kỳ"],
              ] as const
            ).map(([k, label]) => (
              <Button key={k} variant={view === k ? "primary" : "default"} onClick={() => setView(k)}>
                {label}
              </Button>
            ))}
          </div>
        </div>
        <div style={{ fontSize: 12, color: "#607d8b" }}>
          {withObs.length} / {rows.length} dòng đã ghép được thực đo
        </div>
      </div>

      {tides.error && <ErrorNotice error={tides.error} onRetry={tides.reload} />}

      {station && (
        <section
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
            marginBottom: 16,
          }}
        >
          <h2>Dự báo và thực đo — {station}</h2>
          <LineChart series={series} height={300} yLabel="m" />
        </section>
      )}

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <h2>
          {view === "station"
            ? "Sai số trung bình theo trạm"
            : view === "lead"
              ? "Sai số trung bình theo chân trời"
              : "Sai số trung bình theo thời kỳ"}
        </h2>
        <BarChart
          data={
            view === "station"
              ? byStation.map((r) => ({ label: r.label, value: r.value }))
              : view === "lead"
                ? byLead.map((r) => ({ label: r.label, value: r.value }))
                : byPeriod.map((r) => ({ label: r.label, value: r.value }))
          }
          yLabel="MAE ΔH (m)"
          color="#1565c0"
        />
        {view === "station" && byStation.length > 0 && (
          <div style={{ fontSize: 12, color: "#607d8b", marginTop: 6 }}>
            Δt trung bình:{" "}
            {byStation
              .map((r) => `${r.label}: ${r.dt !== null ? `${r.dt.toFixed(2)} h` : "—"}`)
              .join(" · ")}
          </div>
        )}
      </section>

      {stats.data && (
        <section
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
            marginBottom: 16,
          }}
        >
          <h2>
            Thống kê đỉnh triều{" "}
            {stats.data.year ? `(${stats.data.year})` : "(tất cả năm)"}
          </h2>
          <DataTable
            columns={[
              { key: "station", header: "Trạm", render: (r) => <strong>{r.station ?? "—"}</strong> },
              { key: "n", header: "Số đỉnh", align: "right" },
              {
                key: "median",
                header: "Trung vị (m)",
                align: "right",
                render: (r) => fmtNum(r.median),
              },
              { key: "max", header: "Max (m)", align: "right", render: (r) => fmtNum(r.max) },
              { key: "max_ts", header: "Đỉnh lớn nhất", render: (r) => formatDateTime(r.max_ts) },
              {
                key: "level_1",
                header: "Vượt BĐ I",
                align: "right",
                render: (r) => r.exceed?.level_1 ?? 0,
              },
              {
                key: "level_2",
                header: "Vượt BĐ II",
                align: "right",
                render: (r) => r.exceed?.level_2 ?? 0,
              },
              {
                key: "level_3",
                header: "Vượt BĐ III",
                align: "right",
                render: (r) => r.exceed?.level_3 ?? 0,
              },
            ]}
            rows={stats.data.stations}
            rowKey={(r) => r.station ?? ""}
            loading={stats.loading}
            empty="Chưa có thống kê đỉnh triều."
            maxHeight={300}
          />
        </section>
      )}

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
        }}
      >
        <h2>Bảng so sánh chi tiết</h2>
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r, i) => `${r.station}-${r.issued_at}-${i}`}
          loading={compare.loading}
          empty="Chưa có dự báo nào để so sánh."
          maxHeight={460}
        />
      </section>
    </div>
  );
}
