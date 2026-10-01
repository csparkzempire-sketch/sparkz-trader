import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API: uvicorn app.main:app --port 8000 (from backend/). The dev server proxies /api and /ws to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
