import { defineConfig } from "@playwright/test";

const baseURL = process.env.SUPPORTOPS_REPRO_BROWSER_BASE_URL;
const browser = process.env.SUPPORTOPS_REPRO_BROWSER;
if (!baseURL || !["chromium", "msedge"].includes(browser ?? ""))
  throw new Error("独立浏览器需要显式地址与 chromium / msedge。");

export default defineConfig({
  testDir: "./e2e",
  testMatch: "reproduction.spec.ts",
  workers: 1,
  retries: 0,
  reporter: "list",
  timeout: 90000,
  use: {
    baseURL,
    browserName: "chromium",
    ...(browser === "msedge" ? { channel: "msedge" } : {}),
    viewport: { width: 1440, height: 1000 },
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
