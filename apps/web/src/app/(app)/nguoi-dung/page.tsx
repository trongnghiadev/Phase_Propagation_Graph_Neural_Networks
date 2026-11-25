"use client";

/**
 * Màn hình **Người dùng**.
 *
 * Endpoint dùng:
 * - `GET /api/v1/users` — danh sách tài khoản (cần quyền `MANAGE_USERS`).
 *   API trả `perm` = `{collection: [tên quyền]}` và `perm_default` = quyền mặc định (`*`).
 * - `PUT /api/v1/users/{id}/perm` — cập nhật quyền nguyên tử (một lệnh có điều kiện `perm_ver`):
 *   `{grant: [...], revoke: [...], collections: {coll: [flags]}, perm_ver, reason}`;
 *   `reason` bắt buộc; 409 nếu `perm_ver` lệch, 422 nếu tên quyền/collection sai, 423 nếu đang bị khoá.
 * - `PATCH /api/v1/users/{id}/active` — khoá/mở khoá tài khoản.
 *
 * Quy ước bitmask (12 bit) — xem `apps/api/src/api/permissions.py`:
 *   READ=1, CREATE=2, UPDATE=4, DELETE=8, EXPORT=16, APPROVE=32, RUN_JOB=64,
 *   VIEW_AUDIT=128, MANAGE_USERS=256, MANAGE_CACHE=512, RUN_FORECAST=1024,
 *   MANAGE_MODEL=2048.
 */

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
import { formatDateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { usePermission } from "@/lib/session";
import {
  COLLECTIONS,
  DEFAULT_ROLE_MASKS,
  PERM_FLAG_LABELS,
  PERM_FLAGS,
  type PermFlagName,
} from "@/lib/permissions";
import type { UserItem } from "@/lib/types";

/** Các collection thường dùng để gán quyền riêng. */
const COMMON_COLLECTIONS = [
  COLLECTIONS.stations,
  COLLECTIONS.tides,
  COLLECTIONS.bulletins,
  COLLECTIONS.bulletinFiles,
  COLLECTIONS.qcFlags,
  COLLECTIONS.forecasts,
  COLLECTIONS.models,
  COLLECTIONS.alerts,
  COLLECTIONS.reservoirs,
  COLLECTIONS.adminUnits,
  COLLECTIONS.jobs,
  COLLECTIONS.audit,
  COLLECTIONS.users,
  COLLECTIONS.cache,
];

export default function UsersPage() {
  const { canManageUsers } = usePermission();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<UserItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const users = useAsync<{ count: number; items: UserItem[] }>(
    () => api.users({ q: q || undefined, limit: 500 }),
    [q],
  );

  async function toggleActive(u: UserItem) {
    setBusy(true);
    setErr(null);
    try {
      await api.setUserActive(u._id ?? u.user_id ?? "", {
        active: !u.active,
        reason: u.active ? "Khoá tài khoản từ giao diện" : "Mở khoá tài khoản từ giao diện",
      });
      setOk(`Đã ${u.active ? "khoá" : "mở khoá"} tài khoản ${u.username}.`);
      users.reload();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<UserItem>[] = [
    { key: "username", header: "Tên đăng nhập", render: (u) => <strong>{u.username}</strong> },
    { key: "display_name", header: "Tên hiển thị", render: (u) => u.display_name ?? "—" },
    {
      key: "roles",
      header: "Vai trò",
      render: (u) =>
        (u.roles ?? []).length ? (
          (u.roles ?? []).map((r) => (
            <Badge key={r} color="#455a64">
              {r} ({DEFAULT_ROLE_MASKS[r] ?? "?"})
            </Badge>
          ))
        ) : (
          <span style={{ color: "#90a4ae" }}>—</span>
        ),
    },
    {
      key: "active",
      header: "Trạng thái",
      render: (u) => (
        <Badge color={u.active ? "#2e7d32" : "#c62828"}>{u.active ? "Hoạt động" : "Đã khoá"}</Badge>
      ),
    },
    {
      key: "perm",
      header: "Quyền mặc định (*)",
      render: (u) => (
        <span style={{ fontSize: 11 }}>
          {(u.perm_default ?? []).map((f) => PERM_FLAG_LABELS[f as PermFlagName] ?? f).join(", ") ||
            "—"}
        </span>
      ),
    },
    { key: "perm_ver", header: "Phiên bản quyền", align: "right", render: (u) => u.perm_ver ?? "—" },
    { key: "last_login", header: "Đăng nhập cuối", render: (u) => formatDateTime(u.last_login) },
    {
      key: "action",
      header: "",
      render: (u) =>
        canManageUsers(COLLECTIONS.users) ? (
          <div style={{ display: "flex", gap: 6 }}>
            <Button variant="primary" onClick={() => setEditing(u)}>
              Quyền
            </Button>
            <Button disabled={busy} onClick={() => void toggleActive(u)}>
              {u.active ? "Khoá" : "Mở khoá"}
            </Button>
          </div>
        ) : (
          <span style={{ fontSize: 12, color: "#90a4ae" }}>Chỉ đọc</span>
        ),
    },
  ];

  return (
    <div>
      <h1>Người dùng</h1>
      <p style={{ color: "#607d8b", marginTop: 0 }}>
        Quyền được lưu dạng <b>bitmask 12 bit</b>. Khi đổi quyền, <code>perm_ver</code> tăng
        nên token đang dùng mất hiệu lực ngay — người dùng phải đăng nhập lại.
      </p>

      {users.error && <ErrorNotice error={users.error} onRetry={users.reload} />}
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {ok && <InfoNotice kind="success">{ok}</InfoNotice>}
      {!canManageUsers(COLLECTIONS.users) && (
        <InfoNotice kind="warn">
          Bạn cần quyền <code>MANAGE_USERS</code> để quản lý tài khoản.
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
          alignItems: "flex-end",
        }}
      >
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>Tìm</div>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="tên đăng nhập hoặc tên hiển thị"
            style={{ ...inputStyle, width: 240 }}
          />
        </div>
        <Button onClick={users.reload}>Tải lại</Button>
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#607d8b" }}>
          {users.data?.count ?? 0} tài khoản
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
        <DataTable
          columns={columns}
          rows={users.data?.items ?? []}
          rowKey={(u) => u._id ?? u.user_id ?? u.username}
          loading={users.loading}
          empty="Không có tài khoản nào."
          maxHeight={520}
        />
      </section>

      {editing && (
        <PermissionEditor
          user={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setOk(`Đã cập nhật quyền của ${editing.username}.`);
            setEditing(null);
            users.reload();
          }}
          onConflict={() => {
            setEditing(null);
            users.reload();
          }}
        />
      )}
    </div>
  );
}

/**
 * Hộp thoại sửa quyền.
 *
 * Người dùng chọn:
 * - quyền mặc định (`*`) — tên quyền bật/tắt;
 * - quyền riêng theo collection.
 *
 * Gửi lên API dạng `grant` / `revoke` (danh sách **tên quyền**) — backend chuyển sang
 * mask và cập nhật nguyên tử bằng `$bit`.
 */
function PermissionEditor({
  user,
  onClose,
  onSaved,
  onConflict,
}: {
  user: UserItem;
  onClose: () => void;
  onSaved: () => void;
  onConflict: () => void;
}) {
  const [defaultFlags, setDefaultFlags] = useState<Set<string>>(
    new Set(user.perm_default ?? []),
  );
  const [collections, setCollections] = useState<Record<string, Set<string>>>(() => {
    const out: Record<string, Set<string>> = {};
    Object.entries(user.perm ?? {}).forEach(([coll, flags]) => {
      if (coll === "*") return;
      out[coll] = new Set(flags);
    });
    return out;
  });
  const [activeColl, setActiveColl] = useState<string>("stations");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);

  /** Quyền riêng của collection đang chọn. */
  const collFlags = collections[activeColl] ?? new Set<string>();

  function toggleDefault(flag: string) {
    setDefaultFlags((prev) => {
      const next = new Set(prev);
      if (next.has(flag)) next.delete(flag);
      else next.add(flag);
      return next;
    });
  }

  function toggleColl(flag: string) {
    setCollections((prev) => {
      const next = { ...prev };
      const cur = new Set(next[activeColl] ?? []);
      if (cur.has(flag)) cur.delete(flag);
      else cur.add(flag);
      next[activeColl] = cur;
      return next;
    });
  }

  /** Tính `grant`/`revoke` so với trạng thái ban đầu. */
  const diff = useMemo(() => {
    const initialDefault = new Set(user.perm_default ?? []);
    const grant: string[] = [];
    const revoke: string[] = [];
    defaultFlags.forEach((f) => {
      if (!initialDefault.has(f)) grant.push(f);
    });
    initialDefault.forEach((f) => {
      if (!defaultFlags.has(f)) revoke.push(f);
    });

    const collPayload: Record<string, string[]> = {};
    Object.entries(collections).forEach(([coll, flags]) => {
      const initial = new Set((user.perm ?? {})[coll] ?? []);
      const g: string[] = [];
      const r: string[] = [];
      flags.forEach((f) => {
        if (!initial.has(f)) g.push(f);
      });
      initial.forEach((f) => {
        if (!flags.has(f)) r.push(f);
      });
      if (g.length || r.length) collPayload[coll] = [...flags];
    });
    return { grant, revoke, collPayload };
  }, [defaultFlags, collections, user]);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await api.updateUserPerm(user._id ?? user.user_id ?? "", {
        grant: diff.grant,
        revoke: diff.revoke,
        collections: Object.keys(diff.collPayload).length ? diff.collPayload : undefined,
        // Khoá lạc quan: `perm_ver` lúc mở hộp thoại (409 nếu người khác vừa đổi quyền).
        perm_ver: user.perm_ver ?? 1,
        reason: reason.trim(),
      });
      onSaved();
    } catch (e) {
      setErr(toError(e));
    } finally {
      setBusy(false);
    }
  }

  const allFlagNames = Object.keys(PERM_FLAGS) as PermFlagName[];
  const changed =
    diff.grant.length > 0 || diff.revoke.length > 0 || Object.keys(diff.collPayload).length > 0;
  const conflict = err instanceof ApiRequestError && err.status === 409;

  return (
    <Modal
      open
      title={`Quyền của ${user.username}`}
      onClose={onClose}
      width={900}
    >
      {err && <ErrorNotice error={err} onDismiss={() => setErr(null)} />}
      {conflict && (
        <div style={{ marginBottom: 10 }}>
          <Button onClick={onConflict}>Đóng và tải lại danh sách (quyền đã bị người khác đổi)</Button>
        </div>
      )}
      <div style={{ fontSize: 12, color: "#607d8b", marginBottom: 8 }}>
        Phiên bản quyền đã xem: <code>{user.perm_ver ?? 1}</code>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        <div>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Quyền mặc định (collection <code>*</code>)</h3>
          <p style={{ fontSize: 12, color: "#607d8b", marginTop: 0 }}>
            Áp dụng cho mọi collection chưa khai báo riêng.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {allFlagNames.map((f) => {
              const on = defaultFlags.has(f);
              return (
                <button
                  key={f}
                  type="button"
                  onClick={() => toggleDefault(f)}
                  title={`${f} = ${PERM_FLAGS[f]}`}
                  style={{
                    padding: "3px 9px",
                    borderRadius: 12,
                    fontSize: 12,
                    cursor: "pointer",
                    border: `1px solid ${on ? "#1565c0" : "#cfd8dc"}`,
                    background: on ? "#e3f2fd" : "#fff",
                    color: on ? "#0d47a1" : "#78909c",
                    fontWeight: on ? 700 : 400,
                  }}
                >
                  {PERM_FLAG_LABELS[f]}{" "}
                  <code style={{ fontSize: 10, opacity: 0.7 }}>{PERM_FLAGS[f]}</code>
                </button>
              );
            })}
          </div>
          <div style={{ fontSize: 12, color: "#607d8b", marginTop: 8 }}>
            Mask hiện tại:{" "}
            <code>
              {[...defaultFlags].reduce(
                (acc, f) => acc | (PERM_FLAGS[f as PermFlagName] ?? 0),
                0,
              )}
            </code>
          </div>
        </div>

        <div>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Quyền riêng theo collection</h3>
          <select
            value={activeColl}
            onChange={(e) => setActiveColl(e.target.value)}
            style={{ ...inputStyle, marginBottom: 8 }}
          >
            {COMMON_COLLECTIONS.map((c) => (
              <option key={c} value={c}>
                {c}
                {collections[c] ? " *" : ""}
              </option>
            ))}
          </select>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {allFlagNames.map((f) => {
              const on = collFlags.has(f);
              return (
                <button
                  key={f}
                  type="button"
                  onClick={() => toggleColl(f)}
                  title={`${f} = ${PERM_FLAGS[f]}`}
                  style={{
                    padding: "3px 9px",
                    borderRadius: 12,
                    fontSize: 12,
                    cursor: "pointer",
                    border: `1px solid ${on ? "#2e7d32" : "#cfd8dc"}`,
                    background: on ? "#e8f5e9" : "#fff",
                    color: on ? "#1b5e20" : "#78909c",
                    fontWeight: on ? 700 : 400,
                  }}
                >
                  {PERM_FLAG_LABELS[f]}{" "}
                  <code style={{ fontSize: 10, opacity: 0.7 }}>{PERM_FLAGS[f]}</code>
                </button>
              );
            })}
          </div>
          <div style={{ fontSize: 12, color: "#607d8b", marginTop: 8 }}>
            Dấu <code>*</code> trong danh sách = collection đang có quyền riêng.
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
          Lý do đổi quyền (bắt buộc)
        </label>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={512}
          placeholder="ví dụ: bổ sung quyền duyệt cho vai trò reviewer"
          style={inputStyle}
        />
        {!reason.trim() && (
          <div style={{ fontSize: 12, color: "#ef6c00", marginTop: 4 }}>
            Cần nhập lý do (ghi vào nhật ký thay đổi).
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <Button
          variant="primary"
          disabled={busy || !changed || !reason.trim()}
          onClick={() => void save()}
        >
          {busy ? "Đang lưu…" : "Lưu quyền"}
        </Button>
        <Button onClick={onClose}>Hủy</Button>
      </div>
      <div style={{ fontSize: 12, color: "#78909c", marginTop: 8 }}>
        Thay đổi sẽ làm token hiện tại của tài khoản này mất hiệu lực (perm_ver tăng).
      </div>
    </Modal>
  );
}
