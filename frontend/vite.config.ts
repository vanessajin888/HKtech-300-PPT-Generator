import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";

// 精简版配置：Vite 只负责前端 React 和把 `/api` 代理到本机 8001。
// ONLYOFFICE 的所有前端资源（api.js、/web-apps、allfonts 等）全部走 Docker 那条 ngrok 域名，
// 由 VITE_ONLYOFFICE_API_JS_URL 直接指定，不再经过 Vite 代理。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: [".ngrok-free.app"],
    proxy: {
      "/api": {
        target: "http://localhost:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});

