"use client";

/**
 * Khung ứng dụng: thanh điều hướng + kiểm tra phiên đăng nhập.
 *
 * Quy ước ẩn/hiện theo bitmask: mỗi mục điều hướng khai báo quyền tối thiểu cần có
 * (`collection` + `flag` trong `NAV`). Mục không đủ quyền thì **không hiện** (không chỉ
 * disable), đúng như yêu cầu "ẩn/hiện nút theo bitmask". Backend vẫn kiểm tra thật.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useEffect } from "react";

import { SessionProvider, useSession } from "@/lib/session";
import { PERM_FLAGS } from "@/lib/permissions";

interface NavItem {
  href: string;
  label: string;
  icon: string;
  /** Collection trên API dùng làm khoá quyền. */
  collection: string;
  /** Cờ quyền tối thiểu. */
  flag: number;
  group: string;
}

const NAV: NavItem[] = [
  { href: "/", label: "Tổng quan", icon: "📊", collection: "forecasts", flag: PERM_FLAGS.READ, group: "Dự báo" },
  { href: "/ban-do", label: "Bản đồ dự báo", icon: "🗺️", collection: "forecasts", flag: PERM_FLAGS.READ, group: "Dự báo" },
  { href: "/lan-truyen-pha", label: "Lan truyền pha", icon: "🌊", collection: "forecasts", flag: PERM_FLAGS.READ, group: "Dự báo" },
  { href: "/so-sanh-kttv", label: "So sánh KTTV", icon: "📈", collection: "forecasts", flag: PERM_FLAGS.READ, group: "Dự báo" },
  { href: "/canh-bao", label: "Cảnh báo", icon: "⚠️", collection: "alerts", flag: PERM_FLAGS.READ, group: "Dự báo" },
  { href: "/ban-tin", label: "Bản tin", icon: "📄", collection: "hydromet_bulletins", flag: PERM_FLAGS.READ, group: "Dữ liệu" },
  { href: "/qc", label: "QC số liệu", icon: "🔍", collection: "tide_extremes", flag: PERM_FLAGS.READ, group: "Dữ liệu" },
  { href: "/ho-chua", label: "Hồ chứa", icon: "💧", collection: "reservoir_daily", flag: PERM_FLAGS.READ, group: "Dữ liệu" },
  { href: "/danh-muc-tram", label: "Danh mục trạm", icon: "📍", collection: "stations", flag: PERM_FLAGS.READ, group: "Dữ liệu" },
  { href: "/dia-gioi", label: "Địa giới theo thời gian", icon: "🗓️", collection: "admin_units", flag: PERM_FLAGS.READ, group: "Dữ liệu" },
  { href: "/hang-doi-duyet", label: "Hàng đợi duyệt", icon: "✅", collection: "qc_flags", flag: PERM_FLAGS.READ, group: "Quản trị" },
  { href: "/jobs", label: "Job", icon: "⚙️", collection: "jobs", flag: PERM_FLAGS.READ, group: "Quản trị" },
  { href: "/mo-hinh", label: "Mô hình", icon: "🧠", collection: "models", flag: PERM_FLAGS.READ, group: "Quản trị" },
  { href: "/nhat-ky", label: "Nhật ký", icon: "📜", collection: "audit_logs", flag: PERM_FLAGS.READ, group: "Quản trị" },
  { href: "/nguoi-dung", label: "Người dùng", icon: "👤", collection: "users", flag: PERM_FLAGS.READ, group: "Quản trị" },
  { href: "/he-thong", label: "Hệ thống", icon: "🖧", collection: "cache", flag: PERM_FLAGS.READ, group: "Quản trị" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, username, displayName, role, logout, can, error } =
    useSession();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !authenticated) {
      router.replace("/dang-nhap");
    }
  }, [loading, authenticated, router]);

  if (loading) {
    return (
      <div style={centerBox}>
        <div>Đang kiểm tra phiên đăng nhập…</div>
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div style={centerBox}>
        <div>
          <p>Phiên đăng nhập đã hết hạn. Đang chuyển tới màn hình đăng nhập…</p>
          {error && <p style={{ color: "#c62828" }}>{error}</p>}
        </div>
      </div>
    );
  }

  const visible = NAV.filter((n) => can(n.collection, n.flag));
  const groups = [...new Set(visible.map((n) => n.group))];

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          background: "linear-gradient(90deg, #08306b, #0d47a1)",
          color: "#fff",
          padding: "10px 18px",
          display: "flex",
          alignItems: "center",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div style={{ fontWeight: 700, fontSize: 15 }}>
            Dự báo đỉnh mực nước — GNN lan truyền pha
          </div>
          <div style={{ fontSize: 12, opacity: 0.85 }}>
            Hệ thống sông Đồng Nai – Sài Gòn – Vàm Cỏ
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 13 }}>
            {displayName || username}
            {role ? <span style={{ opacity: 0.75 }}> · {role}</span> : null}
          </span>
          <button
            type="button"
            onClick={() => void logout()}
            style={{
              background: "rgba(255,255,255,0.16)",
              color: "#fff",
              border: "1px solid rgba(255,255,255,0.4)",
              borderRadius: 4,
              padding: "4px 10px",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            Đăng xuất
          </button>
        </div>
      </header>

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <nav
          style={{
            width: 232,
            flexShrink: 0,
            background: "#fff",
            borderRight: "1px solid var(--line)",
            padding: "10px 0",
            overflowY: "auto",
          }}
        >
          {groups.map((g) => (
            <div key={g}>
              <div
                style={{
                  padding: "8px 16px 4px",
                  fontSize: 11,
                  textTransform: "uppercase",
                  letterSpacing: 0.6,
                  color: "#90a4ae",
                  fontWeight: 700,
                }}
              >
                {g}
              </div>
              {visible
                .filter((n) => n.group === g)
                .map((n) => {
                  const active = pathname === n.href;
                  return (
                    <Link
                      key={n.href}
                      href={n.href}
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "center",
                        padding: "7px 16px",
                        fontSize: 13,
                        color: active ? "#0d47a1" : "#37474f",
                        background: active ? "#e8f0fb" : "transparent",
                        borderLeft: active ? "3px solid #0d47a1" : "3px solid transparent",
                        fontWeight: active ? 700 : 400,
                        textDecoration: "none",
                      }}
                    >
                      <span aria-hidden>{n.icon}</span>
                      <span>{n.label}</span>
                    </Link>
                  );
                })}
            </div>
          ))}
        </nav>

        <main style={{ flex: 1, padding: 18, overflowX: "auto" }}>{children}</main>
      </div>

      <footer
        style={{
          borderTop: "1px solid var(--line)",
          background: "#fff",
          padding: "8px 18px",
          fontSize: 12,
          color: "#78909c",
        }}
      >
        Bản đồ nền ©{" "}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          tác giả OpenStreetMap
        </a>{" "}
        (ODbL) · Giao diện GĐ8 — quyền kiểm tra thật ở API, giao diện chỉ ẩn/hiện nút.
      </footer>
    </div>
  );
}

const centerBox: React.CSSProperties = {
  minHeight: "100vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  color: "#607d8b",
  textAlign: "center",
  padding: 24,
};

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <Shell>{children}</Shell>
    </SessionProvider>
  );
}
