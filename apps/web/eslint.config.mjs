/**
 * Cấu hình ESLint (flat config, ESLint 9) cho `apps/web`.
 *
 * Dùng `eslint-config-next` (đã hỗ trợ flat config từ v15) để có các quy tắc
 * React/Next.js mặc định: `react-hooks`, `jsx-a11y`, `@next/next/*`.
 *
 * Chạy: `npm run lint` (hoặc `npx eslint .`).
 */

import { dirname } from "path";
import { fileURLToPath } from "url";

import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "out/**", "next-env.d.ts"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // Frontend dùng `unknown` là chính; `any` chỉ xuất hiện ở vài chỗ ép kiểu tạm thời.
      "@typescript-eslint/no-explicit-any": "warn",
      // Cho phép `!` khi đã kiểm tra null ở trên (mẫu phổ biến trong React).
      "@typescript-eslint/no-non-null-assertion": "off",
    },
  },
];

export default eslintConfig;
