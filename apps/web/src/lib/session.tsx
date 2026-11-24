"use client";

/**
 * Ngữ cảnh đăng nhập: giữ access/refresh token, thông tin người dùng và bảng quyền
 * (bitmask) để ẩn/hiện nút theo vai trò.
 *
 * Nguồn quyền: `GET /api/v1/auth/me` trả `perm` = `{collection: mask}` (và
 * `collection_perm`). Backend kiểm tra quyền thật ở mọi endpoint — frontend chỉ
 * dùng mask để **ẩn/hiện nút**, không phải để bảo mật.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api, onSessionExpired, tokenStore, ApiRequestError } from "./api";
import { PERM_FLAGS, can, maskOf, type PermTable } from "./permissions";
import type { MeResponse } from "./types";

export interface SessionState {
  /** Đang khôi phục phiên từ localStorage lúc khởi động. */
  loading: boolean;
  /** Đã đăng nhập (có access token hợp lệ). */
  authenticated: boolean;
  username: string;
  displayName: string;
  role: string;
  roles: string[];
  userId: string;
  permVer: number;
  /** Bảng quyền theo collection: `{collection: mask}`. */
  perm: PermTable;
  error: string | null;
}

export interface SessionApi extends SessionState {
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
  /** Có quyền `flag` trên `collection` không. */
  can: (collection: string, flag: number) => boolean;
  /** Mask quyền của một collection (đã áp dụng quy tắc `*` mặc định). */
  maskFor: (collection: string) => number;
}

const initial: SessionState = {
  loading: true,
  authenticated: false,
  username: "",
  displayName: "",
  role: "",
  roles: [],
  userId: "",
  permVer: 0,
  perm: {},
  error: null,
};

const SessionContext = createContext<SessionApi | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>(initial);

  const applyMe = useCallback((me: MeResponse) => {
    const perm: PermTable = {};
    // `collection_perm` là bản đầy đủ; `perm` cũng có sẵn — ưu tiên `collection_perm`.
    const source = me.collection_perm && Object.keys(me.collection_perm).length
      ? me.collection_perm
      : me.perm ?? {};
    for (const [k, v] of Object.entries(source)) perm[k] = Number(v) || 0;
    setState({
      loading: false,
      authenticated: true,
      username: me.username ?? "",
      displayName: me.display_name || me.username || "",
      role: me.role ?? "",
      roles: me.roles ?? [],
      userId: me.user_id ?? "",
      permVer: me.perm_ver ?? 0,
      perm,
      error: null,
    });
  }, []);

  const reload = useCallback(async () => {
    if (!tokenStore.getAccess()) {
      setState({ ...initial, loading: false });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    try {
      const me = await api.me();
      applyMe(me);
    } catch (err) {
      if (err instanceof ApiRequestError && err.isUnauthorized) {
        tokenStore.clear();
        setState({ ...initial, loading: false });
      } else {
        setState((s) => ({
          ...s,
          loading: false,
          error: err instanceof Error ? err.message : "Không đọc được thông tin người dùng",
        }));
      }
    }
  }, [applyMe]);

  // Khôi phục phiên lúc tải trang.
  useEffect(() => {
    void reload();
  }, [reload]);

  // Backend báo token hết hiệu lực (401) → xoá phiên và đưa về màn hình đăng nhập.
  useEffect(() => {
    return onSessionExpired(() => {
      setState({ ...initial, loading: false, error: "Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại." });
    });
  }, []);

  const login = useCallback(
    async (username: string, password: string) => {
      setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const tokens = await api.login(username, password);
        tokenStore.set(tokens);
        const me = await api.me();
        applyMe(me);
      } catch (err) {
        tokenStore.clear();
        const message = err instanceof Error ? err.message : "Đăng nhập thất bại";
        setState({ ...initial, loading: false, error: message });
        throw err;
      }
    },
    [applyMe],
  );

  const logout = useCallback(async () => {
    const refresh = tokenStore.getRefresh();
    try {
      await api.logout(refresh);
    } catch {
      // Đăng xuất ở phía server có thể thất bại (token hết hạn / API chết) —
      // vẫn xoá phiên cục bộ để không giữ token trên trình duyệt.
    }
    tokenStore.clear();
    setState({ ...initial, loading: false });
  }, []);

  const canFn = useCallback(
    (collection: string, flag: number) => can(state.perm, collection, flag),
    [state.perm],
  );

  const maskFor = useCallback(
    (collection: string) => maskOf(state.perm, collection),
    [state.perm],
  );

  const value = useMemo<SessionApi>(
    () => ({ ...state, login, logout, reload, can: canFn, maskFor }),
    [state, login, logout, reload, canFn, maskFor],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** Hook lấy ngữ cảnh phiên. */
export function useSession(): SessionApi {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession phải nằm trong <SessionProvider>");
  return ctx;
}

/** Hook tiện ích: quyền theo tên cờ (tiện hơn `can(coll, PERM_FLAGS.X)`). */
export function usePermission(): {
  can: (collection: string, flag: number) => boolean;
  canRead: (collection: string) => boolean;
  canUpdate: (collection: string) => boolean;
  canApprove: (collection: string) => boolean;
  canExport: (collection: string) => boolean;
  canRunJob: (collection: string) => boolean;
  canManageUsers: (collection: string) => boolean;
  canManageCache: (collection: string) => boolean;
  canRunForecast: (collection: string) => boolean;
  canManageModel: (collection: string) => boolean;
  canViewAudit: (collection: string) => boolean;
} {
  const { can: canFn } = useSession();
  return useMemo(
    () => ({
      can: canFn,
      canRead: (c) => canFn(c, PERM_FLAGS.READ),
      canUpdate: (c) => canFn(c, PERM_FLAGS.UPDATE),
      canApprove: (c) => canFn(c, PERM_FLAGS.APPROVE),
      canExport: (c) => canFn(c, PERM_FLAGS.EXPORT),
      canRunJob: (c) => canFn(c, PERM_FLAGS.RUN_JOB),
      canManageUsers: (c) => canFn(c, PERM_FLAGS.MANAGE_USERS),
      canManageCache: (c) => canFn(c, PERM_FLAGS.MANAGE_CACHE),
      canRunForecast: (c) => canFn(c, PERM_FLAGS.RUN_FORECAST),
      canManageModel: (c) => canFn(c, PERM_FLAGS.MANAGE_MODEL),
      canViewAudit: (c) => canFn(c, PERM_FLAGS.VIEW_AUDIT),
    }),
    [canFn],
  );
}
