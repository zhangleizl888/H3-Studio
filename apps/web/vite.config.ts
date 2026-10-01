import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

const USE_MOCK = process.env.VITE_USE_MOCK !== "false";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    host: "127.0.0.1",
    // 后端起来后走真实 API；mock 模式下这些代理不会被用到
    proxy: USE_MOCK
      ? undefined
      : {
          "/api": { target: "http://127.0.0.1:8788", changeOrigin: true },
          "/ws": { target: "ws://127.0.0.1:8788", ws: true },
        },
  },
});
