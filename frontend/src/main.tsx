import { Component, StrictMode, Suspense, lazy, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { FIRM, telHref } from "./landing/firm";

// После выкатки старые куски бандла с сервера пропадают (в имени хэш), а
// вкладка, открытая до выкатки, или index.html из кэша service worker'а
// ещё ссылаются на них. Тогда lazy() падает, и без обработки на экране
// навсегда остаётся пусто (Suspense fallback={null}) — так «не открывался»
// /o-nas/. Лечится одной перезагрузкой: свежий index.html знает новые имена.
// Метка в sessionStorage — чтобы не уйти в бесконечный цикл, если кусок
// не грузится по другой причине (нет связи).
const RELOAD_KEY = "ei:chunk-reload-at";
const RELOAD_COOLDOWN_MS = 30_000;

function reloadOnceForStaleChunk(): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_KEY) || 0);
    if (Date.now() - last < RELOAD_COOLDOWN_MS) return false;
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  } catch {
    // sessionStorage недоступен (приватный режим и т.п.) — перезагрузку
    // всё равно пробуем, но только если ещё не пробовали в этой загрузке.
    if ((window as { __eiReloaded?: boolean }).__eiReloaded) return false;
    (window as { __eiReloaded?: boolean }).__eiReloaded = true;
  }
  window.location.reload();
  return true;
}

// Vite сам шлёт это событие, когда не смог подгрузить кусок (modulepreload / import()).
window.addEventListener("vite:preloadError", (event) => {
  if (reloadOnceForStaleChunk()) event.preventDefault();
});

function lazyWithReload<T extends ComponentType<object>>(load: () => Promise<{ default: T }>) {
  return lazy(() =>
    load().catch((error: unknown) => {
      if (reloadOnceForStaleChunk()) {
        // Страница уже перезагружается — не показываем ошибку на долю секунды.
        return new Promise<{ default: T }>(() => {});
      }
      throw error;
    }),
  );
}

/** Вместо пустого экрана — понятный текст и кнопка «Обновить». */
class LoadErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 16,
          padding: 24,
          textAlign: "center",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <p style={{ margin: 0, fontSize: 18 }}>Страница не загрузилась до конца.</p>
        <p style={{ margin: 0, opacity: 0.7 }}>
          Возможно, пропала связь или сайт только что обновился.
        </p>
        <button
          type="button"
          onClick={() => {
            try {
              sessionStorage.removeItem(RELOAD_KEY);
            } catch {
              /* ничего */
            }
            window.location.reload();
          }}
          style={{ padding: "10px 20px", fontSize: 16, borderRadius: 8, cursor: "pointer" }}
        >
          Обновить
        </button>
        <p style={{ margin: 0, opacity: 0.7 }}>
          Или позвоните: <a href={telHref()}>{FIRM.phone}</a>
        </p>
      </div>
    );
  }
}

// Экраны грузятся по пути отдельными кусками: одностраничнику на телефоне клиента
// незачем тянуть весь кабинет, а кабинету — сайт.
const App = lazyWithReload(() => import("./App"));
const PublicRequestForm = lazyWithReload(() => import("./components/PublicRequestForm"));
const Landing = lazyWithReload(() => import("./landing/Landing"));
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
    <LoadErrorBoundary>
      <Suspense fallback={null}>
        {isLanding ? <Landing /> : isPublicRequestForm ? <PublicRequestForm /> : <App />}
      </Suspense>
    </LoadErrorBoundary>
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
