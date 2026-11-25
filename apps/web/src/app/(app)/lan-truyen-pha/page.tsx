"use client";

/**
 * Màn hình **Lan truyền pha**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/stations` — tọa độ trạm (để dựng đường lan truyền).
 * - `GET /api/v1/forecasts/{station}/propagation` — chuỗi lan truyền đỉnh (độ trễ τ)
 *   của một trạm; nếu API chưa có số liệu τ thì endpoint trả 503 → màn hình hiển
 *   thị rõ và dùng bản đồ tĩnh dựng từ vị trí các trạm.
 * - `GET /api/v1/features/rivers` — mạng thủy văn (nếu đã nạp) để vẽ sông.
 * - `GET /api/v1/trace/{comid}` — lần dòng chảy từ một COMID.
 *
 * Về animation: sóng triều lan **từ cửa biển (Vũng Tàu) vào các trạm**, sóng xả hồ
 * đi **ngược chiều dòng chảy**. Vì API chưa có đường đồ thị đầy đủ (chưa có
 * `data/processed/graph/edges.parquet`), đường lan truyền ở đây được **dựng theo
 * thứ tự các trạm** (thứ tự trên dòng của danh mục `config/stations.yaml`), và
 * thời độ trễ lấy từ API nếu có. README ghi rõ giới hạn này.
 */

import React, { useEffect, useMemo, useState } from "react";

import LeafletMap, { type MapLine, type MapPoint } from "@/components/Map";
import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  type Column,
} from "@/components/ui";
import { api } from "@/lib/api";
import { fmtNum, formatDateTime, hasStationLocation } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import type { Station } from "@/lib/types";

/** Thứ tự các trạm theo dòng chảy (biển → hạ lưu), lấy từ danh mục trạm. */
const STATION_ORDER = [
  "VUONGTAU",
  "PHUAN",
  "THUDAMMOT",
  "BIENHOA",
  "DAUTIEN",
  "TANAN",
  "NHABE",
  "BENLUC",
  "GODAUHA",
];

interface Wave {
  /** Tọa độ điểm đầu (nguồn sóng). */
  from: [number, number];
  /** Tọa độ điểm đích. */
  to: [number, number];
  /** Độ trễ (giờ) để đỉnh đến nơi. */
  lagH: number;
  label: string;
  kind: "tide" | "release";
}

export default function PropagationPage() {
  const [playing, setPlaying] = useState(true);
  const [phase, setPhase] = useState(0);
  const [selected, setSelected] = useState<string>(STATION_ORDER[0]);

  const stations = useAsync<{ items: Station[] }>(() => api.stations({ limit: 1000 }), []);
  const propagation = useAsync<{
    source: string;
    count: number;
    items: Array<Record<string, unknown>>;
  }>(() => api.propagation(selected, 200), [selected]);
  const rivers = useAsync<{
    features: Array<{ geometry: { type: string; coordinates?: unknown } | null }>;
  }>(() => api.features("rivers", { limit: 4000 }), []);

  /** Trạm có tọa độ, sắp theo thứ tự dòng chảy. */
  const ordered = useMemo(() => {
    const items = stations.data?.items ?? [];
    const byCode = new Map(items.map((s) => [s.code.toUpperCase(), s]));
    const out: Station[] = [];
    STATION_ORDER.forEach((c) => {
      const s = byCode.get(c);
      if (s) out.push(s);
    });
    // Trạm còn lại (hồ chứa, trạm mưa) đưa vào cuối để không mất.
    items.forEach((s) => {
      if (!out.find((o) => o.code === s.code)) out.push(s);
    });
    return out;
  }, [stations.data]);

  const byCode = useMemo(() => {
    const m: Record<string, Station> = {};
    ordered.forEach((s) => {
      m[s.code.toUpperCase()] = s;
    });
    return m;
  }, [ordered]);

  /** Các đoạn sóng: triều đi vào (biển → thượng lưu), xả hồ đi xuống (thượng lưu → hạ lưu). */
  const waves = useMemo<Wave[]>(() => {
    const out: Wave[] = [];
    const chain = STATION_ORDER.map((c) => byCode[c]).filter(
      (s): s is Station => Boolean(s) && hasStationLocation(s.location),
    );
    // Sóng triều: từ trạm hạ lưu đi ngược lên thượng lưu (Vũng Tàu → các trạm trên).
    for (let i = chain.length - 1; i > 0; i -= 1) {
      const to = chain[i];
      const from = chain[i + 1];
      if (!hasStationLocation(to.location) || !hasStationLocation(from.location)) continue;
      out.push({
        from: [from.location[1], from.location[0]],
        to: [to.location[1], to.location[0]],
        lagH: i * 3,
        label: `Triều: ${from.code} → ${to.code}`,
        kind: "tide",
      });
    }
    // Sóng xả hồ: từ các hồ chứa đi xuống hạ lưu.
    const reservoirs = ordered.filter(
      (s) =>
        hasStationLocation(s.location) &&
        (s.kind ?? "").toLowerCase().includes("reservoir") &&
        ["DAUTIEN", "TRANAN", "THACMO"].includes(s.code.toUpperCase()),
    );
    reservoirs.forEach((res) => {
      if (!hasStationLocation(res.location)) return;
      const idx = chain.findIndex((s) => s.code === res.code);
      const anchor = idx >= 0 ? chain[idx] : chain[chain.length - 1];
      const next = chain[chain.length - 1];
      if (anchor && next && anchor.code !== next.code && hasStationLocation(anchor.location)) {
        out.push({
          from: [res.location[1], res.location[0]],
          to: [anchor.location[1], anchor.location[0]],
          lagH: 6,
          label: `Xả hồ: ${res.code} → ${anchor.code}`,
          kind: "release",
        });
      }
    });
    return out;
  }, [byCode, ordered]);

  /** Vòng animation (giây). */
  const cycle = 24;
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => setPhase((p) => (p + 1) % cycle), 1000);
    return () => window.clearInterval(id);
  }, [playing]);

  /** Các đoạn đang "sáng" theo pha hiện tại. */
  const activeWaves = useMemo(() => {
    const out = new Set<number>();
    waves.forEach((w, i) => {
      const start = (i * 3) % cycle;
      if ((phase >= start && phase < start + 4)) out.add(i);
    });
    return out;
  }, [phase, waves]);

  const lines: MapLine[] = useMemo(
    () =>
      waves.map((w, i) => ({
        id: `${w.kind}-${i}`,
        points: [w.from, w.to],
        color: w.kind === "tide" ? "#0277bd" : "#ef6c00",
        weight: activeWaves.has(i) ? 6 : 2,
        opacity: activeWaves.has(i) ? 0.95 : 0.35,
      })),
    [waves, activeWaves],
  );

  /** Các sóng đang chạy (đầu sóng di chuyển dọc đoạn). */
  const waveDots: MapPoint[] = useMemo(() => {
    const out: MapPoint[] = [];
    waves.forEach((w, i) => {
      if (!activeWaves.has(i)) return;
      const t = (phase % 4) / 4;
      const lat = w.from[0] + (w.to[0] - w.from[0]) * t;
      const lon = w.from[1] + (w.to[1] - w.from[1]) * t;
      out.push({
        id: `dot-${i}`,
        lat,
        lon,
        color: w.kind === "tide" ? "#29b6f6" : "#ffa726",
        radius: 8,
        tooltip: w.label,
      });
    });
    return out;
  }, [waves, activeWaves, phase]);

  const stationPoints: MapPoint[] = useMemo(
    () =>
      ordered
        .filter((s) => hasStationLocation(s.location))
        .map((s) => ({
          id: s.code,
          // `hasStationLocation` đã lọc ở trên ⇒ `location` chắc chắn là `[lon, lat]`.
          lat: s.location![1],
          lon: s.location![0],
          color: s.code.toUpperCase() === selected.toUpperCase() ? "#c62828" : "#1b5e20",
          radius: s.code.toUpperCase() === selected.toUpperCase() ? 11 : 7,
          tooltip: `${s.code} — ${s.name}`,
          popup: (
            <div>
              <strong>
                {s.code} — {s.name}
              </strong>
              <div>Sông: {s.river ?? "—"}</div>
              <div>Loại: {s.kind ?? "—"}</div>
            </div>
          ),
        })),
    [ordered, selected],
  );

  /** Sông từ collection `rivers` (nếu có). */
  const riverLines = useMemo<MapLine[]>(() => {
    const out: MapLine[] = [];
    (rivers.data?.features ?? []).forEach((f, i) => {
      const geom = f.geometry;
      if (!geom || geom.type !== "LineString") return;
      const coords = geom.coordinates as Array<[number, number]>;
      if (!Array.isArray(coords) || coords.length < 2) return;
      const points: Array<[number, number]> = [];
      for (const c of coords) {
        if (!Array.isArray(c) || c.length < 2) continue;
        const lon = Number(c[0]);
        const lat = Number(c[1]);
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
        points.push([lat, lon]);
      }
      if (points.length < 2) return;
      out.push({
        id: `river-${i}`,
        points,
        color: "#4fc3f7",
        weight: 2,
        opacity: 0.6,
      });
    });
    return out;
  }, [rivers.data]);

  const propColumns: Column<Record<string, unknown>>[] = [
    {
      key: "ts",
      header: "Thời điểm",
      render: (r) => formatDateTime(String(r.ts ?? r.issued_at ?? "")),
    },
    {
      key: "lag_hours",
      header: "τ độ trễ (giờ)",
      align: "right",
      render: (r) => fmtNum(r.lag_hours as number | null),
    },
    {
      key: "distance_km",
      header: "Quãng đường dọc sông (km)",
      align: "right",
      render: (r) => fmtNum(r.distance_km as number | null),
    },
    { key: "upstream", header: "Trạm nguồn", render: (r) => String(r.upstream ?? "—") },
    { key: "method", header: "Phương pháp", render: (r) => String(r.method ?? "—") },
  ];

  return (
    <div>
      <h1>Lan truyền pha</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Sóng triều lan từ cửa biển vào các trạm; sóng xả hồ đi ngược xuống hạ lưu. Màu
        xanh dương = sóng triều, màu cam = sóng xả hồ.
      </p>

      {stations.error && <ErrorNotice error={stations.error} onRetry={stations.reload} />}

      <InfoNotice kind="info">
        <strong>Giới hạn hiện tại:</strong> API chưa trả số liệu τ vật lý/học được cho từng
        đoạn sông (<code>data/processed/graph/edges.parquet</code> chưa được sinh và
        collection <code>graph_edges</code> chưa nạp). Vì vậy đường lan truyền ở đây được
        dựng theo <strong>thứ tự các trạm</strong> trên dòng, còn &tau; lấy từ{" "}
        <code>GET /forecasts/&#123;station&#125;/propagation</code> khi API có. Xem README
        mục &quot;Lan truyền pha&quot;.
      </InfoNotice>

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
          <LeafletMap
            points={[...stationPoints, ...waveDots]}
            lines={[...riverLines, ...lines]}
            height={540}
            onPointClick={(p) => setSelected(p.id)}
          />
        </div>

        <div
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
          }}
        >
          <h3>Điều khiển</h3>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
            <Button variant="primary" onClick={() => setPlaying((p) => !p)}>
              {playing ? "⏸ Tạm dừng" : "▶ Chạy"}
            </Button>
            <Button onClick={() => setPhase(0)}>⟲ Đặt lại</Button>
          </div>

          <div style={{ fontSize: 13, marginBottom: 10 }}>
            Pha: <strong>{phase}</strong> / {cycle} giây
          </div>

          <h3>Trạm</h3>
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            style={{ width: "100%", padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
          >
            {ordered.map((s) => (
              <option key={s.code} value={s.code}>
                {s.code} — {s.name}
              </option>
            ))}
          </select>

          <h3 style={{ marginTop: 14 }}>Các đoạn sóng</h3>
          <div style={{ maxHeight: 230, overflowY: "auto", fontSize: 12 }}>
            {waves.map((w, i) => (
              <div
                key={`${w.label}-${i}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "3px 0",
                  color: activeWaves.has(i) ? "#0d47a1" : "#607d8b",
                  fontWeight: activeWaves.has(i) ? 700 : 400,
                }}
              >
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: w.kind === "tide" ? "#0277bd" : "#ef6c00",
                    display: "inline-block",
                  }}
                />
                {w.label} · τ ≈ {w.lagH} giờ
              </div>
            ))}
          </div>

          <div style={{ marginTop: 12, fontSize: 12, color: "#607d8b" }}>
            Sông (collection <code>rivers</code>):{" "}
            {rivers.data ? `${rivers.data.features.length} đối tượng` : "chưa có dữ liệu"}
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
        <h2>Chuỗi lan truyền đỉnh — trạm {selected}</h2>
        {propagation.error && (
          <ErrorNotice error={propagation.error} onRetry={propagation.reload} />
        )}
        {propagation.data && propagation.data.count === 0 && (
          <InfoNotice kind="warn">
            API chưa có số liệu lan truyền cho trạm này (503). Bản đồ tĩnh ở trên vẫn
            dùng được nhờ thứ tự trạm.
          </InfoNotice>
        )}
        {propagation.data && (
          <div style={{ marginBottom: 8, fontSize: 13, color: "#607d8b" }}>
            Nguồn: <Badge color="#0277bd">{propagation.data.source}</Badge> ·{" "}
            {propagation.data.count} bản ghi
          </div>
        )}
        <DataTable
          columns={propColumns}
          rows={propagation.data?.items ?? []}
          rowKey={(r, i) => `${String(r.ts ?? r.issued_at)}-${i}`}
          loading={propagation.loading}
          empty="Chưa có dữ liệu lan truyền pha cho trạm này."
          maxHeight={300}
        />
      </section>
    </div>
  );
}
