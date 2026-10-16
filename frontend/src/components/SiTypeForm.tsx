import { useEffect, useMemo, useRef, useState } from "react";
import {
  createSiType,
  fetchFifCard,
  fetchSiTypePresets,
  readFifLimits,
  updateSiType,
  type FifCard,
  type FifLimitsResult,
  type LimitPreset,
  type SiType,
  type SiTypeInput,
  type SiTypePresets,
} from "../api";
import SiTypeDocuments, { FifDocumentButtons } from "./FifDocuments";

/**
 * Форма типа СИ — вместо Django-админки и ручного JSON с характеристиками.
 *
 * Главное для поверителя: не вводить семь чисел руками. Он выбирает шаблон
 * по Ду (наборы взяты из уже проверенных типов справочника), значения
 * подставляются, при необходимости их можно поправить. Тип, заведённый
 * поверителем, метролог проверяет позже — до этого протокол не подписывается.
 */

let presetsPromise: Promise<SiTypePresets> | null = null;
export function loadPresets(): Promise<SiTypePresets> {
  if (!presetsPromise) {
    presetsPromise = fetchSiTypePresets().catch((exc) => {
      presetsPromise = null;
      throw exc;
    });
  }
  return presetsPromise;
}

/** Условный проход из наименования: «СВК 15-3-2» → 15, «СГВ-20» → 20. */
export function guessDn(text: string): number | null {
  const match = text.match(/(?:^|\D)(15|20|25|32|40|50)(?:\D|$)/);
  return match ? Number(match[1]) : null;
}

function toText(value: number | string | undefined): string {
  if (value === undefined || value === null || value === "") return "";
  return String(value).replace(".", ",");
}

function sameLimits(a: Record<string, string>, b: Record<string, number>): boolean {
  return Object.keys(b).every((key) => Number(String(a[key] ?? "").replace(",", ".")) === b[key]);
}

export type SiTypeDraft = Partial<SiTypeInput> & {
  interval_months?: number | null;
  /** Что поверитель вводил в поиске — по нему выбираем модификацию из карточки ФИФ. */
  query?: string;
};

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString("ru-RU") : "";
}

/** Модификация из карточки, больше всего похожая на то, что искали. */
function bestVariant(variants: string[], query: string): string | null {
  const q = query.trim().toLowerCase().replace(/[\s-]/g, "");
  if (!q) return null;
  const norm = (v: string) => v.toLowerCase().replace(/[\s-]/g, "");
  const exact = variants.find((v) => norm(v) === q) ?? variants.find((v) => norm(v).includes(q));
  if (exact) return exact;
  // Ввели конкретную модификацию, которой в реестре нет («СВК 15-3-2» при «типа СВК») —
  // берём как ввели.
  if (variants.some((v) => q.startsWith(norm(v)))) return query.trim();
  return null;
}

type AiState = "idle" | "reading" | "done" | "failed";

export default function SiTypeForm({
  initial,
  existing,
  onSaved,
  onCancel,
  title,
}: {
  initial?: SiTypeDraft;
  existing?: SiType;
  onSaved: (siType: SiType) => void;
  onCancel?: () => void;
  title?: string;
}) {
  const source = existing ?? initial ?? {};
  const [info, setInfo] = useState<SiTypePresets | null>(null);
  const [loadError, setLoadError] = useState("");
  const [name, setName] = useState(source.name ?? "");
  const [registry, setRegistry] = useState(source.registry_number ?? "");
  const [manufacturer, setManufacturer] = useState(source.manufacturer ?? "");
  const [intervalMonths, setIntervalMonths] = useState(() => {
    const value = existing?.verification_interval_months ?? initial?.interval_months ?? initial?.verification_interval_months;
    return value ? String(value) : "";
  });
  const [family, setFamily] = useState<number | null>(existing?.family ?? null);
  const [limits, setLimits] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(existing?.limits ?? initial?.limits ?? {}).map(([k, v]) => [k, toText(v)])),
  );
  const [presetKey, setPresetKey] = useState<string | null>(null);
  const [guessed, setGuessed] = useState(false);
  const [showLimits, setShowLimits] = useState(Boolean(existing));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // --- Госреестр (ФИФ): карточка типа и чтение описания типа ------------
  const [fifUuid, setFifUuid] = useState(existing?.fif_uuid || initial?.fif_uuid || "");
  const [card, setCard] = useState<FifCard | null>(null);
  const [cardState, setCardState] = useState<"idle" | "loading" | "failed">("idle");
  const [cardError, setCardError] = useState("");
  const [ai, setAi] = useState<AiState>("idle");
  const [aiResult, setAiResult] = useState<FifLimitsResult | null>(null);
  const [aiError, setAiError] = useState("");
  // Ref, а не state: ответ модели приходит позже, и проверять надо
  // актуальное значение, а не то, что было при запуске чтения.
  const limitsTouched = useRef(false);
  const setLimitsTouched = (value: boolean) => {
    limitsTouched.current = value;
  };
  const [readFor, setReadFor] = useState("");

  const readLimits = async (uuid: string, variant: string) => {
    setAi("reading");
    setAiError("");
    setReadFor(variant);
    try {
      const result = await readFifLimits(uuid, variant);
      setAiResult(result);
      setAi("done");
      if (result.found && !limitsTouched.current) {
        setLimits((current) => ({
          ...current,
          ...Object.fromEntries(Object.entries(result.limits).map(([k, v]) => [k, toText(v)])),
        }));
        setPresetKey(null);
        setGuessed(false);
        setShowLimits(true);
      }
    } catch (exc) {
      setAi("failed");
      setAiError(exc instanceof Error ? exc.message : "Не удалось прочитать описание типа");
    }
  };

  const loadCard = async (ident: string, { fillForm }: { fillForm: boolean }) => {
    setCardState("loading");
    setCardError("");
    try {
      const data = await fetchFifCard(ident);
      setCard(data);
      setFifUuid(data.uuid);
      setCardState("idle");
      if (!fillForm) return;
      const variant = bestVariant(data.variants, initial?.query ?? "") ?? bestVariant(data.variants, name);
      const chosenName = variant ?? (name.trim() || data.variants[0] || data.title);
      setName(chosenName);
      setRegistry((current) => current || data.registry_number);
      setManufacturer((current) => current || data.manufacturer);
      const months = Array.from(new Set(data.mpi.map((m) => m.months)));
      if (months.length === 1) setIntervalMonths(String(months[0]));
      if (data.limits_reader) void readLimits(data.uuid, chosenName);
    } catch (exc) {
      setCardState("failed");
      setCardError(exc instanceof Error ? exc.message : "Реестр ФИФ не ответил");
    }
  };

  const cardRequested = useRef(false);
  useEffect(() => {
    // Ref — чтобы в dev (StrictMode) не читать описание типа дважды.
    if (cardRequested.current) return;
    cardRequested.current = true;
    if (!existing && initial?.fif_uuid) void loadCard(initial.fif_uuid, { fillForm: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadPresets()
      .then((data) => {
        setInfo(data);
        setFamily((current) => current ?? data.default_family);
        if (existing) {
          const match = data.presets.find((p) => sameLimits(limits, p.limits));
          setPresetKey(match ? match.key : null);
          return;
        }
        if (Object.keys(limits).length > 0) return;
        const dn = guessDn(`${source.name ?? ""} ${initial?.registry_number ?? ""}`);
        const preset = dn ? data.presets.find((p) => p.dn === dn) : undefined;
        if (preset) {
          applyPreset(preset, false);
          setGuessed(true);
        } else {
          setShowLimits(false);
        }
      })
      .catch((exc) => setLoadError(exc instanceof Error ? exc.message : "Не удалось загрузить шаблоны"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const calculatorKey = useMemo(
    () => info?.families.find((f) => f.id === family)?.calculator_key ?? "water_meter",
    [info, family],
  );
  const needsLimits = calculatorKey === "water_meter";

  function applyPreset(preset: LimitPreset, byUser = true) {
    setPresetKey(preset.key);
    setGuessed(false);
    if (byUser) setLimitsTouched(true);
    setLimits(Object.fromEntries(Object.entries(preset.limits).map(([k, v]) => [k, toText(v)])));
  }

  const missing = info ? info.fields.filter((f) => !String(limits[f.key] ?? "").trim()) : [];

  // Не <form>: форма типа встраивается внутрь формы «Новый счётчик» и сверки
  // скана, а вложенные формы в HTML запрещены — Enter/submit ушёл бы во внешнюю.
  const submit = async () => {
    setError("");
    if (!name.trim()) return setError("Впишите наименование — как на корпусе или в свидетельстве");
    if (!registry.trim()) return setError("Впишите номер в Госреестре (например 12345-06)");
    if (needsLimits && missing.length > 0) {
      setShowLimits(true);
      return setError("Выберите шаблон по Ду или заполните все характеристики");
    }
    const payload: SiTypeInput = {
      family,
      name: name.trim(),
      registry_number: registry.trim(),
      manufacturer: manufacturer.trim(),
      verification_interval_months: intervalMonths ? Number(intervalMonths) : null,
    };
    if (fifUuid) payload.fif_uuid = fifUuid;
    if (card) {
      payload.type_title = card.title;
      payload.certificate_valid_to = card.valid_to;
    }
    if (needsLimits) payload.limits = limits;
    setBusy(true);
    try {
      const saved = existing ? await updateSiType(existing.id, payload) : await createSiType(payload);
      onSaved(saved);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить тип");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="card type-form"
      onKeyDown={(event) => {
        if (event.key === "Enter" && (event.target as HTMLElement).tagName === "INPUT") {
          event.preventDefault();
          void submit();
        }
      }}
    >
      {title && <h3>{title}</h3>}
      {loadError && <div className="error">{loadError}</div>}

      {cardState === "loading" && <div className="fif-panel"><span className="sub">Загружаю карточку из Госреестра…</span></div>}
      {cardState === "failed" && (
        <div className="hint warn-text">
          Госреестр не ответил ({cardError}). Заполните поля вручную — или попробуйте ещё раз позже.
        </div>
      )}
      {card && (
        <div className="fif-panel">
          <div>
            <b>Госреестр № {card.registry_number}</b> · {card.title}
          </div>
          {card.manufacturer && <div className="sub">{card.manufacturer}</div>}
          {card.valid_to && (
            <div className="sub">
              {card.is_valid === false ? (
                <span className="pill warn">
                  свидетельство истекло {formatDate(card.valid_to)} — поверять можно приборы, выпущенные до этой даты
                </span>
              ) : (
                <>Свидетельство об утверждении типа действует до {formatDate(card.valid_to)}</>
              )}
            </div>
          )}
          {card.already_added.length > 0 && (
            <div className="sub">
              По этому номеру уже заведены: {card.already_added.map((t) => t.name).join(", ")}
            </div>
          )}
          {!existing && card.already_added.some((t) => t.name.trim().toLowerCase() === name.trim().toLowerCase()) && (
            <div className="ai-note ok">«{name.trim()}» уже есть в справочнике — нажмите «Завести тип», и будет выбран существующий.</div>
          )}

          {card.variants.length > 1 && (
            <div className="field">
              <label>Модификация — нажмите, чтобы подставить в наименование</label>
              <div className="chip-row">
                {card.variants.map((variant) => (
                  <button
                    type="button"
                    key={variant}
                    className={`chip ${name === variant ? "active" : ""}`}
                    onClick={() => setName(variant)}
                  >
                    {variant}
                  </button>
                ))}
              </div>
            </div>
          )}

          {new Set(card.mpi.map((m) => m.months)).size > 1 && (
            <div className="field">
              <label>Межповерочный интервал по реестру</label>
              <div className="chip-row">
                {card.mpi.map((m) => (
                  <button
                    type="button"
                    key={`${m.parameter}-${m.months}`}
                    className={`chip ${intervalMonths === String(m.months) ? "active" : ""}`}
                    onClick={() => setIntervalMonths(String(m.months))}
                  >
                    {m.months} мес. · {m.parameter || "все"}
                  </button>
                ))}
              </div>
            </div>
          )}

          <FifDocumentButtons documents={card.documents} />
        </div>
      )}

      <div className="field-row">
        <div className="field" style={{ flex: 2 }}>
          <label htmlFor="tf-name">Наименование<span className="req">*</span></label>
          <input id="tf-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="например СВК 15-3-2" />
        </div>
        <div className="field">
          <label htmlFor="tf-reg">Номер в Госреестре<span className="req">*</span></label>
          <input id="tf-reg" value={registry} onChange={(e) => setRegistry(e.target.value)} placeholder="12345-06" />
        </div>
      </div>
      <div className="field-row">
        <div className="field" style={{ flex: 2 }}>
          <label htmlFor="tf-man">Изготовитель</label>
          <input
            id="tf-man"
            value={manufacturer}
            onChange={(e) => setManufacturer(e.target.value)}
            placeholder="важно, если одинаковое название у разных заводов"
          />
        </div>
        <div className="field">
          <label htmlFor="tf-int">Межповерочный интервал, мес.</label>
          <input
            id="tf-int"
            inputMode="numeric"
            value={intervalMonths}
            onChange={(e) => setIntervalMonths(e.target.value.replace(/\D/g, ""))}
            placeholder="72"
          />
        </div>
      </div>

      {info && info.families.length > 1 && (
        <div className="field">
          <label htmlFor="tf-family">Вид СИ</label>
          <select id="tf-family" value={family ?? ""} onChange={(e) => setFamily(Number(e.target.value))}>
            {info.families.map((f) => (
              <option key={f.id} value={f.id}>{f.name}</option>
            ))}
          </select>
        </div>
      )}

      {needsLimits && info && (
        <>
          {ai === "reading" && (
            <div className="ai-note reading">Читаю описание типа — ищу расходы и погрешности для «{readFor}»… Это до минуты.</div>
          )}
          {ai === "done" && aiResult?.found && (
            <div className="ai-note ok">
              <b>Значения взяты из описания типа{aiResult.variant_matched ? ` (${aiResult.variant_matched})` : ""} — сверьте.</b>
              {aiResult.missing.length > 0 && <div>Не нашёл часть полей — дозаполните вручную.</div>}
              {aiResult.quote && <blockquote>{aiResult.quote}</blockquote>}
              {aiResult.comment && <div className="sub">{aiResult.comment}</div>}
            </div>
          )}
          {ai === "done" && aiResult && !aiResult.found && (
            <div className="ai-note warn">
              В описании типа расходы не нашлись{aiResult.comment ? `: ${aiResult.comment}` : ""}. Выберите шаблон по Ду.
            </div>
          )}
          {ai === "failed" && <div className="ai-note warn">Описание типа прочитать не вышло ({aiError}). Выберите шаблон по Ду.</div>}
          {card && card.limits_reader && ai !== "reading" && name.trim() && name.trim() !== readFor && (
            <button type="button" className="link" onClick={() => void readLimits(card.uuid, name.trim())}>
              Прочитать характеристики для «{name.trim()}» из описания типа
            </button>
          )}
          <div className="field">
            <label>Характеристики — выберите шаблон по Ду</label>
            <div className="preset-grid">
              {info.presets.map((preset) => (
                <button
                  type="button"
                  key={preset.key}
                  className={`preset ${presetKey === preset.key ? "active" : ""}`}
                  onClick={() => applyPreset(preset)}
                >
                  <b>{preset.label}</b>
                  <span>{preset.detail}</span>
                  {preset.examples.length > 0 && <span className="sub">как у {preset.examples.join(", ")}</span>}
                </button>
              ))}
            </div>
            {guessed && <p className="hint">Шаблон подобран по наименованию — проверьте, что Ду совпадает.</p>}
          </div>

          <button type="button" className="link" onClick={() => setShowLimits((v) => !v)}>
            {showLimits ? "Скрыть характеристики" : presetKey ? "Посмотреть или поправить значения" : "Ввести значения вручную"}
          </button>

          {showLimits && (
            <div className="limits-grid">
              {info.fields.map((f) => (
                <div className="field" key={f.key}>
                  <label htmlFor={`tf-${f.key}`}>{f.label}</label>
                  <input
                    id={`tf-${f.key}`}
                    inputMode="decimal"
                    value={limits[f.key] ?? ""}
                    onChange={(e) => {
                      const value = e.target.value;
                      setLimits((current) => ({ ...current, [f.key]: value }));
                      setPresetKey(null);
                      setLimitsTouched(true);
                    }}
                  />
                </div>
              ))}
              <p className="hint">Значения берутся из описания типа в Госреестре (раздел «Метрологические характеристики»).</p>
            </div>
          )}
        </>
      )}

      {existing && existing.fif_uuid && !card && (
        <div className="field">
          <label>Документы из Госреестра</label>
          <SiTypeDocuments siTypeId={existing.id} />
        </div>
      )}
      {existing && !fifUuid && cardState !== "loading" && registry.trim() && (
        <button type="button" className="link" onClick={() => void loadCard(registry.trim(), { fillForm: false })}>
          Найти № {registry.trim()} в Госреестре — подтянуть методику поверки и описание типа
        </button>
      )}

      {!existing && !info?.can_review && (
        <p className="hint">
          Тип сразу можно использовать. Метролог проверит характеристики — до этого протокол по нему не подпишется.
        </p>
      )}

      <div className="actions">
        <button className="primary big" type="button" onClick={() => void submit()} disabled={busy}>
          {busy ? "Сохраняю…" : existing ? "Сохранить" : "Завести тип"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
        )}
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}
