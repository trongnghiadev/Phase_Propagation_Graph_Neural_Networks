"use client";

/**
 * Layout của nhóm route đã đăng nhập (`(app)`).
 *
 * Màn hình đăng nhập nằm ở `src/app/dang-nhap/page.tsx` (ngoài nhóm này) để không
 * bị khung ứng dụng (thanh điều hướng) bao quanh.
 */

import AppShell from "@/components/AppShell";

export default function AppGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
