import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The research API runs on :8100 (uvicorn app.main:app --port 8100) so it can sit next to
// the Sparkz Trader API on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { "/api": { target: "http://localhost:8100", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
  preview: {
    port: 4174,
    proxy: { "/api": { target: "http://localhost:8100", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
