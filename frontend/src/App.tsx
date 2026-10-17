import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { fetchMe, isAuthenticated, logout, type Me } from "./api";
import Attestations from "./components/Attestations";
import Availability from "./components/Availability";
import FieldWork from "./components/FieldWork";
import JournalTab from "./components/JournalTab";
import Login from "./components/Login";
import RequestsInbox from "./components/RequestsInbox";
import SiTypes from "./components/SiTypes";
import Tasks from "./components/Tasks";

// Ассистент — отдельный чанк: поверителю в поле на телефоне он не нужен при каждой загрузке.
const Assistant = lazy(() => import("./assistant/Assistant"));

/* Навигация (решение 17.10.2026, холст «EI_doc — новый интерфейс», вариант 2):
 * - «Журнал» и «Нормоконтроль» — одна вкладка «Журнал»: у поверителя просто
 *   журнал своих протоколов, у менеджера ещё нумерация и работа с Аршин;
 * - «Наряды» убраны — выезды живут во вкладке «Поверки»: менеджер видит все,
 *   поверитель — только свои. */
type Tab = "field-work" | "journal" | "requests" | "availability" | "tasks" | "assistant" | "si-types" | "attestations";

type NavItem = { key: Tab; label: string; icon: ReactNode; managerOnly?: boolean };

const svg = (children: ReactNode) => (
  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);

const ICONS: Record<Tab, ReactNode> = {
  "field-work": svg(<><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.7 2.7L16 9.8" /></>),
  journal: svg(<><rect x="5" y="3" width="14" height="18" rx="2.5" /><path d="M9 8h6M9 12h6M9 16h3" /></>),
  requests: svg(<><path d="M3 13h5l1.5 2.5h5L16 13h5" /><path d="M5 5h14l2 8v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z" /></>),
  availability: svg(<><rect x="3.5" y="5" width="17" height="15" rx="2.5" /><path d="M3.5 10h17M8 3v4M16 3v4" /></>),
  tasks: svg(<><path d="M9 6h11M9 12h11M9 18h11" /><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" /></>),
  assistant: svg(<path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6z" />),
  "si-types": svg(<><circle cx="12" cy="12" r="8" /><path d="M12 12l4-3" /></>),
  attestations: svg(<path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z" />),
};

const WORK: NavItem[] = [
  { key: "field-work", label: "Поверки", icon: ICONS["field-work"] },
  { key: "journal", label: "Журнал", icon: ICONS.journal },
  { key: "requests", label: "Заявки", icon: ICONS.requests, managerOnly: true },
  { key: "availability", label: "График", icon: ICONS.availability },
  { key: "tasks", label: "Задачи", icon: ICONS.tasks },
  { key: "assistant", label: "Ассистент", icon: ICONS.assistant },
];

const REFERENCE: NavItem[] = [
  { key: "si-types", label: "Типы СИ", icon: ICONS["si-types"] },
  { key: "attestations", label: "Аттестация ИО", icon: ICONS.attestations, managerOnly: true },
];

/** На телефоне в таб-баре — три главных, остальное под «Ещё». */
const PHONE_TABS: Tab[] = ["field-work", "journal", "availability"];

/** Кто видит фирму целиком. Поверитель — только своё. */
const isManagerRole = (role: string | undefined) => !!role && role !== "verifier";

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("") || "?";

const todayLine = () => {
  const text = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export default function App() {
  const [authenticated, setAuthenticated] = useState(isAuthenticated());
  const [tab, setTab] = useState<Tab>("field-work");
  const [me, setMe] = useState<Me | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    if (!authenticated) {
      setMe(null);
      return;
    }
    void fetchMe()
      .then(setMe)
      .catch(() => setMe(null));
  }, [authenticated]);

  if (!authenticated) return <Login onSuccess={() => setAuthenticated(true)} />;

  const isManager = isManagerRole(me?.role);
  const visible = (items: NavItem[]) => items.filter((item) => isManager || !item.managerOnly);
  const work = visible(WORK);
  const reference = visible(REFERENCE);
  const all = [...work, ...reference];

  const displayName = me?.employee?.full_name ?? me?.email ?? "";
  const roleName = me?.role_display ?? "";

  const go = (next: Tab) => {
    setTab(next);
    setMoreOpen(false);
    window.scrollTo({ top: 0 });
  };

  const signOut = () => {
    logout();
    setAuthenticated(false);
  };

  const heads: Partial<Record<Tab, { title: string; subtitle?: string }>> = {
    "field-work": { title: isManager ? "Все поверки" : "Мои поверки", subtitle: todayLine() },
    journal: { title: "Журнал", subtitle: isManager ? "Протоколы всех поверителей" : "Мои протоколы" },
    requests: { title: "Заявки", subtitle: "С сайта и по телефону" },
    availability: { title: "График" },
    tasks: { title: "Задачи" },
    "si-types": { title: "Типы СИ" },
    attestations: { title: "Аттестация ИО" },
  };
  const head = heads[tab];

  const sideItem = (item: NavItem) => (
    <button
      type="button"
      key={item.key}
      className={`side-item ${tab === item.key ? "active" : ""}`}
      aria-current={tab === item.key ? "page" : undefined}
      onClick={() => go(item.key)}
    >
      {item.icon}
      <span className="label">{item.label}</span>
    </button>
  );

  const meCard = displayName && (
    <div className="side-me">
      <div className="avatar">{initials(displayName)}</div>
      <div className="who">
        <b>{displayName}</b>
        <span>{roleName}</span>
      </div>
      <button type="button" onClick={signOut}>
        Выйти
      </button>
    </div>
  );

  const phoneTabs = all.filter((item) => PHONE_TABS.includes(item.key));
  const phoneMore = all.filter((item) => !PHONE_TABS.includes(item.key));
  const moreActive = phoneMore.some((item) => item.key === tab);

  return (
    <div className="app">
      <nav className="sidebar" aria-label="Разделы">
        <div className="brand">
          <div className="brand-mark">ЕИ</div>
          <div className="brand-name">EI_doc</div>
        </div>
        <div className="side-group">Работа</div>
        {work.map(sideItem)}
        <div className="side-group">Справочники</div>
        {reference.map(sideItem)}
        <div className="spacer" />
        {meCard}
      </nav>

      <div className="workspace">
        {head && (
          <header className="page-head">
            <div className="titles">
              <h1>{head.title}</h1>
              {head.subtitle && <div className="subtitle">{head.subtitle}</div>}
            </div>
          </header>
        )}

        <main>
          {tab === "field-work" && <FieldWork me={me} isManager={isManager} />}
          {tab === "journal" && <JournalTab isManager={isManager} onOpenTypes={() => go("si-types")} />}
          {tab === "requests" && <RequestsInbox />}
          {tab === "availability" && <Availability />}
          {tab === "tasks" && <Tasks />}
          {tab === "si-types" && <SiTypes me={me} />}
          {tab === "attestations" && <Attestations me={me} />}
          {tab === "assistant" && (
            <Suspense fallback={<div className="loading">Загрузка ассистента…</div>}>
              <Assistant />
            </Suspense>
          )}
        </main>
      </div>

      {moreOpen && (
        <>
          <div className="more-backdrop" onClick={() => setMoreOpen(false)} />
          <div className="more-sheet glass" role="menu">
            {phoneMore.map(sideItem)}
            {meCard}
          </div>
        </>
      )}

      <div className="tabbar">
        <div className="tabs glass">
          {phoneTabs.map((item) => (
            <button
              type="button"
              key={item.key}
              className={`tab ${tab === item.key && !moreOpen ? "active" : ""}`}
              onClick={() => go(item.key)}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
          <button
            type="button"
            className={`tab ${moreActive || moreOpen ? "active" : ""}`}
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((open) => !open)}
          >
            {svg(<><circle cx="5" cy="12" r="1.3" /><circle cx="12" cy="12" r="1.3" /><circle cx="19" cy="12" r="1.3" /></>)}
            Ещё
          </button>
        </div>
      </div>
    </div>
  );
}
