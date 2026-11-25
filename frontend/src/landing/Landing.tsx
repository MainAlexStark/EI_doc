/** Режим 5 «Сайт» — две версии одностраничника фирмы (urls.py → frontend_index, main.tsx):
 *
 *  - «meters»   — /schetchiki/ (и старый /o-nas/): поверка счётчиков для населения,
 *                 заголовок по городу посетителя — Киров или Яранск;
 *  - «business» — /biznes/: поверка СИ и аттестация оборудования для организаций.
 *
 * Бело-чёрная тема, фон — весы из символов (AsciiField): заголовок, цена, два-три факта, форма.
 * SEO-разметка по умолчанию — в frontend/index.html, title/description/canonical
 * меняются здесь под версию. */

import { useEffect, useState } from "react";
import PublicRequestForm from "../components/PublicRequestForm";
import AsciiField from "./AsciiField";
import { FIRM, telHref } from "./firm";
import "./landing.css";

export type SiteVariant = "meters" | "business";
type City = "kirov" | "yaransk";

const CITY_NAME: Record<City, string> = { kirov: "Киров", yaransk: "Яранск" };
const CITY_IN: Record<City, string> = { kirov: "в Кирове", yaransk: "в Яранске" };
const CITY_KEY = "ei-site-city";

const OFFICIAL =
  "Работаем официально: аттестованы в области метрологии, результат каждой поверки вносим в ФГИС «Аршин».";

const STEPS: Record<SiteVariant, [string, string][]> = {
  meters: [
    ["Заявка", "на сайте или по телефону"],
    ["Звонок", "согласуем день и окно визита — 2 часа"],
    ["Поверка", "у вас дома или у нас в офисе"],
    ["Результат", "вносим в ФГИС «Аршин»"],
  ],
  business: [
    ["Заявка", "список приборов или оборудования"],
    ["Договор", "согласуем объём, сроки и место работ"],
    ["Работы", "на вашей площадке или в нашей лаборатории"],
    ["Документы", "запись в ФГИС «Аршин», протоколы, аттестаты, акт"],
  ],
};

const FAQ: Record<SiteVariant, [string, string][]> = {
  meters: [
    ["Нужно ли снимать счётчик?", "Нет, поверяем на месте — нужна только вода из крана. Если удобнее, можете снять счётчик сами и привезти к нам в офис."],
    ["Какой документ я получу?", "Результат поверки мы вносим в ФГИС «Аршин» — управляющая компания видит его там сама, ничего приносить не нужно."],
    ["Что если счётчик не пройдёт поверку?", "Выдадим извещение о непригодности и подскажем, какой счётчик поставить взамен."],
  ],
  business: [
    ["Работаете по всей России?", "Да. Поверку и аттестацию проводим с выездом на вашу площадку или в нашей лаборатории — формат согласуем под объём работ."],
    ["Какие документы получим?", "Запись о поверке в ФГИС «Аршин», по запросу — свидетельства; для аттестации — протокол и аттестат. Работаем по договору."],
    ["Работаете с госзаказчиками?", "Да, участвуем в закупках по 44-ФЗ и 223-ФЗ."],
    ["Что будет без поверки?", "Измерения неповеренными приборами не имеют юридической силы, а за их применение предусмотрен штраф по ст. 19.19 КоАП РФ."],
    ["Только запускаем производство — с чего начать?", "Оставьте заявку и опишите, что будете делать. Подскажем, какие приборы нужно поверять, а какое оборудование — аттестовать."],
  ],
};

const BUSINESS_SERVICES: [string, string][] = [
  ["Поверка средств измерений", "Манометры, весы, термометры, датчики и другие СИ — на вашей площадке или в лаборатории."],
  ["Аттестация испытательного оборудования", "Камеры, стенды, печи — по ГОСТ Р 8.568, с протоколом и аттестатом."],
  ["Запуск производства", "Подскажем, какие приборы нужны для законной работы, что поверить и что аттестовать."],
];

/** Адрес версии по городу — у Яранска своя страница, чтобы поисковики видели обе. */
const METERS_PATH: Record<City, string> = { kirov: "/schetchiki/", yaransk: "/schetchiki/yaransk/" };

function cityFromUrl(): City | null {
  if (window.location.pathname.startsWith("/schetchiki/yaransk")) return "yaransk";
  const fromQuery = new URLSearchParams(window.location.search).get("gorod");
  return fromQuery === "kirov" || fromQuery === "yaransk" ? fromQuery : null;
}

/** Город: адрес (/schetchiki/yaransk/, ?gorod=) → выбор посетителя → город по IP → Киров. */
function useCity(enabled: boolean): [City, (c: City) => void] {
  const [city, setCityState] = useState<City>(() => {
    const fromUrl = cityFromUrl();
    if (fromUrl) return fromUrl;
    try {
      const saved = localStorage.getItem(CITY_KEY);
      if (saved === "kirov" || saved === "yaransk") return saved;
    } catch { /* приватный режим — просто не помним */ }
    return "kirov";
  });

  useEffect(() => {
    if (!enabled || cityFromUrl()) return;
    let saved: string | null = null;
    try { saved = localStorage.getItem(CITY_KEY); } catch { /* нет хранилища */ }
    if (saved) return; // посетитель уже выбрал сам
    let cancelled = false;
    void fetch("/api/hub/visitor-city/")
      .then((r) => (r.ok ? r.json() : { city: "" }))
      .then((data: { city?: string }) => {
        if (!cancelled && (data.city === "kirov" || data.city === "yaransk")) setCityState(data.city);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [enabled]);

  const setCity = (next: City) => {
    setCityState(next);
    try { localStorage.setItem(CITY_KEY, next); } catch { /* нет хранилища */ }
    window.history.replaceState(null, "", METERS_PATH[next] + window.location.hash);
  };
  return [city, setCity];
}

function setMeta(title: string, description: string, canonical: string) {
  document.title = title;
  document.querySelector('meta[name="description"]')?.setAttribute("content", description);
  document.querySelector('meta[property="og:title"]')?.setAttribute("content", title);
  document.querySelector('meta[property="og:description"]')?.setAttribute("content", description);
  document.querySelector('link[rel="canonical"]')?.setAttribute("href", canonical);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#ffffff");
}

export default function Landing({ variant = "meters" }: { variant?: SiteVariant }) {
  const meters = variant === "meters";
  const [city, setCity] = useCity(meters);
  const [scrolled, setScrolled] = useState(false);
  const tel = telHref();

  useEffect(() => {
    if (meters) {
      setMeta(
        `Поверка счётчиков воды ${CITY_IN[city]} — без снятия, от 500 ₽ | Единица Измерения`,
        `Поверка счётчиков воды ${CITY_IN[city]}: на дому без демонтажа или в офисе. 500 ₽ за счётчик в городе, 600 ₽ в пригороде. Официально, запись в ФГИС «Аршин». Тел. ${FIRM.phone}.`,
        METERS_PATH[city],
      );
    } else {
      setMeta(
        "Метрология для бизнеса: поверка СИ и аттестация оборудования | Единица Измерения",
        `Поверка средств измерений и аттестация испытательного оборудования по всей России. Работаем официально по договору, запись в ФГИС «Аршин». Тел. ${FIRM.phone}.`,
        "/biznes/",
      );
    }
  }, [meters, city]);

  useEffect(() => {
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => { setScrolled(window.scrollY > 320); ticking = false; });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="landing">
      <a className="l-skip" href="#zayavka">К форме заявки</a>
      <AsciiField />

      <header className="l-top">
        <a className="l-brand" href="#top" aria-label={`${FIRM.short} — наверх`}>
          <span className="l-logo-mark" aria-hidden="true">ЕИ</span>
          <span className="l-logo-name">{FIRM.short}</span>
        </a>
        {meters && (
          <div className="l-city" role="group" aria-label="Город">
            {(Object.keys(CITY_NAME) as City[]).map((c) => (
              <button key={c} type="button" className={c === city ? "on" : ""} aria-pressed={c === city} onClick={() => setCity(c)}>
                {CITY_NAME[c]}
              </button>
            ))}
          </div>
        )}
        <a className="l-top-alt" href={meters ? "/biznes/" : "/schetchiki/"}>
          {meters ? "Для бизнеса →" : "Счётчики для жителей →"}
        </a>
        <a className="l-top-phone" href={tel}>{FIRM.phone}</a>
      </header>

      <main id="top">
        <section className="l-hero" aria-labelledby="l-h1">
          {!meters && <p className="l-kicker">Поверка и аттестация по всей России</p>}
          {meters ? (
            <h1 id="l-h1">Поверка счётчиков<br /><span className="l-h1-city">{CITY_NAME[city]}</span></h1>
          ) : (
            <h1 id="l-h1">Метрология<br />для вашего бизнеса</h1>
          )}
          <p className="l-lead">
            {meters
              ? "Поверим счётчики воды у вас дома без снятия — или примем счётчик в офисе, если вы сняли его сами."
              : "Поверка средств измерений и аттестация испытательного оборудования для предприятий, лабораторий и торговли — по договору, с полным пакетом документов."}
          </p>

          {meters && (
            <div className="l-prices" aria-label="Цены">
              <div><b>500 ₽</b><span>за счётчик в городе</span></div>
              <div><b>600 ₽</b><span>за счётчик в пригороде</span></div>
            </div>
          )}

          <div className="l-actions">
            <a className="l-btn" href="#zayavka">Оставить заявку</a>
            <a className="l-btn ghost" href={tel}>Позвонить</a>
          </div>
          <p className="l-official">{OFFICIAL}</p>
        </section>

        {meters ? (
          <section className="l-section" id="sposoby" aria-labelledby="h-sposoby">
            <h2 id="h-sposoby">Два способа</h2>
            <div className="l-grid two">
              <div>
                <span className="l-code">01</span>
                <b>Выезд без демонтажа</b>
                <p>Приедем в согласованное окно — 2 часа. Счётчик не снимаем и пломбы не трогаем — нужна только вода из крана.</p>
              </div>
              <div>
                <span className="l-code">02</span>
                <b>С демонтажом — в офисе</b>
                <p>Сняли счётчик сами? Привезите его к нам: {FIRM.address}, {FIRM.hours}.</p>
              </div>
            </div>
          </section>
        ) : (
          <section className="l-section" id="uslugi" aria-labelledby="h-uslugi">
            <h2 id="h-uslugi">Что делаем</h2>
            <div className="l-grid three">
              {BUSINESS_SERVICES.map(([head, text], i) => (
                <div key={head}>
                  <span className="l-code">{String(i + 1).padStart(2, "0")}</span>
                  <b>{head}</b>
                  <p>{text}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="l-section" id="kak" aria-labelledby="h-kak">
          <h2 id="h-kak">Как проходит поверка</h2>
          <ol className="l-steps">
            {STEPS[variant].map(([head, text], i) => (
              <li key={head}>
                <span className="l-code">{String(i + 1).padStart(2, "0")}</span>
                <b>{head}</b>
                <small>{text}</small>
              </li>
            ))}
          </ol>
        </section>

        <section className="l-section l-form" id="zayavka" aria-label="Заявка на поверку">
          <PublicRequestForm embedded focus={variant} />
        </section>

        <section className="l-section" id="voprosy" aria-labelledby="h-faq">
          <h2 id="h-faq">Частые вопросы</h2>
          <div className="l-faq">
            {FAQ[variant].map(([q, a]) => (
              <details key={q}>
                <summary>{q}</summary>
                <p>{a}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="l-section" id="kontakty" aria-labelledby="h-kontakty">
          <h2 id="h-kontakty">Контакты</h2>
          <address className="l-address">
            <div>
              <span className="l-code">телефон</span>
              <a className="l-big" href={tel}>{FIRM.phone}</a>
            </div>
            <div>
              <span className="l-code">офис</span>
              <span>{FIRM.address}</span>
              <a className="l-map" href={FIRM.mapUrl} target="_blank" rel="noreferrer">на Яндекс Картах ↗</a>
            </div>
            <div>
              <span className="l-code">часы</span>
              <span>офис {FIRM.hours}</span>
              <span>выезды {FIRM.visits}</span>
            </div>
          </address>
        </section>
      </main>

      <footer className="l-foot">
        <span>{FIRM.name}</span>
        {meters ? <a href="/biznes/">Для бизнеса: поверка СИ и аттестация →</a> : <a href="/schetchiki/">Поверка счётчиков для жителей →</a>}
        <a href="/">Вход для сотрудников</a>
      </footer>

      <nav className={`l-bar ${scrolled ? "show" : ""}`} aria-label="Быстрые действия">
        <a className="call" href={tel}>Позвонить</a>
        <a className="go" href="#zayavka">Заявка</a>
      </nav>
    </div>
  );
}
