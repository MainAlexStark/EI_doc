import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке фронт ходит на Django напрямую (Vite dev-сервер на 5173).
// В бою собранный dist встраивается в образ backend и отдаётся самим
// Django через whitenoise — отдельного nginx в проде больше нет
// (см. deploy/backend.Dockerfile, config/settings/base.py: STATICFILES_DIRS).
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "build" ? "/static/frontend/" : "/",
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: true },
}));
