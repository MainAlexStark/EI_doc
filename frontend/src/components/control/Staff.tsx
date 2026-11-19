import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  createStaff, fetchStaff, initials, resetStaffPassword, teamColors, updateStaff,
  type StaffList, type StaffPayload, type StaffRow,
} from "../../dispatchApi";
import Icon from "../../ui/Icon";
import { confirmDialog, Modal } from "../../ui/Modal";
import { Alert, EmptyState, PageActions, SearchField, Spinner, Switch } from "../../ui/controls";

/** «Сотрудники» — завести человека одним действием: ФИО, должность, роль, почта.
 *
 * Учётная запись заводится вместе с карточкой, временный пароль показывается
 * один раз — его передают человеку, при входе он его сменит. Менять могут
 * владелец и руководитель, остальное руководство только смотрит. */

const ruDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("ru-RU") : "");

function PasswordNote({ email, password, onClose }: { email: string; password: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Modal open onClose={onClose} title="Временный пароль" subtitle="Показывается один раз — передайте его сотруднику"
           footer={<button type="button" className="primary" onClick={onClose}>Готово</button>}>
      <div className="kv">
        <div><span className="k">Логин</span><span className="v">{email}</span></div>
        <div><span className="k">Пароль</span><span className="v mono big-secret">{password}</span></div>
      </div>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard?.writeText(`Вход в EI_doc\nЛогин: ${email}\nПароль: ${password}`).then(() => setCopied(true));
        }}
      >
        <Icon name="copy" size={15} /> {copied ? "Скопировано" : "Скопировать логин и пароль"}
      </button>
    </Modal>
  );
}

function StaffForm({
  row, list, onClose, onSaved,
}: {
  row: StaffRow | null;
  list: StaffList;
  onClose: () => void;
  onSaved: (row: StaffRow, password: string) => void;
}) {
  const [form, setForm] = useState<StaffPayload>(() => ({
    full_name: row?.full_name ?? "",
    position: row?.position ?? "Поверитель",
    email: row?.email ?? "",
    role: row?.role || "verifier",
    tab_number: row?.tab_number ?? "",
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key: keyof StaffPayload) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload: StaffPayload = { ...form };
      if (row) {
        // Неизменённое не шлём — роль без почты, например, сервер справедливо отвергнет.
        (Object.keys(payload) as (keyof StaffPayload)[]).forEach((k) => {
          if ((row as unknown as Record<string, unknown>)[k] === payload[k]) delete payload[k];
        });
        if (!payload.email && !row.email) delete payload.role;
      }
      const result = row ? await updateStaff(row.id, payload) : await createStaff(payload);
      onSaved(result.employee, result.password);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={row ? row.full_name : "Новый сотрудник"}
      subtitle={row ? `Табельный № ${row.tab_number}` : "Почта даёт вход в EI_doc — пароль покажем после сохранения"}
      footer={
        <>
          <button type="button" onClick={onClose}>Отмена</button>
          <button type="submit" form="staff-form" className="primary" disabled={busy || !form.full_name?.trim()}>
            {busy ? "Сохраняю…" : row ? "Сохранить" : "Завести"}
          </button>
        </>
      }
    >
      <form id="staff-form" className="form-grid" onSubmit={(e) => void submit(e)}>
        {error && <Alert>{error}</Alert>}
        <label className="field span-all">
          <span className="field-label">ФИО</span>
          <input autoFocus value={form.full_name} onChange={set("full_name")} placeholder="Иванов Иван Иванович" />
        </label>
        <label className="field">
          <span className="field-label">Должность</span>
          <input value={form.position} onChange={set("position")} />
        </label>
        <label className="field">
          <span className="field-label">Табельный №</span>
          <input value={form.tab_number} onChange={set("tab_number")} placeholder={list.next_tab_number} />
          <small className="hint">Входит в номер протокола</small>
        </label>
        <label className="field">
          <span className="field-label">Почта для входа</span>
          <input type="email" value={form.email} onChange={set("email")} placeholder="без входа в EI_doc" />
        </label>
        <label className="field">
          <span className="field-label">Роль</span>
          <select value={form.role} onChange={set("role")} disabled={!form.email}>
            {list.roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>
      </form>
    </Modal>
  );
}

export default function Staff({ meId }: { meId?: number | null }) {
  const [list, setList] = useState<StaffList | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [showFired, setShowFired] = useState(false);
  const [editing, setEditing] = useState<StaffRow | "new" | null>(null);
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(null);

  const reload = useCallback(() => {
    fetchStaff(showFired).then(setList).catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось загрузить"));
  }, [showFired]);
  useEffect(reload, [reload]);

  const colors = useMemo(() => teamColors((list?.staff ?? []).map((s) => s.id)), [list]);

  if (!list) return error ? <Alert>{error}</Alert> : <Spinner />;
  const q = query.trim().toLowerCase();
  const rows = list.staff.filter((s) => !q || `${s.full_name} ${s.email} ${s.position} ${s.tab_number}`.toLowerCase().includes(q));

  const toggleActive = async (row: StaffRow) => {
    if (row.is_active) {
      const ok = await confirmDialog({
        title: `Уволить ${row.short_name}?`,
        text: "Пропадёт из списков назначения, вход в EI_doc закроется. Протоколы и история остаются.",
        ok: "Уволить", danger: true,
      });
      if (!ok) return;
    }
    try {
      await updateStaff(row.id, { is_active: !row.is_active });
      reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось");
    }
  };

  const reset = async (row: StaffRow) => {
    const ok = await confirmDialog({ title: `Новый пароль для ${row.short_name}?`, text: "Старый перестанет работать.", ok: "Сбросить" });
    if (!ok) return;
    try {
      const result = await resetStaffPassword(row.id);
      setSecret(result);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось сбросить пароль");
    }
  };

  return (
    <div>
      {list.can_admin && (
        <PageActions>
          <button type="button" className="primary" onClick={() => setEditing("new")}>
            <Icon name="plus" size={16} /> Сотрудник
          </button>
        </PageActions>
      )}
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {!list.can_admin && <Alert tone="info">Менять сотрудников могут владелец и руководитель — здесь только просмотр.</Alert>}
      <div className="toolbar">
        <SearchField value={query} onChange={setQuery} placeholder="ФИО, почта, должность" className="grow" />
        <Switch checked={showFired} onChange={setShowFired} label="Уволенные" />
      </div>
      {rows.length === 0 ? (
        <EmptyState icon="users" title="Никого не нашли" />
      ) : (
        <div className="table-wrap">
          <table className="data-table staff-table">
            <thead>
              <tr>
                <th>Сотрудник</th>
                <th>Таб. №</th>
                <th>Вход</th>
                <th>Роль</th>
                <th>Аттестация</th>
                <th>Telegram</th>
                {list.can_admin && <th />}
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className={s.is_active ? "" : "fired"}>
                  <td>
                    <div className="who-cell">
                      <span className="avatar sm" style={{ background: colors.get(s.id) }}>{initials(s.full_name)}</span>
                      <div>
                        <b>{s.full_name}</b>
                        <div className="sub">{s.position || "—"}{s.is_active ? "" : " · уволен"}</div>
                      </div>
                    </div>
                  </td>
                  <td className="mono">{s.tab_number}</td>
                  <td>
                    {s.email ? (
                      <>
                        {s.email}
                        <div className="sub">{s.last_login ? `входил ${ruDate(s.last_login)}` : "ещё не входил"}</div>
                      </>
                    ) : <span className="sub">без входа</span>}
                  </td>
                  <td>{s.role_display}</td>
                  <td>
                    {s.attestation_valid_to ? (
                      <span className={`pill ${s.attestation_ok ? "ok" : "bad"}`}>
                        {s.attestation_ok ? `до ${ruDate(s.attestation_nearest ?? s.attestation_valid_to)}` : `истекла ${ruDate(s.attestation_valid_to)}`}
                      </span>
                    ) : <span className="sub">нет</span>}
                  </td>
                  <td>{s.telegram_linked ? <Icon name="check" size={15} /> : <span className="sub">—</span>}</td>
                  {list.can_admin && (
                    <td className="row-actions">
                      <button type="button" className="sm" onClick={() => setEditing(s)}>Изменить</button>
                      {s.email && s.is_active && <button type="button" className="sm" onClick={() => void reset(s)}>Пароль</button>}
                      {s.id !== meId && (
                        <button type="button" className={`sm ${s.is_active ? "danger" : ""}`} onClick={() => void toggleActive(s)}>
                          {s.is_active ? "Уволить" : "Вернуть"}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <StaffForm
          row={editing === "new" ? null : editing}
          list={list}
          onClose={() => setEditing(null)}
          onSaved={(row, password) => {
            setEditing(null);
            reload();
            if (password) setSecret({ email: row.email, password });
          }}
        />
      )}
      {secret && <PasswordNote email={secret.email} password={secret.password} onClose={() => setSecret(null)} />}
    </div>
  );
}
