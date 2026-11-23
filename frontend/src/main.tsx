import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
// Экраны грузятся по пути отдельными кусками: одностраничнику на телефоне клиента
// незачем тянуть весь кабинет, а кабинету — сайт.
const App = lazy(() => import("./App"));
const PublicRequestForm = lazy(() => import("./components/PublicRequestForm"));
const Landing = lazy(() => import("./landing/Landing"));
import "@fontsource-variable/golos-text";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "./styles.css";
import "./control.css";
import "./ui/shell.css";

// Публичная форма заявки живёт на отдельном пути того же бандла — своего
// клиентского роутера в проекте нет (см. App.tsx), поэтому выбор экрана
// здесь один раз, по window.location, а не внутри дерева компонентов.
// Одностраничник фирмы (/o-nas/) — тоже публичный, со встроенной формой заявки.
const path = window.location.pathname;
const isLanding = path.startsWith("/o-nas");
const isPublicRequestForm = isLanding || path.startsWith("/zayavka");

if (!isPublicRequestForm) {
  // кабинет: своё имя вкладки и не в поиске (index.html описывает сайт фирмы)
  document.title = "EI_doc — журнал поверок";
  const robots = document.createElement("meta");
  robots.name = "robots";
  robots.content = "noindex";
  document.head.appendChild(robots);
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Suspense fallback={null}>
      {isLanding ? <Landing /> : isPublicRequestForm ? <PublicRequestForm /> : <App />}
    </Suspense>
  </StrictMode>,
);

// Офлайн-режим — только для внутреннего кабинета (см. src/offline/), не для
// публичной формы заявки: регистрируем сами и жёстко на корень "/", а не тем
// путём, что подставил бы vite-plugin-pwa по своему base ("/static/frontend/"),
// иначе scope service worker'а ограничился бы этим префиксом и не покрыл SPA.
// sw.js Django отдаёт из корня нарочно — см. apps.core.views.frontend_root_file.
if (!isPublicRequestForm && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      // Офлайн — это плюс, а не обязательное условие работы: если что-то
      // не так с регистрацией, кабинет просто продолжает работать онлайн.
    });
  });
}
