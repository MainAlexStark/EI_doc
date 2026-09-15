import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// В разработке фронт ходит на Django напрямую (Vite dev-сервер на 5173).
// В бою собранный dist встраивается в образ backend и отдаётся самим
// Django через whitenoise — отдельного nginx в проде больше нет
// (см. deploy/backend.Dockerfile, config/settings/base.py: STATICFILES_DIRS).
export default defineConfig(({ command }) => ({
  plugins: [
    react(),
    VitePWA({
      // Манифест и регистрация — свои (public/manifest.webmanifest,
      // src/main.tsx): плагину оставлена только генерация самого service
      // worker'а (precache собранного бандла), потому что она единственная,
      // которую реально тяжело писать руками — список файлов и их хэши
      // меняются на каждой сборке. base для precache-путей плагин берёт из
      // конфига vite сам (/static/frontend/…) — это правильно, оттуда
      // whitenoise и правда раздаёт бандл; scope/start_url в манифесте
      // намеренно "/", это отдельный вопрос от того, где физически лежат
      // JS/CSS файлы (см. src/main.tsx, apps.core.views.frontend_root_file).
      manifest: false,
      injectRegister: null,
      registerType: "autoUpdate",
      // Совпадает с build.base ниже — иначе precache-манифест в sw.js
      // получится с путями без префикса "/static/frontend/", а бандл
      // реально раздаётся только по нему (whitenoise, STATICFILES_DIRS).
      base: command === "build" ? "/static/frontend/" : "/",
      workbox: {
        // Единственный маршрут SPA — "/" (см. apps.core.views.frontend_index).
        // /zayavka — отдельная точка входа того же бандла, но её должен
        // отдавать сервер честно (публичная форма без офлайна не нуждается
        // в подмене), поэтому в кэш навигации не включаем.
        navigateFallback: "/",
        navigateFallbackDenylist: [/^\/api\//, /^\/admin\//, /^\/zayavka\//],
        // precache-пути по умолчанию относительные — считаются от адреса
        // самого sw.js. Он нарочно лежит в корне "/" (чтобы scope был на всё
        // SPA, см. main.tsx), а бандл раздаётся под /static/frontend/ —
        // поэтому пути к ассетам донабиваем префиксом руками. index.html не
        // трогаем: его relative "index.html" сам резолвится в "/index.html",
        // а этот путь Django отдаёт отдельно (frontend_root_file) — то же
        // содержимое, что и "/" (frontend_index), только под точным именем,
        // которого просит workbox для directoryIndex-соответствия с "/".
        manifestTransforms: [
          (entries) => ({
            manifest: entries.map((entry) =>
              entry.url === "index.html" ? entry : { ...entry, url: `/static/frontend/${entry.url}` },
            ),
          }),
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  base: command === "build" ? "/static/frontend/" : "/",
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: true },
}));
