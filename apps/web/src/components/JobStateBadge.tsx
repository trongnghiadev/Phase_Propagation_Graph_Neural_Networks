"use client";

/**
 * Badge trạng thái của tác vụ nền (`jobs`).
 *
 * Tách riêng khỏi trang `jobs/page.tsx` vì Next.js App Router chỉ cho phép export
 * mặc định (`default`) cùng một số trường đặc biệt (`metadata`, `generateStaticParams`…)
 * từ file trang — export thêm component sẽ làm build fail.
 */

import React from "react";

import { Badge } from "@/components/ui";

/** Màu badge theo trạng thái của tác vụ. */
export function JobStateBadge({ state }: { state?: string | null }) {
  const color =
    state === "done" || state === "success" || state === "succeeded"
      ? "#2e7d32"
      : state === "failed" || state === "error"
        ? "#c62828"
        : state === "running"
          ? "#1565c0"
          : state === "sample"
            ? "#f9a825"
            : "#78909c";
  return <Badge color={color}>{state ?? "—"}</Badge>;
}

export default JobStateBadge;
