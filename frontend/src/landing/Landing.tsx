import { useEffect, useRef, useState } from "react";
import { fetchFamilies, type Family } from "../api";
import PublicRequestForm from "../components/PublicRequestForm";
import { CALIPER, GAUGE, PLANE, PLANE_SIDE, READINGS, SCALES, SHIP, THERMO, WATER_METER } from "./ascii";
import "./landing.css";

// Контакты фирмы. Пустая строка — блок не показывается; заполнить, когда
// решим, какой номер и почту светить на сайте.
const PHONE: string = "";
const EMAIL: string = "";

const money = (value: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);

/** Куда и с какой скоростью плывёт каждый рисунок фона. */
const ART: { art: string; className: string }[] = [
  { art: PLANE, className: "a-plane" },
  { art: PLANE_SIDE, className: "a-plane-side" },
  { art: SHIP, className: "a-ship" },
  { art: GAUGE, className: "a-gauge" },
  { art: WATER_METER, className: "a-meter" },
  { art: CALIPER, className: "a-caliper" },
  { art: SCALES, className: "a-scales" },
  { art: THERMO, className: "a-thermo" },
];

function AsciiBackground() {
  const ref = useRef<HTMLDivElement>(null);

  // Лёгкий параллакс от мыши: рисунки смещаются на несколько пикселей.
  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    const onMove = (event: MouseEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        el.style.setProperty("--mx", String(event.clientX / window.innerWidth - 0.5));
        el.style.setProperty("--my", String(event.clientY / window.innerHeight - 0.5));
      });
    };
    window.addEventListener("mousemove", onMove);
    return () => {
      window.removeEventListener("mousemove", onMove);
      cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div className="ascii-bg" ref={ref} aria-hidden="true">
      <div className="ascii-grid" />
      {ART.map(({ art, className }) => (
        <pre key={className} className={`ascii-art ${className}`}>{art}</pre>
      ))}
      {READINGS.map((line, index) => (
        <pre key={line} className={`ascii-ticker t${index}`}>
          {`${line}      ${line}      ${line}      `}
        </pre>
      ))}
      <div className="ascii-vignette" />
    </div>
  );
}

const REASONS = [
  {
    tag: "01",
    title: "Расчёты, которые не оспорить",
    text:
      "Когда вы платите поставщику или берёте деньги с покупателя по показаниям прибора, " +
      "аргументом в споре будет только поверенный прибор. Показания «просроченного» — просто цифры.",
  },
  {
    tag: "02",
    title: "Проверки без сюрпризов",
    text:
      "Закон 102-ФЗ «Об обеспечении единства измерений» требует поверять средства измерений " +
      "в сфере госрегулирования. За работу на неповеренных приборах — ответственность по ст. 19.19 КоАП РФ.",
  },
  {
    tag: "03",
    title: "Счётчики — по факту, а не по нормативу",
    text:
      "Истёк межповерочный интервал — показания счётчика перестают приниматься, и начисления " +
      "уходят сначала на средний расход, а потом на норматив (ПП РФ № 354). Своевременная поверка дешевле.",
  },
  {
    tag: "04",
    title: "Тендеры и госконтракты",
    text:
      "Заказчики проверяют, что оборудование подрядчика поверено. Мы сами работаем по госзакупкам: " +
      "знаем, какие нужны документы и пропуска, ездим на режимные объекты и в командировки по области.",
  },
  {
    tag: "05",
    title: "Безопасность людей",
    text:
      "Манометр на котле, весы на производстве, термометр в лаборатории — за каждым прибором чьё-то " +
      "решение. Честная поверка значит, что это решение принято по правильным цифрам.",
  },
  {
    tag: "06",
    title: "Проверьте нас сами",
    text:
      "Каждый результат мы передаём в ФГИС «Аршин» — с 2020 года только запись в реестре подтверждает " +
      "поверку. Найти свой прибор по заводскому номеру может любой, без звонков нам.",
  },
];

const PRINCIPLES = [
  ["Не «рисуем» поверку.", "Прибор не годен — пишем «не годен» и объясняем, что с ним."],
  ["Без записи в «Аршине» поверки нет.", "Сведения уходят в реестр, протокол — вам."],
  ["Цена известна до выезда.", "Примерная стоимость считается прямо в заявке."],
  ["Время — интервалом в 2 часа,", "а не «ждите в течение дня»."],
  ["Работаем по договору и с документами,", "которые примет бухгалтерия и проверяющий."],
];

const STEPS = [
  ["Заявка", "Адрес, приборы и удобный день — две минуты в форме ниже."],
  ["Выезд", "Поверитель приезжает в согласованный двухчасовой интервал с эталонами."],
  ["Поверка", "Измерения на месте, без снятия прибора там, где это допускает методика."],
  ["Аршин и протокол", "Результат в ФГИС «Аршин», протокол — вам на руки или по почте."],
];

const FALLBACK_FAMILIES = [
  "Счётчики воды", "Теплосчётчики", "Манометры и напоромеры", "Термометры",
  "Весы и гири", "Средства измерения длины", "Электроизмерительные приборы",
];

export default function Landing() {
  const [families, setFamilies] = useState<Family[]>([]);

  useEffect(() => {
    document.title = "Единица Измерения — поверка средств измерений в Кирове";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#05070d");
    void fetchFamilies().then((response) => setFamilies(response.families));
  }, []);

  return (
    <div className="landing">
      <AsciiBackground />

      <header className="l-nav">
        <a href="#top" className="l-logo">
          <span className="l-logo-mark">ЕИ</span>
          <span>Единица Измерения</span>
        </a>
        <nav>
          <a href="#zachem">Зачем</a>
          <a href="#chto">Что поверяем</a>
          <a href="#kak">Как работаем</a>
          <a href="#zayavka" className="l-nav-cta">Оставить заявку</a>
        </nav>
      </header>

      <main id="top">
        <section className="l-hero">
          <p className="l-kicker">
            <span className="l-prompt">&gt;</span> метрологическая служба · г. Киров и область
            <span className="l-caret" />
          </p>
          <h1>
            Честные измерения —<br />
            <span className="l-accent">основа честного бизнеса</span>
          </h1>
          <p className="l-lead">
            Каждый кубометр, килограмм и мегапаскаль, за который платят деньги или отвечают за чью-то
            безопасность, должен быть измерен поверенным прибором. Мы поверяем средства измерений
            и вносим результаты в ФГИС «Аршин» — чтобы ваши расчёты, отчёты и проверки стояли
            на законном основании.
          </p>
          <div className="l-cta">
            <a href="#zayavka" className="l-btn l-btn-primary">Оставить заявку на поверку</a>
            <a href="#zachem" className="l-btn l-btn-ghost">Зачем это нужно</a>
          </div>
          <dl className="l-facts">
            <div><dt>102-ФЗ</dt><dd>работаем по закону об единстве измерений</dd></div>
            <div><dt>«Аршин»</dt><dd>каждый результат — в федеральном реестре</dd></div>
            <div><dt>2 часа</dt><dd>интервал визита вместо «весь день»</dd></div>
            <div><dt>Выезды</dt><dd>по городу, области и в командировки</dd></div>
          </dl>
        </section>

        <section className="l-section" id="zachem">
          <div className="l-section-head">
            <span className="l-label">// зачем бизнесу поверка</span>
            <h2>В России честно работать можно только с поверенными приборами</h2>
            <p>
              Поверка — не формальность для галочки. Это то, что превращает показания прибора в цифру,
              которой верят контрагент, суд, налоговая и проверяющий.
            </p>
          </div>
          <div className="l-grid">
            {REASONS.map((reason) => (
              <article key={reason.tag} className="l-card">
                <span className="l-card-tag">[{reason.tag}]</span>
                <h3>{reason.title}</h3>
                <p>{reason.text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="l-section l-split">
          <div className="l-section-head">
            <span className="l-label">// как работаем мы</span>
            <h2>Мы тоже ведём честный бизнес</h2>
            <p>
              Метрология держится на доверии. Поэтому у нас короткий список правил, которые
              не обсуждаются — ни с клиентом, ни внутри фирмы.
            </p>
            <a
              className="l-link"
              href="https://fgis.gost.ru/fundmetrology/cm/results"
              target="_blank"
              rel="noreferrer"
            >
              Проверить поверку прибора в ФГИС «Аршин» ↗
            </a>
          </div>
          <div className="l-term" role="list">
            <div className="l-term-bar"><i /><i /><i /><span>principles.txt</span></div>
            <div className="l-term-body">
              <div className="l-term-cmd"><span className="l-prompt">$</span> cat principles.txt</div>
              {PRINCIPLES.map(([head, tail], index) => (
                <div key={head} className="l-term-line" role="listitem">
                  <span className="l-term-num">{String(index + 1).padStart(2, "0")}</span>
                  <span><b>{head}</b> {tail}</span>
                </div>
              ))}
              <div className="l-term-cmd"><span className="l-prompt">$</span> <span className="l-caret" /></div>
            </div>
          </div>
        </section>

        <section className="l-section" id="chto">
          <div className="l-section-head">
            <span className="l-label">// что поверяем</span>
            <h2>Приборы, с которыми к нам приходят</h2>
            <p>Цены — ориентир; точная стоимость зависит от прибора и выезда. Не нашли свой — напишите в комментарии к заявке.</p>
          </div>
          <ul className="l-families">
            {families.length > 0
              ? families.map((family) => (
                  <li key={family.id}>
                    <span>{family.name}</span>
                    {Number(family.price) > 0 && <b>от {money(Number(family.price))} ₽</b>}
                  </li>
                ))
              : FALLBACK_FAMILIES.map((name) => (
                  <li key={name}><span>{name}</span></li>
                ))}
          </ul>
        </section>

        <section className="l-section" id="kak">
          <div className="l-section-head">
            <span className="l-label">// как это происходит</span>
            <h2>Четыре шага от заявки до записи в реестре</h2>
          </div>
          <ol className="l-steps">
            {STEPS.map(([title, text], index) => (
              <li key={title}>
                <span className="l-step-n">{index + 1}</span>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="l-section l-form-section" id="zayavka">
          <div className="l-section-head">
            <span className="l-label">// заявка</span>
            <h2>Оставьте заявку — подберём поверителя и время</h2>
            <p>
              Выберите приборы и адрес, отметьте удобный день. Диспетчер перезвонит и подтвердит визит.
              {PHONE && <> Или позвоните: <a className="l-link" href={`tel:${PHONE.replace(/[^\d+]/g, "")}`}>{PHONE}</a>.</>}
            </p>
          </div>
          <div className="l-form-wrap">
            <PublicRequestForm embedded />
          </div>
        </section>
      </main>

      <footer className="l-foot">
        <div>
          <b>ООО «Единица Измерения»</b> · поверка средств измерений · г. Киров
          {PHONE && <> · <a href={`tel:${PHONE.replace(/[^\d+]/g, "")}`}>{PHONE}</a></>}
          {EMAIL && <> · <a href={`mailto:${EMAIL}`}>{EMAIL}</a></>}
        </div>
        <div className="l-foot-meta">
          <span>© {new Date().getFullYear()}</span>
          <a href="/">Вход для сотрудников</a>
        </div>
      </footer>
    </div>
  );
}
