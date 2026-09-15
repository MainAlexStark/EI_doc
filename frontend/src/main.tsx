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
