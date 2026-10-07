import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // 独立复现有专用地址 / 凭据，不加入既有开发环境的浏览器入口。
  testIgnore: "reproduction.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  timeout: 90000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    browserName: "chromium",
    channel: "msedge",
    viewport: { width: 1440, height: 1000 },
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
