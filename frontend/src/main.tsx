import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import PublicRequestForm from "./components/PublicRequestForm";
import "./styles.css";

// Публичная форма заявки живёт на отдельном пути того же бандла — своего
// клиентского роутера в проекте нет (см. App.tsx), поэтому выбор экрана
// здесь один раз, по window.location, а не внутри дерева компонентов.
const isPublicRequestForm = window.location.pathname.startsWith("/zayavka");

createRoot(document.getElementById("root")!).render(
  <StrictMode>{isPublicRequestForm ? <PublicRequestForm /> : <App />}</StrictMode>,
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
