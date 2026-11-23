import { Component, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fetchMe, fetchRequests, fetchSiTypes, isAuthenticated, logout, type Me } from "./api";
import Login from "./components/Login";
import { isControlRole } from "./dispatchApi";
import { registerDefaultSources } from "./modes/commandSources";
import { countProgramsForReview } from "./modes/att-templates/api";
import { registerTemplateSources } from "./modes/si-templates/commands";
import { countTemplatesForReview } from "./modes/si-templates/api";
import { HASH_ALIASES, SECTIONS, SECTION_BY_KEY, SITE_URL, homeFor, isVisible, phoneTabs } from "./modes/registry";
import type { Access, GoOptions, ModeProps, SectionDef, SectionKey } from "./modes/types";
import { CommandPalette, openCommandPalette, useCommands, type CommandItem } from "./ui/CommandPalette";
import { EmptyState } from "./ui/EmptyState";
import { HotkeyHelp, useHotkey } from "./ui/Hotkeys";
import Icon from "./ui/Icon";
import Kbd from "./ui/Kbd";
import { DialogHost } from "./ui/Modal";
import { Sheet } from "./ui/Sheet";
import { SkeletonList } from "./ui/Skeleton";
import { Toaster } from "./ui/Toast";
import { PageActionsContext } from "./ui/controls";
import { usePersistent } from "./ui/hooks";
import { emitModeAction } from "./ui/modeActions";
import { withViewTransition } from "./ui/transition";

/* Оболочка v2 (docs/rebuild.md, 21.11.2026): навигация по РЕЖИМАМ работы.
 * Режимы, их права, иконки, клавиши и главные действия — в modes/registry.ts;
 * здесь только каркас: боковая панель, шапка, таб-бар телефона, палитра, клавиши.
 * Раздел хранится в адресе (#/intake, #/si-templates/12) — переживает перезагрузку,
 * работает «Назад». Старые адреса (#/field-work…) перенаправляются. */

const REVIEWER_ROLES = new Set(["owner", "head", "metrologist"]);
const isManagerRole = (role: string | undefined) => !!role && role !== "verifier";

const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "").join("") || "?";

/** «Стариков Сергей Владимирович» → «Стариков С. В.» */
const shortName = (name: string) => {
  const [last, ...rest] = name.split(/\s+/).filter(Boolean);
  if (!last || rest.length === 0) return name;
  return `${last} ${rest.map((part) => `${part[0]}.`).join(" ")}`;
};

export const todayLine = () => {
  const text = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  return text.charAt(0).toUpperCase() + text.slice(1);
};

type Route = { key: SectionKey | null; sub: string };

function readHash(): Route {
  const [raw = "", ...rest] = window.location.hash.replace(/^#\/?/, "").split("/");
  const aliased = HASH_ALIASES[raw];
  if (aliased) {
    // старый адрес — тихо переписываем, хвост (#/protocol-templates/12) сохраняем
    window.history.replaceState(null, "", `#/${[aliased, ...rest].join("/")}`);
    return { key: aliased, sub: rest.join("/") };
  }
  const section = SECTION_BY_KEY.get(raw as SectionKey);
  return section && section.key !== "site" ? { key: section.key as SectionKey, sub: rest.join("/") } : { key: null, sub: "" };
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(isAuthenticated());
  const [route, setRoute] = useState<Route>(readHash);
  const [me, setMe] = useState<Me | null>(null);
  const [counts, setCounts] = useState<Partial<Record<SectionKey, number>>>({});
  const [menuOpen, setMenuOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [actionsSlot, setActionsSlot] = useState<HTMLElement | null>(null);
  const [sidebar, setSidebar] = usePersistent<"full" | "mini">("ei_doc_sidebar", "full", ["full", "mini"]);
  const [theme, setTheme] = usePersistent<"auto" | "light" | "dark">("ei_doc_theme", "auto", ["auto", "light", "dark"]);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLElement>(null);

  // Тема — только кабинет; публичные страницы не трогаем (см. styles.css).
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.app = "cabinet";
    if (theme === "auto") delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (!authenticated) {
      setMe(null);
      return;
    }
    void fetchMe().then(setMe).catch(() => setMe(null));
  }, [authenticated]);

  useEffect(() => {
    const onHash = () => withViewTransition(() => setRoute(readHash()));
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
    };
  }, []);

  const access: Access = useMemo(
    () => ({
      role: me?.role,
      isManager: isManagerRole(me?.role),
      isControl: isControlRole(me?.role),
      isReviewer: !!me && REVIEWER_ROLES.has(me.role),
    }),
    [me],
  );
  const home = homeFor(access);
  const visibleSections = useMemo(() => SECTIONS.filter((section) => isVisible(section, access)), [access]);
  const current: SectionKey = route.key ?? home;
  const currentDef = SECTION_BY_KEY.get(current)!;

  // Пока профиль не пришёл, права неизвестны — ничего не перенаправляем.
  // Пришёл: нет раздела в адресе — домой; раздел не по роли — тоже домой.
  useEffect(() => {
    if (!me) return;
    if (!isVisible(currentDef, access)) {
      window.history.replaceState(null, "", `#/${home}`);
      setRoute({ key: home, sub: "" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me]);

  const refreshCounts = useCallback(() => {
    if (!me) return;
    const next: Partial<Record<SectionKey, number>> = {};
    const jobs: Promise<unknown>[] = [];
    if (isManagerRole(me.role)) {
      jobs.push(fetchRequests({ status: "new" }).then((list) => (next.requests = list.length)).catch(() => undefined));
    }
    if (REVIEWER_ROLES.has(me.role)) {
      jobs.push(fetchSiTypes({ needs_review: true }).then((list) => (next["si-types"] = list.length)).catch(() => undefined));
      // Шаблоны поверки и аттестации, ждущие метролога.
      jobs.push(countTemplatesForReview().then((n) => (next["si-templates"] = n)).catch(() => undefined));
      jobs.push(countProgramsForReview().then((n) => (next["att-templates"] = n)).catch(() => undefined));
    }
    void Promise.all(jobs).then(() => setCounts(next));
  }, [me]);

  useEffect(() => {
    refreshCounts();
    const timer = window.setInterval(refreshCounts, 120_000);
    return () => window.clearInterval(timer);
  }, [refreshCounts, current]);

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
  }, [authenticated, current]);

  /** Перейти в режим/раздел. trip — сразу открыть командировку; action — попросить режим что-то сделать. */
  const go = useCallback(
    (target: string, options: GoOptions = {}) => {
      const key = (HASH_ALIASES[target] ?? target) as SectionKey | "site";
      if (key === "site") {
        window.open(SITE_URL, "_blank", "noopener");
        return;
      }
      const def = SECTION_BY_KEY.get(key);
      if (!def) return;
      if (options.trip) {
        try {
          sessionStorage.setItem("ei_doc_trips_view", JSON.stringify({ kind: "trip", id: options.trip }));
        } catch {
          /* не страшно */
        }
      }
      setMoreOpen(false);
      setMenuOpen(false);
      const sub = options.sub ?? "";
      const hash = `#/${[key, sub].filter(Boolean).join("/")}`;
      const changed = key !== route.key || sub !== route.sub || !!options.trip;
      if (changed) {
        if (window.location.hash !== hash) window.history.pushState(null, "", hash);
        withViewTransition(() => setRoute({ key, sub }));
        window.scrollTo({ top: 0 });
      }
      if (options.action) emitModeAction(key, options.action, options.payload);
    },
    [route],
  );

  const signOut = useCallback(() => {
    logout();
    setMenuOpen(false);
    setMoreOpen(false);
    setAuthenticated(false);
  }, []);

  const primary = currentDef.primary && (currentDef.primary.visible ?? (() => true))(access) ? currentDef.primary : null;
  const runPrimary = () => primary && emitModeAction(current, primary.action);

  // --- клавиши ------------------------------------------------------------
  const digitModes = visibleSections.filter((section) => section.digit);
  useHotkey(
    digitModes.map((section) => String(section.digit)),
    (event) => {
      const digit = Number(event.code.startsWith("Digit") ? event.code.slice(5) : event.key);
      const section = digitModes.find((item) => item.digit === digit);
      if (section) go(section.key);
    },
    { enabled: authenticated, description: "Режимы 1…8", group: "Переходы" },
  );
  useHotkey("n", runPrimary, {
    enabled: authenticated && !!primary,
    description: primary ? `${primary.label} — новое в текущем режиме` : undefined,
    group: "Действия",
  });
  useHotkey("?", () => setHelpOpen((open) => !open), { enabled: authenticated, description: "Подсказка по клавишам", group: "Общее" });
  useHotkey("escape", () => setHelpOpen(false), { enabled: helpOpen, description: "Закрыть окно", group: "Общее", global: true, preventDefault: false });

  // --- палитра команд -----------------------------------------------------
  const commands: CommandItem[] = useMemo(() => {
    if (!authenticated) return [];
    const nav = visibleSections.map((section) => ({
      id: `go:${section.key}`,
      title: section.key === "site" ? "Открыть сайт фирмы" : section.title,
      subtitle: section.description,
      icon: section.icon,
      group: "Перейти",
      keywords: section.keywords,
      hint: section.digit ? String(section.digit) : undefined,
      run: () => go(section.key),
    }));
    const action = (id: string, title: string, mode: SectionKey, act: string, icon: CommandItem["icon"], keywords = ""): CommandItem => ({
      id, title, icon, group: "Действия", keywords, run: () => go(mode, { action: act }),
    });
    const actions: CommandItem[] = [
      action("act:intake", "Новая приёмка", "intake", "new", "box", "приём принесли приборы коробка акт"),
      action("act:visit", "Новая запись клиента на выезд", "city", "new-visit", "phone", "заявка звонок запись адрес"),
      action("act:trip", "Новая командировка", "trips", "new-trip", "car", "поездка выезд район"),
      ...(access.isManager ? [action("act:procurement", "Новая закупка", "procurements", "new-procurement", "contract", "контракт 44 223")] : []),
      { id: "act:keys", title: "Клавиши", subtitle: "Все сочетания", icon: "keyboard", group: "Действия", hint: "?", run: () => setHelpOpen(true) },
      {
        id: "act:theme", title: theme === "dark" ? "Светлая тема" : "Тёмная тема", icon: theme === "dark" ? "sun" : "moon", group: "Действия",
        keywords: "тема оформление ночь", run: () => setTheme(theme === "dark" ? "light" : "dark"),
      },
      { id: "act:logout", title: "Выйти", icon: "logout", group: "Действия", keywords: "выход сменить пользователя", run: signOut },
    ];
    return [...nav, ...actions];
  }, [authenticated, visibleSections, access.isManager, go, theme, setTheme, signOut]);
  useCommands("shell", commands, [commands]);

  const goRef = useRef(go);
  goRef.current = go;
  useEffect(() => {
    if (!me) return;
    return registerDefaultSources((key, options) => goRef.current(key, options), access);
  }, [me, access]);
  // Типы СИ, шаблоны поверки и аттестации — в палитре команд (modes/si-templates/commands.ts).
  useEffect(() => {
    if (!me) return;
    return registerTemplateSources((key, options) => goRef.current(key, options), access);
  }, [me, access]);

  if (!authenticated) return <Login onSuccess={() => setAuthenticated(true)} />;

  const displayName = me?.employee?.full_name ?? me?.email ?? "";
  const roleName = me?.role_display ?? "";
  const mini = sidebar === "mini";

  const subtitles: Partial<Record<SectionKey, string>> = {
    control: `${todayLine()} · работа всех сотрудников`,
    city: `${todayLine()} · ${currentDef.description}`,
    journal: access.isManager ? "Протоколы всех поверителей" : "Мои протоколы",
  };
  const subtitle = subtitles[current] ?? currentDef.description;
  const flush = !!currentDef.flush;
  const ModeComponent = currentDef.component;
  const modeProps: ModeProps = { me, isManager: access.isManager, isControl: access.isControl, isReviewer: access.isReviewer, go, refreshCounts };

  const sideItem = (section: SectionDef, variant: "side" | "sheet" = "side") => {
    const count = counts[section.key as SectionKey];
    const active = section.key === current;
    const label = <span className="label">{section.title}</span>;
    const badge = count ? <span className={`count ${section.key === "requests" ? "badge" : ""}`}>{count}</span> : null;
    const digit = section.digit && variant === "side" ? <Kbd className="side-digit kbd-hint">{section.digit}</Kbd> : null;
    if (section.href) {
      return (
        <a key={section.key} className="side-item" href={section.href} target="_blank" rel="noopener" title={mini ? `${section.title} — в новой вкладке` : undefined}>
          <Icon name={section.icon} />
          {label}
          <Icon name="arrowUpRight" size={14} className="side-ext" />
          {digit}
        </a>
      );
    }
    return (
      <button
        type="button"
        key={section.key}
        className={`side-item ${active ? "active" : ""}`}
        aria-current={active ? "page" : undefined}
        title={mini ? section.title : undefined}
        onClick={() => go(section.key)}
      >
        <Icon name={section.icon} />
        {label}
        {badge}
        {!badge && digit}
      </button>
    );
  };

  const group = (name: SectionDef["group"]) => visibleSections.filter((section) => section.group === name);

  const phoneKeys = phoneTabs(access).filter((key) => visibleSections.some((section) => section.key === key));
  const phoneMain = phoneKeys.map((key) => SECTION_BY_KEY.get(key)!);
  const phoneMore = visibleSections.filter((section) => !phoneKeys.includes(section.key as SectionKey));
  const moreActive = phoneMore.some((section) => section.key === current);
  const moreCount = phoneMore.reduce((sum, section) => sum + (counts[section.key as SectionKey] ?? 0), 0);

  return (
    <PageActionsContext.Provider value={actionsSlot}>
      <div className={`app ${mini ? "side-mini" : ""}`} data-section={current}>
        <a className="skip-link" href="#main">К содержимому</a>
        <nav className="sidebar" aria-label="Режимы и разделы">
          <div className="brand">
            <div className="brand-mark" aria-hidden="true">ЕИ</div>
            <div className="brand-name">
              EI_doc
              <small>Единица Измерения</small>
            </div>
            <button
              type="button"
              className="icon-btn sm side-toggle"
              onClick={() => setSidebar(mini ? "full" : "mini")}
              aria-label={mini ? "Развернуть панель" : "Свернуть панель до иконок"}
              title={mini ? "Развернуть панель" : "Свернуть панель"}
            >
              <Icon name={mini ? "expand" : "collapse"} size={16} />
            </button>
          </div>

          <button type="button" className="side-search" onClick={() => openCommandPalette()} title="Найти или сделать — ⌘K">
            <Icon name="search" size={16} />
            <span className="label">Найти или сделать</span>
            <Kbd keys="mod+k" className="kbd-hint" />
          </button>

          {group("home").map((section) => sideItem(section))}
          <div className="side-group">Режимы</div>
          {group("modes").map((section) => sideItem(section))}
          <div className="side-group">Работа</div>
          {group("work").map((section) => sideItem(section))}
          {group("reference").length > 0 && <div className="side-group">Справочники</div>}
          {group("reference").map((section) => sideItem(section))}
          <div className="spacer" />
          {displayName && (
            <div className="side-me">
              <button
                type="button"
                className="side-me-btn"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                title={mini ? displayName : undefined}
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
                  theme={theme}
                  onTheme={setTheme}
                  onHelp={() => {
                    setMenuOpen(false);
                    setHelpOpen(true);
                  }}
                  onClose={() => setMenuOpen(false)}
                  onSignOut={signOut}
                />
              )}
            </div>
          )}
        </nav>

        <div className="workspace" ref={workspaceRef}>
          <header className={`page-head ${flush ? "compact" : ""} ${current === "today" ? "is-today" : ""}`} ref={headRef}>
            <div className="titles">
              <h1>{currentDef.title}</h1>
              {subtitle && <div className="subtitle">{subtitle}</div>}
            </div>
            <div className="page-actions" ref={setActionsSlot} />
            <button type="button" className="icon-btn phone-only head-search" onClick={() => openCommandPalette()} aria-label="Поиск и команды">
              <Icon name="search" size={20} />
            </button>
          </header>

          <main id="main" className={flush ? "flush" : ""} tabIndex={-1}>
            <ModeBoundary key={current} onHome={() => go(home)}>
              <Suspense fallback={<div className="mode-loading"><SkeletonList rows={5} /></div>}>
                {ModeComponent && <ModeComponent {...modeProps} />}
              </Suspense>
            </ModeBoundary>
          </main>
        </div>

        {primary && (
          <button type="button" className="mode-fab phone-only" onClick={runPrimary}>
            <Icon name={primary.icon} size={20} />
            {primary.label}
          </button>
        )}

        <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} side="bottom" size="sm" className="more-panel" title="Ещё">
          <div className="more-list">
            {phoneMore.map((section) => sideItem(section, "sheet"))}
            <div className="menu-sep" />
            <button type="button" className="side-item" onClick={() => { setMoreOpen(false); openCommandPalette(); }}>
              <Icon name="search" />
              <span className="label">Найти или сделать</span>
            </button>
            {displayName && (
              <button type="button" className="side-item" onClick={signOut}>
                <Icon name="logout" />
                <span className="label">Выйти · {shortName(displayName)}</span>
              </button>
            )}
          </div>
        </Sheet>

        <nav className="tabbar" aria-label="Главные режимы">
          <div className="tabs glass">
            {phoneMain.map((section) => (
              <button
                type="button"
                key={section.key}
                className={`tab ${current === section.key && !moreOpen ? "active" : ""}`}
                aria-current={current === section.key ? "page" : undefined}
                onClick={() => go(section.key)}
              >
                <span className="tab-icon">
                  <Icon name={section.icon} size={22} />
                  {counts[section.key as SectionKey] ? <span className="tab-badge">{counts[section.key as SectionKey]}</span> : null}
                </span>
                {section.short}
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
        </nav>

        <CommandPalette />
        <HotkeyHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
        <Toaster />
        <DialogHost />
      </div>
    </PageActionsContext.Provider>
  );
}

/** Режим не загрузился (нет связи, обновили бандл) или упал — сказать, что делать, а не белый экран. */
class ModeBoundary extends Component<{ children: ReactNode; onHome: () => void }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    const chunk = /dynamically imported module|Failed to fetch|Loading chunk/i.test(this.state.error.message);
    return (
      <EmptyState
        tone="error"
        title={chunk ? "Раздел не загрузился" : "В разделе что-то сломалось"}
        action={{ label: "Обновить страницу", icon: "refresh", onClick: () => window.location.reload() }}
        secondary={{ label: "На главную", icon: "home", onClick: this.props.onHome }}
      >
        {chunk
          ? "Похоже, пропала связь или вышла новая версия. Обновите страницу — несохранённые на телефоне поверки останутся в очереди."
          : "Обновите страницу. Если повторится — сообщите, что нажимали перед этим."}
      </EmptyState>
    );
  }
}

function AccountMenu({
  name, email, theme, onTheme, onHelp, onClose, onSignOut,
}: {
  name: string;
  email: string;
  theme: "auto" | "light" | "dark";
  onTheme: (theme: "auto" | "light" | "dark") => void;
  onHelp: () => void;
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
  const themes = [
    { value: "auto" as const, label: "Как в системе" },
    { value: "light" as const, label: "Светлая" },
    { value: "dark" as const, label: "Тёмная" },
  ];
  return (
    <div className="menu account-menu" ref={ref} role="menu">
      <div className="menu-head">
        <b>{name}</b>
        {email}
      </div>
      <div className="menu-sep" />
      <div className="menu-label">Тема</div>
      <div className="theme-pick" role="group" aria-label="Тема">
        {themes.map((item) => (
          <button
            type="button"
            key={item.value}
            className={theme === item.value ? "active" : ""}
            aria-pressed={theme === item.value}
            onClick={() => onTheme(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="menu-sep" />
      <button type="button" className="menu-item" role="menuitem" onClick={onHelp}>
        <Icon name="keyboard" size={16} />
        Клавиши
        <Kbd className="menu-kbd">?</Kbd>
      </button>
      <button type="button" className="menu-item" role="menuitem" onClick={onSignOut}>
        <Icon name="logout" size={16} />
        Выйти
      </button>
    </div>
  );
}
