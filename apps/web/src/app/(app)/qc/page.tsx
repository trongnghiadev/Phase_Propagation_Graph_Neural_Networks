"use client";

/**
 * Màn hình **QC số liệu**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/review?status=suspect` — cờ QC chưa quyết định (`qc_flags`).
 * - `GET /api/v1/stations/{code}/tides` — chuỗi đỉnh/chân triều của một trạm.
 * - `PATCH /api/v1/tides/{station}/{ts}` — quyết định QC cho một đỉnh/chân:
 *   `{status: rejected|corrected|confirmed_ok, corrected_value?, kind?, reason, version?}`.
 *   API tạo cờ thủ công nếu chưa có; khi cờ đã có thì **bắt buộc** `version`
 *   (thiếu → 428, lệch → 409). API không ghi trực tiếp `tide_extremes`: G0 áp ở lần chạy sau.
 * - `GET /api/v1/bulletins` — bản tin để mở PDF đối chiếu.
 */

import Link from "next/link";
import React, { useMemo, useState } from "react";

import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  Modal,
  inputStyle,
  type Column,
} from "@/components/ui";
import { ApiRequestError, api, toError } from "@/lib/api";
import { formatActor, formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";
import type { Bulletin, ReviewItem, TideDecision, TideItem } from "@/lib/types";

/** Một bản ghi triều cần quyết định QC. */
interface EditTarget extends TideItem {
  /** Mã trạm — bắt buộc để gọi `PATCH /tides/{station}/{ts}`. */
  station: string;
  /** Cờ QC `suspect` đang có cho phần tử này (nếu đã tải được). */
  flag?: ReviewItem | null;
}

/** Khoá ghép cờ QC ↔ phần tử triều: trạm + loại + thời điểm (so theo mili giây UTC). */
function tideKey(station: string | null | undefined, kind: string | null | undefined,
                 ts: string | null | undefined): string {
  const t = ts ? Date.parse(ts) : NaN;
  return `${station ?? ""}|${kind ?? ""}|${Number.isNaN(t) ? ts ?? "" : t}`;
}

export default function QcPage() {
  const { canUpdate, canApprove } = usePermission();
  const [station, setStation] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  // Cờ QC chưa quyết định (collection `qc_flags`, status = suspect).
  const flags = useAsync<{ count: number; items: ReviewItem[] }>(
    () => api.reviewQueue({ status: "suspect", limit: 500 }),
    [],
  );
  // Bản tin để mở PDF đối chiếu.
  const bulletins = useAsync<{ items: Bulletin[] }>(() => api.bulletins({ limit: 100 }), []);
  // Thực đo của trạm đang chọn.
  const tides = useAsync<{ station: string; count: number; items: TideItem[] }>(
    () =>
      station.trim()
        ? api.stationTides(station.trim(), { limit: 4000 })
        : Promise.resolve({ station: "", count: 0, items: [] as TideItem[] }),
    [station],
  );

  const flagByKey = useMemo(() => {
    const map = new Map<string, ReviewItem>();
    (flags.data?.items ?? []).forEach((f) => map.set(tideKey(f.station, f.kind, f.ts), f));
    return map;
  }, [flags.data]);

  const rows = useMemo(() => {
    const code = tides.data?.station || station.trim();
    const out: EditTarget[] = [];
    (tides.data?.items ?? []).forEach((t) => {
      const st = t.station ?? code;
      const flag = flagByKey.get(tideKey(st, t.kind, t.ts)) ?? null;
      if (showAll || flag || t.flag_version != null || (t.qc && t.qc !== "ok"))
        out.push({ ...t, station: st, flag });
    });
    return out;
  }, [tides.data, flagByKey, station, showAll]);

  const tideColumns: Column<EditTarget>[] = [
    { key: "station", header: "Trạm", render: (r) => <strong>{r.station}</strong> },
    { key: "ts", header: "Thời điểm", render: (r) => formatDateTime(r.ts) },
    {
      key: "value",
      header: "Mực nước (m)",
      align: "right",
      render: (r) => (typeof r.value === "number" ? r.value.toFixed(3) : "—"),
    },
    {
      key: "kind",
      header: "Loại",
      render: (r) => (r.kind === "high" ? "Triều cao" : r.kind === "low" ? "Triều thấp" : "—"),
    },
    {
      key: "qc",
      header: "QC (đã áp)",
      render: (r) =>
        r.qc && r.qc !== "ok" ? <Badge color="#ef6c00">{r.qc}</Badge> : <Badge color="#2e7d32">ok</Badge>,
    },
    {
      key: "flag",
      header: "Cờ chờ duyệt",
      render: (r) =>
        r.flag ? (
          <Badge color="#f9a825" title={r.flag.reason ?? undefined}>
            {(r.flag.rules ?? []).join(", ") || "suspect"} · v{r.flag.version}
          </Badge>
        ) : r.flag_version != null ? (
          <Badge color="#78909c" title="Cờ đã có quyết định (không còn chờ duyệt)">
            {r.flag_status ?? "đã quyết định"}
            {r.flag_retired ? " · đã gỡ" : ""} · v{r.flag_version}
          </Badge>
        ) : (
          "—"
        ),
    },
    {
      key: "flag_decided_by",
      header: "Người quyết định",
      render: (r) =>
        r.flag_decided_by
          ? `${formatActor(r.flag_decided_by, r.flag_decided_by_label)} · ${formatDateTime(r.flag_decided_at)}`
          : "—",
    },
    { key: "source_file", header: "Bản tin", render: (r) => r.source_file ?? "—" },
    {
      key: "action",
      header: "",
      render: (r) =>
        canUpdate(COLLECTIONS.tides) ? (
          <Button
            variant="primary"
            onClick={() => {
              setOk(null);
              setEditing(r);
            }}
          >
            Quyết định QC
          </Button>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>Chỉ đọc</span>
        ),
    },
  ];

  const flagColumns: Column<ReviewItem>[] = [
    { key: "station", header: "Trạm", render: (r) => r.station ?? "—" },
    { key: "ts", header: "Thời điểm", render: (r) => formatDateTime(r.ts) },
    { key: "kind", header: "Loại", render: (r) => r.kind ?? "—" },
    {
      key: "value",
      header: "Giá trị (m)",
      align: "right",
      render: (r) => (typeof r.value === "number" ? r.value.toFixed(3) : "—"),
    },
    { key: "rules", header: "Luật", render: (r) => (r.rules ?? []).join(", ") || r.origin || "—" },
    { key: "reason", header: "Lý do nghi ngờ", render: (r) => r.reason ?? "—" },
    { key: "version", header: "Phiên bản", align: "right" },
  ];

  return (
    <div>
      <h1>Kiểm soát chất lượng (QC)</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Quyết định QC cho từng đỉnh/chân triều (xác nhận đúng, hiệu chỉnh hoặc loại bỏ) kèm lý do.
        Quyết định ghi vào <code>qc_flags</code> và <code>audit_logs</code>; số liệu{" "}
        <code>tide_extremes</code> được cập nhật ở lần chạy G0 kế tiếp. Duyệt cờ theo hàng đợi ở màn
        hình{" "}
        <Link href="/hang-doi-duyet" style={{ color: "#1565c0" }}>
          Hàng đợi kiểm duyệt
        </Link>
        .
      </p>

      {flags.error && <ErrorNotice error={flags.error} onRetry={flags.reload} />}
      {tides.error && <ErrorNotice error={tides.error} onRetry={tides.reload} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}

      <section style={sectionStyle}>
        <h2 style={{ marginTop: 0 }}>Cờ QC chưa quyết định ({flags.data?.count ?? 0})</h2>
        {!canApprove(COLLECTIONS.qcFlags) && (
          <div style={{ fontSize: 12, color: "#78909c", marginBottom: 6 }}>
            Bạn không có quyền <code>APPROVE</code> trên <code>qc_flags</code>: chỉ xem.
          </div>
        )}
        <DataTable
          columns={flagColumns}
          rows={flags.data?.items ?? []}
          rowKey={(r) => r.id}
          loading={flags.loading}
          empty="Không có cờ QC nào đang chờ."
          maxHeight={300}
        />
      </section>

      <section style={sectionStyle}>
        <h2 style={{ marginTop: 0 }}>Quyết định QC theo trạm</h2>
        <div style={{ display: "flex", gap: 14, alignItems: "flex-end", marginBottom: 10, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Trạm (để tải chuỗi triều)</div>
            <input
              value={station}
              onChange={(e) => setStation(e.target.value)}
              placeholder="mã trạm, ví dụ: VUNGTAU"
              style={{ ...inputStyle, width: 220 }}
            />
          </div>
          <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            Hiện cả bản ghi đạt
          </label>
          <Button
            onClick={() => {
              tides.reload();
              flags.reload();
            }}
          >
            Tải lại
          </Button>
          <div style={{ fontSize: 12, color: "#607d8b" }}>{rows.length} bản ghi</div>
        </div>
        <DataTable
          columns={tideColumns}
          rows={rows}
          rowKey={(r, i) => `${r.station}-${r.kind}-${r.ts}-${i}`}
          loading={tides.loading}
          empty={
            station.trim()
              ? "Trạm này không có bản ghi nào cần rà soát."
              : "Nhập mã trạm để xem các bản ghi cần rà soát."
          }
          maxHeight={360}
        />
      </section>

      <section style={sectionStyle}>
        <h2 style={{ marginTop: 0 }}>Bản tin để đối chiếu PDF</h2>
        {bulletins.error && <ErrorNotice error={bulletins.error} onRetry={bulletins.reload} />}
        <DataTable
          columns={[
            { key: "source_file", header: "Tệp nguồn" },
            { key: "bulletin_date", header: "Ngày", render: (r) => formatDateTime(r.bulletin_date) },
            { key: "bulletin_type", header: "Loại", render: (r) => r.bulletin_type ?? "—" },
            {
              key: "warnings",
              header: "Cảnh báo parse",
              render: (r) => {
                const n = r.parse?.warnings?.length ?? 0;
                return n > 0 ? <Badge color="#ef6c00">{n}</Badge> : <Badge color="#2e7d32">0</Badge>;
              },
            },
            {
              key: "action",
              header: "",
              render: (r) => (
                <a
                  className="btn"
                  href={`/ban-tin?q=${encodeURIComponent(r.source_file)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Mở ở màn hình Bản tin
                </a>
              ),
            },
          ]}
          rows={(bulletins.data?.items ?? []).filter((b) => (b.parse?.warnings?.length ?? 0) > 0)}
          rowKey={(r) => r.source_file}
          loading={bulletins.loading}
          empty="Không có bản tin nào có cảnh báo parse."
          maxHeight={280}
        />
      </section>

      {editing && (
        <TideDecisionDialog
          target={editing}
          onClose={() => setEditing(null)}
          onDone={(msg) => {
            setEditing(null);
            setOk(msg);
            tides.reload();
            flags.reload();
          }}
        />
      )}
    </div>
  );
}

/**
 * Hộp thoại quyết định QC cho một phần tử triều.
 *
 * `version`: lấy từ `flag_version` mà `GET /stations/{code}/tides` trả kèm từng điểm có cờ QC
 * (kể cả cờ đã quyết định), dự phòng bằng cờ `suspect` của hàng đợi. Nhờ vậy không cần dựa vào 428.
 * Nếu API vẫn trả 428/409 (người khác vừa quyết định), hộp thoại ghi nhận phiên bản hiện tại mà API
 * báo và yêu cầu người dùng **bấm xác nhận lần nữa** — không tự gửi lại.
 */
function TideDecisionDialog({
  target,
  onClose,
  onDone,
}: {
  target: EditTarget;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const [decision, setDecision] = useState<TideDecision["status"]>("confirmed_ok");
  const [corrected, setCorrected] = useState(
    typeof target.value === "number" ? String(target.value) : "",
  );
  const [reason, setReason] = useState("");
  const [version, setVersion] = useState<number | undefined>(
    target.flag_version ?? target.flag?.version ?? undefined,
  );
  const [needsConfirm, setNeedsConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);

  const correctedNum = corrected.trim() === "" ? NaN : Number(corrected);
  const needsValue = decision === "corrected";
  const valid = reason.trim().length > 0 && (!needsValue || Number.isFinite(correctedNum));
  const kind = target.kind === "high" || target.kind === "low" ? target.kind : undefined;

  async function save() {
    if (!target.ts) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await api.patchTide(target.station, target.ts, {
        status: decision,
        corrected_value: needsValue ? correctedNum : undefined,
        kind,
        reason: reason.trim(),
        version,
      });
      onDone(
        `Đã ghi quyết định QC cho ${target.station} @ ${formatDateTime(target.ts)}` +
          `${res.flag_created ? " (tạo cờ thủ công mới)" : ""}. ` +
          "Số liệu triều sẽ cập nhật sau lần chạy G0 kế tiếp.",
      );
    } catch (e) {
      const er = toError(e);
      if (er instanceof ApiRequestError && (er.status === 428 || er.status === 409)) {
        const cur = er.currentVersion;
        if (cur !== null) {
          setVersion(cur);
          setNeedsConfirm(true);
        }
      }
      setErr(er);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open title={`Quyết định QC — ${target.station} @ ${formatDateTime(target.ts)}`} onClose={onClose}>
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {needsConfirm && (
        <InfoNotice kind="warn">
          Phần tử này đã có cờ QC với phiên bản <strong>{version}</strong> (có thể người khác vừa quyết
          định). Hãy kiểm tra ở màn hình Hàng đợi kiểm duyệt; nếu vẫn muốn ghi đè, bấm{" "}
          <strong>Xác nhận ghi với phiên bản {version}</strong>.
        </InfoNotice>
      )}
      <div style={{ fontSize: 13, marginBottom: 10, color: "#607d8b" }}>
        Giá trị hiện tại:{" "}
        <strong>{typeof target.value === "number" ? `${target.value.toFixed(3)} m` : "—"}</strong>
        {target.kind ? ` · ${target.kind === "high" ? "đỉnh" : "chân"}` : ""}
        {target.source_file ? ` · bản tin: ${target.source_file}` : ""}
        {version !== undefined ? ` · phiên bản cờ: ${version}` : " · chưa có cờ (sẽ tạo cờ thủ công)"}
      </div>

      <label style={labelStyle}>Quyết định</label>
      <select
        value={decision}
        onChange={(e) => setDecision(e.target.value as TideDecision["status"])}
        style={inputStyle}
      >
        <option value="confirmed_ok">Xác nhận giá trị đúng (confirmed_ok)</option>
        <option value="corrected">Hiệu chỉnh giá trị (corrected)</option>
        <option value="rejected">Loại bỏ giá trị (rejected)</option>
      </select>

      {needsValue && (
        <>
          <label style={labelStyle}>Giá trị hiệu chỉnh (m, bắt buộc)</label>
          <input
            type="number"
            step="0.001"
            value={corrected}
            onChange={(e) => setCorrected(e.target.value)}
            style={inputStyle}
          />
        </>
      )}

      <label style={labelStyle}>Lý do (bắt buộc)</label>
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={3}
        maxLength={1000}
        placeholder="Ví dụ: đối chiếu bản tin PDF ngày 12/01, giá trị bị lệch 0.12 m"
        style={{ ...inputStyle, fontFamily: "inherit" }}
      />
      {!reason.trim() && (
        <div style={{ fontSize: 12, color: "#ef6c00", marginTop: 4 }}>Cần nhập lý do.</div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <Button
          variant={decision === "rejected" ? "danger" : "primary"}
          disabled={busy || !valid}
          onClick={() => void save()}
        >
          {busy ? "Đang lưu…" : needsConfirm ? `Xác nhận ghi với phiên bản ${version}` : "Ghi quyết định"}
        </Button>
        <Button onClick={onClose}>Hủy</Button>
      </div>
    </Modal>
  );
}

const sectionStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid var(--line)",
  borderRadius: 6,
  padding: 14,
  marginBottom: 16,
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 13,
  fontWeight: 600,
  margin: "10px 0 4px",
};
