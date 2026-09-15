import { useState } from "react";
import { isAuthenticated, logout } from "./api";
import Journal from "./components/Journal";
import Login from "./components/Login";
import Normocontrol from "./components/Normocontrol";
import RequestsInbox from "./components/RequestsInbox";
import Tasks from "./components/Tasks";
import WorkOrders from "./components/WorkOrders";

type Tab = "journal" | "normocontrol" | "requests" | "work-orders" | "tasks";

const TABS: { key: Tab; label: string }[] = [
  { key: "journal", label: "Журнал" },
  { key: "normocontrol", label: "Нормоконтроль" },
  { key: "requests", label: "Заявки" },
  { key: "work-orders", label: "Наряды" },
  { key: "tasks", label: "Задачи" },
];

export default function App() {
  const [authenticated, setAuthenticated] = useState(isAuthenticated());
  const [tab, setTab] = useState<Tab>("journal");

  if (!authenticated) return <Login onSuccess={() => setAuthenticated(true)} />;

  return (
    <div className="app">
      <header className="topbar">
        <h1>EI_doc</h1>
        <nav>
          {TABS.map((item) => (
            <button
              key={item.key}
              className={tab === item.key ? "active" : ""}
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="spacer" />
        <button
          onClick={() => {
            logout();
            setAuthenticated(false);
          }}
        >
          Выйти
        </button>
      </header>

      <main>
        {tab === "journal" && <Journal />}
        {tab === "normocontrol" && <Normocontrol />}
        {tab === "requests" && <RequestsInbox />}
        {tab === "work-orders" && <WorkOrders />}
        {tab === "tasks" && <Tasks />}
      </main>
    </div>
  );
}
