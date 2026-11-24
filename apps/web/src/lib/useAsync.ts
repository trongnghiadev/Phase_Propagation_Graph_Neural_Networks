"use client";

/**
 * Hook gọi API có quản lý trạng thái tải / lỗi.
 *
 * - `reload()` gọi lại dữ liệu (dùng cho nút "Tải lại").
 * - Giữ lỗi cũ khi đổi bộ lọc? Không — luôn thay bằng lỗi mới nhất.
 * - `null` cho `data` khi đang tải lần đầu.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiRequestError } from "@/lib/api";

export interface AsyncState<T> {
  data: T | null;
  /**
   * Lỗi đã chuẩn hoá về `Error` (nếu API trả về lỗi không phải `Error` thì bọc lại).
   * Kiểu `Error | null` giúp JSX có thể dùng trực tiếp `{state.error && <ErrorNotice …/>}`.
   */
  error: Error | null;
  loading: boolean;
  reload: () => void;
  setData: (d: T | null) => void;
}

/** Chuẩn hoá giá trị bị ném về `Error` để hiển thị an toàn trong JSX. */
function toError(err: unknown): Error {
  if (err instanceof Error) return err;
  if (typeof err === "string") return new Error(err);
  try {
    return new Error(JSON.stringify(err));
  } catch {
    return new Error("Lỗi không xác định");
  }
}

export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[] = [],
  options: { immediate?: boolean } = {},
): AsyncState<T> {
  const { immediate = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(immediate);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!immediate && tick === 0) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fnRef
      .current()
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(toError(err));
          // 401 đã được xử lý ở tầng api client (đã refresh hoặc xoá phiên).
          if (err instanceof ApiRequestError && err.status === 403) {
            /* giữ nguyên: thông báo thiếu quyền hiển thị bên dưới */
          }
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  return { data, error, loading, reload, setData };
}
