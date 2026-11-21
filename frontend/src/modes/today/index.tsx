/** «Сегодня» — новая главная. Не витрина одинаковых карточек, а рабочий стол:
 *  слева — день человека (адреса в городе или, у руководства, вся фирма), справа —
 *  показания режимов: живые цифры и одно главное действие у каждого. Внизу — тихий
 *  указатель остальных режимов с клавишами. Каждая плитка грузится сама по себе. */

import { useEffect, useState, type ReactNode } from "react";
import {
  fetchProcurements, fetchRequests, fetchSiTypes, fetchTrip, fetchTrips, fetchWorkOrders,
  type HubRequest, type ProcurementRow, type Trip, type TripBrief, type TripStop, type WorkOrder,
} from "../../api";
import { fetchOverview, type Overview } from "../../dispatchApi";
import { addresses, dateRange, daysUntil, instruments, plural, today as todayIso } from "../../components/tripUtil";
import Icon, { type IconName } from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { Skeleton } from "../../ui/Skeleton";
import { SECTION_BY_KEY, SITE_URL } from "../registry";
import type { ModeProps, SectionKey } from "../types";
import "./today.css";

type Loaded<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "error" };

function useLoad<T>(load: (() => Promise<T>) | null, deps: unknown[]): Loaded<T> {
  const [value, setValue] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    if (!load) return;
    let alive = true;
    setValue({ state: "loading" });
    load()
      .then((data) => alive && setValue({ state: "ok", data }))
      .catch(() => alive && setValue({ state: "error" }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return value;
}

const greeting = () => {
  const hour = new Date().getHours();
  if (hour < 5) return "Доброй ночи";
  if (hour < 12) return "Доброе утро";
  if (hour < 18) return "Добрый день";
  return "Добрый вечер";
};

/** «Стариков Сергей Владимирович» → «Сергей Владимирович» */
const callName = (full: string) => {
  const parts = full.split(/\s+/).filter(Boolean);
  return parts.length >= 2 ? parts.slice(1).join(" ") : full;
};

const hhmm = (value: string | null | undefined) => (value ? value.slice(0, 5) : "");
const ageText = (iso: string) => {
  const hours = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 3_600_000));
  if (hours < 1) return "меньше часа";
  if (hours < 48) return `${hours} ${plural(hours, "час", "часа", "часов")}`;
  const days = Math.round(hours / 24);
  return `${days} ${plural(days, "день", "дня", "дней")}`;
};

const coversToday = (trip: TripBrief, day: string) =>
  !!trip.date_start && trip.date_start <= day && (trip.date_end ?? trip.date_start) >= day;
const isMine = (trip: TripBrief, employeeId: number | undefined) =>
  !!employeeId && (trip.lead.id === employeeId || trip.members.some((member) => member.id === employeeId));

const isOpenLab = (order: WorkOrder) => !order.issued_at && order.status !== "cancelled";
const isReadyLab = (order: WorkOrder) =>
  isOpenLab(order) && order.verifications_total > 0 && order.verifications_ready >= order.verifications_total;

export default function TodayMode({ me, isManager, isControl, isReviewer, go }: ModeProps) {
  const day = todayIso();
  const employeeId = me?.employee?.id;
  const ready = !!me;

  const trips = useLoad<TripBrief[]>(ready ? () => fetchTrips({ state: "open" }) : null, [ready]);
  const cityTrip: TripBrief | null =
    trips.state === "ok"
      ? trips.data.find((t) => t.kind === "city" && coversToday(t, day) && (isMine(t, employeeId) || !isManager)) ?? null
      : null;
  const cityDetail = useLoad<Trip | null>(
    trips.state === "ok" ? () => (cityTrip ? fetchTrip(cityTrip.id) : Promise.resolve(null)) : null,
    [trips.state, cityTrip?.id],
  );
  const lab = useLoad<WorkOrder[]>(
    ready ? () => fetchWorkOrders({ kind: "lab", trip: "none", ...(isManager || !employeeId ? {} : { assigned_employee: String(employeeId) }) }) : null,
    [ready, isManager, employeeId],
  );
  const requests = useLoad<HubRequest[]>(ready && isManager ? () => fetchRequests({ status: "new" }) : null, [ready, isManager]);
  const procurements = useLoad<ProcurementRow[]>(ready ? () => fetchProcurements({ state: "open" }) : null, [ready]);
  const review = useLoad<number>(ready && isReviewer ? () => fetchSiTypes({ needs_review: true }).then((list) => list.length) : null, [ready, isReviewer]);
  const overview = useLoad<Overview>(ready && isControl ? () => fetchOverview() : null, [ready, isControl]);

  const name = me?.employee?.full_name ? callName(me.employee.full_name) : "";
  const dateText = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="today">
      <header className="today-hello">
        <h1>{greeting()}{name ? `, ${name}` : ""}</h1>
        <p>
          <span className="today-date">{dateText}</span>
          {isControl && (
            <button type="button" className="link" onClick={() => go("control")}>
              Панель фирмы <Icon name="arrowRight" size={14} />
            </button>
          )}
        </p>
      </header>

      <div className="today-grid">
        {isControl ? (
          <FirmDay overview={overview} go={go} />
        ) : (
          <CityDay trips={trips} trip={cityTrip} detail={cityDetail} go={go} />
        )}

        <div className="today-readings">
          <IntakeReading lab={lab} go={go} />
          {isManager && <RequestsReading requests={requests} go={go} />}
          <TripsReading trips={trips} employeeId={employeeId} isManager={isManager} go={go} />
          <ProcurementsReading procurements={procurements} go={go} />
          {isReviewer && <ReviewReading review={review} go={go} />}
        </div>
      </div>

      <ModeIndex go={go} hide={["intake", "trips", "procurements", ...(isManager ? ["requests" as const] : []), ...(isReviewer ? ["si-templates" as const] : []), ...(isControl ? [] : ["city" as const])]} isManager={isManager} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// День: адреса в городе (поверитель) или вся фирма (руководство)

function CityDay({
  trips, trip, detail, go,
}: {
  trips: Loaded<TripBrief[]>;
  trip: TripBrief | null;
  detail: Loaded<Trip | null>;
  go: ModeProps["go"];
}) {
  const day = todayIso();
  const loading = trips.state === "loading" || (trip && detail.state === "loading");
  const stops: TripStop[] =
    detail.state === "ok" && detail.data
      ? detail.data.stops.filter((stop) => !stop.planned_date || stop.planned_date === day)
      : [];
  const done = stops.filter((stop) => stop.status !== "planned").length;
  const next = stops.filter((stop) => stop.status === "planned");

  return (
    <section className="today-day" aria-labelledby="today-day-title">
      <div className="day-head">
        <span className="day-icon"><Icon name="route" size={20} /></span>
        <div className="grow">
          <h3 id="today-day-title">Мой день в городе</h3>
          <div className="sub">
            {loading ? <Skeleton width={160} height={12} /> : trip ? `${trip.title} · ${addresses(stops.length)}` : "Адресов на сегодня нет"}
          </div>
        </div>
        <Kbd className="kbd-hint">1</Kbd>
      </div>

      {loading && (
        <ol className="day-stops" aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <li key={index}><Skeleton width={44} height={14} /><Skeleton width={`${70 - index * 10}%`} height={14} /></li>
          ))}
        </ol>
      )}

      {!loading && trips.state === "error" && (
        <p className="day-note">Не удалось загрузить выезды — проверьте связь. Записанное на телефоне не пропадёт.</p>
      )}

      {!loading && trip && stops.length > 0 && (
        <>
          <div className="day-meter" role="img" aria-label={`Пройдено ${done} из ${stops.length}`}>
            <span className="data big">{done}</span>
            <span className="data of">/ {stops.length}</span>
            <span className="meter-text">{next.length ? `осталось ${addresses(next.length)}` : "все адреса пройдены"}</span>
            <span className="meter-bar"><span style={{ width: `${(done / Math.max(stops.length, 1)) * 100}%` }} /></span>
          </div>
          <ol className="day-stops">
            {(next.length ? next : stops).slice(0, 5).map((stop) => (
              <li key={stop.id} className={stop.status !== "planned" ? "is-done" : ""}>
                <span className="data when">
                  {stop.window_start ? `${hhmm(stop.window_start)}–${hhmm(stop.window_end)}` : stop.eta ? hhmm(stop.eta) : "—"}
                </span>
                <span className="where">
                  <b>{stop.address}</b>
                  <span className="sub">
                    {[stop.client || stop.contact_name, stop.planned_count ? instruments(stop.planned_count) : ""].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </li>
            ))}
          </ol>
          {next.length > 5 && <div className="sub day-more">и ещё {addresses(next.length - 5)}</div>}
        </>
      )}

      {!loading && trips.state === "ok" && (!trip || stops.length === 0) && (
        <p className="day-note">
          Подтверждённые заявки сами встают в ваш день. Позвонил клиент — запишите его, адрес появится здесь.
        </p>
      )}

      <div className="day-actions">
        {trip ? (
          <button type="button" className="primary" onClick={() => go("city", { action: "open-trip", payload: trip.id })}>
            Открыть день <Icon name="arrowRight" size={16} />
          </button>
        ) : (
          <button type="button" className="primary" onClick={() => go("city", { action: "new-visit" })}>
            <Icon name="phone" size={16} /> Записать клиента
          </button>
        )}
        <button type="button" className="ghost" onClick={() => go("city")}>Карта и все выезды</button>
      </div>
    </section>
  );
}

function FirmDay({ overview, go }: { overview: Loaded<Overview>; go: ModeProps["go"] }) {
  const t = overview.state === "ok" ? overview.data.totals : null;
  const attention = overview.state === "ok" ? overview.data.attention.slice(0, 4) : [];
  return (
    <section className="today-day" aria-labelledby="today-firm-title">
      <div className="day-head">
        <span className="day-icon"><Icon name="chart" size={20} /></span>
        <div className="grow">
          <h3 id="today-firm-title">Фирма сегодня</h3>
          <div className="sub">
            {t ? `в городе ${t.in_city}, в командировках ${t.on_trip}, свободны ${t.free}` : overview.state === "error" ? "Сводка не загрузилась" : <Skeleton width={200} height={12} />}
          </div>
        </div>
      </div>
      {t ? (
        <div className="day-meter">
          <span className="data big">{t.stops_done}</span>
          <span className="data of">/ {t.stops_total}</span>
          <span className="meter-text">адресов пройдено{t.stops_waiting ? `, ждут ${t.stops_waiting}` : ""}</span>
          <span className="meter-bar"><span style={{ width: `${(t.stops_done / Math.max(t.stops_total, 1)) * 100}%` }} /></span>
        </div>
      ) : (
        overview.state === "loading" && <Skeleton height={44} radius={10} />
      )}
      {attention.length > 0 && (
        <>
          <div className="day-sub-title">Требует внимания</div>
          <ol className="day-stops">
            {attention.map((item) => (
              <li key={`${item.type}-${item.id}`}>
                <span className="data when">{item.time ? hhmm(item.time) : item.date ? item.date.slice(8, 10) + "." + item.date.slice(5, 7) : "—"}</span>
                <span className="where">
                  <b>{item.title}</b>
                  <span className="sub">{item.subtitle}</span>
                </span>
              </li>
            ))}
          </ol>
        </>
      )}
      {t && attention.length === 0 && <p className="day-note">Ничего не горит: заявки разобраны, задачи в сроке.</p>}
      <div className="day-actions">
        <button type="button" className="primary" onClick={() => go("control")}>
          Открыть Панель <Icon name="arrowRight" size={16} />
        </button>
        <button type="button" className="ghost" onClick={() => go("city")}>Карта выездов</button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Показания режимов: цифра — что она значит — одно действие

function Reading({
  mode, icon, value, unit, children, tone, action, onOpen, loading,
}: {
  mode: SectionKey;
  icon: IconName;
  value: ReactNode;
  unit?: string;
  children: ReactNode;
  tone?: "pass" | "signal" | "fail";
  action?: { label: string; icon?: IconName; onClick: () => void };
  onOpen: () => void;
  loading?: boolean;
}) {
  const def = SECTION_BY_KEY.get(mode);
  return (
    <section className={`reading ${tone ? `tone-${tone}` : ""}`}>
      <button type="button" className="reading-main" onClick={onOpen} aria-label={`${def?.title}: открыть`}>
        <span className="reading-title">
          <Icon name={icon} size={16} />
          {def?.title}
          {def?.digit && <Kbd className="kbd-hint">{def.digit}</Kbd>}
        </span>
        <span className="reading-value">
          {loading ? <Skeleton width={56} height={30} radius={6} /> : <span className="data">{value}</span>}
          {unit && !loading && <span className="reading-unit">{unit}</span>}
        </span>
        <span className="reading-text">{loading ? <Skeleton width="80%" height={12} /> : children}</span>
      </button>
      {action && (
        <button type="button" className="reading-action" onClick={action.onClick}>
          {action.icon && <Icon name={action.icon} size={15} />}
          {action.label}
        </button>
      )}
    </section>
  );
}

function IntakeReading({ lab, go }: { lab: Loaded<WorkOrder[]>; go: ModeProps["go"] }) {
  const open = lab.state === "ok" ? lab.data.filter(isOpenLab) : [];
  const readyCount = open.filter(isReadyLab).length;
  return (
    <Reading
      mode="intake"
      icon="box"
      loading={lab.state === "loading"}
      value={lab.state === "error" ? "—" : open.length}
      unit={plural(open.length, "приёмка", "приёмки", "приёмок")}
      tone={readyCount ? "pass" : undefined}
      onOpen={() => go("intake")}
      action={{ label: "Новая приёмка", icon: "plus", onClick: () => go("intake", { action: "new" }) }}
    >
      {lab.state === "error"
        ? "Не загрузилось — нет связи?"
        : open.length === 0
          ? "В лаборатории пусто. Принесли приборы — заведите приёмку."
          : readyCount
            ? <><b className="pass-text">{readyCount} готовы к выдаче</b>, остальные в поверке</>
            : "в работе, к выдаче пока нет"}
    </Reading>
  );
}

function RequestsReading({ requests, go }: { requests: Loaded<HubRequest[]>; go: ModeProps["go"] }) {
  const list = requests.state === "ok" ? requests.data : [];
  const oldest = list.reduce<HubRequest | null>((min, r) => (!min || r.created_at < min.created_at ? r : min), null);
  return (
    <Reading
      mode="requests"
      icon="inbox"
      loading={requests.state === "loading"}
      value={requests.state === "error" ? "—" : list.length}
      unit={plural(list.length, "новая", "новые", "новых")}
      tone={list.length ? "signal" : undefined}
      onOpen={() => go("requests")}
      action={list.length ? { label: "Разобрать", icon: "arrowRight", onClick: () => go("requests") } : undefined}
    >
      {requests.state === "error"
        ? "Не загрузилось"
        : oldest
          ? <>самая давняя ждёт {ageText(oldest.created_at)} — {oldest.contact_name || oldest.address || `№${oldest.id}`}</>
          : "Все заявки разобраны"}
    </Reading>
  );
}

function TripsReading({
  trips, employeeId, isManager, go,
}: {
  trips: Loaded<TripBrief[]>;
  employeeId?: number;
  isManager: boolean;
  go: ModeProps["go"];
}) {
  const day = todayIso();
  const list = trips.state === "ok" ? trips.data.filter((t) => t.kind !== "city" && (isManager || isMine(t, employeeId))) : [];
  const now = list.filter((t) => t.status === "active" || coversToday(t, day));
  const upcoming = list
    .filter((t) => t.status === "planned" && !coversToday(t, day) && t.date_start && t.date_start > day)
    .sort((a, b) => (a.date_start ?? "").localeCompare(b.date_start ?? ""))[0];
  const inDays = upcoming ? daysUntil(upcoming.date_start) : null;
  return (
    <Reading
      mode="trips"
      icon="car"
      loading={trips.state === "loading"}
      value={trips.state === "error" ? "—" : now.length}
      unit={now.length ? "идут сейчас" : "сейчас нет"}
      onOpen={() => go("trips")}
      action={{ label: "Новая командировка", icon: "plus", onClick: () => go("trips", { action: "new-trip" }) }}
    >
      {now[0] ? (
        <>{now[0].title}{now[0].stops_total ? ` · ${now[0].stops_done}/${now[0].stops_total} адресов` : ""}</>
      ) : upcoming ? (
        <>Ближайшая — {upcoming.title}, {dateRange(upcoming.date_start, upcoming.date_end)}{inDays != null && inDays > 0 ? ` (через ${inDays} ${plural(inDays, "день", "дня", "дней")})` : ""}</>
      ) : (
        "Командировок не запланировано"
      )}
    </Reading>
  );
}

function ProcurementsReading({ procurements, go }: { procurements: Loaded<ProcurementRow[]>; go: ModeProps["go"] }) {
  const list = procurements.state === "ok" ? procurements.data : [];
  const nearest = list
    .filter((p) => p.deadline)
    .sort((a, b) => (a.deadline ?? "").localeCompare(b.deadline ?? ""))[0];
  const left = nearest ? daysUntil(nearest.deadline) : null;
  const planned = list.reduce((sum, p) => sum + p.progress.planned, 0);
  const entered = list.reduce((sum, p) => sum + p.progress.entered, 0);
  return (
    <Reading
      mode="procurements"
      icon="contract"
      loading={procurements.state === "loading"}
      value={procurements.state === "error" ? "—" : list.length}
      unit={plural(list.length, "контракт", "контракта", "контрактов")}
      tone={left != null && left <= 7 ? "signal" : undefined}
      onOpen={() => go("procurements")}
      action={nearest ? { label: nearest.label, icon: "arrowRight", onClick: () => go("procurements", { action: "open-procurement", payload: nearest.id }) } : undefined}
    >
      {list.length === 0
        ? "Контрактов в работе нет"
        : <>
            {planned > 0 && <>поверено <span className="data">{entered}</span> из <span className="data">{planned}</span>{nearest ? " · " : ""}</>}
            {nearest && left != null && (left < 0 ? `срок по ${nearest.label} прошёл` : `ближайший срок через ${left} ${plural(left, "день", "дня", "дней")}`)}
          </>}
    </Reading>
  );
}

function ReviewReading({ review, go }: { review: Loaded<number>; go: ModeProps["go"] }) {
  const count = review.state === "ok" ? review.data : 0;
  return (
    <Reading
      mode="si-templates"
      icon="layers"
      loading={review.state === "loading"}
      value={review.state === "error" ? "—" : count}
      unit={plural(count, "тип СИ", "типа СИ", "типов СИ")}
      tone={count ? "signal" : undefined}
      onOpen={() => go("si-templates")}
      action={count ? { label: "Проверить", icon: "checkCircle", onClick: () => go("si-types") } : undefined}
    >
      {count ? "ждут проверки метролога" : "Все типы проверены — можно готовить шаблоны"}
    </Reading>
  );
}

// ---------------------------------------------------------------------------
// Указатель остальных режимов — тихая линейка, не карточки

function ModeIndex({ go, hide, isManager }: { go: ModeProps["go"]; hide: SectionKey[]; isManager: boolean }) {
  const keys: (SectionKey | "site")[] = ["city", "si-templates", "att-templates", "site", "journal"];
  const rows = keys
    .filter((key) => !hide.includes(key as SectionKey))
    .map((key) => SECTION_BY_KEY.get(key)!)
    .filter((def) => def && (def.key !== "att-templates" || isManager));
  if (!rows.length) return null;
  return (
    <nav className="mode-index" aria-label="Другие режимы">
      {rows.map((def) =>
        def.href ? (
          <a key={def.key} className="mode-index-row" href={SITE_URL} target="_blank" rel="noopener">
            <Icon name={def.icon} size={18} />
            <span className="grow"><b>{def.title}</b><span className="sub">{def.description}</span></span>
            {def.digit && <Kbd className="kbd-hint">{def.digit}</Kbd>}
            <Icon name="arrowUpRight" size={16} className="muted" />
          </a>
        ) : (
          <button key={def.key} type="button" className="mode-index-row" onClick={() => go(def.key)}>
            <Icon name={def.icon} size={18} />
            <span className="grow"><b>{def.title}</b><span className="sub">{def.description}</span></span>
            {def.digit && <Kbd className="kbd-hint">{def.digit}</Kbd>}
            <Icon name="chevronRight" size={16} className="muted" />
          </button>
        ),
      )}
    </nav>
  );
}
