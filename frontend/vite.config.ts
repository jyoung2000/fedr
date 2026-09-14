import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 700 },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8935", changeOrigin: true, ws: true } },
  },
});
