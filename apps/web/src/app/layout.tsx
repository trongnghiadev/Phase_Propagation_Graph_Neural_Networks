import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Dự báo đỉnh mực nước — Đồng Nai · Sài Gòn · Vàm Cỏ",
  description:
    "Hệ thống quản lý dữ liệu phân tán tích hợp dự báo đỉnh mực nước trên mạng lưới sông " +
    "Việt Nam bằng mạng nơ-ron đồ thị lan truyền pha (GĐ8 frontend).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  );
}
