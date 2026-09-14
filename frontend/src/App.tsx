import { useState } from "react";
import { isAuthenticated, logout } from "./api";
import Journal from "./components/Journal";
import Login from "./components/Login";
import Normocontrol from "./components/Normocontrol";

type Tab = "journal" | "normocontrol";

export default function App() {
  const [authenticated, setAuthenticated] = useState(isAuthenticated());
  const [tab, setTab] = useState<Tab>("journal");

  if (!authenticated) return <Login onSuccess={() => setAuthenticated(true)} />;

  return (
    <div className="app">
      <header className="topbar">
        <h1>EI_doc</h1>
        <nav>
          <button
            className={tab === "journal" ? "active" : ""}
            onClick={() => setTab("journal")}
          >
            Журнал
          </button>
          <button
            className={tab === "normocontrol" ? "active" : ""}
            onClick={() => setTab("normocontrol")}
          >
            Нормоконтроль
          </button>
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

      <main>{tab === "journal" ? <Journal /> : <Normocontrol />}</main>
    </div>
  );
}
