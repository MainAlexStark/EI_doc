import { useEffect, useMemo, useState } from "react";
import {
  createSiType,
  fetchSiTypePresets,
  updateSiType,
  type LimitPreset,
  type SiType,
  type SiTypeInput,
  type SiTypePresets,
} from "../api";

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

export type SiTypeDraft = Partial<SiTypeInput> & { interval_months?: number | null };

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
          applyPreset(preset);
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

  function applyPreset(preset: LimitPreset) {
    setPresetKey(preset.key);
    setGuessed(false);
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
                    }}
                  />
                </div>
              ))}
              <p className="hint">Значения берутся из описания типа в Госреестре (раздел «Метрологические характеристики»).</p>
            </div>
          )}
        </>
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
