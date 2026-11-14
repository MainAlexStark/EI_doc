import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchMe, fetchRequests, fetchSiTypes, isAuthenticated, logout, type Me } from "./api";
import Attestations from "./components/Attestations";
import Availability from "./components/Availability";
import FieldWork from "./components/FieldWork";
import JournalTab from "./components/JournalTab";
import Login from "./components/Login";
import RequestsInbox from "./components/RequestsInbox";
import SiTypes from "./components/SiTypes";
import Tasks from "./components/Tasks";
import Trips from "./components/Trips";
import Icon, { type IconName } from "./ui/Icon";
import { DialogHost } from "./ui/Modal";
import { PageActionsContext, Spinner } from "./ui/controls";

// Ассистент — отдельный чанк: поверителю в поле на телефоне он не нужен при каждой загрузке.
const Assistant = lazy(() => import("./assistant/Assistant"));

/* Навигация (решение 17.10.2026, холст «EI_doc — новый интерфейс», вариант 2):
 * - «Журнал» и «Нормоконтроль» — одна вкладка «Журнал»: у поверителя просто
 *   журнал своих протоколов, у менеджера ещё нумерация и работа с Аршин;
 * - «Наряды» убраны — выезды живут во вкладке «Поверки»: менеджер видит все,
 *   поверитель — только свои.
 * Раздел хранится в адресе (#/journal) — переживает перезагрузку, работает «Назад». */
type Tab = "field-work" | "trips" | "journal" | "requests" | "availability" | "tasks" | "assistant" | "si-types" | "attestations";

type NavItem = { key: Tab; label: string; icon: IconName; managerOnly?: boolean; /** Короче — для таб-бара телефона. */ short?: string };

const WORK: NavItem[] = [
  { key: "field-work", label: "Поверки", icon: "checkCircle" },
  { key: "trips", label: "Командировки", short: "Поездки", icon: "car" },
  { key: "journal", label: "Журнал", icon: "doc" },
  { key: "requests", label: "Заявки", icon: "inbox", managerOnly: true },
  { key: "availability", label: "График", icon: "calendar" },
  { key: "tasks", label: "Задачи", icon: "tasks" },
  { key: "assistant", label: "Ассистент", icon: "sparkle" },
];

const REFERENCE: NavItem[] = [
  { key: "si-types", label: "Типы СИ", icon: "gauge" },
  { key: "attestations", label: "Аттестация ИО", icon: "shield", managerOnly: true },
];

const ALL_TABS = new Set<Tab>([...WORK, ...REFERENCE].map((item) => item.key));

/** Разделы во всю высоту окна — без отступов main: календарь, заявки, ассистент. */
const FLUSH: Tab[] = ["availability", "requests", "assistant"];

/** На телефоне в таб-баре — четыре главных, остальное под «Ещё». */
const PHONE_TABS: Tab[] = ["field-work", "trips", "journal", "availability"];

/** Кто видит фирму целиком. Поверитель — только своё. */
const isManagerRole = (role: string | undefined) => !!role && role !== "verifier";
const REVIEWER_ROLES = new Set(["owner", "head", "metrologist"]);

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("") || "?";

/** «Стариков Сергей Владимирович» → «Стариков С. В.» — в боковой панели полное ФИО не помещается. */
const shortName = (name: string) => {
  const [last, ...rest] = name.split(/\s+/).filter(Boolean);
  if (!last || rest.length === 0) return name;
  return `${last} ${rest.map((part) => `${part[0]}.`).join(" ")}`;
};

const todayLine = () => {
  const text = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  return text.charAt(0).toUpperCase() + text.slice(1);
};

const tabFromHash = (): Tab => {
  const key = window.location.hash.replace(/^#\/?/, "") as Tab;
  return ALL_TABS.has(key) ? key : "field-work";
};

export default function App() {
  const [authenticated, setAuthenticated] = useState(isAuthenticated());
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [me, setMe] = useState<Me | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [actionsSlot, setActionsSlot] = useState<HTMLElement | null>(null);
  const [counts, setCounts] = useState<Partial<Record<Tab, number>>>({});
  const workspaceRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!authenticated) {
      setMe(null);
      return;
    }
    void fetchMe()
      .then(setMe)
      .catch(() => setMe(null));
  }, [authenticated]);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const isManager = isManagerRole(me?.role);

  // Счётчики в боковой панели: новые заявки и типы СИ на проверке.
  const refreshCounts = useCallback(() => {
    if (!me) return;
    const next: Partial<Record<Tab, number>> = {};
    const jobs: Promise<unknown>[] = [];
    if (isManagerRole(me.role)) {
      jobs.push(fetchRequests({ status: "new" }).then((list) => (next.requests = list.length)).catch(() => undefined));
    }
    if (REVIEWER_ROLES.has(me.role)) {
      jobs.push(fetchSiTypes({ needs_review: true }).then((list) => (next["si-types"] = list.length)).catch(() => undefined));
    }
    void Promise.all(jobs).then(() => setCounts(next));
  }, [me]);

  useEffect(() => {
    refreshCounts();
    const timer = window.setInterval(refreshCounts, 120_000);
    return () => window.clearInterval(timer);
  }, [refreshCounts, tab]);

  // Высота шапки — для разделов во всю высоту окна (календарь, заявки, ассистент).
  useLayoutEffect(() => {
    const head = headRef.current;
    const workspace = workspaceRef.current;
    if (!head || !workspace) return;
    const apply = () => workspace.style.setProperty("--head-top", `${head.getBoundingClientRect().height}px`);
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(head);
    return () => observer.disconnect();
  }, [authenticated, tab]);

  if (!authenticated) return <Login onSuccess={() => setAuthenticated(true)} />;

  const visible = (items: NavItem[]) => items.filter((item) => isManager || !item.managerOnly);
  const work = visible(WORK);
  const reference = visible(REFERENCE);
  const all = [...work, ...reference];

  const displayName = me?.employee?.full_name ?? me?.email ?? "";
  const roleName = me?.role_display ?? "";

  const go = (next: Tab) => {
    setMoreOpen(false);
    setMenuOpen(false);
    if (next !== tab) {
      window.history.pushState(null, "", `#/${next}`);
      setTab(next);
    }
    window.scrollTo({ top: 0 });
  };

  const signOut = () => {
    logout();
    setMenuOpen(false);
    setMoreOpen(false);
    setAuthenticated(false);
  };

  const heads: Record<Tab, { title: string; subtitle?: string }> = {
    "field-work": { title: isManager ? "Все поверки" : "Мои поверки", subtitle: todayLine() },
    trips: { title: "Командировки", subtitle: "Выезды по районам и работы по закупкам" },
    journal: { title: "Журнал", subtitle: isManager ? "Протоколы всех поверителей" : "Мои протоколы" },
    requests: { title: "Заявки", subtitle: "С сайта и по телефону" },
    availability: { title: "График", subtitle: "Когда я могу выезжать — заявители на сайте видят эти дни" },
    tasks: { title: "Задачи" },
    assistant: { title: "Ассистент", subtitle: "Метролог-помощник: ФГИС «Аршин», методики, файлы компании" },
    "si-types": { title: "Типы СИ", subtitle: "Справочник средств измерений и шаблоны поверки" },
    attestations: { title: "Аттестация ИО", subtitle: "Испытательное оборудование" },
  };
  const head = heads[tab];
  const flush = FLUSH.includes(tab);

  const sideItem = (item: NavItem) => {
    const count = counts[item.key];
    return (
      <button
        type="button"
        key={item.key}
        className={`side-item ${tab === item.key ? "active" : ""}`}
        aria-current={tab === item.key ? "page" : undefined}
        onClick={() => go(item.key)}
      >
        <Icon name={item.icon} />
        <span className="label">{item.label}</span>
        {count ? <span className={`count ${item.key === "requests" ? "badge" : ""}`}>{count}</span> : null}
      </button>
    );
  };

  const meButton = displayName && (
    <div className="side-me">
      <button
        type="button"
        className="side-me-btn"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
      >
        <span className="avatar">{initials(displayName)}</span>
        <span className="who">
          <b title={displayName}>{shortName(displayName)}</b>
          <span>{roleName}</span>
        </span>
        <Icon name="chevronDown" size={14} />
      </button>
      {menuOpen && (
        <AccountMenu
          name={displayName}
          email={me?.email ?? ""}
          onClose={() => setMenuOpen(false)}
          onSignOut={signOut}
        />
      )}
    </div>
  );

  const phoneTabs = all.filter((item) => PHONE_TABS.includes(item.key));
  const phoneMore = all.filter((item) => !PHONE_TABS.includes(item.key));
  const moreActive = phoneMore.some((item) => item.key === tab);
  const moreCount = phoneMore.reduce((sum, item) => sum + (item.key === "requests" ? counts.requests ?? 0 : 0), 0);

  return (
    <PageActionsContext.Provider value={actionsSlot}>
      <div className="app">
        <nav className="sidebar" aria-label="Разделы">
          <div className="brand">
            <div className="brand-mark">ЕИ</div>
            <div className="brand-name">
              EI_doc
              <small>Единица Измерения</small>
            </div>
          </div>
          <div className="side-group">Работа</div>
          {work.map(sideItem)}
          <div className="side-group">Справочники</div>
          {reference.map(sideItem)}
          <div className="spacer" />
          {meButton}
        </nav>

        <div className="workspace" ref={workspaceRef}>
          <header className={`page-head ${flush ? "compact" : ""}`} ref={headRef}>
            <div className="titles">
              <h1>{head.title}</h1>
              {head.subtitle && <div className="subtitle">{head.subtitle}</div>}
            </div>
            <div className="page-actions" ref={setActionsSlot} />
          </header>

          <main className={flush ? "flush" : ""}>
            {tab === "field-work" && <FieldWork me={me} isManager={isManager} />}
            {tab === "trips" && <Trips me={me} isManager={isManager} />}
            {tab === "journal" && <JournalTab isManager={isManager} onOpenTypes={() => go("si-types")} />}
            {tab === "requests" && <RequestsInbox onChanged={refreshCounts} />}
            {tab === "availability" && <Availability me={me} />}
            {tab === "tasks" && <Tasks me={me} />}
            {tab === "si-types" && <SiTypes me={me} onChanged={refreshCounts} />}
            {tab === "attestations" && <Attestations me={me} />}
            {tab === "assistant" && (
              <Suspense fallback={<Spinner label="Загружаю ассистента…" />}>
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
              <div className="menu-sep" />
              {displayName && (
                <button type="button" className="side-item" onClick={signOut}>
                  <Icon name="logout" />
                  <span className="label">Выйти · {shortName(displayName)}</span>
                </button>
              )}
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
                <Icon name={item.icon} size={22} />
                {item.short ?? item.label}
              </button>
            ))}
            <button
              type="button"
              className={`tab ${moreActive || moreOpen ? "active" : ""}`}
              aria-expanded={moreOpen}
              onClick={() => setMoreOpen((open) => !open)}
            >
              <span className="tab-icon">
                <Icon name="more" size={22} />
                {moreCount > 0 && <span className="tab-badge">{moreCount}</span>}
              </span>
              Ещё
            </button>
          </div>
        </div>
        <DialogHost />
      </div>
    </PageActionsContext.Provider>
  );
}

function AccountMenu({
  name, email, onClose, onSignOut,
}: {
  name: string;
  email: string;
  onClose: () => void;
  onSignOut: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.parentElement?.contains(event.target as Node)) onClose();
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);
  return (
    <div className="menu" ref={ref} role="menu" style={{ bottom: "calc(100% + 6px)", left: 0, right: 0 }}>
      <div className="menu-head">
        <b>{name}</b>
        {email}
      </div>
      <div className="menu-sep" />
      <button type="button" className="menu-item" role="menuitem" onClick={onSignOut}>
        <Icon name="logout" size={16} />
        Выйти
      </button>
    </div>
  );
}

