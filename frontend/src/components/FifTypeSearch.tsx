import { useEffect, useRef, useState } from "react";
import type { SiTypeSuggestion } from "../api";
import { searchSiTypes } from "../offline/sync";
import { parseYear } from "../registryYear";
import RstStatus from "./RstStatus";
import { loadPresets, type SiTypeDraft } from "./SiTypeForm";

/**
 * Поиск типа СИ в Госреестре (ФИФ, РСТ-метрология) — с первого символа, по чему угодно:
 * наименованию, обозначению, номеру в реестре, изготовителю.
 *
 * Тип в EI_doc обязан существовать в реестре: поверитель выбирает строку из подсказок, и
 * форма типа заполняется сама. Завести тип руками (реестр не отвечает, типа там нет) может
 * только метролог — кнопка видна только ему.
 *
 * Если известен год выпуска прибора (`year`), остаются только записи реестра, действовавшие
 * в тот год: для счётчика 2020 года — 16078-13, а не 16078-05 и не 16078-22. Год не указан —
 * показываем всё.
 */
export default function FifTypeSearch({
  onPickFif,
  onPickLocal,
  label = "Тип СИ",
  inputId = "fif-type-search",
  initialQuery = "",
  autoFocus,
  year,
}: {
  onPickFif: (draft: SiTypeDraft) => void;
  onPickLocal?: (item: SiTypeSuggestion) => void;
  label?: string;
  inputId?: string;
  initialQuery?: string;
  autoFocus?: boolean;
  /** Год выпуска прибора, как введён в форме; пустой или недописанный — без отбора по году. */
  year?: string | number | null;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<SiTypeSuggestion[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [offline, setOffline] = useState(false);
  const [fifUnavailable, setFifUnavailable] = useState(false);
  const [canManual, setCanManual] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [failures, setFailures] = useState(0);
  const [hiddenByYear, setHiddenByYear] = useState(0);
  // «Показать все» — поверитель сам снял отбор по году (ошибся годом, редкий случай с типом).
  const [ignoreYear, setIgnoreYear] = useState(false);
  const madeIn = parseYear(year);
  const filterYear = ignoreYear ? null : madeIn;
  const debounce = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    loadPresets()
      .then((data) => setCanManual(Boolean(data.can_manual)))
      .catch(() => setCanManual(false));
  }, []);

  useEffect(() => {
    clearTimeout(debounce.current);
    if (query.trim().length < 2) {
      setResults([]);
      setSearched(false);
      return;
    }
    setSearching(true);
    debounce.current = setTimeout(async () => {
      const found = await searchSiTypes(query, filterYear);
      setResults(found.results);
      setHiddenByYear(found.hiddenByYear);
      setOffline(found.offline);
      setFifUnavailable(found.fifUnavailable);
      setSearchError(found.error ?? "");
      if (found.fifUnavailable) setFailures((n) => n + 1); // индикатор «РСТ» перечитает статус
      setSearching(false);
      setSearched(true);
    }, 300);
    return () => clearTimeout(debounce.current);
  }, [query, filterYear]);

  useEffect(() => setIgnoreYear(false), [madeIn]);

  const local = results.filter((item) => item.si_type_id !== null);
  const fif = results.filter((item) => item.si_type_id === null);
  const looksLikeNumber = /\d{3,}-\d{2}/.test(query);

  return (
    <div className="field">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <label htmlFor={inputId}>{label}</label>
        <RstStatus signal={failures} />
      </div>
      <input
        id={inputId}
        className="big-input"
        value={query}
        autoFocus={autoFocus}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Название, обозначение, номер в Госреестре или изготовитель"
        autoComplete="off"
      />
      {query.trim().length > 0 && query.trim().length < 2 && <p className="hint">Введите ещё хотя бы один символ</p>}
      {searching && <p className="hint">Ищу в Госреестре…</p>}
      {offline && searched && (
        <p className="hint warn-text">
          Нет связи — ищу только среди уже заведённых типов. Новый тип можно будет завести, когда связь появится.
        </p>
      )}
      {!offline && fifUnavailable && searched && (
        <p className="hint warn-text">
          Госреестр (ФИФ) сейчас не отвечает — показываю только заведённые типы. Попробуйте чуть позже
          {canManual ? " или заведите тип вручную." : "."}
          {searchError && <span className="sub" style={{ display: "block" }}>{searchError}</span>}
        </p>
      )}

      {searched && !searching && madeIn && (hiddenByYear > 0 || ignoreYear) && (
        <p className="hint">
          {ignoreYear
            ? "Показаны все типы, без учёта года выпуска. "
            : `Показаны типы, действовавшие в ${madeIn} году выпуска; не подходят по году: ${hiddenByYear}. `}
          <button type="button" className="link" onClick={() => setIgnoreYear((value) => !value)}>
            {ignoreYear ? `Только подходящие для ${madeIn} года` : "Показать все"}
          </button>
        </p>
      )}

      {searched && !searching && (
        <div className="pick-list">
          {local.length > 0 && <div className="pick-head">Уже в системе</div>}
          {local.map((item) => (
            <button
              type="button"
              key={`l-${item.si_type_id}`}
              className="pick-item"
              disabled={!onPickLocal}
              onClick={() => onPickLocal?.(item)}
            >
              <b>{item.name}</b>
              <span className="sub">
                № {item.registry_number} · {item.manufacturer || "изготовитель не указан"}
              </span>
              {item.ambiguous && <span className="pill warn">такое название у нескольких — сверьте изготовителя</span>}
              {item.needs_review && <span className="pill muted">ждёт проверки</span>}
            </button>
          ))}

          {fif.length > 0 && <div className="pick-head">Из Госреестра — выберите, остальное заполнится само</div>}
          {fif.map((item) => (
            <button
              type="button"
              key={`f-${item.registry_number}-${item.manufacturer}-${item.fif_uuid}`}
              className="pick-item new"
              onClick={() =>
                onPickFif({
                  name: item.name || item.notation,
                  registry_number: item.registry_number,
                  manufacturer: item.manufacturer,
                  interval_months: item.interval_months,
                  fif_uuid: item.fif_uuid,
                  query,
                })
              }
            >
              <b>+ {item.notation || item.name}</b>
              {item.title && <span className="sub">{item.title}</span>}
              <span className="sub">
                № {item.registry_number} · {item.manufacturer || "изготовитель не указан"}
              </span>
              {item.ambiguous && <span className="pill warn">такое название у нескольких — сверьте изготовителя</span>}
            </button>
          ))}

          {results.length === 0 && (
            <div className="empty small">
              Ничего не нашлось. Попробуйте другое слово: обозначение с корпуса без модификации («СВК» вместо
              «СВК 15-3-2»), номер в Госреестре или изготовителя.
            </div>
          )}

          {!offline && canManual && (
            <button
              type="button"
              className="pick-item manual"
              onClick={() =>
                onPickFif({ name: looksLikeNumber ? "" : query.trim(), registry_number: looksLikeNumber ? query.trim() : "" })
              }
            >
              Нет в Госреестре — завести тип вручную (только метролог)
            </button>
          )}
          {!offline && !canManual && results.length === 0 && (
            <p className="hint">Тип заводится только из Госреестра. Если его там точно нет — попросите метролога.</p>
          )}
        </div>
      )}
    </div>
  );
}
