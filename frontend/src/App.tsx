import { useEffect, useState } from "react";
import { fetchMe, isAuthenticated, logout, type Me } from "./api";
import Availability from "./components/Availability";
import FieldWork from "./components/FieldWork";
import Journal from "./components/Journal";
import Login from "./components/Login";
import Normocontrol from "./components/Normocontrol";
import RequestsInbox from "./components/RequestsInbox";
import Tasks from "./components/Tasks";
import WorkOrders from "./components/WorkOrders";

type Tab = "journal" | "normocontrol" | "requests" | "work-orders" | "field-work" | "tasks" | "availability";

const TABS: { key: Tab; label: string }[] = [
  { key: "journal", label: "Журнал" },
  { key: "normocontrol", label: "Нормоконтроль" },
  { key: "requests", label: "Заявки" },
  { key: "work-orders", label: "Наряды" },
  { key: "field-work", label: "Поверки" },
  { key: "tasks", label: "Задачи" },
  { key: "availability", label: "Мой график" },
];

export default function App() {
  const [authenticated, setAuthenticated] = useState(isAuthenticated());
  const [tab, setTab] = useState<Tab>("journal");
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!authenticated) {
      setMe(null);
      return;
    }
    void fetchMe().then(setMe).catch(() => setMe(null));
  }, [authenticated]);

  if (!authenticated) return <Login onSuccess={() => setAuthenticated(true)} />;

  const displayName = me?.employee?.full_name ?? me?.email ?? "";

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
        {displayName && <span className="me-name">{displayName}</span>}
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
        {tab === "field-work" && <FieldWork me={me} />}
        {tab === "tasks" && <Tasks />}
        {tab === "availability" && <Availability />}
      </main>
    </div>
  );
}
