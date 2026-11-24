"use client";

/**
 * Màn hình Tổng quan (Dashboard).
 *
 * Endpoint dùng:
 * - `GET /api/v1/stats/tide` — thống kê đỉnh triều, số lần vượt từng cấp báo động
 *   (ngưỡng lấy từ `stations.alarm_history` — không dùng ngưỡng cố định).
 * - `GET /api/v1/stations` — danh mục trạm (lọc theo tỉnh cũ/mới qua `admin`).
 * - `GET /api/v1/admin-units/provinces?at=` — tỉnh hiệu lực tại ngày; API trả kèm
 *   `era` (luôn kèm `era` theo luật địa giới ADR 0003).
 * - `GET /api/v1/forecasts/latest` — dự báo mới nhất.
 * - `GET /api/v1/alerts` — cảnh báo gần đây.
 */

import React, { useMemo, useState } from "react";

import {
  BarChart,
  Badge,
  DataTable,
  ErrorNotice,
  InfoNotice,
  type Column,
} from "@/components/ui";
import { Card, Section } from "@/components/Cards";
import { api } from "@/lib/api";
import {
  EXCEED_META,
  fmtInt,
  fmtNum,
  formatDate,
  formatDateTime,
  severityLabel,
  statusLabel,
  statusColor,
  todayVN,
} from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import type { AlertItem, Province, Station, TideStats } from "@/lib/types";

const YEARS = [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017];

export default function DashboardPage() {
  const [year, setYear] = useState<number>(2026);
  const [at, setAt] = useState<string>(todayVN());
  const [provinceCode, setProvinceCode] = useState<string>("");
  const [eraFilter, setEraFilter] = useState<"all" | "before" | "from">("all");

  const stats = useAsync<TideStats>(() => api.tideStats({ year }), [year]);
  const provinces = useAsync<{ at: string; era: string; items: Province[] }>(
    () => api.provinces(at),
    [at],
  );
  const stations = useAsync<{ items: Station[] }>(() => api.stations({ limit: 1000 }), []);
  const alerts = useAsync<{ items: AlertItem[] }>(() => api.alerts({ limit: 20 }), []);
  const forecasts = useAsync<{ items: Array<Record<string, unknown>>; source: string; note?: string }>(
    () => api.forecastsLatest({ limit: 20 }),
    [],
  );

  /** Trạm thuộc tỉnh đang chọn, lọc theo thời kỳ địa giới. */
  const filteredStations = useMemo(() => {
    const items = stations.data?.items ?? [];
    if (!provinceCode) return items;
    const code = Number(provinceCode);
    return items.filter((s) => {
      const admin = (s.admin ?? {}) as Record<string, unknown>;
      const before = admin.before_2025_07 as Record<string, unknown> | undefined;
      const from = admin.from_2025_07 as Record<string, unknown> | undefined;
      const matchBefore = before && Number(before.code) === code;
      const matchFrom = from && Number(from.code) === code;
      if (eraFilter === "before") return Boolean(matchBefore);
      if (eraFilter === "from") return Boolean(matchFrom);
      return Boolean(matchBefore || matchFrom);
    });
  }, [stations.data, provinceCode, eraFilter]);

  const statColumns: Column<TideStats["stations"][number]>[] = [
    {
      key: "station",
      header: "Trạm",
      render: (r) => <strong>{r.station ?? "—"}</strong>,
    },
    { key: "n", header: "Số đỉnh", align: "right", render: (r) => fmtInt(r.n) },
    { key: "max", header: "Max (m)", align: "right", render: (r) => fmtNum(r.max) },
    { key: "median", header: "Trung vị (m)", align: "right", render: (r) => fmtNum(r.median) },
    {
      key: "bd1",
      header: "Vượt BĐ I",
      align: "right",
      render: (r) => (
        <Badge color={r.exceed.level_1 ? EXCEED_META[1].color : "#90a4ae"}>
          {r.exceed.level_1}
        </Badge>
      ),
    },
    {
      key: "bd2",
      header: "Vượt BĐ II",
      align: "right",
      render: (r) => (
        <Badge color={r.exceed.level_2 ? EXCEED_META[2].color : "#90a4ae"}>
          {r.exceed.level_2}
        </Badge>
      ),
    },
    {
      key: "bd3",
      header: "Vượt BĐ III",
      align: "right",
      render: (r) => (
        <Badge color={r.exceed.level_3 ? EXCEED_META[3].color : "#90a4ae"}>
          {r.exceed.level_3}
        </Badge>
      ),
    },
    { key: "max_ts", header: "Đỉnh lớn nhất", render: (r) => formatDate(r.max_ts) },
  ];

  return (
    <div>
      <h1>Tổng quan dự báo đỉnh mực nước</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Lưu lượng triều cường và dự báo trên hệ thống Đồng Nai – Sài Gòn – Vàm Cỏ.
      </p>

      {stats.error && <ErrorNotice error={stats.error} onRetry={stats.reload} />}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
          gap: 12,
          marginBottom: 16,
        }}
      >
        <Card
          title="Trạm trong danh mục"
          value={fmtInt(stations.data?.items.length ?? 0)}
          hint={`${filteredStations.length} trạm sau khi lọc`}
        />
        <Card
          title="Tỉnh hiệu lực tại ngày"
          value={fmtInt(provinces.data?.items.length ?? 0)}
          hint={`Thời kỳ: ${provinces.data?.era ?? "—"}`}
        />
        <Card
          title="Cảnh báo gần nhất"
          value={fmtInt(alerts.data?.items.length ?? 0)}
          hint="Từ GET /alerts"
        />
        <Card
          title="Đỉnh triều quan sát (năm)"
          value={fmtInt(stats.data?.n_documents ?? 0)}
          hint={`Năm ${year}`}
        />
      </div>

      {/* ---- Bộ lọc ---- */}
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
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Năm thống kê</div>
          <select
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            style={selStyle}
          >
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
            Ngày xác định tỉnh hiệu lực
          </div>
          <input
            type="date"
            value={at}
            onChange={(e) => setAt(e.target.value)}
            style={selStyle}
          />
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Tỉnh</div>
          <select
            value={provinceCode}
            onChange={(e) => setProvinceCode(e.target.value)}
            style={selStyle}
          >
            <option value="">— Tất cả —</option>
            {(provinces.data?.items ?? []).map((p) => (
              <option key={p.code} value={p.code}>
                {p.name} ({p.code})
              </option>
            ))}
          </select>
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
            Thời kỳ địa giới của trạm
          </div>
          <select
            value={eraFilter}
            onChange={(e) => setEraFilter(e.target.value as typeof eraFilter)}
            style={selStyle}
          >
            <option value="all">Tất cả</option>
            <option value="before">Tỉnh cũ (trước 01/07/2025)</option>
            <option value="from">Tỉnh mới (từ 01/07/2025)</option>
          </select>
        </div>
      </div>

      {provinces.error && <ErrorNotice error={provinces.error} onRetry={provinces.reload} />}

      {provinces.data && (
        <InfoNotice kind="info">
          Ngày <strong>{provinces.data.at}</strong> thuộc thời kỳ địa giới{" "}
          <code>{provinces.data.era}</code> — mọi lời gọi <code>admin-units</code> đều
          kèm <code>era</code> (ADR 0003).
        </InfoNotice>
      )}

      {/* ---- Biểu đồ vượt báo động theo trạm ---- */}
      {stats.data && stats.data.stations.length > 0 && (
        <Section title="Số lần vượt báo động theo trạm (năm dự báo)">
          <BarChart
            title={`Đỉnh triều vượt BĐ I — năm ${year}`}
            data={stats.data.stations.map((s) => ({
              label: s.station ?? "?",
              value: s.exceed.level_1,
            }))}
            yLabel="số lần"
            color="#f9a825"
          />
          <div style={{ height: 12 }} />
          <BarChart
            title={`Đỉnh triều vượt BĐ III — năm ${year}`}
            data={stats.data.stations.map((s) => ({
              label: s.station ?? "?",
              value: s.exceed.level_3,
            }))}
            yLabel="số lần"
            color="#c62828"
          />
        </Section>
      )}

      {/* ---- Bảng thống kê ---- */}
      <Section
        title={`Thống kê đỉnh triều và vượt báo động — năm ${year}`}
        extra={
          <span style={{ fontSize: 12, color: "#607d8b" }}>
            Ngưỡng lấy từ <code>stations.alarm_history</code> (theo thời gian)
          </span>
        }
      >
        <DataTable
          columns={statColumns}
          rows={stats.data?.stations ?? []}
          rowKey={(r) => r.station ?? String(Math.random())}
          loading={stats.loading}
          empty="Chưa có thống kê cho năm này."
          maxHeight={420}
        />
      </Section>

      {/* ---- Dự báo mới nhất ---- */}
      <Section title="Dự báo mới nhất">
        {forecasts.error && <ErrorNotice error={forecasts.error} onRetry={forecasts.reload} />}
        {forecasts.data?.note && <InfoNotice kind="warn">{forecasts.data.note}</InfoNotice>}
        <DataTable
          columns={[
            { key: "station", header: "Trạm", render: (r) => String(r.station ?? "—") },
            { key: "issued_at", header: "Phát hành", render: (r) => formatDateTime(r.issued_at as string) },
            { key: "peak_ts", header: "Thời điểm đỉnh", render: (r) => formatDateTime(r.peak_ts as string) },
            {
              key: "peak_value",
              header: "Cao độ đỉnh (m)",
              align: "right",
              render: (r) => fmtNum(r.peak_value as number),
            },
            { key: "model_version", header: "Phiên bản mô hình" },
            {
              key: "source",
              header: "Nguồn",
              render: (r) => <Badge color={r.source === "sample" ? "#ef6c00" : "#2e7d32"}>{String(r.source ?? "—")}</Badge>,
            },
          ]}
          rows={forecasts.data?.items ?? []}
          rowKey={(r, i) => `${String(r.station)}-${String(r.issued_at)}-${i}`}
          loading={forecasts.loading}
          empty="Chưa có dự báo nào (503 nếu chưa có mô hình đã huấn luyện)."
          maxHeight={360}
        />
      </Section>

      {/* ---- Cảnh báo gần đây ---- */}
      <Section title="Cảnh báo gần đây">
        {alerts.error && <ErrorNotice error={alerts.error} onRetry={alerts.reload} />}
        <DataTable
          columns={[
            { key: "station", header: "Trạm", render: (r) => r.station ?? "—" },
            {
              key: "severity",
              header: "Mức",
              render: (r) => (
                <Badge color={r.severity === "3" ? "#c62828" : r.severity === "2" ? "#ef6c00" : "#f9a825"}>
                  {severityLabel(r.severity)}
                </Badge>
              ),
            },
            { key: "message", header: "Nội dung" },
            { key: "issued_at", header: "Phát hành", render: (r) => formatDateTime(r.issued_at) },
            {
              key: "status",
              header: "Trạng thái",
              render: (r) => (
                <Badge color={statusColor(r.status)}>{statusLabel(r.status)}</Badge>
              ),
            },
          ]}
          rows={alerts.data?.items ?? []}
          rowKey={(r) => r.alert_id}
          loading={alerts.loading}
          empty="Không có cảnh báo nào."
          maxHeight={320}
        />
      </Section>
    </div>
  );
}

const selStyle: React.CSSProperties = {
  padding: "6px 8px",
  border: "1px solid #cfd8dc",
  borderRadius: 4,
  fontSize: 13,
  background: "#fff",
  minWidth: 150,
};
