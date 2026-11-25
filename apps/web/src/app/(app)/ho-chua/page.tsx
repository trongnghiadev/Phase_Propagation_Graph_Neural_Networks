"use client";

/**
 * Màn hình **Hồ chứa**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/reservoirs` — danh sách hồ có số liệu.
 * - `GET /api/v1/reservoirs/{name}/daily` — mực nước, lưu lượng vào, xả tràn, chạy máy.
 *
 * Mỗi bản ghi có `province` = `{era, code, note, provisional}` — địa giới **luôn kèm `era`**
 * (ADR 0006), nên màn hình này hiển thị thời kỳ địa giới của từng bản ghi.
 */

import React, { useMemo, useState } from "react";

import {
  Badge,
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
import type { ReservoirDaily } from "@/lib/types";

export default function ReservoirsPage() {
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const list = useAsync<{ items: string[] }>(() => api.reservoirs(), []);
  const daily = useAsync<{ items: ReservoirDaily[]; from: string | null; to: string | null }>(
    () =>
      name
        ? api.reservoirDaily(name, {
            from: from || undefined,
            to: to || undefined,
            limit: 2000,
          })
        : Promise.resolve({ items: [] as ReservoirDaily[], from: null, to: null }),
    [name, from, to],
  );

  /** Chuỗi thời gian cho biểu đồ. */
  const series = useMemo<Series[]>(() => {
    const items = [...(daily.data?.items ?? [])].sort((a, b) =>
      String(a.date ?? "").localeCompare(String(b.date ?? "")),
    );
    const pts = items.map((d) => ({
      x: String(d.date ?? "").slice(0, 10),
      y: d.water_level_m ?? null,
    }));
    const inflow = items.map((d) => ({ x: String(d.date ?? "").slice(0, 10), y: d.inflow_m3s ?? null }));
    const outflow = items.map((d) => ({
      x: String(d.date ?? "").slice(0, 10),
      y: d.outflow_m3s ?? null,
    }));
    const spill = items.map((d) => ({
      x: String(d.date ?? "").slice(0, 10),
      y: d.spillway_m3s ?? null,
    }));
    return [
      { name: "Mực nước hồ (m)", color: "#1565c0", points: pts },
      { name: "Lưu lượng vào (m³/s)", color: "#2e7d32", points: inflow },
      { name: "Lưu lượng ra (m³/s)", color: "#ef6c00", points: outflow },
      { name: "Xả tràn (m³/s)", color: "#c62828", points: spill },
    ];
  }, [daily.data]);

  const columns: Column<ReservoirDaily>[] = [
    { key: "date", header: "Ngày", render: (r) => formatDateTime(r.date) },
    {
      key: "water_level_m",
      header: "Mực nước (m)",
      align: "right",
      render: (r) => fmtNum(r.water_level_m),
    },
    {
      key: "inflow_m3s",
      header: "Lưu lượng vào (m³/s)",
      align: "right",
      render: (r) => fmtNum(r.inflow_m3s),
    },
    {
      key: "outflow_m3s",
      header: "Lưu lượng ra (m³/s)",
      align: "right",
      render: (r) => fmtNum(r.outflow_m3s),
    },
    {
      key: "spillway_m3s",
      header: "Xả tràn (m³/s)",
      align: "right",
      render: (r) => fmtNum(r.spillway_m3s),
    },
    {
      key: "turbine_m3s",
      header: "Chạy máy (m³/s)",
      align: "right",
      render: (r) => fmtNum(r.turbine_m3s),
    },
    {
      key: "storage_hectom3",
      header: "Dung tích (hm³)",
      align: "right",
      render: (r) => fmtNum(r.storage_hectom3),
    },
    {
      key: "rain_mm",
      header: "Mưa (mm)",
      align: "right",
      render: (r) => fmtNum(r.rain_mm),
    },
    {
      key: "province",
      header: "Địa giới (era)",
      render: (r) =>
        r.province ? (
          <span>
            <Badge color="#455a64">{r.province.era ?? "—"}</Badge>{" "}
            {r.province.code ?? ""}
            {r.province.provisional ? " (tạm)" : ""}
          </span>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <div>
      <h1>Hồ chứa</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Mực nước hồ, lưu lượng vào — ra — xả tràn và dung tích. Địa giới của mỗi bản ghi
        luôn kèm <code>era</code> (thời kỳ địa giới) theo quy tắc của dự án.
      </p>

      {list.error && <ErrorNotice error={list.error} onRetry={list.reload} />}
      {daily.error && <ErrorNotice error={daily.error} onRetry={daily.reload} />}

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
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Hồ</div>
          <select
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13, minWidth: 200 }}
          >
            <option value="">— Chọn hồ —</option>
            {(list.data?.items ?? []).map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Từ ngày</div>
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            style={{ padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
          />
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Đến ngày</div>
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            style={{ padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
          />
        </div>
        {name && (
          <div style={{ fontSize: 12, color: "#607d8b" }}>
            Khoảng: {formatDateTime(daily.data?.from)} → {formatDateTime(daily.data?.to)} ·{" "}
            {daily.data?.items?.length ?? 0} bản ghi
          </div>
        )}
      </div>

      {!name && (
        <InfoNotice kind="info">
          Chọn một hồ để xem biểu đồ và bảng số liệu theo ngày.
        </InfoNotice>
      )}

      {name && (
        <>
          <section
            style={{
              background: "#fff",
              border: "1px solid var(--line)",
              borderRadius: 6,
              padding: 14,
              marginBottom: 16,
            }}
          >
            <h2>Biểu đồ — {name}</h2>
            <LineChart series={series} height={320} yLabel="m / m³·s⁻¹" />
          </section>

          <section
            style={{
              background: "#fff",
              border: "1px solid var(--line)",
              borderRadius: 6,
              padding: 14,
            }}
          >
            <h2>Số liệu theo ngày</h2>
            <DataTable
              columns={columns}
              rows={daily.data?.items ?? []}
              rowKey={(r, i) => `${r.date}-${i}`}
              loading={daily.loading}
              empty="Không có số liệu hồ chứa trong khoảng này."
              maxHeight={480}
            />
          </section>
        </>
      )}
    </div>
  );
}
