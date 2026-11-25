"use client";

/**
 * Màn hình **Địa giới theo thời gian**.
 *
 * Endpoint dùng (luôn kèm `era` theo luật địa giới — ADR 0003):
 * - `GET /api/v1/admin-units/eras` — hai thời kỳ và mốc hiệu lực.
 * - `GET /api/v1/admin-units/provinces?at=YYYY-MM-DD` — tỉnh hiệu lực tại ngày
 *   (API suy ra `era` từ ngày và trả về trong payload).
 * - `GET /api/v1/admin-units/provinces/{era}/{code}` — chi tiết tỉnh theo thời kỳ.
 * - `GET /api/v1/admin-units/wards/{era}/{code}/mapping` — ánh xạ xã cũ ↔ mới.
 * - `GET /api/v1/stations` — trạm thuộc tỉnh nào ở mỗi thời kỳ (`admin`/`admin_history`).
 */

import React, { useMemo, useState } from "react";

import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  Modal,
  type Column,
} from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import type { Province, Station, Ward, WardMapping } from "@/lib/types";

/** Ngày mốc hiệu lực của kỳ địa giới mới (01/07/2025 giờ VN). */
const ERA_SPLIT = "2025-07-01";

/**
 * Giá trị rỗng cho `wardMap` — đủ shape để `?.mappings?.length` không ném TypeError
 * (placeholder `{}` thiếu thuộc tính ⇒ `.length` của `undefined` ⇒ lỗi runtime).
 * Hằng số ở cấp module để không tạo object mới ở mỗi lần render.
 */
const EMPTY_WARD_MAP: {
  era: string;
  province_code: number;
  wards: Ward[];
  mappings: WardMapping[];
} = { era: "", province_code: 0, wards: [], mappings: [] };

export default function AdminUnitsPage() {
  const [at, setAt] = useState("");
  const [era] = useState<string>("");
  const [selected, setSelected] = useState<Province | null>(null);
  const [err, setErr] = useState<Error | null>(null);

  const eras = useAsync<{ eras: string[]; before: string; from: string; effective_utc: string }>(
    () => api.eras(),
    [],
  );
  const provinces = useAsync<{ at: string; era: string; count: number; items: Province[] }>(
    () => api.provinces(at || undefined),
    [at],
  );
  const stations = useAsync<{ items: Station[] }>(() => api.stations({ limit: 1000 }), []);

  /** `era` hiệu lực — lấy từ API (đã tính từ `at`), không tự suy luận. */
  const effectiveEra = provinces.data?.era ?? era;

  /** Chi tiết tỉnh + ánh xạ xã (chỉ khi chọn một tỉnh). */
  const provinceDetail = useAsync<Province & Record<string, unknown>>(
    () =>
      selected && effectiveEra
        ? api.province(effectiveEra, selected.code)
        : Promise.resolve({} as never),
    [selected, effectiveEra],
  );
  const wardMap = useAsync<{
    era: string;
    province_code: number;
    wards: Ward[];
    mappings: WardMapping[];
  }>(
    () =>
      selected && effectiveEra
        ? api.wardMapping(effectiveEra, selected.code)
        : Promise.resolve(EMPTY_WARD_MAP),
    [selected, effectiveEra],
  );

  /** Trạm theo tỉnh ở mỗi thời kỳ (dựa trên `admin` / `admin_history`). */
  const stationsByEra = useMemo(() => {
    const out: Record<string, Array<{ code: string; name: string; province: string; note?: string }>> =
      {};
    (stations.data?.items ?? []).forEach((s) => {
      const admin = (s.admin ?? {}) as Record<string, unknown>;
      const history = (s.admin_history ?? []) as Array<Record<string, unknown>>;
      const entries: Array<{ era: string; province: string; note?: string }> = [];
      const eraNow = (admin.era as string) ?? "";
      const provNow = (admin.province_name as string) ?? (admin.province as string) ?? "";
      if (eraNow && provNow) {
        entries.push({ era: eraNow, province: provNow, note: admin.note as string | undefined });
      }
      history.forEach((h) => {
        const e = (h.era as string) ?? "";
        const p = (h.province_name as string) ?? (h.province as string) ?? "";
        if (e && p) entries.push({ era: e, province: p, note: h.note as string | undefined });
      });
      entries.forEach((en) => {
        const list = (out[en.era] ??= []);
        list.push({ code: s.code, name: s.name, province: en.province, note: en.note });
      });
    });
    return out;
  }, [stations.data]);

  const provinceColumns: Column<Province>[] = [
    { key: "code", header: "Mã", align: "right", render: (p) => String(p.code) },
    { key: "name", header: "Tên tỉnh" },
    { key: "era", header: "Thời kỳ", render: (p) => <Badge color="#455a64">{p.era}</Badge> },
    {
      key: "valid_from",
      header: "Hiệu lực từ",
      render: (p) => formatDateTime(p.valid_from),
    },
    {
      key: "valid_to",
      header: "Hiệu lực đến",
      render: (p) => (p.valid_to ? formatDateTime(p.valid_to) : "hiện hành"),
    },
    {
      key: "successor",
      header: "Tỉnh kế thừa / hợp thành",
      render: (p) =>
        p.successor ? (
          <span>
            {p.successor.name ?? "—"}{" "}
            {p.successor.code != null && (
              <code style={{ fontSize: 11 }}>({p.successor.code})</code>
            )}
          </span>
        ) : (
          "—"
        ),
    },
    {
      key: "action",
      header: "",
      render: (p) => (
        <Button variant="primary" onClick={() => setSelected(p)}>
          Chi tiết
        </Button>
      ),
    },
  ];

  return (
    <div>
      <h1>Địa giới hành chính theo thời gian</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Dự án dùng hai thời kỳ địa giới, mốc chuyển đổi 01/07/2025 (giờ Việt Nam) — mã
        đơn vị ở hai thời kỳ có thể là hai đơn vị khác nhau (ví dụ mã 80: Long An → Tây
        Ninh). Mọi truy vấn đều dùng <code>era</code> rõ ràng.
      </p>

      {eras.error && <ErrorNotice error={eras.error} onRetry={eras.reload} />}
      {provinces.error && <ErrorNotice error={provinces.error} onRetry={provinces.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}

      {eras.data && (
        <InfoNotice kind="info">
          Thời kỳ <code>{eras.data.before}</code> (trước {eras.data.effective_utc}) và{" "}
          <code>{eras.data.from}</code> (từ {eras.data.effective_utc}).
        </InfoNotice>
      )}

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
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
            Ngày xem (giờ Việt Nam)
          </div>
          <input
            type="date"
            value={at}
            onChange={(e) => setAt(e.target.value)}
            style={{ padding: 6, borderRadius: 4, border: "1px solid #cfd8dc", fontSize: 13 }}
          />
        </div>
        <div style={{ fontSize: 12, color: "#607d8b" }}>
          Thời kỳ suy ra từ ngày:{" "}
          <Badge color="#455a64">{effectiveEra || "—"}</Badge>
        </div>
        <Button
          onClick={() => {
            setAt("");
            provinces.reload();
          }}
        >
          Xem hiện tại
        </Button>
        <Button
          onClick={() => {
            setAt("2025-06-30");
            provinces.reload();
          }}
        >
          Trước mốc (30/06/2025)
        </Button>
        <Button
          onClick={() => {
            setAt(ERA_SPLIT);
            provinces.reload();
          }}
        >
          Từ mốc (01/07/2025)
        </Button>
      </div>

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <h2 style={{ marginTop: 0 }}>
          Tỉnh có hiệu lực tại {provinces.data?.at ?? "—"} ({provinces.data?.count ?? 0} tỉnh)
        </h2>
        <DataTable
          columns={provinceColumns}
          rows={provinces.data?.items ?? []}
          rowKey={(p) => `${p.era}-${p.code}`}
          loading={provinces.loading}
          empty="Không có tỉnh nào hiệu lực tại ngày này."
          maxHeight={420}
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
        <h2 style={{ marginTop: 0 }}>Trạm thuộc tỉnh nào ở mỗi thời kỳ</h2>
        {Object.keys(stationsByEra).length === 0 ? (
          <InfoNotice kind="warn">
            Chưa có trạm nào gắn thông tin địa giới (<code>admin</code> /{" "}
            <code>admin_history</code>) trong dữ liệu hiện tại.
          </InfoNotice>
        ) : (
          Object.entries(stationsByEra).map(([e, list]) => (
            <div key={e} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
                <Badge color="#455a64">{e}</Badge> — {list.length} trạm
              </div>
              <DataTable
                columns={[
                  { key: "code", header: "Mã trạm", render: (r) => <strong>{r.code}</strong> },
                  { key: "name", header: "Tên" },
                  { key: "province", header: "Tỉnh" },
                  { key: "note", header: "Ghi chú", render: (r) => r.note ?? "—" },
                ]}
                rows={list}
                rowKey={(r) => `${r.code}-${r.province}`}
                empty="—"
                maxHeight={240}
              />
            </div>
          ))
        )}
      </section>

      <Modal
        open={Boolean(selected)}
        title={`Tỉnh ${selected?.name ?? ""} (${selected?.code ?? ""}) — ${effectiveEra}`}
        onClose={() => setSelected(null)}
        width={880}
      >
        {selected && (
          <>
            {provinceDetail.error && (
              <ErrorNotice error={provinceDetail.error} onRetry={provinceDetail.reload} />
            )}
            {provinceDetail.data && (
              <table style={{ fontSize: 13, width: "100%", marginBottom: 12 }}>
                <tbody>
                  {Object.entries(provinceDetail.data)
                    .filter(([k]) => !k.startsWith("_"))
                    .slice(0, 14)
                    .map(([k, v]) => (
                      <tr key={k}>
                        <td style={{ padding: "3px 10px 3px 0", color: "#607d8b", width: 170 }}>
                          {k}
                        </td>
                        <td style={{ padding: "3px 0" }}>
                          {typeof v === "object" && v !== null
                            ? JSON.stringify(v)
                            : String(v ?? "—")}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}

            <h3 style={{ marginTop: 0 }}>
              Ánh xạ xã ({wardMap.data?.mappings?.length ?? 0} bản ghi) — era{" "}
              <code>{effectiveEra}</code>
            </h3>
            {wardMap.error && <ErrorNotice error={wardMap.error} onRetry={wardMap.reload} />}
            <DataTable
              columns={[
                {
                  key: "old",
                  header: "Xã cũ",
                  render: (m: WardMapping) =>
                    m.old ? `${m.old.ward_name ?? "—"} (${m.old.ward_code ?? "?"})` : "—",
                },
                {
                  key: "new",
                  header: "Xã mới",
                  render: (m: WardMapping) =>
                    m.new ? `${m.new.ward_name ?? "—"} (${m.new.ward_code ?? "?"})` : "—",
                },
              ]}
              rows={wardMap.data?.mappings ?? []}
              rowKey={(m, i) => `${m.old?.ward_code ?? i}-${m.new?.ward_code ?? i}`}
              loading={wardMap.loading}
              empty="Không có ánh xạ xã cho tỉnh này ở thời kỳ đã chọn."
              maxHeight={360}
            />
          </>
        )}
      </Modal>
    </div>
  );
}
