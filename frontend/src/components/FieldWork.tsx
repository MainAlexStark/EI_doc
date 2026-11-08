import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteVerificationPhoto,
  downloadBlank,
  downloadVerificationProtocol,
  fetchVerificationPhotoUrl,
  fetchVerificationPhotos,
  fetchWorkOrderScans,
  fetchWorkOrders,
  setWorkOrderStatus,
  uploadScan,
  uploadVerificationPhoto,
  type FieldVerification,
  type LayoutsResponse,
  type Me,
  type MeasurementRowPayload,
  type MeasurementsPayload,
  type MeasurementsResult,
  type ScanUploadResult,
  type TemplateForVerification,
  type TemplateMeasurementsPayload,
  type TemplateReadings,
  type VerificationPhoto,
  type WorkOrder,
} from "../api";
import Icon from "../ui/Icon";
import { confirmDialog } from "../ui/Modal";
import { EmptyState, PageActions, Segmented, Spinner } from "../ui/controls";
import ScanReview from "./ScanReview";
import SiTypeDocuments from "./FifDocuments";
import SiTypePicker, { type PickedType } from "./SiTypePicker";
import {
  addVerification,
  flush,
  isOnline,
  listVerifications,
  loadLayouts,
  loadTemplateInfo,
  outboxErrors,
  pendingCount,
  refreshSiTypeCache,
  refreshTemplateCache,
  removeOutboxItem,
  saveMeasurements,
  subscribe,
} from "../offline/sync";
import type { CachedVerification, OutboxItem } from "../offline/db";

/** Экран поверителя: наряд → счётчики по нему → измерения. Рассчитан на
 * телефон и на работу без связи (см. src/offline/sync.ts). Счётчики воды — своя
 * форма (apps.verification.calculators.water_meter), остальные СИ — форма по шаблону
 * поверки (apps.verification.templates, шаблон составляет ассистент).
 *
 * Устроен как три шага с крупными кнопками вместо одной длинной страницы:
 * сначала карточки нарядов, потом список счётчиков наряда, потом измерения
 * одного счётчика. Бумажный бланк для режимных объектов убран в
 * раскрывающийся блок — он нужен редко. */

function StatusBadge({ v }: { v: CachedVerification }) {
  if (v.sync_state === "pending") return <span className="pill warn">не отправлено</span>;
  if (v.needs_review) return <span className="pill warn">на сверку</span>;
  if (v.status === "ready") return <span className="pill ok">готова</span>;
  if (v.status === "accepted") return <span className="pill ok">принята</span>;
  return <span className="pill muted">нет измерений</span>;
}

function formatDate(value: string | null): string {
  if (!value) return "без даты";
  const date = new Date(value);
  const today = new Date();
  const tomorrow = new Date();
  tomorrow.setDate(today.getDate() + 1);
  if (date.toDateString() === today.toDateString()) return "сегодня";
  if (date.toDateString() === tomorrow.toDateString()) return "завтра";
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

/** Фото конкретного СИ — табличка, повреждение, место установки и т. п.
 * В отличие от измерений это НЕ идёт в офлайн-очередь (см. models.py
 * VerificationPhoto): требует, чтобы поверка уже была на сервере, т. е.
 * server_id — до синхронизации кнопка просто объясняет, почему фото пока
 * приложить нельзя. */
function PhotosPanel({ verification }: { verification: CachedVerification }) {
  const serverId = verification.server_id;
  const [photos, setPhotos] = useState<VerificationPhoto[]>([]);
  const [urls, setUrls] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    if (serverId == null) return;
    try {
      setPhotos(await fetchVerificationPhotos(serverId));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить фото");
    }
  }, [serverId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const next: Record<number, string> = {};
      for (const photo of photos) {
        try {
          next[photo.id] = await fetchVerificationPhotoUrl(photo.id);
        } catch {
          /* одно неудачное фото не должно ломать остальные */
        }
      }
      if (!cancelled) setUrls(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [photos]);

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || serverId == null) return;
    setBusy(true);
    setError("");
    try {
      await uploadVerificationPhoto(serverId, file);
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить фото");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (photoId: number) => {
    setBusy(true);
    setError("");
    try {
      await deleteVerificationPhoto(photoId);
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось удалить фото");
    } finally {
      setBusy(false);
    }
  };

  if (serverId == null) {
    return (
      <div className="card">
        <h4>Фото прибора</h4>
        <p className="hint">Можно будет добавить, когда счётчик отправится на сервер — сейчас он ещё в очереди.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h4>Фото прибора</h4>
      {photos.length === 0 && <p className="sub">Пока фото нет — сфотографируйте табличку или повреждение, если нужно</p>}
      {photos.length > 0 && (
        <div className="photo-grid">
          {photos.map((photo) => (
            <div className="photo-thumb" key={photo.id}>
              {urls[photo.id] ? (
                <img src={urls[photo.id]} alt={photo.caption || "фото СИ"} />
              ) : (
                <span className="photo-thumb-empty"><span className="spinner sm" /></span>
              )}
              <button
                type="button"
                className="icon-btn"
                title="Удалить фото"
                aria-label="Удалить фото"
                onClick={() => void handleDelete(photo.id)}
                disabled={busy}
              >
                <Icon name="trash" size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
      <button type="button" className="big" onClick={() => fileInput.current?.click()} disabled={busy}>
        <Icon name="camera" />
        {busy ? "Загружаю…" : "Сфотографировать"}
      </button>
      <input
        ref={fileInput}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: "none" }}
        onChange={(event) => void handleUpload(event)}
      />
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function AddInstrumentForm({
  workOrderId,
  onAdded,
  onCancel,
}: {
  workOrderId: number;
  onAdded: (v: CachedVerification) => void;
  onCancel: () => void;
}) {
  const [siType, setSiType] = useState<PickedType | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [serial, setSerial] = useState("");
  const [year, setYear] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!siType) {
      setError("Сначала выберите тип счётчика");
      return;
    }
    if (!serial.trim()) {
      setError("Впишите заводской номер");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await addVerification(workOrderId, {
        si_type_id: siType.id,
        serial_number: serial.trim(),
        manufacture_year: year ? Number(year) : null,
      });
      onAdded(created);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось добавить счётчик");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h3>Новый счётчик</h3>
      <SiTypePicker value={siType} onChange={setSiType} inputId="fw-si-type" onDraftChange={setDrafting} />

      {siType && (
        <>
          <div className="field-row">
            <div className="field">
              <label htmlFor="fw-serial">Заводской номер<span className="req">*</span></label>
              <input
                id="fw-serial"
                className="big-input"
                value={serial}
                onChange={(event) => setSerial(event.target.value)}
                placeholder="с корпуса счётчика"
                autoFocus
              />
            </div>
            <div className="field" style={{ maxWidth: 160 }}>
              <label htmlFor="fw-year">Год выпуска</label>
              <input
                id="fw-year"
                className="big-input"
                inputMode="numeric"
                value={year}
                onChange={(event) => setYear(event.target.value.replace(/\D/g, "").slice(0, 4))}
                placeholder="если есть"
              />
            </div>
          </div>
        </>
      )}

      {!drafting && (
        <div className="actions">
          {siType && (
            <button className="primary big" type="submit" disabled={busy}>
              {busy ? "Добавляю…" : "Добавить и перейти к измерениям"}
            </button>
          )}
          <button type="button" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </form>
  );
}

type RowState = {
  flow_rate: string;
  volume_standard: string;
  reading_start: string;
  reading_end: string;
  pulses: string;
  volume_meter: string;
};

type VolumeMethod = "readings" | "pulses" | "direct";

const emptyRow = (): RowState => ({
  flow_rate: "", volume_standard: "", reading_start: "", reading_end: "", pulses: "", volume_meter: "",
});

const LAYOUT_LABELS: Record<string, string> = {
  compact3: "3 пролива (обычная)",
  extended9: "9 проливов (полная)",
};

function NumberInput({
  label,
  value,
  onChange,
  id,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  id: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} inputMode="decimal" className="big-input" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function MeasurementForm({
  verification,
  layoutsInfo,
  onSaved,
}: {
  verification: CachedVerification;
  layoutsInfo: LayoutsResponse;
  onSaved: () => void;
}) {
  const layoutKeys = Object.keys(layoutsInfo.layouts);
  const [layout, setLayout] = useState(layoutKeys[0] ?? "");
  const [meterClass, setMeterClass] = useState(layoutsInfo.classes[0] ?? "В");
  const [pulseWeight, setPulseWeight] = useState("");
  const [unitType, setUnitType] = useState("");
  const [waterTemp, setWaterTemp] = useState("");
  const [method, setMethod] = useState<VolumeMethod>("readings");
  const [checks, setChecks] = useState<Record<string, boolean>>(
    Object.fromEntries(Object.keys(layoutsInfo.checks).map((key) => [key, true])),
  );
  const modes = layoutsInfo.layouts[layout] ?? [];
  const [rows, setRows] = useState<RowState[]>(() => modes.map(() => emptyRow()));

  useEffect(() => {
    setRows(modes.map(() => emptyRow()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout]);

  const updateRow = (index: number, patch: Partial<RowState>) =>
    setRows((current) => {
      const next = current.map((row, idx) => (idx === index ? { ...row, ...patch } : row));
      // Показания в конце пролива — это показания в начале следующего:
      // подставляем, если поверитель туда ещё ничего не вписал.
      if (patch.reading_end !== undefined && next[index + 1]) {
        const following = next[index + 1];
        const previousEnd = current[index]?.reading_end ?? "";
        if (!following.reading_start || following.reading_start === previousEnd) {
          next[index + 1] = { ...following, reading_start: patch.reading_end };
        }
      }
      return next;
    });

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<MeasurementsResult | null>(null);
  const [queuedNotice, setQueuedNotice] = useState(false);

  // Непригоден не по расчётной погрешности — осмотр, повреждение и т. п.
  // (тж. непроходимая механическая поломка, когда измерения вообще снять
  // нельзя). См. apps.verification.measurements.apply().
  const [manualUnsuitable, setManualUnsuitable] = useState(false);
  const [manualReason, setManualReason] = useState("");

  const volumeFilled = (row: RowState) =>
    method === "readings"
      ? Boolean(row.reading_start.trim() && row.reading_end.trim())
      : method === "pulses"
        ? Boolean(row.pulses.trim())
        : Boolean(row.volume_meter.trim());

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setResult(null);
    setQueuedNotice(false);

    const allRowsEmpty = rows.every((row) => !row.flow_rate.trim() && !row.volume_standard.trim());
    const incomplete = rows.findIndex(
      (row) => !(row.flow_rate.trim() && row.volume_standard.trim() && volumeFilled(row)),
    );
    if (incomplete >= 0 && !(manualUnsuitable && allRowsEmpty)) {
      setError(
        `Не заполнен пролив ${incomplete + 1}: нужны расход, объём по эталону и ` +
          (method === "readings" ? "показания счётчика в начале и в конце" : method === "pulses" ? "импульсы" : "объём по счётчику") +
          ". Если прибор физически не проверить — отметьте «Непригоден» ниже и оставьте проливы пустыми.",
      );
      return;
    }
    if (method === "pulses" && !pulseWeight.trim() && !(manualUnsuitable && allRowsEmpty)) {
      setError("Для подсчёта по импульсам укажите коэффициент K (литров на импульс)");
      return;
    }
    if (manualUnsuitable && !manualReason.trim()) {
      setError("Укажите причину непригодности");
      return;
    }

    setBusy(true);
    try {
      const payload: MeasurementsPayload = {
        layout,
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
            : rows.map((row): MeasurementRowPayload => {
                const built: MeasurementRowPayload = { flow_rate: row.flow_rate, volume_standard: row.volume_standard };
                if (method === "readings") {
                  built.reading_start = row.reading_start;
                  built.reading_end = row.reading_end;
                } else if (method === "pulses") {
                  built.pulses = Number(row.pulses);
                } else {
                  built.volume_meter = row.volume_meter;
                }
                return built;
              }),
      };
      const outcome = await saveMeasurements(verification.client_id, payload);
      if (outcome.queued) setQueuedNotice(true);
      else setResult(outcome.result);
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить измерения");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h3>Измерения</h3>

      <div className="seg-row">
        <div className="field">
          <label>Вода</label>
          <Segmented
            value={unitType}
            onChange={setUnitType}
            options={[
              { value: "х/в", label: "Холодная" },
              { value: "г/в", label: "Горячая" },
            ]}
          />
        </div>
        <div className="field">
          <label>Класс</label>
          <Segmented
            value={meterClass}
            onChange={setMeterClass}
            options={layoutsInfo.classes.map((item) => ({ value: item, label: item }))}
          />
        </div>
        {layoutKeys.length > 1 && (
          <div className="field">
            <label>Сколько проливов</label>
            <Segmented
              value={layout}
              onChange={setLayout}
              options={layoutKeys.map((key) => ({ value: key, label: LAYOUT_LABELS[key] ?? key }))}
            />
          </div>
        )}
      </div>

      <div className="field">
        <label>Объём по счётчику считаю</label>
        <Segmented
          value={method}
          onChange={setMethod}
          options={[
            { value: "readings", label: "по показаниям" },
            { value: "pulses", label: "по импульсам" },
            { value: "direct", label: "вручную" },
          ]}
        />
      </div>
      {method === "pulses" && (
        <div className="field" style={{ maxWidth: 260 }}>
          <label htmlFor="fw-pulse">Коэффициент K, л/имп.<span className="req">*</span></label>
          <input id="fw-pulse" className="big-input" inputMode="decimal" value={pulseWeight} onChange={(e) => setPulseWeight(e.target.value)} />
        </div>
      )}

      <div className="runs">
        {modes.map((mode, index) => {
          const row = rows[index] ?? emptyRow();
          return (
            <fieldset className="run" key={index}>
              <legend>
                Пролив {index + 1} · {mode.label} <span className="sub">{mode.seconds} с</span>
              </legend>
              <div className="run-grid">
                <NumberInput id={`r${index}-q`} label="Расход Q, м³/ч" value={row.flow_rate} onChange={(v) => updateRow(index, { flow_rate: v })} />
                <NumberInput id={`r${index}-ve`} label="Объём по эталону, м³" value={row.volume_standard} onChange={(v) => updateRow(index, { volume_standard: v })} />
                {method === "readings" && (
                  <>
                    <NumberInput id={`r${index}-rs`} label="Показания в начале" value={row.reading_start} onChange={(v) => updateRow(index, { reading_start: v })} />
                    <NumberInput id={`r${index}-re`} label="Показания в конце" value={row.reading_end} onChange={(v) => updateRow(index, { reading_end: v })} />
                  </>
                )}
                {method === "pulses" && (
                  <NumberInput id={`r${index}-p`} label="Импульсы" value={row.pulses} onChange={(v) => updateRow(index, { pulses: v.replace(/\D/g, "") })} />
                )}
                {method === "direct" && (
                  <NumberInput id={`r${index}-vm`} label="Объём по счётчику, м³" value={row.volume_meter} onChange={(v) => updateRow(index, { volume_meter: v })} />
                )}
              </div>
            </fieldset>
          );
        })}
      </div>

      <div className="field">
        <label>Проверки пройдены</label>
        <div className="check-row">
          {Object.entries(layoutsInfo.checks).map(([key, label]) => (
            <label key={key} className={`check-chip ${checks[key] ?? true ? "on" : "off"}`}>
              <input
                type="checkbox"
                checked={checks[key] ?? true}
                onChange={(event) => setChecks((current) => ({ ...current, [key]: event.target.checked }))}
              />
              {label.replace(/\s*\(.*\)$/, "")}
            </label>
          ))}
        </div>
        <p className="hint">Снимите галочку, если проверка не пройдена — счётчик будет признан непригодным.</p>
      </div>

      <details className="more">
        <summary>Дополнительно: температура воды</summary>
        <div className="field" style={{ maxWidth: 260, marginTop: 8 }}>
          <label htmlFor="fw-water-temp">Температура воды, °C</label>
          <input id="fw-water-temp" className="big-input" inputMode="decimal" value={waterTemp} onChange={(e) => setWaterTemp(e.target.value)} placeholder="если измеряли" />
        </div>
      </details>

      <div className={`unsuitable ${manualUnsuitable ? "on" : ""}`}>
        <label className="toggle">
          <input type="checkbox" checked={manualUnsuitable} onChange={(event) => setManualUnsuitable(event.target.checked)} />
          Счётчик непригоден по другой причине (разбит, заклинило, нет пломбы…)
        </label>
        {manualUnsuitable && (
          <>
            <div className="chip-row">
              {layoutsInfo.common_unsuitability_reasons.map((reason) => (
                <button
                  type="button"
                  key={reason}
                  className={`chip ${manualReason === reason ? "active" : ""}`}
                  onClick={() => setManualReason(reason)}
                >
                  {reason}
                </button>
              ))}
            </div>
            <div className="field">
              <label htmlFor="fw-reason-text">Причина<span className="req">*</span></label>
              <input
                id="fw-reason-text"
                className="big-input"
                value={manualReason}
                onChange={(event) => setManualReason(event.target.value)}
                placeholder="выберите выше или впишите свою"
              />
            </div>
            <p className="hint">Если снять измерения невозможно — оставьте проливы пустыми.</p>
          </>
        )}
      </div>

      <button className="primary big wide" type="submit" disabled={busy}>
        {busy ? "Сохраняю…" : "Сохранить и посчитать"}
      </button>
      {error && <div className="error">{error}</div>}
      {queuedNotice && (
        <div className="verdict queued">Связи нет — сохранено в телефоне. Отправится само, когда появится связь.</div>
      )}
      {result && (
        <div className={`verdict ${result.suitable ? "ok" : "bad"}`}>
          <b>{result.suitable ? "ГОДЕН" : "НЕ ГОДЕН"}</b>
          {!result.suitable && <span>{result.reasons.join("; ")}</span>}
          {result.needs_review && <span>есть строки на сверку у метролога</span>}
        </div>
      )}
    </form>
  );
}

/** Измерения по шаблону поверки (манометры, весы, гири… — всё, кроме счётчиков воды).
 * Шаблон составил ассистент по методике, метролог проверил; поверитель вносит только параметры
 * прибора (если их нет у типа), отметки по операциям и показания в поверяемых точках. Считает
 * и печатает протокол EI_doc (apps.verification.templates). Отправка — через ту же офлайн-очередь. */
function TemplateMeasurementForm({
  verification,
  info,
  onSaved,
}: {
  verification: CachedVerification;
  info: TemplateForVerification;
  onSaved: () => void;
}) {
  const template = info.template!;
  const plan = template.plan;
  const saved = info.saved ?? null;
  const fromType = new Set(info.params_from_type ?? []);
  const [params, setParams] = useState<Record<string, string>>(() =>
    Object.fromEntries(plan.params.map((p) => [p.key, (info.params?.[p.key] ?? "").replace(".", ",")])),
  );
  const [modes, setModes] = useState(info.modes ?? []);
  const [kind, setKind] = useState<"periodic" | "primary">((saved?.kind as "periodic" | "primary") ?? "periodic");
  const [operations, setOperations] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      plan.operations.map((op) => [op.key, saved?.operations?.find((x) => x.key === op.key)?.passed !== false]),
    ),
  );
  const [readings, setReadings] = useState<TemplateReadings>(() => {
    const out: TemplateReadings = {};
    for (const m of plan.modes) {
      const prev = saved?.readings?.[m.key];
      out[m.key] = {
        display: prev?.display ?? "",
        readings: Object.fromEntries(
          plan.points.map((p) => [
            p.key,
            Array.from({ length: plan.repeats }, (_, i) => (prev?.readings?.[p.key]?.[i] ?? "").replace(".", ",")),
          ]),
        ),
      };
    }
    return out;
  });
  const [conditions, setConditions] = useState<Record<string, string>>(() => ({ ...(saved?.conditions ?? {}) }));
  const [manualUnsuitable, setManualUnsuitable] = useState(Boolean(saved?.manual_unsuitable));
  const [manualReason, setManualReason] = useState(saved?.manual_unsuitability_reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<MeasurementsResult | null>(null);
  const [queuedNotice, setQueuedNotice] = useState(false);

  // Точки зависят от параметров прибора (25 % ВПИ и т. п.) — пересчитываем на сервере, когда всё внесено.
  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    if (plan.params.some((p) => !params[p.key]?.trim())) return;
    const handle = setTimeout(() => {
      loadTemplateInfo(verification, params)
        .then((fresh) => {
          if (fresh.modes?.length) setModes(fresh.modes);
        })
        .catch(() => undefined);
    }, 400);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey]);

  const setCell = (mode: string, point: string, index: number, value: string) =>
    setReadings((current) => {
      const next = { ...current, [mode]: { ...current[mode], readings: { ...current[mode].readings } } };
      const row = [...next[mode].readings[point]];
      row[index] = value;
      next[mode].readings[point] = row;
      return next;
    });

  const labelOf = (key: string) => modes.find((m) => m.key === key)?.label || plan.modes.find((m) => m.key === key)?.label || key;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setResult(null);
    setQueuedNotice(false);
    const missingParam = plan.params.find((p) => !params[p.key]?.trim());
    if (missingParam) {
      setError(`Не заполнен параметр прибора: ${missingParam.label}`);
      return;
    }
    const empty = Object.values(readings).every((m) => Object.values(m.readings).every((row) => row.every((v) => !v.trim())));
    if (!(manualUnsuitable && empty)) {
      for (const m of plan.modes) {
        for (const p of plan.points) {
          if (readings[m.key].readings[p.key].some((v) => !v.trim())) {
            setError(`Не заполнено: ${labelOf(m.key)}, ${p.label}. Если прибор не проверить — отметьте «Непригоден» ниже.`);
            return;
          }
        }
        if (plan.uses_display && !readings[m.key].display?.trim()) {
          setError(`Не заполнено показание индикатора: ${labelOf(m.key)}`);
          return;
        }
      }
    }
    if (manualUnsuitable && !manualReason.trim()) {
      setError("Укажите причину непригодности");
      return;
    }
    const dot = (v: string) => v.trim().replace(",", ".");
    const payload: TemplateMeasurementsPayload = {
      template_id: template.id,
      kind,
      params: Object.fromEntries(Object.entries(params).filter(([k]) => !fromType.has(k)).map(([k, v]) => [k, dot(v)])),
      operations,
      readings:
        manualUnsuitable && empty
          ? {}
          : Object.fromEntries(
              Object.entries(readings).map(([mode, data]) => [
                mode,
                {
                  display: data.display ? dot(data.display) : "",
                  readings: Object.fromEntries(Object.entries(data.readings).map(([p, row]) => [p, row.map(dot)])),
                },
              ]),
            ),
      conditions: Object.fromEntries(Object.entries(conditions).filter(([, v]) => v.trim()).map(([k, v]) => [k, dot(v)])),
      manual_unsuitable: manualUnsuitable,
      manual_unsuitability_reason: manualUnsuitable ? manualReason.trim() : "",
    };
    setBusy(true);
    try {
      const outcome = await saveMeasurements(verification.client_id, payload);
      if (outcome.queued) setQueuedNotice(true);
      else setResult(outcome.result);
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить измерения");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h3>Поверка по шаблону</h3>
      <p className="hint">
        {template.title} · {plan.method}
        {template.needs_review && <> · <span className="pill warn">шаблон ждёт проверки метролога</span></>}
      </p>

      <div className="field">
        <label>Вид поверки</label>
        <Segmented
          value={kind}
          onChange={setKind}
          options={[
            { value: "periodic", label: "Периодическая" },
            { value: "primary", label: "Первичная" },
          ]}
        />
      </div>

      {plan.params.length > 0 && (
        <div className="form-grid">
          {plan.params.map((p) =>
            fromType.has(p.key) ? (
              <div className="field" key={p.key}>
                <label>{p.label}</label>
                <div className="sub">{params[p.key]} {p.unit} · из типа СИ</div>
              </div>
            ) : (
              <NumberInput
                key={p.key}
                id={`tp-${p.key}`}
                label={`${p.label}${p.unit ? `, ${p.unit}` : ""}`}
                value={params[p.key] ?? ""}
                onChange={(v) => setParams((current) => ({ ...current, [p.key]: v }))}
              />
            ),
          )}
        </div>
      )}

      <div className="field">
        <label>Операции поверки — соответствует</label>
        <div className="check-row">
          {plan.operations.map((op) => (
            <label key={op.key} className={`check-chip ${operations[op.key] ? "on" : "off"}`}>
              <input
                type="checkbox"
                checked={operations[op.key]}
                onChange={(event) => setOperations((current) => ({ ...current, [op.key]: event.target.checked }))}
              />
              {op.label}
            </label>
          ))}
        </div>
        <p className="hint">Снимите галочку, если операция не пройдена — прибор будет признан непригодным.</p>
      </div>

      <div className="runs">
        {plan.modes.map((m) => (
          <fieldset className="run" key={m.key}>
            <legend>
              {labelOf(m.key)}
              {plan.unit && !labelOf(m.key).includes(plan.unit) && <span className="sub"> {plan.unit}</span>}
            </legend>
            <div className="run-grid">
              {plan.points.flatMap((p) =>
                readings[m.key].readings[p.key].map((value, i) => (
                  <NumberInput
                    key={`${p.key}-${i}`}
                    id={`t-${m.key}-${p.key}-${i}`}
                    label={plan.repeats > 1 ? `${p.label}, №${i + 1}` : p.label}
                    value={value}
                    onChange={(v) => setCell(m.key, p.key, i, v)}
                  />
                )),
              )}
              {plan.uses_display && (
                <NumberInput
                  id={`t-${m.key}-display`}
                  label="Показание индикатора"
                  value={readings[m.key].display ?? ""}
                  onChange={(v) => setReadings((current) => ({ ...current, [m.key]: { ...current[m.key], display: v } }))}
                />
              )}
            </div>
          </fieldset>
        ))}
      </div>

      {plan.extra_conditions.length > 0 && (
        <div className="form-grid">
          {plan.extra_conditions.map((c) => (
            <NumberInput
              key={c.key}
              id={`tc-${c.key}`}
              label={`${c.label}${c.unit ? `, ${c.unit}` : ""}`}
              value={conditions[c.key] ?? ""}
              onChange={(v) => setConditions((current) => ({ ...current, [c.key]: v }))}
            />
          ))}
        </div>
      )}

      <div className={`unsuitable ${manualUnsuitable ? "on" : ""}`}>
        <label className="toggle">
          <input type="checkbox" checked={manualUnsuitable} onChange={(event) => setManualUnsuitable(event.target.checked)} />
          Прибор непригоден по другой причине (разбит, нет пломбы, не работает…)
        </label>
        {manualUnsuitable && (
          <div className="field">
            <label htmlFor="tp-reason">Причина<span className="req">*</span></label>
            <input id="tp-reason" className="big-input" value={manualReason} onChange={(e) => setManualReason(e.target.value)} />
          </div>
        )}
      </div>

      <button className="primary big wide" type="submit" disabled={busy}>
        {busy ? "Сохраняю…" : "Сохранить и посчитать"}
      </button>
      {error && <div className="error">{error}</div>}
      {queuedNotice && (
        <div className="verdict queued">Связи нет — сохранено в телефоне. Отправится само, когда появится связь.</div>
      )}
      {result && (
        <div className={`verdict ${result.suitable ? "ok" : "bad"}`}>
          <b>{result.suitable ? "ГОДЕН" : "НЕ ГОДЕН"}</b>
          {!result.suitable && <span>{result.reasons.join("; ")}</span>}
        </div>
      )}
    </form>
  );
}

/** Протокол поверки — черновик, пока не подписан в журнале. */
function ProtocolDownloads({ verification, wordToo }: { verification: CachedVerification; wordToo: boolean }) {
  const [error, setError] = useState("");
  if (verification.server_id == null || verification.status === "draft") return null;
  const get = (fmt: "pdf" | "docx") =>
    downloadVerificationProtocol(verification.server_id!, fmt).catch((exc) =>
      setError(exc instanceof Error ? exc.message : "Не удалось скачать протокол"),
    );
  return (
    <div className="card">
      <div className="actions">
        <button type="button" onClick={() => void get("pdf")}><Icon name="download" size={16} /> Протокол PDF</button>
        {wordToo && <button type="button" onClick={() => void get("docx")}><Icon name="download" size={16} /> Протокол Word</button>}
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

/** Какая форма нужна этому прибору: счётчик воды — своя, остальное — по шаблону поверки. */
function VerificationEntry({
  verification,
  layoutsInfo,
  onSaved,
}: {
  verification: CachedVerification;
  layoutsInfo: LayoutsResponse | null;
  onSaved: () => void;
}) {
  const [info, setInfo] = useState<(TemplateForVerification & { offline?: boolean }) | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    loadTemplateInfo(verification)
      .then(setInfo)
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить шаблон поверки"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [verification.client_id, verification.server_id]);

  if (error) return <div className="error">{error}</div>;
  if (!info) return <div className="loading">Загружаю форму измерений…</div>;
  if (info.engine === "template") {
    if (!info.template) {
      return (
        <div className="card">
          <b>Для этого типа нет шаблона поверки</b>
          <p className="hint">
            Попросите ассистента: «сделай шаблон протокола поверки для {verification.si_type_name}» — он соберёт
            его по методике из ФГИС, метролог проверит.
          </p>
        </div>
      );
    }
    return (
      <>
        <TemplateMeasurementForm key={verification.client_id} verification={verification} info={info} onSaved={onSaved} />
        <ProtocolDownloads verification={verification} wordToo />
      </>
    );
  }
  return layoutsInfo ? (
    <>
      <MeasurementForm key={verification.client_id} verification={verification} layoutsInfo={layoutsInfo} onSaved={onSaved} />
      <ProtocolDownloads verification={verification} wordToo={false} />
    </>
  ) : (
    <div className="loading">Загружаю форму измерений…</div>
  );
}

function SyncBar({
  online,
  queued,
  problems,
  onRemove,
}: {
  online: boolean;
  queued: number;
  problems: OutboxItem[];
  onRemove: (id: string) => void;
}) {
  return (
    <>
      <div className={`sync-bar ${online ? "ok" : "off"} ${queued > 0 ? "has-queue" : ""}`}>
        <Icon name={online ? (queued > 0 ? "upload" : "wifi") : "wifiOff"} size={17} />
        <span className="grow">
          {online ? "" : "Нет связи — всё сохраняется в телефоне. "}
          {queued > 0 && `Ждут отправки: ${queued}`}
        </span>
        {queued > 0 && (
          // Не завязано на online — статус связи браузера ненадёжен, а сама
          // попытка отправки сама разберётся, есть сеть или нет (см. flush()).
          <button type="button" className="sm" onClick={() => void flush()}>
            Отправить сейчас
          </button>
        )}
      </div>
      {problems.length > 0 && (
        <div className="card warn-card">
          <b>Не удалось отправить</b>
          <ul className="item-list">
            {problems.map((item) => (
              <li key={item.id}>
                <span className="name">
                  {item.kind === "create_verification" ? "Новый счётчик" : "Измерения"}
                  {" — "}
                  <span className="sub">{item.last_error || "не удалось отправить"}</span>
                </span>
                <button type="button" className="sm" onClick={() => onRemove(item.id)}>
                  Убрать
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

const OPEN_STATUSES = ["planned", "in_progress"];

type Period = "active" | "closed";

const dayKey = (value: string | null) => (value ? new Date(value).toDateString() : "");

function sortOrders(list: WorkOrder[]): WorkOrder[] {
  const key = (o: WorkOrder) => `${o.scheduled_date ?? "9999"}T${o.scheduled_time ?? "99"}`;
  return [...list].sort((a, b) => key(a).localeCompare(key(b)));
}

/** Разбивка выездов на секции, как в списке «Почты»: сегодня, завтра, скоро. */
function groupOrders(list: WorkOrder[], period: Period): { title: string; orders: WorkOrder[]; withDate: boolean }[] {
  if (period === "closed") {
    const sorted = sortOrders(list).reverse();
    return [
      { title: "Закрыты", orders: sorted.filter((o) => o.status === "done"), withDate: true },
      { title: "Отменены", orders: sorted.filter((o) => o.status === "cancelled"), withDate: true },
    ].filter((g) => g.orders.length > 0);
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const sorted = sortOrders(list);
  const at = (o: WorkOrder) => (o.scheduled_date ? new Date(o.scheduled_date) : null);
  return [
    { title: "Просрочены", orders: sorted.filter((o) => { const d = at(o); return d != null && d < today && dayKey(o.scheduled_date) !== today.toDateString(); }), withDate: true },
    { title: "Сегодня", orders: sorted.filter((o) => dayKey(o.scheduled_date) === today.toDateString()), withDate: false },
    { title: "Завтра", orders: sorted.filter((o) => dayKey(o.scheduled_date) === tomorrow.toDateString()), withDate: false },
    { title: "Скоро", orders: sorted.filter((o) => { const d = at(o); return d != null && d > tomorrow && dayKey(o.scheduled_date) !== tomorrow.toDateString(); }), withDate: true },
    { title: "Без даты", orders: sorted.filter((o) => !o.scheduled_date), withDate: false },
  ].filter((g) => g.orders.length > 0);
}

/** Строка под адресом выезда. Время показано отдельной колонкой слева — здесь только кто и что. */
function orderLine(o: WorkOrder, isManager: boolean): string {
  return [o.client, isManager ? o.assigned_employee : "", o.note].filter(Boolean).join(" · ");
}

export default function FieldWork({ me, isManager = false }: { me: Me | null; isManager?: boolean }) {
  const [period, setPeriod] = useState<Period>("active");
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<number | null>(null);
  const [verifications, setVerifications] = useState<CachedVerification[]>([]);
  const [selectedVerification, setSelectedVerification] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [layoutsInfo, setLayoutsInfo] = useState<LayoutsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [online, setOnline] = useState(isOnline());
  const [queued, setQueued] = useState(0);
  const [outboxProblems, setOutboxProblems] = useState<OutboxItem[]>([]);

  // Бланк с QR и распознавание — второй срез офлайна (claude/scans.md).
  // В отличие от остального экрана это НЕ офлайн-функция: печать и
  // распознавание требуют связи, ими пользуются уже вернувшись с
  // режимного объекта, где телефон вообще нельзя было доставать.
  const [scans, setScans] = useState<ScanUploadResult[]>([]);
  const [scanBusy, setScanBusy] = useState(false);
  const [scanError, setScanError] = useState("");
  const [reviewScanId, setReviewScanId] = useState<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const update = () => setOnline(isOnline());
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  // Справочник типов — в телефон заранее, чтобы искать счётчик и без связи.
  useEffect(() => {
    void refreshSiTypeCache();
    void refreshTemplateCache();
  }, []);

  const refreshQueueCount = useCallback(() => {
    void pendingCount().then(setQueued);
    void outboxErrors().then(setOutboxProblems);
  }, []);

  const reloadVerifications = useCallback(async () => {
    if (selectedOrder == null) return;
    setVerifications(await listVerifications(selectedOrder));
  }, [selectedOrder]);

  const reloadScans = useCallback(async () => {
    if (selectedOrder == null) {
      setScans([]);
      return;
    }
    try {
      setScans(await fetchWorkOrderScans(selectedOrder));
    } catch (exc) {
      setScanError(exc instanceof Error ? exc.message : "Не удалось загрузить сканы");
    }
  }, [selectedOrder]);

  useEffect(() => {
    void reloadScans();
    setReviewScanId(null);
  }, [reloadScans]);

  const handlePrintBlank = () => {
    if (selectedOrder == null) return;
    setScanError("");
    downloadBlank(selectedOrder).catch((exc) => setScanError(exc instanceof Error ? exc.message : "Не удалось скачать бланк"));
  };

  const handleUploadScan = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || selectedOrder == null) return;
    setScanBusy(true);
    setScanError("");
    try {
      const uploaded = await uploadScan(selectedOrder, file);
      setScans((current) => [uploaded, ...current]);
      setReviewScanId(uploaded.id);
    } catch (exc) {
      setScanError(exc instanceof Error ? exc.message : "Не удалось загрузить фото");
    } finally {
      setScanBusy(false);
    }
  };

  const handleScanApplied = (verification: FieldVerification) => {
    setReviewScanId(null);
    void reloadScans();
    void reloadVerifications();
    setSelectedVerification(verification.client_id);
  };

  useEffect(() => {
    refreshQueueCount();
    return subscribe(() => {
      refreshQueueCount();
      void reloadVerifications();
    });
  }, [refreshQueueCount, reloadVerifications]);

  useEffect(() => {
    // Менеджер видит выезды всей фирмы, поверитель — только свои.
    const employeeId = me?.employee?.id;
    if (!isManager && !employeeId) return;
    setLoading(true);
    fetchWorkOrders(isManager ? {} : { assigned_employee: String(employeeId) })
      .then(setOrders)
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить выезды"))
      .finally(() => setLoading(false));
  }, [me, isManager]);

  const cancelOrder = async (target: WorkOrder) => {
    const ok = await confirmDialog({
      title: "Отменить выезд?",
      text: `${target.site || `Выезд №${target.id}`} — выезд пропадёт у поверителя, заявка станет отменённой.`,
      ok: "Отменить выезд",
      danger: true,
    });
    if (!ok) return;
    setError("");
    try {
      const updated = await setWorkOrderStatus(target.id, "cancelled");
      setOrders((current) => current.map((o) => (o.id === target.id ? { ...o, ...updated } : o)));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось отменить выезд");
    }
  };

  useEffect(() => {
    loadLayouts()
      .then(setLayoutsInfo)
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить раскладки измерений"));
  }, []);

  useEffect(() => {
    void reloadVerifications();
  }, [reloadVerifications]);

  // Прокрутка наверх при смене шага — на телефоне иначе остаёшься внизу.
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [selectedOrder, selectedVerification]);

  if (!me) return <Spinner />;
  if (!isManager && !me.employee) {
    return (
      <EmptyState icon="lock" title="Нет привязки к сотруднику">
        Экран доступен сотрудникам с привязанной учётной записью — попросите руководителя связать её с вашей карточкой.
      </EmptyState>
    );
  }

  const order = orders.find((o) => o.id === selectedOrder) ?? null;
  const activeVerification = verifications.find((v) => v.client_id === selectedVerification) ?? null;

  const syncBar = (
    <>
    <PageActions>
      <span className={`status-dot ${online ? "" : "off"}`}>{online ? "На связи" : "Нет связи"}</span>
    </PageActions>
    <SyncBar
      online={online}
      queued={queued}
      problems={outboxProblems}
      onRemove={(id) => void removeOutboxItem(id).then(refreshQueueCount)}
    />
    </>
  );

  // --- Шаг 1: выезды ---------------------------------------------------
  if (selectedOrder == null) {
    const shown = orders.filter((o) => OPEN_STATUSES.includes(o.status) === (period === "active"));
    const sections = groupOrders(shown, period);
    return (
      <div className="narrow">
        {syncBar}
        <div className="toolbar">
          <Segmented
            value={period}
            options={[
              { value: "active", label: "Активные" },
              { value: "closed", label: "Завершённые" },
            ]}
            onChange={setPeriod}
          />
        </div>
        {error && <div className="error">{error}</div>}
        {loading && <Spinner />}
        {!loading && shown.length === 0 && (
          <EmptyState icon={period === "closed" ? "checkCircle" : "car"} title={
            period === "closed"
              ? "Завершённых выездов пока нет"
              : isManager
                ? "Открытых выездов нет"
                : "Открытых выездов на вас нет"
          }>
            {period === "active" && (isManager ? "Выезды появляются, когда заявку подтверждают в «Заявках»." : "Когда диспетчер назначит выезд, он появится здесь и в «Графике».")}
          </EmptyState>
        )}
        {sections.map((section) => (
          <div key={section.title}>
            <div className={`list-section ${section.title === "Просрочены" ? "crit" : ""}`}>
              {section.title} <span className="count">{section.orders.length}</span>
            </div>
            <div className="card-list">
              {section.orders.map((o) => (
                <button type="button" className="card tap" key={o.id} onClick={() => setSelectedOrder(o.id)}>
                  <div className="order-row">
                    <div className="order-time">
                      {o.scheduled_time ? o.scheduled_time.slice(0, 5) : section.withDate && o.scheduled_date ? "" : "—"}
                      {section.withDate && o.scheduled_date && (
                        <small>{new Date(o.scheduled_date).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "")}</small>
                      )}
                    </div>
                    <div className="grow">
                      <b>{o.site || "Адрес не указан"}</b>
                      <div className="sub">{orderLine(o, isManager)}</div>
                    </div>
                    {o.verifications_total > 0 && (
                      <div className="order-progress">
                        {o.verifications_accepted} из {o.verifications_total}
                        <div className="mini-bar"><i style={{ width: `${(o.verifications_accepted / o.verifications_total) * 100}%` }} /></div>
                      </div>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  // --- Шаг 3: измерения одного счётчика --------------------------------
  if (activeVerification) {
    return (
      <div className="narrow">
        {syncBar}
        <button type="button" className="back" onClick={() => setSelectedVerification(null)}>
          <Icon name="chevronLeft" size={20} /> {order?.site || "Все счётчики"}
        </button>
        <div className="card">
          <div className="card-head">
            <div className="grow">
              <b>{activeVerification.si_type_name || "Счётчик"}</b>
              <div className="sub">Заводской № {activeVerification.serial_number}</div>
            </div>
            <StatusBadge v={activeVerification} />
          </div>
          {online && activeVerification.si_type_id ? <SiTypeDocuments siTypeId={activeVerification.si_type_id} /> : null}
        </div>
        <VerificationEntry
          key={activeVerification.client_id}
          verification={activeVerification}
          layoutsInfo={layoutsInfo}
          onSaved={() => void reloadVerifications()}
        />
        <PhotosPanel verification={activeVerification} />
        <button type="button" className="big wide" onClick={() => { setSelectedVerification(null); setAdding(true); }}>
          <Icon name="plus" /> Следующий счётчик
        </button>
      </div>
    );
  }

  // --- Шаг 2: счётчики наряда -------------------------------------------
  return (
    <div className="narrow">
      {syncBar}
      <button type="button" className="back" onClick={() => { setSelectedOrder(null); setAdding(false); }}>
        <Icon name="chevronLeft" size={20} /> Поверки
      </button>
      <div className="detail-title">
        {order && (
          <div className="sub">
            {[formatDate(order.scheduled_date), order.scheduled_time?.slice(0, 5)].filter(Boolean).join(", ")}
          </div>
        )}
        <h2>{order?.site || "Адрес не указан"}</h2>
      </div>
      {order && (
        <div className="kv">
          <div><span className="k">Клиент</span><span className="v">{order.client || "—"}</span></div>
          <div><span className="k">Поверитель</span><span className="v">{order.assigned_employee || "—"}</span></div>
          {order.request_id != null && (
            <div><span className="k">Основание</span><span className="v">Заявка №{order.request_id}</span></div>
          )}
          {order.note && <div><span className="k">Примечание</span><span className="v">{order.note}</span></div>}
          <div>
            <span className="k">Состояние</span>
            <span className="v">{order.status_display}</span>
          </div>
        </div>
      )}
      {order && isManager && OPEN_STATUSES.includes(order.status) && (
        <div className="row" style={{ justifyContent: "flex-end", margin: "-8px 0 8px" }}>
          <button type="button" className="sm ghost danger" onClick={() => void cancelOrder(order)}>
            <Icon name="ban" size={14} /> Отменить выезд
          </button>
        </div>
      )}
      {error && <div className="error">{error}</div>}

      <h2>Приборы <span className="count">{verifications.length}</span></h2>
      {verifications.length === 0 && !adding && <div className="empty small" style={{ textAlign: "left", padding: "0 4px 12px" }}>Пока ни одного — добавьте первый</div>}
      {verifications.length > 0 && (
        <p className="hint" style={{ margin: "-8px 4px 14px" }}>Выезд закроется сам, когда все протоколы примут в журнале.</p>
      )}
      <div className="card-list">
        {verifications.map((v) => (
          <button type="button" className="card tap" key={v.client_id} onClick={() => setSelectedVerification(v.client_id)}>
            <div className="card-head">
              <div className="grow">
                <b>{v.si_type_name || "Счётчик"}</b>
                <div className="sub">№ {v.serial_number}</div>
              </div>
              <StatusBadge v={v} />
            </div>
          </button>
        ))}
      </div>

      {adding ? (
        <AddInstrumentForm
          workOrderId={selectedOrder}
          onAdded={(v) => {
            setVerifications((current) => [v, ...current]);
            setAdding(false);
            setSelectedVerification(v.client_id);
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <button type="button" className="primary big wide" onClick={() => setAdding(true)}>
          <Icon name="plus" /> Добавить прибор
        </button>
      )}

      <details className="more card" open={scans.some((s) => s.verification == null) || reviewScanId != null}>
        <summary>Режимный объект, телефон нельзя — бумажный бланк</summary>
        <p className="hint">
          Распечатайте бланк, заполните от руки на месте, а вернувшись на связь, сфотографируйте его —
          система распознает цифры, вы только сверите.
        </p>
        <div className="actions">
          <button type="button" onClick={handlePrintBlank}><Icon name="printer" size={16} /> Распечатать бланк</button>
          <button type="button" onClick={() => fileInput.current?.click()} disabled={scanBusy}>
            <Icon name="camera" size={16} />
            {scanBusy ? "Загружаю…" : "Сфотографировать заполненный бланк"}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            capture="environment"
            style={{ display: "none" }}
            onChange={(event) => void handleUploadScan(event)}
          />
        </div>
        {scanError && <div className="error">{scanError}</div>}

        {scans.length > 0 && (
          <ul className="item-list">
            {scans.map((s) => (
              <li key={s.id}>
                <span className="name">
                  Бланк от {new Date(s.created_at).toLocaleString("ru-RU")}
                  {s.verification != null && <span className="pill ok"> внесён</span>}
                  {s.verification == null && s.error && <span className="pill warn"> {s.error}</span>}
                </span>
                {s.verification == null && (
                  <button type="button" className="sm" onClick={() => setReviewScanId(s.id === reviewScanId ? null : s.id)}>
                    {s.id === reviewScanId ? "Свернуть" : "Сверить"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        {reviewScanId != null && layoutsInfo && (
          <ScanReview
            scan={scans.find((s) => s.id === reviewScanId)!}
            layoutsInfo={layoutsInfo}
            onApplied={handleScanApplied}
            onDismiss={() => setReviewScanId(null)}
          />
        )}
      </details>
    </div>
  );
}
