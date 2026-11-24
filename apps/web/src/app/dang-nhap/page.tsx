"use client";

/**
 * Màn hình đăng nhập.
 *
 * Gọi `POST /api/v1/auth/login` (xem `apps/api/src/api/router/auth.py`):
 * thân yêu cầu = `{ username, password }`; phản hồi = `access_token` (15 phút),
 * `refresh_token` (7 ngày), `expires_in`, `user_id`, `username`, `role`, `perm_ver`.
 *
 * Token được lưu trong `localStorage` (xem `src/lib/api.ts` → `tokenStore`) vì
 * backend chưa hỗ trợ cookie httpOnly. Hạn chế bảo mật đã ghi rõ trong README.
 */

import { useRouter } from "next/navigation";
import React, { useState } from "react";

import { ErrorNotice, Button, Field, inputStyle } from "@/components/ui";
import { ApiRequestError, toError } from "@/lib/api";
import { SessionProvider, useSession } from "@/lib/session";

function LoginForm() {
  const { login, error: sessionError } = useSession();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
      router.replace("/");
    } catch (err) {
      setError(toError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #08306b 0%, #0d47a1 55%, #1565c0 100%)",
        padding: 20,
      }}
    >
      <div
        style={{
          background: "#fff",
          borderRadius: 10,
          width: "100%",
          maxWidth: 400,
          padding: 28,
          boxShadow: "0 16px 48px rgba(0,0,0,0.35)",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 18 }}>
          <div style={{ fontSize: 30 }}>🌊</div>
          <h1 style={{ margin: "6px 0 2px", fontSize: 18 }}>
            Dự báo đỉnh mực nước
          </h1>
          <div style={{ color: "#607d8b", fontSize: 13 }}>
            GNN lan truyền pha — Đồng Nai · Sài Gòn · Vàm Cỏ
          </div>
        </div>

        {error ? (
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        ) : sessionError ? (
          <ErrorNotice error={sessionError} />
        ) : null}

        <form onSubmit={onSubmit}>
          <Field label="Tên đăng nhập">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              autoFocus
              style={inputStyle}
              placeholder="viewer_seed"
            />
          </Field>
          <Field label="Mật khẩu">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              style={inputStyle}
            />
          </Field>
          <div style={{ marginTop: 14 }}>
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Đang đăng nhập…" : "Đăng nhập"}
            </Button>
          </div>
        </form>

        {error instanceof ApiRequestError && error.status === 423 && (
          <div style={{ marginTop: 10, fontSize: 13, color: "#7a4b00" }}>
            Tài khoản đang bị khoá tạm do đăng nhập sai nhiều lần. Thử lại sau ít phút.
          </div>
        )}

        <div
          style={{
            marginTop: 20,
            paddingTop: 14,
            borderTop: "1px solid #eceff3",
            fontSize: 12,
            color: "#78909c",
          }}
        >
          Tài khoản demo do <code>api.auth.seed</code> sinh (mật khẩu ngẫu nhiên, chỉ
          hiển thị một lần khi bootstrap). Không ghi mật khẩu vào repo — xem
          <code> apps/web/README.md</code>.
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <SessionProvider>
      <LoginForm />
    </SessionProvider>
  );
}
