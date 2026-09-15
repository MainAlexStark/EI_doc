import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchWorkOrders,
  suggestSiTypes,
  type LayoutsResponse,
  type Me,
  type MeasurementRowPayload,
  type MeasurementsPayload,
  type MeasurementsResult,
  type SiTypeSuggestion,
  type WorkOrder,
} from "../api";
import {
  addVerification,
  flush,
  isOnline,
  listVerifications,
  loadLayouts,
  pendingCount,
  saveMeasurements,
  subscribe,
} from "../offline/sync";
import type { CachedVerification } from "../offline/db";

/** Экран поверителя: наряд → приборы по нему → ввод измерений — рассчитан на
 * работу без связи (см. src/offline/sync.ts). Раскладка измерений сейчас
 * только для счётчиков воды (apps.verification.calculators.water_meter —
 * единственный подключённый калькулятор на бэкенде). */

function StatusBadge({ v }: { v: CachedVerification }) {
  if (v.sync_state === "pending") return <span className="pill warn">не отправлено</span>;
  if (v.needs_review) return <span className="pill warn">на сверку</span>;
  if (v.status === "ready") return <span className="pill ok">готова</span>;
  if (v.status === "accepted") return <span className="pill ok">принята</span>;
  return <span className="pill muted">черновик</span>;
}

function AddInstrumentForm({
  workOrderId,
  onAdded,
}: {
  workOrderId: number;
  onAdded: (v: CachedVerification) => void;
}) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<SiTypeSuggestion[]>([]);
  const [showList, setShowList] = useState(false);
  const [siTypeId, setSiTypeId] = useState<number | null>(null);
  const [serial, setSerial] = useState("");
  const [year, setYear] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const debounce = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    clearTimeout(debounce.current);
    if (siTypeId || query.trim().length < 2) {
      setSuggestions([]);
      return;
    }
    debounce.current = setTimeout(async () => {
      const response = await suggestSiTypes(query);
      // Полевой экран работает только с уже заведёнными типами СИ — карточка
      // из ФИФ (si_type_id === null) требует отдельного шага заведения типа,
      // это вне этого экрана и вне офлайн-режима (нужна связь с ФИФ).
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

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!siTypeId) {
      setError("Выберите тип СИ из списка подсказок");
      return;
    }
    if (!serial.trim()) {
      setError("Укажите заводской номер");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await addVerification(workOrderId, {
        si_type_id: siTypeId,
        serial_number: serial.trim(),
        manufacture_year: year ? Number(year) : null,
      });
      onAdded(created);
      setSiTypeId(null);
      setQuery("");
      setSerial("");
      setYear("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось добавить СИ");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="new-slot-form" onSubmit={submit}>
      <div className="field-row">
        <div className="field suggest-field">
          <label htmlFor="fw-si-type">Тип СИ</label>
          <input
            id="fw-si-type"
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
          <label htmlFor="fw-serial">Зав. номер</label>
          <input id="fw-serial" value={serial} onChange={(event) => setSerial(event.target.value)} placeholder="№" />
        </div>
        <div className="field">
          <label htmlFor="fw-year">Год выпуска</label>
          <input
            id="fw-year"
            value={year}
            onChange={(event) => setYear(event.target.value.replace(/\D/g, ""))}
            placeholder="необязательно"
            style={{ width: 100 }}
          />
        </div>
      </div>
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Добавляю…" : "Добавить СИ"}
      </button>
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

const emptyRow = (): RowState => ({
  flow_rate: "", volume_standard: "", reading_start: "", reading_end: "", pulses: "", volume_meter: "",
});

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
    setRows((current) => current.map((row, idx) => (idx === index ? { ...row, ...patch } : row)));

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<MeasurementsResult | null>(null);
  const [queuedNotice, setQueuedNotice] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setResult(null);
    setQueuedNotice(false);
    try {
      const payload: MeasurementsPayload = {
        layout,
        meter_class: meterClass,
        checks,
        pulse_weight: pulseWeight || undefined,
        unit_type: unitType || undefined,
        water_temperature: waterTemp || undefined,
        rows: rows.map((row): MeasurementRowPayload => {
          const built: MeasurementRowPayload = { flow_rate: row.flow_rate, volume_standard: row.volume_standard };
          if (row.reading_start) built.reading_start = row.reading_start;
          if (row.reading_end) built.reading_end = row.reading_end;
          if (row.pulses) built.pulses = Number(row.pulses);
          if (row.volume_meter) built.volume_meter = row.volume_meter;
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
    <form className="new-slot-form" onSubmit={submit}>
      <h4>
        {verification.si_type_name || "СИ"} № {verification.serial_number}
      </h4>
      <div className="field-row">
        <div className="field">
          <label htmlFor="fw-layout">Раскладка</label>
          <select id="fw-layout" value={layout} onChange={(event) => setLayout(event.target.value)}>
            {layoutKeys.map((key) => (
              <option key={key} value={key}>
                {key === "compact3" ? "короткая (3 пролива)" : key === "extended9" ? "полная (9 проливов)" : key}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="fw-class">Класс</label>
          <select id="fw-class" value={meterClass} onChange={(event) => setMeterClass(event.target.value)}>
            {layoutsInfo.classes.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="fw-unit">Тип счётчика</label>
          <select id="fw-unit" value={unitType} onChange={(event) => setUnitType(event.target.value)}>
            <option value="">—</option>
            <option value="г/в">горячая вода</option>
            <option value="х/в">холодная вода</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="fw-pulse">Коэфф. импульсов K</label>
          <input
            id="fw-pulse"
            value={pulseWeight}
            onChange={(event) => setPulseWeight(event.target.value)}
            placeholder="необязательно"
            style={{ width: 110 }}
          />
        </div>
        <div className="field">
          <label htmlFor="fw-water-temp">Темп. воды, °C</label>
          <input
            id="fw-water-temp"
            value={waterTemp}
            onChange={(event) => setWaterTemp(event.target.value)}
            placeholder="необязательно"
            style={{ width: 110 }}
          />
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

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Режим</th>
              <th>Q, м³/ч</th>
              <th>Vэтал, м³</th>
              <th>Показания начало</th>
              <th>Показания конец</th>
              <th>Импульсы</th>
              <th>V счётчика</th>
            </tr>
          </thead>
          <tbody>
            {modes.map((mode, index) => (
              <tr key={index}>
                <td>
                  {mode.label} <span className="sub">{mode.seconds} с</span>
                </td>
                <td>
                  <input value={rows[index]?.flow_rate ?? ""} onChange={(e) => updateRow(index, { flow_rate: e.target.value })} style={{ width: 70 }} />
                </td>
                <td>
                  <input value={rows[index]?.volume_standard ?? ""} onChange={(e) => updateRow(index, { volume_standard: e.target.value })} style={{ width: 80 }} />
                </td>
                <td>
                  <input value={rows[index]?.reading_start ?? ""} onChange={(e) => updateRow(index, { reading_start: e.target.value })} style={{ width: 90 }} />
                </td>
                <td>
                  <input value={rows[index]?.reading_end ?? ""} onChange={(e) => updateRow(index, { reading_end: e.target.value })} style={{ width: 90 }} />
                </td>
                <td>
                  <input value={rows[index]?.pulses ?? ""} onChange={(e) => updateRow(index, { pulses: e.target.value })} style={{ width: 70 }} />
                </td>
                <td>
                  <input value={rows[index]?.volume_meter ?? ""} onChange={(e) => updateRow(index, { volume_meter: e.target.value })} style={{ width: 80 }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Сохраняю…" : "Сохранить измерения"}
      </button>
      {error && <div className="error">{error}</div>}
      {queuedNotice && <div className="hint">Связи нет — сохранено на устройстве, отправится само при её появлении.</div>}
      {result && (
        <div className={result.suitable ? "hint ok" : "error"}>
          {result.suitable ? "Годен" : `Не годен: ${result.reasons.join("; ")}`}
          {result.needs_review && " · есть строки на сверку"}
        </div>
      )}
    </form>
  );
}

export default function FieldWork({ me }: { me: Me | null }) {
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<number | null>(null);
  const [verifications, setVerifications] = useState<CachedVerification[]>([]);
  const [selectedVerification, setSelectedVerification] = useState<string | null>(null);
  const [layoutsInfo, setLayoutsInfo] = useState<LayoutsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [online, setOnline] = useState(isOnline());
  const [queued, setQueued] = useState(0);

  useEffect(() => {
    const update = () => setOnline(isOnline());
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  const refreshQueueCount = useCallback(() => {
    void pendingCount().then(setQueued);
  }, []);

  const reloadVerifications = useCallback(async () => {
    if (selectedOrder == null) return;
    setVerifications(await listVerifications(selectedOrder));
  }, [selectedOrder]);

  useEffect(() => {
    refreshQueueCount();
    return subscribe(() => {
      refreshQueueCount();
      void reloadVerifications();
    });
  }, [refreshQueueCount, reloadVerifications]);

  useEffect(() => {
    const employeeId = me?.employee?.id;
    if (!employeeId) return;
    setLoading(true);
    fetchWorkOrders({ assigned_employee: String(employeeId) })
      .then((list) => setOrders(list.filter((o) => o.status === "planned" || o.status === "in_progress")))
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить наряды"))
      .finally(() => setLoading(false));
  }, [me]);

  useEffect(() => {
    loadLayouts()
      .then(setLayoutsInfo)
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить раскладки измерений"));
  }, []);

  useEffect(() => {
    void reloadVerifications();
  }, [reloadVerifications]);

  if (!me?.employee) {
    return <div className="empty">Экран доступен только сотрудникам с привязанной учётной записью</div>;
  }

  const activeVerification = verifications.find((v) => v.client_id === selectedVerification) ?? null;

  return (
    <>
      <div className="filters">
        <span className={online ? "pill ok" : "pill warn"}>{online ? "связь есть" : "офлайн"}</span>
        {queued > 0 && <span className="pill warn">в очереди на отправку: {queued}</span>}
        {online && queued > 0 && (
          <button type="button" onClick={() => void flush()}>
            Синхронизировать сейчас
          </button>
        )}
      </div>

      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">Загружаю…</div>}

      <div className="field">
        <label htmlFor="fw-order">Наряд</label>
        <select
          id="fw-order"
          value={selectedOrder ?? ""}
          onChange={(event) => {
            setSelectedOrder(event.target.value ? Number(event.target.value) : null);
            setSelectedVerification(null);
          }}
        >
          <option value="">выберите наряд…</option>
          {orders.map((order) => (
            <option key={order.id} value={order.id}>
              №{order.id} · {order.site} · {order.scheduled_date ? new Date(order.scheduled_date).toLocaleDateString("ru-RU") : "без даты"}
            </option>
          ))}
        </select>
      </div>

      {selectedOrder != null && (
        <>
          <h3>Приборы по наряду</h3>
          {verifications.length === 0 && <div className="empty">Пока ничего не заведено</div>}
          <ul className="item-list">
            {verifications.map((v) => (
              <li key={v.client_id}>
                <span className="name">
                  {v.si_type_name || "СИ"} № {v.serial_number}
                </span>
                <StatusBadge v={v} />
                <button
                  type="button"
                  onClick={() => setSelectedVerification(v.client_id === selectedVerification ? null : v.client_id)}
                >
                  {v.client_id === selectedVerification ? "Свернуть" : "Измерения"}
                </button>
              </li>
            ))}
          </ul>

          <h4>Добавить СИ</h4>
          <AddInstrumentForm
            workOrderId={selectedOrder}
            onAdded={(v) => {
              setVerifications((current) => [v, ...current]);
              setSelectedVerification(v.client_id);
            }}
          />

          {activeVerification && layoutsInfo && (
            <MeasurementForm verification={activeVerification} layoutsInfo={layoutsInfo} onSaved={() => void reloadVerifications()} />
          )}
        </>
      )}
    </>
  );
}
