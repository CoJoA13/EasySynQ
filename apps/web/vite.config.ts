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
  preview: { port: 5173, host: true },
  test: { environment: "jsdom", globals: true },
});
