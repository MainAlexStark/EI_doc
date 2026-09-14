import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке фронт ходит на Django напрямую; в бою оба за одним nginx.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: true },
});
