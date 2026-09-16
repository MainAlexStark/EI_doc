import { useEffect, useRef, useState } from "react";
import {
  applyScan,
  fetchScanImageUrl,
  suggestSiTypes,
  type FieldVerification,
  type LayoutsResponse,
  type MeasurementRowPayload,
  type ScanRecognizedRow,
  type ScanUploadResult,
  type SiTypeSuggestion,
} from "../api";

/** Экран сверки распознанного бланка (второй срез офлайна — claude/scans.md).
 *
 * Фото слева, поля справа — предзаполнены тем, что вернула модель (или пусто,
 * если распознавание не настроено/не справилось: тогда это просто обычный
 * ручной ввод с фотографией под рукой). Строки с низкой уверенностью
 * подсвечены жёлтым. Отправка — один шаг: это и есть подтверждение сверки,
 * отдельного экрана «подтвердите ещё раз» нет.
 */

type RowState = {
  flow_rate: string;
  volume_standard: string;
  reading_start: string;
  reading_end: string;
  confidence: number | null;
};

const emptyRow = (recognized?: ScanRecognizedRow): RowState => ({
  flow_rate: recognized?.flow_rate ?? "",
  volume_standard: recognized?.volume_standard ?? "",
  reading_start: recognized?.reading_start ?? "",
  reading_end: recognized?.reading_end ?? "",
  confidence: recognized?.confidence ?? null,
});

const rowClass = (confidence: number | null) => {
  if (confidence == null) return "scan-row-unknown";
  return confidence < 0.9 ? "scan-row-low" : "scan-row-ok";
};

const UNIT_LABEL: Record<string, string> = { hot: "г/в", cold: "х/в" };
const CLASS_LABEL: Record<string, string> = { A: "А", B: "В" };

export default function ScanReview({
  scan,
  layoutsInfo,
  onApplied,
  onDismiss,
}: {
  scan: ScanUploadResult;
  layoutsInfo: LayoutsResponse;
  onApplied: (v: FieldVerification) => void;
  onDismiss: () => void;
}) {
  const r = scan.recognized || {};
  const modes = layoutsInfo.layouts["compact3"] ?? [];

  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState("");

  const [query, setQuery] = useState(r.si_type_query ?? "");
  const [suggestions, setSuggestions] = useState<SiTypeSuggestion[]>([]);
  const [showList, setShowList] = useState(false);
  const [siTypeId, setSiTypeId] = useState<number | null>(null);
  const [serial, setSerial] = useState(r.serial_number ?? "");
  const [year, setYear] = useState(r.manufacture_year ? String(r.manufacture_year) : "");
  const [meterClass, setMeterClass] = useState(CLASS_LABEL[r.meter_class ?? ""] ?? layoutsInfo.classes[0] ?? "В");
  const [unitType, setUnitType] = useState(UNIT_LABEL[r.unit_type ?? ""] ?? "");
  const [pulseWeight, setPulseWeight] = useState(r.pulse_weight ?? "");
  const [waterTemp, setWaterTemp] = useState(r.water_temperature ?? "");
  const [checks, setChecks] = useState<Record<string, boolean>>(
    Object.fromEntries(
      Object.keys(layoutsInfo.checks).map((key) => [
        key,
        (r.checks as Record<string, boolean | null | undefined> | undefined)?.[key] ?? true,
      ]),
    ),
  );
  const [manualUnsuitable, setManualUnsuitable] = useState(Boolean(r.manual_unsuitable));
  const [manualReason, setManualReason] = useState(r.manual_unsuitability_reason ?? "");
  const [rows, setRows] = useState<RowState[]>(() => {
    const recognizedRows = r.rows ?? [];
    return modes.map((_, index) => emptyRow(recognizedRows[index]));
  });

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const debounce = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    let revoke: string | null = null;
    fetchScanImageUrl(scan.id)
      .then((url) => {
        revoke = url;
        setImageUrl(url);
      })
      .catch((exc) => setImageError(exc instanceof Error ? exc.message : "Не удалось загрузить фото"));
    return () => {
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [scan.id]);

  useEffect(() => {
    clearTimeout(debounce.current);
    if (siTypeId || query.trim().length < 2) {
      setSuggestions([]);
      return;
    }
    debounce.current = setTimeout(async () => {
      const response = await suggestSiTypes(query);
      setSuggestions(response.results.filter((item) => item.si_type_id !== null));
      setShowList(true);
    }, 300);
    return () => clearTimeout(debounce.current);
  }, [query, siTypeId]);

  const pick = (item: SiTypeSuggestion) => {
    setSiTypeId(item.si_type_id);
    setQuery(item.label);
    setShowList(false);
  };

  const updateRow = (index: number, patch: Partial<RowState>) =>
    setRows((current) => current.map((row, idx) => (idx === index ? { ...row, ...patch } : row)));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");

    if (!siTypeId) {
      setError("Выберите тип СИ из списка подсказок — модель прочитала его с бланка, но завести прибор нужно осознанно");
      return;
    }
    if (!serial.trim()) {
      setError("Укажите заводской номер");
      return;
    }
    const allRowsEmpty = rows.every((row) => !row.flow_rate.trim() && !row.volume_standard.trim());
    const allRowsFilled = rows.every((row) => row.flow_rate.trim() && row.volume_standard.trim());
    if (!allRowsFilled && !(manualUnsuitable && allRowsEmpty)) {
      setError("Заполните расход и объём по эталону во всех строках — либо отметьте «Непригоден», если прибор физически не проверить");
      return;
    }
    if (manualUnsuitable && !manualReason.trim()) {
      setError("Укажите причину непригодности");
      return;
    }

    setBusy(true);
    try {
      const verification = await applyScan(scan.id, {
        si_type_id: siTypeId,
        serial_number: serial.trim(),
        manufacture_year: year ? Number(year) : null,
        measurements: {
          layout: "compact3",
          meter_class: meterClass,
          checks,
          pulse_weight: pulseWeight || undefined,
          unit_type: unitType || undefined,
          water_temperature: waterTemp || undefined,
          manual_unsuitable: manualUnsuitable,
          manual_unsuitability_reason: manualUnsuitable ? manualReason.trim() : undefined,
          rows:
            manualUnsuitable && allRowsEmpty
              ? []
              : rows.map((row): MeasurementRowPayload => ({
                  flow_rate: row.flow_rate,
                  volume_standard: row.volume_standard,
                  reading_start: row.reading_start || undefined,
                  reading_end: row.reading_end || undefined,
                })),
        },
      });
      onApplied(verification);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось завести поверку");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scan-review">
      <h4>Сверка скана №{scan.id}</h4>
      {scan.warning && <div className="hint">{scan.warning}</div>}
      {!scan.recognized?.legible && Object.keys(scan.recognized || {}).length > 0 && (
        <div className="hint">Модель отметила фото как плохо читаемое — проверьте все поля особенно внимательно.</div>
      )}

      <div className="scan-review-grid">
        <div className="scan-review-photo">
          {imageError && <div className="error">{imageError}</div>}
          {imageUrl ? <img src={imageUrl} alt="Фото бланка" /> : !imageError && <div className="loading">Загружаю фото…</div>}
        </div>

        <form className="new-slot-form" onSubmit={submit}>
          <div className="field-row">
            <div className="field suggest-field">
              <label htmlFor="sr-si-type">Тип СИ</label>
              <input
                id="sr-si-type"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setSiTypeId(null);
                }}
                onFocus={() => setShowList(suggestions.length > 0)}
                onBlur={() => setTimeout(() => setShowList(false), 150)}
                placeholder="номер в Госреестре или наименование"
                autoComplete="off"
              />
              {showList && (
                <ul className="suggestions">
                  {suggestions.length === 0 && <li className="sub">Совпадений в системе нет — заведите тип через админку</li>}
                  {suggestions.map((item) => (
                    <li key={`${item.registry_number}-${item.manufacturer}`} onMouseDown={() => pick(item)}>
                      <span>{item.label}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="field">
              <label htmlFor="sr-serial">Зав. номер</label>
              <input id="sr-serial" value={serial} onChange={(event) => setSerial(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="sr-year">Год выпуска</label>
              <input id="sr-year" value={year} onChange={(event) => setYear(event.target.value.replace(/\D/g, ""))} style={{ width: 90 }} />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="sr-class">Класс</label>
              <select id="sr-class" value={meterClass} onChange={(event) => setMeterClass(event.target.value)}>
                {layoutsInfo.classes.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="sr-unit">Тип счётчика</label>
              <select id="sr-unit" value={unitType} onChange={(event) => setUnitType(event.target.value)}>
                <option value="">—</option>
                <option value="г/в">горячая вода</option>
                <option value="х/в">холодная вода</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="sr-pulse">Коэфф. K</label>
              <input id="sr-pulse" value={pulseWeight} onChange={(event) => setPulseWeight(event.target.value)} style={{ width: 100 }} />
            </div>
            <div className="field">
              <label htmlFor="sr-water-temp">Темп. воды, °C</label>
              <input id="sr-water-temp" value={waterTemp} onChange={(event) => setWaterTemp(event.target.value)} style={{ width: 100 }} />
            </div>
          </div>

          <div className="filters">
            {Object.entries(layoutsInfo.checks).map(([key, label]) => (
              <label key={key} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  type="checkbox"
                  checked={checks[key] ?? true}
                  onChange={(event) => setChecks((current) => ({ ...current, [key]: event.target.checked }))}
                />
                {label}
              </label>
            ))}
          </div>

          <div className="field" style={{ border: "1px solid var(--line)", borderRadius: "var(--radius)", padding: 10 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={manualUnsuitable} onChange={(event) => setManualUnsuitable(event.target.checked)} />
              НЕПРИГОДЕН (по другой причине, не по погрешности)
            </label>
            {manualUnsuitable && (
              <div className="field-row" style={{ marginTop: 8 }}>
                <div className="field" style={{ flex: 2 }}>
                  <label htmlFor="sr-reason-text">Причина<span className="req">*</span></label>
                  <input id="sr-reason-text" value={manualReason} onChange={(event) => setManualReason(event.target.value)} />
                </div>
              </div>
            )}
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Режим</th>
                  <th>Q, м³/ч</th>
                  <th>Vэтал, м³</th>
                  <th>Показания начало</th>
                  <th>Показания конец</th>
                  <th>Увер.</th>
                </tr>
              </thead>
              <tbody>
                {modes.map((mode, index) => (
                  <tr key={index} className={rowClass(rows[index]?.confidence ?? null)}>
                    <td>{mode.label} <span className="sub">{mode.seconds} с</span></td>
                    <td><input value={rows[index]?.flow_rate ?? ""} onChange={(e) => updateRow(index, { flow_rate: e.target.value })} style={{ width: 70 }} /></td>
                    <td><input value={rows[index]?.volume_standard ?? ""} onChange={(e) => updateRow(index, { volume_standard: e.target.value })} style={{ width: 80 }} /></td>
                    <td><input value={rows[index]?.reading_start ?? ""} onChange={(e) => updateRow(index, { reading_start: e.target.value })} style={{ width: 90 }} /></td>
                    <td><input value={rows[index]?.reading_end ?? ""} onChange={(e) => updateRow(index, { reading_end: e.target.value })} style={{ width: 90 }} /></td>
                    <td className="sub">
                      {rows[index]?.confidence == null ? "—" : `${Math.round((rows[index]!.confidence as number) * 100)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="field-row">
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Завожу поверку…" : "Подтвердить и завести поверку"}
            </button>
            <button type="button" onClick={onDismiss} disabled={busy}>
              Отмена
            </button>
          </div>
          {error && <div className="error">{error}</div>}
        </form>
      </div>
    </div>
  );
}
