import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 独立容器固定服务名，保留原本机 Vite 配置。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    fs: { strict: true },
    proxy: { "/api": "http://api:8010", "/health": "http://api:8010" },
  },
});
