"use client";

/**
 * Màn hình **Danh mục trạm**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/stations` — danh sách trạm (lọc theo loại / tên).
 * - `GET /api/v1/stations/{code}` — chi tiết một trạm (kèm `alarm_history`).
 * - `PATCH /api/v1/stations/{code}` — sửa tên, tọa độ (`location` = GeoJSON Point
 *   `[lon, lat]`), `merit_comid` (gán COMID), `river`, `datum`, `note`.
 *   - 423 nếu đang bị người khác sửa; 409 nếu `version` lệch.
 * - `GET /api/v1/trace/{comid}` — lần dòng chảy theo COMID (dùng để kiểm tra COMID đã gán).
 */

import React, { useMemo, useState } from "react";

import LeafletMap, { type MapPoint } from "@/components/Map";
import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  Field,
  InfoNotice,
  Modal,
  inputStyle,
  type Column,
} from "@/components/ui";
import { api, toError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { AlarmSegment, Station } from "@/lib/types";

export default function StationCatalogPage() {
  const { canUpdate } = usePermission();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [editing, setEditing] = useState<Station | null>(null);
  const [lon, setLon] = useState("");
  const [lat, setLat] = useState("");
  const [comid, setComid] = useState("");
  const [name, setName] = useState("");
  const [river, setRiver] = useState("");
  const [datum, setDatum] = useState("");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);

  const stations = useAsync<{ items: Station[]; total: number }>(
    () => api.stations({ q: q || undefined, kind: kind || undefined, limit: 1000 }),
    [q, kind],
  );

  /** Điểm trên bản đồ (đã có tọa độ). */
  const points = useMemo<MapPoint[]>(
    () =>
      (stations.data?.items ?? [])
        .filter((s) => Array.isArray(s.location) && s.location.length >= 2)
        .map((s) => ({
          id: s.code,
          lat: Number(s.location![1]),
          lon: Number(s.location![0]),
          label: `${s.code} — ${s.name}`,
          popup: (
            <div style={{ fontSize: 12 }}>
              <strong>{s.code}</strong> — {s.name}
              <br />
              Kinh độ: {Number(s.location![0]).toFixed(5)}
              <br />
              Vĩ độ: {Number(s.location![1]).toFixed(5)}
              <br />
              COMID: {s.merit_comid ?? "—"}
            </div>
          ),
        })),
    [stations.data],
  );

  function openEdit(s: Station) {
    setEditing(s);
    setLon(s.location ? String(s.location[0]) : "");
    setLat(s.location ? String(s.location[1]) : "");
    setComid(s.merit_comid != null ? String(s.merit_comid) : "");
    setName(s.name ?? "");
    setRiver(s.river ?? "");
    setDatum(s.datum ?? "");
    setNote(s.note ?? "");
    setReason("");
    setErr(null);
    setPicking(false);
  }

  /** Chọn toạ độ mới bằng cách bấm vào bản đồ. */
  function onPick(point: MapPoint) {
    if (!picking) return;
    setLon(point.lon.toFixed(6));
    setLat(point.lat.toFixed(6));
    setPicking(false);
  }

  async function save() {
    if (!editing) return;
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = { reason };
      if (name.trim() && name !== editing.name) body.name = name.trim();
      if (lon.trim() && lat.trim()) {
        body.location = [Number(lon), Number(lat)];
      }
      if (comid.trim()) body.merit_comid = Number(comid);
      if (river.trim()) body.river = river.trim();
      if (datum.trim()) body.datum = datum.trim();
      if (note.trim()) body.note = note.trim();
      if (editing.version) body.version = editing.version;
      await api.patchStation(editing.code, body);
      setOk(`Đã cập nhật trạm ${editing.code}.`);
      setEditing(null);
      stations.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<Station>[] = [
    { key: "code", header: "Mã", render: (s) => <strong>{s.code}</strong> },
    { key: "name", header: "Tên" },
    { key: "kind", header: "Loại", render: (s) => s.kind ?? "—" },
    { key: "river", header: "Sông", render: (s) => s.river ?? "—" },
    {
      key: "location",
      header: "Tọa độ (lon, lat)",
      render: (s) =>
        s.location ? (
          <code style={{ fontSize: 11 }}>
            {Number(s.location[0]).toFixed(5)}, {Number(s.location[1]).toFixed(5)}
          </code>
        ) : (
          <Badge color="#ef6c00">Chưa có</Badge>
        ),
    },
    {
      key: "merit_comid",
      header: "COMID",
      align: "right",
      render: (s) => s.merit_comid ?? "—",
    },
    { key: "datum", header: "Datum", render: (s) => s.datum ?? "—" },
    {
      key: "alarm_history",
      header: "Ngưỡng báo động",
      render: (s) => (
        <AlarmHistoryBadge segments={s.alarm_history ?? []} />
      ),
    },
    {
      key: "action",
      header: "",
      render: (s) =>
        canUpdate(COLLECTIONS.stations) ? (
          <Button variant="primary" onClick={() => openEdit(s)}>
            Sửa
          </Button>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>Chỉ đọc</span>
        ),
    },
  ];

  return (
    <div>
      <h1>Danh mục trạm</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Sửa toạ độ, gán <code>COMID</code> (mã đoạn sông theo MERIT) và xem lịch sử
        ngưỡng báo động theo thời gian của từng trạm.
      </p>

      {stations.error && <ErrorNotice error={stations.error} onRetry={stations.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}

      <section
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: 14,
          marginBottom: 16,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Vị trí các trạm</h2>
        {points.length > 0 ? (
          <LeafletMap points={points} fitToPoints height={440} />
        ) : (
          <InfoNotice kind="warn">
            Không có trạm nào có toạ độ. Dùng nút “Sửa” để nhập toạ độ (lon, lat) theo
            chuẩn GeoJSON.
          </InfoNotice>
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
        <div style={{ display: "flex", gap: 14, alignItems: "flex-end", marginBottom: 12 }}>
          <div style={{ minWidth: 220 }}>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Tìm theo tên/mã</div>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="ví dụ: Vung Tau"
              style={{ ...inputStyle, width: "auto" }}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Loại</div>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              style={{ ...inputStyle, width: "auto" }}
            >
              <option value="">Tất cả</option>
              <option value="tide">Triều</option>
              <option value="rain">Mưa</option>
              <option value="reservoir">Hồ chứa</option>
            </select>
          </div>
          <Button onClick={stations.reload}>Tải lại</Button>
          <div style={{ fontSize: 12, color: "#607d8b" }}>
            {stations.data?.items.length ?? 0} / {stations.data?.total ?? 0} trạm
          </div>
        </div>

        <DataTable
          columns={columns}
          rows={stations.data?.items ?? []}
          rowKey={(s) => s.code}
          loading={stations.loading}
          empty="Không có trạm nào khớp bộ lọc."
          maxHeight={520}
        />
      </section>

      <Modal
        open={Boolean(editing)}
        title={`Sửa trạm — ${editing?.code ?? ""}`}
        onClose={() => setEditing(null)}
        width={820}
      >
        {editing && (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 16,
                marginBottom: 8,
              }}
            >
              <div>
                <Field label="Tên trạm">
                  <input value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} />
                </Field>
                <Field label="Sông">
                  <input value={river} onChange={(e) => setRiver(e.target.value)} style={inputStyle} />
                </Field>
                <Field label="Datum (mốc đo)">
                  <input value={datum} onChange={(e) => setDatum(e.target.value)} style={inputStyle} />
                </Field>
                <Field label="COMID (đoạn sông MERIT)">
                  <input
                    type="number"
                    value={comid}
                    onChange={(e) => setComid(e.target.value)}
                    style={inputStyle}
                  />
                </Field>
              </div>
              <div>
                <Field
                  label="Toạ độ (GeoJSON Point: lon, lat)"
                  hint="Ví dụ Hội An: 108.33812, 13.88262 (kinh độ trước, vĩ độ sau)"
                >
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      type="number"
                      step="0.000001"
                      value={lon}
                      onChange={(e) => setLon(e.target.value)}
                      placeholder="Kinh độ"
                      style={inputStyle}
                    />
                    <input
                      type="number"
                      step="0.000001"
                      value={lat}
                      onChange={(e) => setLat(e.target.value)}
                      placeholder="Vĩ độ"
                      style={inputStyle}
                    />
                  </div>
                </Field>
                <Button
                  variant={picking ? "primary" : "default"}
                  onClick={() => setPicking((v) => !v)}
                >
                  {picking ? "Đang chọn — bấm vào bản đồ…" : "Chọn toạ độ trên bản đồ"}
                </Button>
                <Field label="Ghi chú">
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={3}
                    style={inputStyle}
                  />
                </Field>
              </div>
            </div>

            {picking && (
              <div style={{ marginBottom: 12 }}>
                <LeafletMap
                  points={points}
                  center={
                    lon && lat
                      ? [Number(lat), Number(lon)]
                      : editing.location
                        ? [Number(editing.location[1]), Number(editing.location[0])]
                        : undefined
                  }
                  zoom={editing.location ? 11 : 6}
                  onPointClick={onPick}
                  height={320}
                />
              </div>
            )}

            {editing.alarm_history && editing.alarm_history.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                  Lịch sử ngưỡng báo động
                </div>
                <AlarmHistoryTable segments={editing.alarm_history} />
              </div>
            )}

            <Field label="Lý do sửa (ghi vào nhật ký)" hint="Bắt buộc có lý do để truy vết.">
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="ví dụ: hiệu chỉnh toạ độ theo khảo sát tháng 11/2026"
                style={inputStyle}
              />
            </Field>

            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <Button
                variant="primary"
                disabled={busy || !reason.trim()}
                onClick={() => void save()}
              >
                {busy ? "Đang lưu…" : "Lưu"}
              </Button>
              <Button onClick={() => setEditing(null)}>Hủy</Button>
            </div>
            {reason.trim() === "" && (
              <div style={{ fontSize: 12, color: "#ef6c00", marginTop: 6 }}>
                Cần nhập lý do sửa trước khi lưu.
              </div>
            )}
          </>
        )}
      </Modal>
    </div>
  );
}

/** Badge tóm tắt ngưỡng báo động hiện hành. */
function AlarmHistoryBadge({ segments }: { segments: AlarmSegment[] }) {
  if (!segments.length) return <span style={{ color: "#90a4ae" }}>—</span>;
  const now = new Date();
  const cur =
    segments.find(
      (s) =>
        (!s.from || new Date(s.from) <= now) && (!s.to || new Date(s.to) >= now),
    ) ?? segments[segments.length - 1];
  return (
    <span style={{ fontSize: 12 }}>
      BĐI {cur.level_1_m ?? "—"} / BĐII {cur.level_2_m ?? "—"} / BĐIII {cur.level_3_m ?? "—"}
      {segments.length > 1 && (
        <Badge color="#607d8b" title="Có nhiều kỳ ngưỡng">
          {" "}
          {segments.length} kỳ
        </Badge>
      )}
    </span>
  );
}

/** Bảng chi tiết lịch sử ngưỡng báo động. */
function AlarmHistoryTable({ segments }: { segments: AlarmSegment[] }) {
  return (
    <div style={{ maxHeight: 180, overflow: "auto", border: "1px solid var(--line)" }}>
      <table className="data-table" style={{ fontSize: 12 }}>
        <thead>
          <tr>
            <th>Từ</th>
            <th>Đến</th>
            <th>BĐ I</th>
            <th>BĐ II</th>
            <th>BĐ III</th>
          </tr>
        </thead>
        <tbody>
          {segments.map((s, i) => (
            <tr key={i}>
              <td>{formatDateTime(s.from)}</td>
              <td>{formatDateTime(s.to)}</td>
              <td>{s.level_1_m ?? "—"}</td>
              <td>{s.level_2_m ?? "—"}</td>
              <td>{s.level_3_m ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
