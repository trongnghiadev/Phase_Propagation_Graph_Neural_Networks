"use client";

/**
 * Màn hình **Hệ thống** (quản trị vận hành).
 *
 * Endpoint dùng:
 * - `GET /api/v1/admin/cache` — thống kê cache (hit/miss, số khoá, TTL).
 * - `DELETE /api/v1/admin/cache` — xoá cache theo `prefix` và/hoặc tăng phiên bản
 *   `ver:{collection}` để vô hiệu cache cũ (cache-aside theo phiên bản).
 * - `GET /api/v1/admin/locks` — các khoá phân tán đang giữ (quét Redis bằng `SCAN`),
 *   kèm fencing token.
 * - `GET /api/v1/admin/health` — tình trạng MongoDB / Redis / khoá.
 *
 * Cần quyền `MANAGE_CACHE` cho cache và khoá; `READ` trên `system` cho health.
 */

import React, { useState } from "react";

import {
  Badge,
  Button,
  DataTable,
  ErrorNotice,
  InfoNotice,
  inputStyle,
  type Column,
} from "@/components/ui";
import { api, toError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import { COLLECTIONS } from "@/lib/permissions";

/** Một khoá phân tán (shape từ `vngis.locks`). */
interface LockRow {
  resource?: string;
  owner?: string;
  fence?: number | number[];
  acquired_at?: string;
  ttl_ms?: number;
  [k: string]: unknown;
}

export default function SystemPage() {
  const { canManageCache } = usePermission();
  const [prefix, setPrefix] = useState("");
  const [collection, setCollection] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const cache = useAsync<{ stats: Record<string, unknown>; info: Record<string, unknown> }>(
    () => api.cacheInfo(),
    [],
  );
  const locks = useAsync<{ count: number; backend: string; items: LockRow[] }>(
    () => api.locks(200),
    [],
  );
  const health = useAsync<Record<string, unknown>>(() => api.adminHealth(), []);

  async function purge() {
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      const res = await api.purgeCache({
        prefix: prefix.trim() || undefined,
        collection: collection.trim() || undefined,
        bump_version: true,
      });
      setOk(
        `Đã xoá ${res.deleted_keys} khoá cache${
          res.bumped_version != null ? `, phiên bản cache → ${res.bumped_version}` : ""
        }.`,
      );
      cache.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  const lockColumns: Column<LockRow>[] = [
    { key: "resource", header: "Tài nguyên", render: (l) => <code style={{ fontSize: 11 }}>{l.resource ?? "—"}</code> },
    { key: "owner", header: "Chủ sở hữu", render: (l) => l.owner ?? "—" },
    {
      key: "fence",
      header: "Fencing token",
      align: "right",
      render: (l) => (Array.isArray(l.fence) ? l.fence.join(", ") : (l.fence ?? "—")),
    },
    { key: "acquired_at", header: "Giữ lúc", render: (l) => formatDateTime(l.acquired_at) },
    {
      key: "ttl_ms",
      header: "TTL (ms)",
      align: "right",
      render: (l) => (l.ttl_ms != null ? String(l.ttl_ms) : "—"),
    },
  ];

  return (
    <div>
      <h1>Hệ thống</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Quản trị cache Redis và khoá phân tán của cụm. Xoá cache an toàn: tăng phiên bản{" "}
        <code>ver:{'{collection}'}</code> làm mọi khoá cache cũ mất hiệu lực mà không cần
        quét toàn bộ.
      </p>

      {cache.error && <ErrorNotice error={cache.error} onRetry={cache.reload} />}
      {locks.error && <ErrorNotice error={locks.error} onRetry={locks.reload} />}
      {health.error && <ErrorNotice error={health.error} onRetry={health.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}
      {!canManageCache(COLLECTIONS.cache) && (
        <InfoNotice kind="warn">
          Bạn cần quyền <code>MANAGE_CACHE</code> để xoá cache và xem khoá.
        </InfoNotice>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
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
          <h2 style={{ marginTop: 0 }}>Tình trạng hệ thống</h2>
          {health.data && (
            <table style={{ fontSize: 13, width: "100%" }}>
              <tbody>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b", width: 150 }}>
                    MongoDB
                  </td>
                  <td style={{ padding: "3px 0" }}>
                    <HealthTag
                      ok={Boolean((health.data.mongodb as { ok?: boolean })?.ok)}
                      detail={JSON.stringify(health.data.mongodb)}
                    />
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b" }}>Redis</td>
                  <td style={{ padding: "3px 0" }}>
                    <HealthTag
                      ok={Boolean((health.data.redis as { ok?: boolean })?.ok)}
                      detail={JSON.stringify(health.data.redis)}
                    />
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b" }}>Backend khoá</td>
                  <td style={{ padding: "3px 0" }}>
                    <code>{String(health.data.lock_backend ?? "—")}</code>
                  </td>
                </tr>
                <tr>
                  <td style={{ padding: "3px 10px 3px 0", color: "#607d8b" }}>Instance</td>
                  <td style={{ padding: "3px 0" }}>
                    <code style={{ fontSize: 11 }}>{String(health.data.instance ?? "—")}</code>
                  </td>
                </tr>
              </tbody>
            </table>
          )}
          <div style={{ marginTop: 10 }}>
            <Button onClick={health.reload}>Kiểm tra lại</Button>
          </div>
        </section>

        <section
          style={{
            background: "#fff",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: 14,
          }}
        >
          <h2 style={{ marginTop: 0 }}>Cache</h2>
          {cache.data && (
            <>
              <div style={{ fontSize: 13, marginBottom: 8 }}>
                <div style={{ fontSize: 12, color: "#607d8b", marginBottom: 4 }}>Thống kê</div>
                <pre style={preStyle}>{JSON.stringify(cache.data.stats, null, 2)}</pre>
                <div style={{ fontSize: 12, color: "#607d8b", margin: "8px 0 4px" }}>
                  Thông tin
                </div>
                <pre style={preStyle}>{JSON.stringify(cache.data.info, null, 2)}</pre>
              </div>
            </>
          )}

          <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 180 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
                Tiền tố khoá Redis (tuỳ chọn)
              </div>
              <input
                value={prefix}
                onChange={(e) => setPrefix(e.target.value)}
                placeholder="ví dụ: cache:stations"
                style={inputStyle}
              />
            </div>
            <div style={{ flex: 1, minWidth: 180 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
                Collection (tăng phiên bản)
              </div>
              <input
                value={collection}
                onChange={(e) => setCollection(e.target.value)}
                placeholder="ví dụ: stations"
                style={inputStyle}
              />
            </div>
            <Button
              variant="danger"
              disabled={busy || !canManageCache(COLLECTIONS.cache)}
              onClick={() => void purge()}
            >
              {busy ? "Đang xoá…" : "Xoá cache"}
            </Button>
          </div>
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
        <h2 style={{ marginTop: 0 }}>
          Khoá phân tán đang giữ ({locks.data?.count ?? 0}) — backend{" "}
          <code>{locks.data?.backend ?? "—"}</code>
        </h2>
        <DataTable
          columns={lockColumns}
          rows={locks.data?.items ?? []}
          rowKey={(l, i) => `${l.resource ?? "lock"}-${i}`}
          loading={locks.loading}
          empty="Không có khoá nào đang được giữ."
          maxHeight={420}
        />
      </section>
    </div>
  );
}

/** Thẻ trạng thái có màu. */
function HealthTag({ ok, detail }: { ok: boolean; detail: string }) {
  return (
    <span title={detail}>
      <Badge color={ok ? "#2e7d32" : "#c62828"}>{ok ? "OK" : "Lỗi"}</Badge>
    </span>
  );
}

const preStyle: React.CSSProperties = {
  background: "#fafbfc",
  border: "1px solid var(--line)",
  borderRadius: 4,
  padding: 8,
  fontSize: 11,
  maxHeight: 180,
  overflow: "auto",
  margin: 0,
};
