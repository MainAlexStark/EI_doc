import { useEffect, useRef, useState } from "react";
import {
  fetchFgisHistory,
  fetchOwnHistory,
  type FgisHistory,
  type FgisVerification,
  type OwnInstrumentHistory,
} from "../api";

const MIN_SERIAL = 4;

function day(value: string | null): string {
  return value ? new Date(value).toLocaleDateString("ru-RU") : "";
}

function expired(validDate: string | null): boolean {
  return Boolean(validDate) && new Date(validDate!) < new Date(new Date().toDateString());
}

/**
 * Прошлые поверки прибора по заводскому номеру: что мы поверяли сами и что есть во ФГИС «Аршин».
 * Нажатие на строку отдаёт её наверх — форма подставляет тип, номер и год выпуска.
 *
 * Своя база отвечает сразу; ФГИС ищет по каждому году отдельно, это секунд пять-десять, поэтому
 * её строки появляются позже и ввод не держат. Без связи блок просто молчит.
 */
export default function InstrumentHistory({
  serial,
  pickedKey,
  onPickOwn,
  onPickFgis,
}: {
  serial: string;
  /** Что уже выбрано («own-12» / «1-529062899») — подсвечивается в списке. */
  pickedKey: string | null;
  onPickOwn: (item: OwnInstrumentHistory) => void;
  onPickFgis: (item: FgisVerification) => void;
}) {
  const [own, setOwn] = useState<OwnInstrumentHistory[]>([]);
  const [fgis, setFgis] = useState<FgisHistory | null>(null);
  const [fgisState, setFgisState] = useState<"idle" | "loading" | "failed">("idle");
  const [searchedFor, setSearchedFor] = useState("");
  const run = useRef(0);

  const query = serial.trim();

  useEffect(() => {
    const current = ++run.current;
    if (query.length < MIN_SERIAL) {
      setOwn([]);
      setFgis(null);
      setFgisState("idle");
      setSearchedFor("");
      return;
    }
    // Номер набирают с корпуса по одному знаку — ждём паузу, чтобы не дёргать ФГИС на каждый.
    const timer = setTimeout(() => {
      setSearchedFor(query);
      setFgis(null);
      setFgisState("loading");
      fetchOwnHistory(query)
        .then((data) => current === run.current && setOwn(data.own))
        .catch(() => current === run.current && setOwn([]));
      fetchFgisHistory(query)
        .then((data) => {
          if (current !== run.current) return;
          setFgis(data.fgis);
          setFgisState("idle");
        })
        .catch(() => current === run.current && setFgisState("failed"));
    }, 700);
    return () => clearTimeout(timer);
  }, [query]);

  if (query.length < MIN_SERIAL || searchedFor !== query) return null;

  const items = fgis?.items ?? [];
  const nothing = fgisState === "idle" && fgis?.ok && items.length === 0 && own.length === 0;

  return (
    <div className="field">
      {own.length > 0 && (
        <div className="pick-list">
          <div className="pick-head">Уже поверяли у нас — нажмите, и поля заполнятся</div>
          {own.map((item) => (
            <button
              type="button"
              key={item.instrument_id}
              className={`pick-item${pickedKey === `own-${item.instrument_id}` ? " picked-row" : ""}`}
              onClick={() => onPickOwn(item)}
            >
              <b>
                {item.si_type.name} · № {item.serial_number}
                {item.manufacture_year ? ` · ${item.manufacture_year} г.` : ""}
              </b>
              <span className="sub">
                {item.last
                  ? `Последняя поверка ${day(item.last.verified_at)}${
                      item.last.protocol_number ? ` · протокол ${item.last.protocol_number}` : ""
                    }${item.last.verifier ? ` · ${item.last.verifier}` : ""}`
                  : "Поверок ещё не было"}
                {item.verifications_count > 1 ? ` · всего поверок: ${item.verifications_count}` : ""}
              </span>
              {(item.owner || item.site) && (
                <span className="sub">{[item.owner, item.site].filter(Boolean).join(" · ")}</span>
              )}
              {item.last && !item.last.suitable && <span className="pill bad">был признан непригодным</span>}
            </button>
          ))}
        </div>
      )}

      {fgisState === "loading" && <p className="hint">Ищу прошлые поверки в «Аршине»… это до 10 секунд, вводить можно дальше</p>}
      {fgisState === "failed" && <p className="hint">Прошлые поверки в «Аршине» найти не удалось — нет связи. Заполните вручную.</p>}
      {fgis && !fgis.ok && (
        <p className="hint warn-text">
          «Аршин» сейчас не отвечает — прошлые поверки не подтянуть, заполните вручную.
          <span className="sub" style={{ display: "block" }}>{fgis.error}</span>
        </p>
      )}

      {items.length > 0 && (
        <div className="pick-list">
          <div className="pick-head">
            Из «Аршина» — нажмите, и поля заполнятся
            {items.length > 1 ? ". Номер числится у нескольких записей — сверьте тип" : ""}
          </div>
          {items.map((item) => (
            <button
              type="button"
              key={item.vri_id}
              className={`pick-item${pickedKey === item.vri_id ? " picked-row" : ""}`}
              onClick={() => onPickFgis(item)}
            >
              <b>
                {item.modification || item.mit_notation} · № {item.mi_number}
              </b>
              <span className="sub">
                {item.mit_title} · Госреестр № {item.mit_number}
              </span>
              <span className="sub">
                Поверен {day(item.verification_date)}
                {item.valid_date ? ` · действует до ${day(item.valid_date)}` : ""} · {item.org_title}
              </span>
              <span className="pills" style={{ marginTop: 4 }}>
                {item.applicable === false && <span className="pill bad">признан непригодным</span>}
                {item.applicable !== false && expired(item.valid_date) && <span className="pill warn">срок поверки истёк</span>}
                {item.applicable !== false && item.valid_date && !expired(item.valid_date) && (
                  <span className="pill ok">поверка действует</span>
                )}
                {!item.si_type && <span className="pill muted">тип нужно будет выбрать</span>}
              </span>
            </button>
          ))}
        </div>
      )}
      {fgis?.ok && fgis.partial && <p className="hint warn-text">{fgis.error} — список может быть неполным.</p>}
      {nothing && (
        <p className="hint">
          Прошлых поверок с таким номером нет ни у нас, ни в «Аршине» за {fgis!.years[0]}–{fgis!.years[1]} годы.
          Проверьте номер (пробелы — как на корпусе) или заполните вручную.
        </p>
      )}
    </div>
  );
}
