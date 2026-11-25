"use client";

/**
 * Màn hình **Bản đồ dự báo triều**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/stations` — tọa độ + `alarm_history` của từng trạm.
 * - `GET /api/v1/forecasts/map` — GeoJSON dự báo (đỉnh dự báo mỗi trạm).
 * - `GET /api/v1/forecasts/compare` — so sánh các lần phát hành (theo trạm).
 * - `GET /api/v1/stations/{code}/tides` — chuỗi đỉnh/chân thực đo của trạm.
 * - `GET /api/v1/stats/tide` — thống kê và số lần vượt báo động.
 *
 * Màu trạm = cấp vượt báo động, tính từ ngưỡng trong `stations.alarm_history`
 * (ngưỡng theo thời gian, không dùng ngưỡng cố định).
 */

import React, { useMemo, useState } from "react";

import LeafletMap, { type MapPoint } from "@/components/Map";
import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  LineChart,
  Modal,
  type Column,
  type Series,
} from "@/components/ui";
import { api } from "@/lib/api";
import {
  EXCEED_META,
  exceedLevel,
  fmtNum,
  formatDateTime,
  hasStationLocation,
  thresholdsAt,
  type AlarmThresholds,
} from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { Station, TideItem } from "@/lib/types";

export default function ForecastMapPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [showObserved, setShowObserved] = useState(true);

  const stations = useAsync<{ items: Station[] }>(() => api.stations({ limit: 1000 }), []);
  const mapData = useAsync<{
    source: string;
    features: Array<{ properties: Record<string, unknown> }>;
    note?: string;
  }>(() => api.forecastsMap(), []);
  const compare = useAsync<{
    source: string;
    groups: Record<string, Array<Record<string, unknown>>>;
    note?: string;
  }>(() => api.forecastsCompare(), []);

  /** Ngưỡng báo động hiện hành của từng trạm (theo `alarm_history`). */
  const thresholds = useMemo(() => {
    const out: Record<string, AlarmThresholds> = {};
    (stations.data?.items ?? []).forEach((s) => {
      out[s.code] = thresholdsAt(s.alarm_history as Array<Record<string, unknown>> | undefined);
    });
    return out;
  }, [stations.data]);

  /** Dự báo theo trạm (từ GeoJSON của `/forecasts/map`). */
  const forecastByStation = useMemo(() => {
    const out: Record<string, { peak_value: number | null; peak_ts: string | null; issued_at: string | null }> = {};
    (mapData.data?.features ?? []).forEach((f) => {
      const p = f.properties ?? {};
      const code = String(p.station ?? "");
      if (!code) return;
      const cur = out[code];
      const val = p.peak_value as number | null;
      if (!cur || (val !== null && (cur.peak_value === null || val > cur.peak_value))) {
        out[code] = {
          peak_value: val,
          peak_ts: (p.peak_ts as string) ?? null,
          issued_at: (p.issued_at as string) ?? null,
        };
      }
    });
    return out;
  }, [mapData.data]);

  /** Các điểm đưa lên bản đồ: trạm mực nước tô theo cấp vượt báo động dự báo. */
  const points: MapPoint[] = useMemo(() => {
    const out: MapPoint[] = [];
    (stations.data?.items ?? []).forEach((s) => {
      if (!s.location || s.location.length < 2) return;
      const [lon, lat] = s.location;
      const fc = forecastByStation[s.code];
      const level = exceedLevel(fc?.peak_value ?? null, thresholds[s.code] ?? {});
      const meta = EXCEED_META[level];
      const isReservoir = (s.kind ?? "").toLowerCase().includes("reservoir");
      out.push({
        id: s.code,
        lat,
        lon,
        color: isReservoir ? "#0277bd" : meta.color,
        radius: fc?.peak_value !== null && fc?.peak_value !== undefined ? 10 : 7,
        tooltip: `${s.code} — ${s.name}`,
        popup: (
          <div style={{ minWidth: 220 }}>
            <strong>
              {s.code} — {s.name}
            </strong>
            <div style={{ marginTop: 4 }}>Loại: {s.kind ?? "—"}</div>
            <div>
              Dự báo đỉnh:{" "}
              <strong>{fc?.peak_value != null ? `${fc.peak_value.toFixed(2)} m` : "chưa có"}</strong>
            </div>
            <div>Thời điểm đỉnh: {formatDateTime(fc?.peak_ts)}</div>
            <div>Mức vượt: {meta.label}</div>
            <div style={{ marginTop: 6 }}>
              <button type="button" onClick={() => setSelected(s.code)}>
                Xem biểu đồ chi tiết
              </button>
            </div>
          </div>
        ),
      });
    });
    return out;
  }, [stations.data, forecastByStation, thresholds]);

  const legend = useMemo(() => {
    const counts: Record<number, number> = { 0: 0, 1: 0, 2: 0, 3: 0 };
    (stations.data?.items ?? []).forEach((s) => {
      const fc = forecastByStation[s.code];
      counts[exceedLevel(fc?.peak_value ?? null, thresholds[s.code] ?? {})] += 1;
    });
    return counts;
  }, [stations.data, forecastByStation, thresholds]);

  const listColumns: Column<Station>[] = [
    { key: "code", header: "Mã", render: (r) => <strong>{r.code}</strong> },
    { key: "name", header: "Tên trạm" },
    { key: "kind", header: "Loại", render: (r) => r.kind ?? "—" },
    {
      key: "peak",
      header: "Đỉnh dự báo (m)",
      align: "right",
      render: (r) => fmtNum(forecastByStation[r.code]?.peak_value ?? null),
    },
    {
      key: "peak_ts",
      header: "Thời điểm đỉnh",
      render: (r) => formatDateTime(forecastByStation[r.code]?.peak_ts ?? null),
    },
    {
      key: "level",
      header: "Mức vượt báo động",
      render: (r) => {
        const lv = exceedLevel(
          forecastByStation[r.code]?.peak_value ?? null,
          thresholds[r.code] ?? {},
        );
        return <Badge color={EXCEED_META[lv].color}>{EXCEED_META[lv].label}</Badge>;
      },
    },
    {
      key: "th",
      header: "Ngưỡng BĐ I / II / III (m)",
      render: (r) => {
        const th = thresholds[r.code];
        if (!th || (th.level_1_m === null && th.level_2_m === null && th.level_3_m === null)) {
          return <span style={{ color: "#90a4ae" }}>không có lịch sử báo động</span>;
        }
        return `${fmtNum(th.level_1_m)} / ${fmtNum(th.level_2_m)} / ${fmtNum(th.level_3_m)}`;
      },
    },
  ];

  return (
    <div>
      <h1>Bản đồ dự báo triều</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Trạm được tô màu theo mức vượt báo động của đỉnh dự báo; ngưỡng lấy từ lịch sử
        cấp báo động của từng trạm (<code>stations.alarm_history</code>).
      </p>

      {stations.error && <ErrorNotice error={stations.error} onRetry={stations.reload} />}
      {mapData.error && <ErrorNotice error={mapData.error} onRetry={mapData.reload} />}
      {mapData.data?.note && (
        <InfoNotice kind="warn">
          {mapData.data.note}
          {mapData.data.source === "sample" ? " (nguồn: dự báo mẫu trong results/)" : ""}
        </InfoNotice>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 2fr) minmax(280px, 1fr)",
          gap: 14,
          marginBottom: 16,
        }}
      >
        <div
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            overflow: "hidden",
          }}
        >
          <LeafletMap points={points} fitToPoints height={520} onPointClick={(p) => setSelected(p.id)} />
        </div>

        <div
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
          }}
        >
          <h3>Chú giải</h3>
          <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 16px" }}>
            {([0, 1, 2, 3] as const).map((lv) => (
              <li key={lv} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span
                  style={{
                    width: 14,
                    height: 14,
                    borderRadius: "50%",
                    background: EXCEED_META[lv].color,
                    display: "inline-block",
                    border: "2px solid #fff",
                    boxShadow: "0 0 0 1px #cfd8dc",
                  }}
                />
                <span style={{ fontSize: 13 }}>
                  {EXCEED_META[lv].label} — {legend[lv]} trạm
                </span>
              </li>
            ))}
            <li style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
              <span
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: "50%",
                  background: "#0277bd",
                  display: "inline-block",
                  border: "2px solid #fff",
                  boxShadow: "0 0 0 1px #cfd8dc",
                }}
              />
              <span style={{ fontSize: 13 }}>Hồ chứa</span>
            </li>
          </ul>

          <h3>Thống kê nhanh</h3>
          <table style={{ fontSize: 13, width: "100%" }}>
            <tbody>
              <tr>
                <td>Số trạm có tọa độ</td>
                <td align="right">
                  <strong>{points.length}</strong>
                </td>
              </tr>
              <tr>
                <td>Trạm có dự báo</td>
                <td align="right">
                  <strong>{Object.keys(forecastByStation).length}</strong>
                </td>
              </tr>
              <tr>
                <td>Nguồn dự báo</td>
                <td align="right">
                  <Badge color={mapData.data?.source === "sample" ? "#ef6c00" : "#2e7d32"}>
                    {mapData.data?.source ?? "—"}
                  </Badge>
                </td>
              </tr>
            </tbody>
          </table>

          <div style={{ marginTop: 14, fontSize: 12, color: "#607d8b" }}>
            Bấm vào một trạm trên bản đồ để xem biểu đồ thực đo + dự báo + khoảng tin cậy.
          </div>
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
        <h2>Danh sách trạm</h2>
        <DataTable
          columns={listColumns}
          rows={stations.data?.items ?? []}
          rowKey={(r) => r.code}
          loading={stations.loading}
          empty="Không có trạm nào."
          onRowClick={(r) => setSelected(r.code)}
          maxHeight={420}
        />
      </section>

      <StationDetailModal
        code={selected}
        onClose={() => setSelected(null)}
        showObserved={showObserved}
        setShowObserved={setShowObserved}
        compareGroups={compare.data?.groups ?? {}}
      />
    </div>
  );
}

/**
 * Hộp thoại chi tiết một trạm: biểu đồ thực đo + dự báo mô hình + dự báo KTTV
 * + khoảng tin cậy.
 */
function StationDetailModal({
  code,
  onClose,
  showObserved,
  setShowObserved,
  compareGroups,
}: {
  code: string | null;
  onClose: () => void;
  showObserved: boolean;
  setShowObserved: (v: boolean) => void;
  compareGroups: Record<string, Array<Record<string, unknown>>>;
}) {
  const { canRunForecast } = usePermission();
  const station = useAsync<Station>(
    () => (code ? api.station(code) : Promise.resolve(null as unknown as Station)),
    [code],
  );
  const tides = useAsync<{ items: TideItem[] }>(
    () =>
      code
        ? api.stationTides(code, { limit: 2000 })
        : Promise.resolve({ items: [] as TideItem[] }),
    [code],
  );
  const propagation = useAsync<{ items: Array<Record<string, unknown>>; source: string }>(
    () =>
      code
        ? api.propagation(code, 100)
        : Promise.resolve({ items: [] as Array<Record<string, unknown>>, source: "—" }),
    [code],
  );

  const series = useMemo<Series[]>(() => {
    const out: Series[] = [];
    if (showObserved) {
      const obs = (tides.data?.items ?? [])
        .filter((t) => t.kind === "high" && t.value !== null)
        .map((t) => ({ x: (t.ts ?? "").slice(0, 10), y: t.value }));
      out.push({ name: "Thực đo (đỉnh)", color: "#1565c0", points: obs });
    }
    const group = code ? compareGroups[code] : undefined;
    if (group?.length) {
      const fc = group
        .map((g) => ({
          x: String(g.peak_ts ?? "").slice(0, 10),
          y: (g.peak_value as number | null) ?? null,
        }))
        .filter((p) => p.y !== null);
      out.push({ name: "Dự báo mô hình", color: "#c62828", points: fc });
    }
    // Khoảng tin cậy — CHỈ có khi API trả `h_q10`/`h_q50`/`h_q90`.
    // `GET /forecasts/compare` ở nhánh collection `forecasts` không có các trường này
    // (chỉ nhánh dự báo mẫu đọc từ CSV trong `results/` mới có) ⇒ không vẽ khoảng rỗng.
    const band = (compareGroups[code ?? ""] ?? [])
      .map((g) => ({
        x: String(g.peak_ts ?? "").slice(0, 10),
        y: (g.h_q10 as number | null) ?? null,
      }))
      .filter((p) => p.y !== null);
    if (band.length) {
      out.push({ name: "Khoảng tin cậy (h10)", color: "#8e24aa", points: band, fill: true });
    }
    return out;
  }, [tides.data, compareGroups, code, showObserved]);

  const thresholds = useMemo(
    () => thresholdsAt(station.data?.alarm_history as Array<Record<string, unknown>> | undefined),
    [station.data],
  );

  const markers = useMemo(() => {
    const out: Array<{ value: number; color: string; label: string }> = [];
    if (thresholds.level_1_m !== null)
      out.push({ value: thresholds.level_1_m, color: "#f9a825", label: "BĐ I" });
    if (thresholds.level_2_m !== null)
      out.push({ value: thresholds.level_2_m, color: "#ef6c00", label: "BĐ II" });
    if (thresholds.level_3_m !== null)
      out.push({ value: thresholds.level_3_m, color: "#c62828", label: "BĐ III" });
    return out;
  }, [thresholds]);

  /** API có trả khoảng tin cậy (`h_q10`/`h_q50`/`h_q90`) cho trạm này không. */
  const hasConfidenceBand = useMemo(
    () =>
      (compareGroups[code ?? ""] ?? []).some((g) => typeof g.h_q10 === "number"),
    [compareGroups, code],
  );

  return (
    <Modal
      open={Boolean(code)}
      title={code ? `Chi tiết trạm ${code}` : ""}
      onClose={onClose}
      width={980}
    >
      {station.error && <ErrorNotice error={station.error} onRetry={station.reload} />}
      {tides.error && <ErrorNotice error={tides.error} onRetry={tides.reload} />}

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 10 }}>
        <div>
          <strong>{station.data?.name ?? code}</strong>
          {station.data?.river ? ` — sông ${station.data.river}` : ""}
        </div>
        <div style={{ color: "#607d8b", fontSize: 13 }}>
          Tọa độ:{" "}
          {hasStationLocation(station.data?.location)
            ? `${Number(station.data.location[1]).toFixed(4)}, ${Number(station.data.location[0]).toFixed(4)}`
            : "—"}
        </div>
        <div style={{ color: "#607d8b", fontSize: 13 }}>
          Ngưỡng: BĐ I {fmtNum(thresholds.level_1_m)} · II {fmtNum(thresholds.level_2_m)} · III{" "}
          {fmtNum(thresholds.level_3_m)} m
        </div>
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
          <input
            type="checkbox"
            checked={showObserved}
            onChange={(e) => setShowObserved(e.target.checked)}
          />
          Hiện thực đo
        </label>
      </div>

      <LineChart
        series={series}
        markers={markers}
        height={300}
        yLabel="m"
        title="Đỉnh triều: thực đo + dự báo + khoảng tin cậy"
      />

      {!hasConfidenceBand && (
        <InfoNotice kind="info">
          <strong>Khoảng tin cậy không có sẵn từ API hiện tại.</strong>{" "}
          <code>GET /forecasts/compare</code> chỉ trả <code>peak_value</code>/<code>peak_ts</code>{" "}
          (nhánh collection <code>forecasts</code>); các trường <code>h_q10</code>/<code>h_q50</code>/
          <code>h_q90</code> chỉ có ở nhánh dự báo mẫu đọc từ <code>results/</code>. Vì vậy biểu đồ
          chỉ vẽ đường thực đo và đường dự báo.
        </InfoNotice>
      )}

      {propagation.data && propagation.data.items.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h3>Lan truyền pha (nguồn: {propagation.data.source})</h3>
          <DataTable
            columns={[
              { key: "ts", header: "Thời điểm", render: (r) => formatDateTime(String(r.ts ?? r.issued_at ?? "")) },
              { key: "lag_hours", header: "Độ trễ (giờ)", align: "right", render: (r) => fmtNum(r.lag_hours as number | null) },
              { key: "distance_km", header: "Quãng đường (km)", align: "right", render: (r) => fmtNum(r.distance_km as number | null) },
              { key: "upstream", header: "Trạm trên dòng", render: (r) => String(r.upstream ?? "—") },
            ]}
            rows={propagation.data.items}
            rowKey={(r, i) => `${String(r.ts ?? r.issued_at)}-${i}`}
            maxHeight={200}
          />
        </div>
      )}

      <div style={{ marginTop: 14, display: "flex", gap: 10 }}>
        <Button onClick={onClose}>Đóng</Button>
        {canRunForecast(COLLECTIONS.forecasts) && (
          <span style={{ fontSize: 12, color: "#607d8b", alignSelf: "center" }}>
            (bạn có quyền chạy dự báo)
          </span>
        )}
      </div>
    </Modal>
  );
}
