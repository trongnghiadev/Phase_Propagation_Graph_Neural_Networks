"use client";

/**
 * Thẻ số liệu và khối nội dung dùng chung cho màn hình Tổng quan.
 *
 * Tách riêng khỏi `app/(app)/page.tsx` vì Next.js App Router chỉ cho phép export mặc
 * định (`default`) cùng một số trường đặc biệt từ file trang.
 */

import React from "react";

/** Thẻ số liệu lớn (tiêu đề – giá trị – ghi chú). */
export function Card({
  title,
  value,
  hint,
}: {
  title: string;
  value: string;
  hint?: string;
}) {
  return (
    <div
      style={{
        background: "#fff",
        border: "1px solid var(--line)",
        borderRadius: 6,
        padding: "12px 14px",
      }}
    >
      <div style={{ fontSize: 12, color: "#607d8b" }}>{title}</div>
      <div style={{ fontSize: 24, fontWeight: 700, margin: "2px 0" }}>{value}</div>
      {hint && <div style={{ fontSize: 11, color: "#90a4ae" }}>{hint}</div>}
    </div>
  );
}

/** Khối nội dung có tiêu đề và phần phụ (`extra`) bên phải. */
export function Section({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      style={{
        background: "#fff",
        border: "1px solid var(--line)",
        borderRadius: 6,
        padding: 14,
        marginBottom: 16,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 8,
        }}
      >
        <h2 style={{ margin: 0 }}>{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}

export default Card;
