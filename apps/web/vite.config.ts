import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, proxy the API + health probes to the FastAPI service.
// In the Compose stack, Caddy performs this routing instead.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/healthz": { target: API_TARGET, changeOrigin: true },
      "/readyz": { target: API_TARGET, changeOrigin: true },
    },
  },
  // Vite preview sits behind Caddy on the internal network (D1) and is never directly
  // exposed, so the Host-header allowlist is redundant; allow any host so a prod domain
  // / air-gap hostname (e.g. easysynq.local) is served rather than blocked.
  preview: { port: 5173, host: true, allowedHosts: true },
  test: {
    environment: "jsdom",
    globals: true,
    css: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
