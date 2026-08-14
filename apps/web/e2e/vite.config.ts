import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const root = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root,
  publicDir: "../public",
  plugins: [react()],
  build: { outDir: "../.playwright-dist", emptyOutDir: true },
  preview: { host: "127.0.0.1", port: 4174, strictPort: true },
});
