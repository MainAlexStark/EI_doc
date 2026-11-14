import { useEffect, useState } from "react";
import { fetchFamilies, type Family } from "../api";
import PublicRequestForm from "../components/PublicRequestForm";
import AsciiField from "./AsciiField";
import "./landing.css";

// Контакты фирмы. Пустая строка — не показываем; заполнить, когда решим,
// какой номер и почту светить на сайте.
const PHONE: string = "";
const EMAIL: string = "";

const money = (value: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);

const RISKS = [
  ["Штраф", "по ст. 19.19 КоАП РФ за применение неповеренных средств измерений"],
  ["Нет юридической силы", "у результатов измерений — их не примут заказчик, суд, проверяющий"],
  ["Стоп на приёмке", "продукции, объекта или госконтракта, пока нет документа о поверке"],
];

const STEPS = [
  "Заявка",
  "Звонок диспетчера",
  "Выезд в 2-часовой интервал",
  "Поверка и запись в ФГИС «Аршин»",
];

export default function Landing() {
  const [families, setFamilies] = useState<Family[]>([]);

  useEffect(() => {
    document.title = "Поверка средств измерений в Кирове — Единица Измерения";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#04060c");
    void fetchFamilies().then((response) => setFamilies(response.families));
  }, []);

  const tel = PHONE.replace(/[^\d+]/g, "");

  return (
    <div className="landing">
      <AsciiField />

      <header className="l-top">
        <span className="l-logo-mark">ЕИ</span>
        <span className="l-logo-name">Единица Измерения</span>
        {PHONE && <a className="l-top-phone" href={`tel:${tel}`}>{PHONE}</a>}
      </header>

      <main>
        <div className="l-first">
          <section className="l-hero">
            <p className="l-kicker">поверка средств измерений · Киров и область</p>
            <h1>
              Законная работа<br />
              начинается с поверки
            </h1>
            <p className="l-lead">
              Без действующей поверки оборудование нельзя применять в производстве, торговле
              и на госконтрактах. Поверим приборы и внесём результаты в ФГИС «Аршин».
            </p>
            <ul className="l-bullets">
              <li>Выезд к вам — интервал визита 2 часа</li>
              <li>Каждый результат — в федеральном реестре</li>
              <li>Документы для проверяющих и заказчиков</li>
            </ul>
            {PHONE && <a className="l-hero-phone" href={`tel:${tel}`}>{PHONE}</a>}
          </section>

          <section className="l-form" id="zayavka">
            <PublicRequestForm embedded />
          </section>
        </div>

        <section className="l-more">
          <h2>Чем грозит неповеренное оборудование</h2>
          <div className="l-risks">
            {RISKS.map(([head, text]) => (
              <div key={head}>
                <b>{head}</b>
                <span>{text}</span>
              </div>
            ))}
          </div>
          <p className="l-note">
            Закон 102-ФЗ «Об обеспечении единства измерений» требует применять поверенные
            средства измерений в сферах госрегулирования: производство по обязательным требованиям,
            торговля, учёт энергоресурсов, охрана труда, здравоохранение, оборона, госзакупки.
            С 2020 года поверку подтверждает только запись в ФГИС «Аршин» —{" "}
            <a href="https://fgis.gost.ru/fundmetrology/cm/results" target="_blank" rel="noreferrer">
              проверить свой прибор ↗
            </a>
          </p>

          <h2>Как это происходит</h2>
          <ol className="l-steps">
            {STEPS.map((step, i) => (
              <li key={step}><span>{String(i + 1).padStart(2, "0")}</span>{step}</li>
            ))}
          </ol>

          {families.length > 0 && (
            <>
              <h2>Цены</h2>
              <ul className="l-prices">
                {families.map((family) => (
                  <li key={family.id}>
                    <span>{family.name}</span>
                    <i />
                    <b>{Number(family.price) > 0 ? `от ${money(Number(family.price))} ₽` : "по запросу"}</b>
                  </li>
                ))}
              </ul>
            </>
          )}

          <a href="#zayavka" className="l-btn">Оставить заявку</a>
        </section>
      </main>

      <footer className="l-foot">
        <span>ООО «Единица Измерения» · г. Киров</span>
        {PHONE && <a href={`tel:${tel}`}>{PHONE}</a>}
        {EMAIL && <a href={`mailto:${EMAIL}`}>{EMAIL}</a>}
        <a href="/">Вход для сотрудников</a>
      </footer>
    </div>
  );
}
