/** Режим 5 «Сайт» — одностраничник фирмы на /o-nas/ (urls.py → frontend_index, main.tsx).
 *
 * Посыл: поверка даёт юридическую возможность честно работать — без документа о
 * поверенном оборудовании нельзя выпускать продукцию, торговать по весу, сдавать объект.
 * Цель страницы — заявка: форма на первом экране, текста минимум. SEO-разметка
 * (title, description, OpenGraph, schema.org LocalBusiness) — в frontend/index.html. */

import { useEffect, useRef, useState } from "react";
import { fetchFamilies, type Family } from "../api";
import PublicRequestForm from "../components/PublicRequestForm";
import AsciiField from "./AsciiField";
import { FIRM, telHref } from "./firm";
import "./landing.css";

const money = (value: number) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);

const SERVICES: { code: string; title: string; text: string }[] = [
  { code: "01", title: "Счётчики воды и тепла", text: "Дома у владельца, без снятия. Приедем в выбранное окно — 2 часа." },
  { code: "02", title: "Приборы предприятий", text: "Манометры, весы, термометры и другие СИ — на месте или в нашей лаборатории." },
  { code: "03", title: "Выезд в районы области", text: "Собираем заявки района в одну поездку — поверка на месте, без отправки приборов в Киров." },
  { code: "04", title: "Аттестация испытательного оборудования", text: "Камеры, стенды, печи — по ГОСТ Р 8.568, с протоколом и аттестатом." },
  { code: "05", title: "Запуск нового производства", text: "Подскажем, какие приборы нужны для законной работы, какие документы и в каком порядке." },
];

const RISKS: [string, string][] = [
  ["Штраф", "за применение неповеренных средств измерений — ст. 19.19 КоАП РФ"],
  ["Измерения без силы", "их не примут заказчик, проверяющий, суд"],
  ["Стоп на приёмке", "продукции, объекта или госконтракта — пока нет записи о поверке"],
];

const STEPS: [string, string][] = [
  ["Заявка", "здесь, на сайте, или по телефону"],
  ["Звонок", "согласуем приборы, день и окно 2 часа"],
  ["Поверка", "на месте или в нашей лаборатории"],
  ["Документы", "результат — в ФГИС «Аршин», по запросу — свидетельство"],
];

const FAQ: [string, string][] = [
  ["Нужно ли снимать счётчик воды?", "Обычно нет: поверяем на месте, вода нужна только из крана. Если прибор не прошёл поверку — скажем, чем заменить."],
  ["Какой документ я получу?", "Результат поверки вносится в ФГИС «Аршин» — это и есть официальное подтверждение. Бумажное свидетельство или извещение о непригодности — по запросу."],
  ["Работаете с организациями и госзаказчиками?", "Да: договор, счёт, закрывающие документы. Участвуем в закупках по 44-ФЗ и 223-ФЗ."],
  ["Я только запускаю производство — с чего начать?", "Оставьте заявку и опишите в комментарии, что будете делать. Подскажем, какие средства измерений нужны, какие из них поверять, а какое оборудование — аттестовать."],
  ["Сколько стоит и когда приедете?", "Ориентир — в форме и в ценах ниже, точную сумму назовём по телефону. В Кирове выезжаем в любой день недели, в районы — по графику поездок."],
];

export default function Landing() {
  const [families, setFamilies] = useState<Family[]>([]);
  // Нижняя панель на телефоне: видна, когда первый экран пролистан и форма
  // не на экране — чтобы позвонить или вернуться к заявке из любого места.
  const formRef = useRef<HTMLElement | null>(null);
  const [formVisible, setFormVisible] = useState(true);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const form = formRef.current;
    if (!form || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setFormVisible(entry.isIntersecting), {
      rootMargin: "-25% 0px -25% 0px",
    });
    observer.observe(form);
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        setScrolled(window.scrollY > 240);
        ticking = false;
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  useEffect(() => {
    document.title = "Поверка счётчиков и средств измерений в Кирове — Единица Измерения";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#04060c");
    void fetchFamilies().then((response) => setFamilies(response.families));
  }, []);

  const tel = telHref();
  const priced = families.filter((f) => Number(f.price) > 0);

  return (
    <div className="landing">
      <a className="l-skip" href="#zayavka">К форме заявки</a>
      <AsciiField />

      <header className="l-top">
        <a className="l-brand" href="#top" aria-label={`${FIRM.short} — наверх`}>
          <span className="l-logo-mark" aria-hidden="true">ЕИ</span>
          <span className="l-logo-name">{FIRM.short}</span>
        </a>
        <nav className="l-nav" aria-label="Разделы">
          <a href="#uslugi">Услуги</a>
          <a href="#kak">Как работаем</a>
          <a href="#voprosy">Вопросы</a>
          <a href="#kontakty">Контакты</a>
        </nav>
        <a className="l-top-phone" href={tel}>{FIRM.phone}</a>
      </header>

      <main id="top">
        <div className="l-first">
          <section className="l-hero" aria-labelledby="l-h1">
            <p className="l-kicker">поверка средств измерений · Киров и область</p>
            <h1 id="l-h1">
              Законная работа<br />
              начинается с&nbsp;поверки
            </h1>
            <p className="l-lead">
              Без записи о поверке оборудование нельзя применять в производстве, торговле
              и на госконтрактах. Поверим приборы и внесём результат в ФГИС «Аршин» —
              от счётчика воды до испытательной камеры.
            </p>
            <ul className="l-bullets">
              <li>Подскажем, что нужно, если вы только начинаете</li>
              <li>Выезд по Кирову в любой день, в районы — по графику</li>
              <li>Счётчики — окном в 2 часа, без снятия</li>
            </ul>
            <dl className="l-contacts">
              <dt>Телефон</dt>
              <dd><a href={tel}>{FIRM.phone}</a></dd>
              <dt>Офис</dt>
              <dd>{FIRM.address} · <span className="nowrap">{FIRM.hours}</span></dd>
            </dl>
          </section>

          <section className="l-form" id="zayavka" ref={formRef} aria-label="Заявка на поверку">
            <PublicRequestForm embedded />
          </section>
        </div>

        <section className="l-more l-section" id="uslugi" aria-labelledby="h-uslugi">
          <h2 id="h-uslugi">Что мы делаем</h2>
          <ul className="l-services">
            {SERVICES.map((s) => (
              <li key={s.code}>
                <span className="l-code">{s.code}</span>
                <b>{s.title}</b>
                <span>{s.text}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="l-more l-section" aria-labelledby="h-risk">
          <h2 id="h-risk">Без поверки — нельзя</h2>
          <div className="l-risks">
            {RISKS.map(([head, text]) => (
              <div key={head}>
                <b>{head}</b>
                <span>{text}</span>
              </div>
            ))}
          </div>
          <p className="l-note">
            102-ФЗ «Об обеспечении единства измерений» требует поверенные средства измерений
            везде, где измерения влияют на безопасность, учёт и расчёты. Подтверждение поверки —
            запись в ФГИС «Аршин»:{" "}
            <a href="https://fgis.gost.ru/fundmetrology/cm/results" target="_blank" rel="noreferrer">
              проверить свой прибор ↗
            </a>
          </p>
        </section>

        <section className="l-more l-section" id="kak" aria-labelledby="h-kak">
          <h2 id="h-kak">Как это работает</h2>
          <ol className="l-steps">
            {STEPS.map(([head, text], i) => (
              <li key={head}>
                <span>{String(i + 1).padStart(2, "0")}</span>
                <b>{head}</b>
                <small>{text}</small>
              </li>
            ))}
          </ol>
        </section>

        {priced.length > 0 && (
          <section className="l-more l-section" aria-labelledby="h-ceny">
            <h2 id="h-ceny">Цены</h2>
            <ul className="l-prices">
              {priced.map((family) => (
                <li key={family.id}>
                  <span>{family.name}</span>
                  <i aria-hidden="true" />
                  <b>от {money(Number(family.price))} ₽</b>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="l-more l-section" id="voprosy" aria-labelledby="h-faq">
          <h2 id="h-faq">Частые вопросы</h2>
          <div className="l-faq">
            {FAQ.map(([q, a]) => (
              <details key={q}>
                <summary>{q}</summary>
                <p>{a}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="l-more l-section" id="kontakty" aria-labelledby="h-kontakty">
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
            {FIRM.email && (
              <div>
                <span className="l-code">почта</span>
                <a href={`mailto:${FIRM.email}`}>{FIRM.email}</a>
              </div>
            )}
          </address>
          <a href="#zayavka" className="l-btn">Оставить заявку</a>
        </section>
      </main>

      <footer className="l-foot">
        <span>{FIRM.name}</span>
        <span>{FIRM.address}</span>
        <a href={tel}>{FIRM.phone}</a>
        <a href="/">Вход для сотрудников</a>
      </footer>

      <nav className={`l-bar ${scrolled && !formVisible ? "show" : ""}`} aria-label="Быстрые действия">
        <a className="call" href={tel}>Позвонить</a>
        <a className="go" href="#zayavka">Заявка</a>
      </nav>
    </div>
  );
}
