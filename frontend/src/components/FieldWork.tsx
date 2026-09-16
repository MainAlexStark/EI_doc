import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteVerificationPhoto,
  downloadBlank,
  fetchVerificationPhotoUrl,
  fetchVerificationPhotos,
  fetchWorkOrderScans,
  fetchWorkOrders,
  suggestSiTypes,
  uploadScan,
  uploadVerificationPhoto,
  type FieldVerification,
  type LayoutsResponse,
  type Me,
  type MeasurementRowPayload,
  type MeasurementsPayload,
  type MeasurementsResult,
  type ScanUploadResult,
  type SiTypeSuggestion,
  type VerificationPhoto,
  type WorkOrder,
} from "../api";
import ScanReview from "./ScanReview";
import {
  addVerification,
  flush,
  isOnline,
  listVerifications,
  loadLayouts,
  outboxErrors,
  pendingCount,
  removeOutboxItem,
  saveMeasurements,
  subscribe,
} from "../offline/sync";
import type { CachedVerification, OutboxItem } from "../offline/db";

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
      <div className="field">
        <h4>Фото прибора</h4>
        <p className="hint">Появится после отправки СИ на сервер — сейчас оно ещё в очереди.</p>
      </div>
    );
  }

  return (
    <div className="field">
      <h4>Фото прибора</h4>
      {photos.length === 0 && <p className="sub">Пока фото нет</p>}
      {photos.length > 0 && (
        <div className="photo-grid">
          {photos.map((photo) => (
            <div className="photo-thumb" key={photo.id}>
              {urls[photo.id] ? (
                <img src={urls[photo.id]} alt={photo.caption || "фото СИ"} />
              ) : (
                <span className="sub">загрузка…</span>
              )}
              <button type="button" onClick={() => void handleDelete(photo.id)} disabled={busy}>
                Удалить
              </button>
            </div>
          ))}
        </div>
      )}
      <button type="button" onClick={() => fileInput.current?.click()} disabled={busy}>
        {busy ? "Загружаю…" : "Добавить фото"}
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

  // Непригоден не по расчётной погрешности — осмотр, повреждение и т. п.
  // (тж. непроходимая механическая поломка, когда измерения вообще снять
  // нельзя). См. apps.verification.measurements.apply().
  const [manualUnsuitable, setManualUnsuitable] = useState(false);
  const [manualReason, setManualReason] = useState("");
  const [reasonPreset, setReasonPreset] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setResult(null);
    setQueuedNotice(false);

    const allRowsEmpty = rows.every((row) => !row.flow_rate.trim() && !row.volume_standard.trim());
    const allRowsFilled = rows.every((row) => row.flow_rate.trim() && row.volume_standard.trim());
    if (!allRowsFilled && !(manualUnsuitable && allRowsEmpty)) {
      setError(
        "Заполните расход и объём по эталону во всех строках — либо, если прибор физически " +
          "не проверить (разбит, заклинило), отметьте «Непригоден» и оставьте таблицу пустой",
      );
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

      <div className="field" style={{ border: "1px solid var(--line)", borderRadius: "var(--radius)", padding: 10 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={manualUnsuitable} onChange={(event) => setManualUnsuitable(event.target.checked)} />
          НЕПРИГОДЕН (по другой причине, не по погрешности)
        </label>
        {manualUnsuitable && (
          <div className="field-row" style={{ marginTop: 8 }}>
            <div className="field">
              <label htmlFor="fw-reason-preset">Частая причина</label>
              <select
                id="fw-reason-preset"
                value={reasonPreset}
                onChange={(event) => {
                  setReasonPreset(event.target.value);
                  if (event.target.value) setManualReason(event.target.value);
                }}
              >
                <option value="">выберите или впишите свою ниже…</option>
                {layoutsInfo.common_unsuitability_reasons.map((reason) => (
                  <option key={reason} value={reason}>{reason}</option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: 2 }}>
              <label htmlFor="fw-reason-text">Причина<span className="req">*</span></label>
              <input
                id="fw-reason-text"
                value={manualReason}
                onChange={(event) => setManualReason(event.target.value)}
                placeholder="например: разбито стекло корпуса"
              />
            </div>
          </div>
        )}
        <p className="hint">
          Если прибор физически не проверить (заклинило, разбит) — оставьте таблицу измерений пустой,
          отправлять будет нечего.
        </p>
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
        {queued > 0 && (
          // Не завязано на online — статус связи браузера ненадёжен, а сама
          // попытка отправки сама разберётся, есть сеть или нет (см. flush()).
          <button type="button" onClick={() => void flush()}>
            Синхронизировать сейчас
          </button>
        )}
      </div>

      {outboxProblems.length > 0 && (
        <ul className="item-list">
          {outboxProblems.map((item) => (
            <li key={item.id}>
              <span className="name">
                {item.kind === "create_verification" ? "Заведение СИ" : "Измерения"}
                {" — "}
                <span className="sub">{item.last_error || "не удалось отправить"}</span>
              </span>
              <button type="button" onClick={() => void removeOutboxItem(item.id).then(refreshQueueCount)}>
                Убрать из очереди
              </button>
            </li>
          ))}
        </ul>
      )}

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
          <h3>Бланк и сканы</h3>
          <p className="hint">
            Для режимных объектов (телефон нельзя пронести) — распечатайте бланк, заполните от руки
            на месте, а вернувшись со связью, сфотографируйте и сверьте распознанное.
          </p>
          <div className="field-row">
            <button type="button" onClick={handlePrintBlank}>Распечатать бланк</button>
            <button type="button" onClick={() => fileInput.current?.click()} disabled={scanBusy}>
              {scanBusy ? "Загружаю…" : "Загрузить фото бланка"}
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
                    Скан №{s.id} от {new Date(s.created_at).toLocaleString("ru-RU")}
                    {s.verification != null && <span className="pill ok"> поверка заведена</span>}
                    {s.verification == null && s.error && <span className="pill warn"> {s.error}</span>}
                  </span>
                  {s.verification == null && (
                    <button type="button" onClick={() => setReviewScanId(s.id === reviewScanId ? null : s.id)}>
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
          {activeVerification && <PhotosPanel verification={activeVerification} />}
        </>
      )}
    </>
  );
}
