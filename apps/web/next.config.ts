import type { NextConfig } from "next";

/**
 * Proxy dev (`NEXT_PUBLIC_API_PROXY=1`): Next.js chuyển `/api/v1/...` sang API thật
 * (`API_PROXY_TARGET`, mặc định `NEXT_PUBLIC_API_BASE_URL`), trình duyệt chỉ gọi cùng origin
 * nên không cần CORS. Khi chạy sau Nginx cùng origin thì tắt proxy và để trống base URL.
 */
const proxyTarget = (process.env.API_PROXY_TARGET || process.env.NEXT_PUBLIC_API_BASE_URL || "").replace(
  /\/+$/,
  "",
);
const useProxy = process.env.NEXT_PUBLIC_API_PROXY === "1" && proxyTarget !== "";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Không dùng `output: "export"`: cần Node server để chạy sau Nginx (profile `ha`).
  eslint: {
    // Không chặn `next build` vì lỗi lint; lint chạy riêng bằng `npm run lint`.
    ignoreDuringBuilds: true,
  },
  async rewrites() {
    return useProxy ? [{ source: "/api/v1/:path*", destination: `${proxyTarget}/api/v1/:path*` }] : [];
  },
  async headers() {
    // Giảm bề mặt XSS/clickjacking (access token ở bộ nhớ, refresh token ở cookie httpOnly).
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
