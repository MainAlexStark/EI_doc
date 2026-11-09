import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchStandardKits,
  importStandardKits,
  saveAuxInstrument,
  saveStandardKit,
  type AuxInstrument,
  type StandardKit,
  type StandardKitsResponse,
} from "../api";
import Icon from "../ui/Icon";
import { Modal, confirmDialog } from "../ui/Modal";
import { Alert, EmptyState, SearchField, Spinner, Switch } from "../ui/controls";

/**
 * «Перечень применяемых эталонов» — что берут на поверку каждого вида СИ.
 *
 * Метролог правит строки руками, загружает новую версию Word-файла (строки обновятся на месте,
 * ссылки шаблонов не порвутся) или пишет ассистенту: «добавилось оборудование, вот обозначения —
 * обнови перечень». Шаблоны поверки ссылаются на эти строки, поверитель выбирает свою на поверке,
 * а в журнал (столбец U и номера эталонов) она попадает сама.
 */
export default function StandardKits() {
  const [data, setData] = useState<StandardKitsResponse | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editKit, setEditKit] = useState<Partial<StandardKit> | null>(null);
  const [editAux, setEditAux] = useState<Partial<AuxInstrument> | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      setData(await fetchStandardKits(showAll));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить перечень");
    }
  }, [showAll]);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = new Map<string, StandardKit[]>();
    for (const kit of data?.kits ?? []) {
      const hay = [kit.section, kit.scope, kit.standards_text, kit.aux_text, kit.fif_numbers.join(" "), kit.employee_name]
        .join(" ")
        .toLowerCase();
      if (q && !q.split(/\s+/).every((word) => hay.includes(word))) continue;
      out.set(kit.section, [...(out.get(kit.section) ?? []), kit]);
    }
    return [...out.entries()];
  }, [data, query]);

  const canEdit = Boolean(data?.can_edit);

  const upload = async (file: File) => {
    const ok = await confirmDialog({
      title: "Обновить перечень по файлу?",
      text: "Строки узнаются по разделу и диапазону и обновятся на месте; строки, которых нет в файле, выключатся.",
      ok: "Обновить",
    });
    if (!ok) return;
    setImporting(true);
    setError("");
    try {
      const r = await importStandardKits(file);
      setNotice(
        `Перечень обновлён: новых строк ${r.kits_created}, обновлено ${r.kits_updated}, выключено ${r.kits_disabled}; ` +
          `вспомогательных СИ — ${r.aux}.`,
      );
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить файл");
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  if (!data && !error) return <Spinner />;

  return (
    <div>
      <div className="toolbar">
        <SearchField className="grow" value={query} onChange={setQuery} placeholder="Вид поверки, эталон, номер в ФИФ, поверитель" />
        <Switch checked={showAll} onChange={setShowAll} label="С выключенными" />
        {canEdit && (
          <>
            <button type="button" className="sm" onClick={() => setEditKit({ section: "", is_active: true })}>
              <Icon name="plus" size={14} /> Строка
            </button>
            <button type="button" className="sm" disabled={importing} onClick={() => fileRef.current?.click()}>
              <Icon name="upload" size={14} /> {importing ? "Загружаю…" : "Загрузить Word"}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".docx"
              hidden
              onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])}
            />
          </>
        )}
      </div>

      {canEdit && (
        <p className="hint">
          Можно и через ассистента: «у нас добавился термометр ТЛ-4 №7, 299-11^7^ — добавь его в поверку манометров».
          Он покажет правки карточкой, применятся после «Подтвердить».
        </p>
      )}
      {error && <Alert>{error}</Alert>}
      {notice && <Alert tone="ok" onClose={() => setNotice("")}>{notice}</Alert>}

      {data && data.kits.length === 0 && (
        <EmptyState icon="layers" title="Перечень пуст">
          {canEdit ? "Загрузите Word-файл «Перечень применяемых эталонов» — строки разберутся сами." : "Метролог ещё не загрузил перечень."}
        </EmptyState>
      )}

      {groups.map(([section, kits]) => (
        <div key={section} style={{ marginBottom: 18 }}>
          <div className="pick-head">{section}</div>
          <div className="card-list">
            {kits.map((kit) => (
              <div className="card type-row" key={kit.id} style={kit.is_active ? undefined : { opacity: 0.55 }}>
                <div className="grow">
                  <b style={{ whiteSpace: "pre-wrap" }}>{firstLines(kit.scope, 3) || "—"}</b>
                  {!kit.is_active && <span className="pill muted" style={{ marginLeft: 8 }}>выключена</span>}
                  {kit.employee_name && <span className="pill" style={{ marginLeft: 8 }}>{kit.employee_name}</span>}
                  <div className="meta" style={{ whiteSpace: "pre-wrap" }}>
                    {kit.standards_text && <span>Эталоны: {firstLines(kit.standards_text, 3)}</span>}
                    {kit.aux_text && <span>СИ: {kit.aux_text.split("\n").filter(Boolean).join("; ")}</span>}
                  </div>
                  <div className="meta">
                    {kit.fif_numbers.length > 0 && (
                      <span className="cell-mono">ФИФ: {kit.fif_numbers.slice(0, 4).join(" | ")}{kit.fif_numbers.length > 4 ? ` +${kit.fif_numbers.length - 4}` : ""}</span>
                    )}
                    {kit.journal_aux && <span className="cell-mono">U: {kit.journal_aux.split("\n")[0]}</span>}
                    {kit.family_names.length > 0 && <span>вид СИ: {kit.family_names.join(", ")}</span>}
                    {kit.note && <span>{kit.note}</span>}
                  </div>
                </div>
                {canEdit && (
                  <div className="row">
                    <button type="button" className="sm" onClick={() => setEditKit(kit)}>
                      <Icon name="pen" size={14} /> Править
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      {data && data.aux.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div className="pick-head">Вспомогательные СИ — обозначение для журнала «номер в реестре^заводской^инвентарный»</div>
          <div className="card-list">
            {data.aux
              .filter((a) => !query.trim() || `${a.name} ${a.designation}`.toLowerCase().includes(query.trim().toLowerCase()))
              .map((aux) => (
                <div className="card type-row" key={aux.id} style={aux.is_active ? undefined : { opacity: 0.55 }}>
                  <div className="grow">
                    <b style={{ whiteSpace: "pre-wrap" }}>{firstLines(aux.name, 2)}</b>
                    <div className="meta">
                      <span className="cell-mono">{aux.designation || "обозначения нет"}</span>
                      {aux.note && <span>{aux.note}</span>}
                    </div>
                  </div>
                  {canEdit && (
                    <div className="row">
                      <button type="button" className="sm" onClick={() => setEditAux(aux)}>
                        <Icon name="pen" size={14} /> Править
                      </button>
                    </div>
                  )}
                </div>
              ))}
          </div>
          {canEdit && (
            <button type="button" className="link" onClick={() => setEditAux({ name: "", designation: "", is_active: true })}>
              + Вспомогательное СИ
            </button>
          )}
        </div>
      )}

      <Modal open={editKit != null} onClose={() => setEditKit(null)} size="lg" title={editKit?.id ? "Строка перечня" : "Новая строка перечня"}>
        {editKit && <KitForm kit={editKit} onDone={() => { setEditKit(null); void load(); }} />}
      </Modal>
      <Modal open={editAux != null} onClose={() => setEditAux(null)} title={editAux?.id ? "Вспомогательное СИ" : "Новое вспомогательное СИ"}>
        {editAux && <AuxForm aux={editAux} onDone={() => { setEditAux(null); void load(); }} />}
      </Modal>
    </div>
  );
}

function firstLines(text: string, n: number): string {
  const lines = text.split("\n").filter((l) => l.trim());
  return lines.slice(0, n).join("\n") + (lines.length > n ? " …" : "");
}

function KitForm({ kit, onDone }: { kit: Partial<StandardKit>; onDone: () => void }) {
  const [form, setForm] = useState({
    section: kit.section ?? "",
    scope: kit.scope ?? "",
    standards_text: kit.standards_text ?? "",
    aux_text: kit.aux_text ?? "",
    journal_aux: kit.journal_aux ?? "",
    fif_numbers: (kit.fif_numbers ?? []).join("\n"),
    note: kit.note ?? "",
    is_active: kit.is_active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await saveStandardKit(kit.id ?? null, {
        ...form,
        fif_numbers: form.fif_numbers.split(/[\s|,;]+/).filter(Boolean),
      });
      onDone();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="field">
        <label htmlFor="kit-section">Раздел<span className="req">*</span></label>
        <input id="kit-section" value={form.section} onChange={set("section")} placeholder="Единый перечень для поверки манометров" />
      </div>
      <div className="field">
        <label htmlFor="kit-scope">Класс точности / диапазон</label>
        <textarea id="kit-scope" rows={2} value={form.scope} onChange={set("scope")} />
      </div>
      <div className="field">
        <label htmlFor="kit-std">Эталоны (наименование)</label>
        <textarea id="kit-std" rows={3} value={form.standards_text} onChange={set("standards_text")} />
      </div>
      <div className="field">
        <label htmlFor="kit-fif">Номера эталонов в ФИФ ОЕИ — по одному в строке или через «|»</label>
        <textarea id="kit-fif" rows={2} className="cell-mono" value={form.fif_numbers} onChange={set("fif_numbers")} placeholder="68984.17.4Р.01152950" />
      </div>
      <div className="field">
        <label htmlFor="kit-aux">СИ, применяемые при поверке</label>
        <textarea id="kit-aux" rows={3} value={form.aux_text} onChange={set("aux_text")} />
      </div>
      <div className="field">
        <label htmlFor="kit-u">Обозначение для журнала (столбец U)</label>
        <textarea id="kit-u" rows={2} className="cell-mono" value={form.journal_aux} onChange={set("journal_aux")} placeholder="69551-17^871455^|2231-72^8787^|" />
      </div>
      <div className="field">
        <label htmlFor="kit-note">Примечание</label>
        <input id="kit-note" value={form.note} onChange={set("note")} />
      </div>
      <Switch checked={form.is_active} onChange={(v) => setForm((f) => ({ ...f, is_active: v }))} label="Используется" />
      {error && <Alert>{error}</Alert>}
      <div className="actions">
        <button type="button" className="primary" disabled={busy || !form.section.trim()} onClick={() => void save()}>
          {busy ? "Сохраняю…" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}

function AuxForm({ aux, onDone }: { aux: Partial<AuxInstrument>; onDone: () => void }) {
  const [name, setName] = useState(aux.name ?? "");
  const [designation, setDesignation] = useState(aux.designation ?? "");
  const [note, setNote] = useState(aux.note ?? "");
  const [active, setActive] = useState(aux.is_active ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await saveAuxInstrument(aux.id ?? null, { name, designation, note, is_active: active });
      onDone();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="field">
        <label htmlFor="aux-name">Наименование<span className="req">*</span></label>
        <input id="aux-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Термометр ТЛ-5 №22" />
      </div>
      <div className="field">
        <label htmlFor="aux-des">Обозначение для журнала</label>
        <input id="aux-des" className="cell-mono" value={designation} onChange={(e) => setDesignation(e.target.value)} placeholder="299-11^22^" />
      </div>
      <div className="field">
        <label htmlFor="aux-note">Примечание</label>
        <input id="aux-note" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <Switch checked={active} onChange={setActive} label="В работе" />
      {error && <Alert>{error}</Alert>}
      <div className="actions">
        <button type="button" className="primary" disabled={busy || !name.trim()} onClick={() => void save()}>
          {busy ? "Сохраняю…" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
