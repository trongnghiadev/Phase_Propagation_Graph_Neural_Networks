import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Không dùng `output: "export"`: cần Node server để chạy sau Nginx (profile `ha`).
  // Mọi lời gọi API đi qua cùng origin -> dùng đường dẫn tương đối `/api/v1/...`.
  // Khi chạy dev, Next.js sẽ proxy `/api` sang API thật (xem `next.config.ts` -> rewrites).
  eslint: {
    // Không chặn `next build` vì lỗi lint; lint chạy riêng bằng `npm run lint`.
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
