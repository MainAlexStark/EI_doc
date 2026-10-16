import { useEffect, useRef, useState } from "react";
import type { SiType, SiTypeSuggestion } from "../api";
import { rememberSiType, searchSiTypes } from "../offline/sync";
import SiTypeForm, { type SiTypeDraft } from "./SiTypeForm";

export type PickedType = { id: number; label: string; needs_review?: boolean };

/**
 * Выбор типа СИ — общий для экрана поверок и сверки скана.
 *
 * Раньше в списке были только уже заведённые типы, а для нового надо было
 * идти в админку. Теперь в том же списке видны карточки из Госреестра (ФИФ):
 * одно нажатие — и тип заводится прямо здесь, с шаблоном характеристик по Ду.
 * Без связи ищем по локальной копии справочника.
 */
export default function SiTypePicker({
  value,
  onChange,
  initialQuery = "",
  inputId = "si-type",
  onDraftChange,
}: {
  value: PickedType | null;
  onChange: (picked: PickedType | null) => void;
  initialQuery?: string;
  inputId?: string;
  /** Идёт заведение нового типа — внешняя форма прячет свои кнопки, чтобы не путать. */
  onDraftChange?: (drafting: boolean) => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<SiTypeSuggestion[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [offline, setOffline] = useState(false);
  const [fifUnavailable, setFifUnavailable] = useState(false);
  const [draft, setDraft] = useState<SiTypeDraft | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    onDraftChange?.(draft !== null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  useEffect(() => {
    clearTimeout(debounce.current);
    if (value || draft || query.trim().length < 2) {
      setResults([]);
      setSearched(false);
      return;
    }
    setSearching(true);
    debounce.current = setTimeout(async () => {
      const found = await searchSiTypes(query);
      setResults(found.results);
      setOffline(found.offline);
      setFifUnavailable(found.fifUnavailable);
      setSearching(false);
      setSearched(true);
    }, 300);
    return () => clearTimeout(debounce.current);
  }, [query, value, draft]);

  const handleCreated = (siType: SiType) => {
    void rememberSiType(siType);
    setDraft(null);
    onChange({ id: siType.id, label: siType.label, needs_review: siType.needs_review });
  };

  if (value) {
    return (
      <div className="field">
        <label>Тип счётчика</label>
        <div className="picked">
          <span>
            {value.label}
            {value.needs_review && <span className="pill warn">ждёт проверки метролога</span>}
          </span>
          <button
            type="button"
            onClick={() => {
              onChange(null);
              setQuery("");
            }}
          >
            Другой
          </button>
        </div>
      </div>
    );
  }

  if (draft) {
    return (
      <SiTypeForm
        title="Новый тип счётчика"
        initial={draft}
        onSaved={handleCreated}
        onCancel={() => setDraft(null)}
      />
    );
  }

  const local = results.filter((item) => item.si_type_id !== null);
  const fif = results.filter((item) => item.si_type_id === null);

  return (
    <div className="field">
      <label htmlFor={inputId}>Тип счётчика</label>
      <input
        id={inputId}
        className="big-input"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Название с корпуса или номер в Госреестре"
        autoComplete="off"
      />
      {query.trim().length > 0 && query.trim().length < 2 && <p className="hint">Введите ещё хотя бы один символ</p>}
      {searching && <p className="hint">Ищу…</p>}
      {offline && searched && (
        <p className="hint warn-text">
          Нет связи — ищу только среди уже заведённых типов. Новый тип можно будет завести, когда связь появится.
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
              onClick={() => onChange({ id: item.si_type_id!, label: item.label, needs_review: item.needs_review })}
            >
              <b>{item.name}</b>
              <span className="sub">
                № {item.registry_number} · {item.manufacturer || "изготовитель не указан"}
              </span>
              {item.ambiguous && <span className="pill warn">такое название у нескольких — сверьте изготовителя</span>}
              {item.needs_review && <span className="pill muted">ждёт проверки</span>}
            </button>
          ))}

          {fif.length > 0 && <div className="pick-head">Из Госреестра — ещё не заведены, нажмите, чтобы завести</div>}
          {fif.map((item) => (
            <button
              type="button"
              key={`f-${item.registry_number}-${item.manufacturer}`}
              className="pick-item new"
              onClick={() =>
                setDraft({
                  name: item.name || item.notation,
                  registry_number: item.registry_number,
                  manufacturer: item.manufacturer,
                  interval_months: item.interval_months,
                })
              }
            >
              <b>+ {item.name || item.notation}</b>
              <span className="sub">
                № {item.registry_number} · {item.manufacturer || "изготовитель не указан"}
              </span>
              {item.ambiguous && <span className="pill warn">такое название у нескольких — сверьте изготовителя</span>}
            </button>
          ))}

          {results.length === 0 && <div className="empty small">Ничего не нашлось</div>}

          {!offline && (
            <button
              type="button"
              className="pick-item manual"
              onClick={() => setDraft({ name: /\d{3,}-\d{2}/.test(query) ? "" : query.trim(), registry_number: /\d{3,}-\d{2}/.test(query) ? query.trim() : "" })}
            >
              Нет нужного — завести тип вручную
              {fifUnavailable && <span className="sub">реестр ФИФ сейчас не отвечает</span>}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
