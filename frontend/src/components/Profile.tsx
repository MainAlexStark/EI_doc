/** «Профиль и пароль» — окно из меню профиля (и по ссылке #/profile из писем).
 *
 * Пароль меняется как в openseller: текущий + новый дважды, после смены остальные
 * входы закрываются, этот остаётся. Ниже — какие письма присылать: категории,
 * которые человеку вообще приходят (заявки — только руководству и наблюдателям).
 * Письма о пароле не выключаются. */

import { useEffect, useState, type FormEvent } from "react";
import {
  changePassword,
  fetchNotifyPrefs,
  saveNotifyPrefs,
  sendTestMail,
  type NotifyPrefs,
} from "../api";
import Icon from "../ui/Icon";
import { Modal } from "../ui/Modal";
import toast from "../ui/Toast";
import { Alert, Spinner, Switch } from "../ui/controls";

export default function Profile({
  open, onClose, name, email,
}: { open: boolean; onClose: () => void; name: string; email: string }) {
  return (
    <Modal open={open} onClose={onClose} size="md" title="Профиль и пароль" subtitle={name ? `${name} · ${email}` : email}>
      {open && (
        <div className="stack profile-stack">
          <PasswordSection email={email} />
          <NotifySection />
        </div>
      )}
    </Modal>
  );
}

const MIN_PASSWORD = 8;

function PasswordSection({ email }: { email: string }) {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const mismatch = password2.length > 0 && password !== password2;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError("");
    setDone("");
    try {
      const detail = await changePassword(current, password, password2);
      setDone(detail);
      setCurrent("");
      setPassword("");
      setPassword2("");
      toast.success("Пароль изменён");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не получилось — попробуйте ещё раз");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="profile-section" onSubmit={submit} aria-labelledby="profile-password">
      <h3 id="profile-password"><Icon name="lock" size={16} /> Пароль</h3>
      {/* скрытое поле — менеджер паролей поймёт, для какой учётной записи новый пароль */}
      <input type="email" autoComplete="username" value={email} readOnly hidden />
      {error && <Alert>{error}</Alert>}
      {done && <Alert tone="ok">{done}</Alert>}
      <div className="field">
        <label htmlFor="pw-current">Текущий пароль</label>
        <input id="pw-current" type={show ? "text" : "password"} autoComplete="current-password" required
          value={current} onChange={(event) => setCurrent(event.target.value)} />
      </div>
      <div className="form-grid profile-grid">
        <div className="field">
          <label htmlFor="pw-new">Новый</label>
          <input id="pw-new" type={show ? "text" : "password"} autoComplete="new-password" required minLength={MIN_PASSWORD}
            value={password} onChange={(event) => setPassword(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="pw-new2">Ещё раз</label>
          <input id="pw-new2" type={show ? "text" : "password"} autoComplete="new-password" required
            value={password2} onChange={(event) => setPassword2(event.target.value)}
            aria-invalid={mismatch || undefined} aria-describedby={mismatch ? "pw-mismatch" : undefined} />
          {mismatch && <span className="hint warn" id="pw-mismatch">Пароли не совпадают</span>}
        </div>
      </div>
      <p className="hint">Не короче {MIN_PASSWORD} символов. После смены остальные входы закроются — на других телефонах
        и компьютерах нужно будет войти заново. Этот вход останется.</p>
      <div className="row profile-actions">
        <button type="button" className="ghost sm" onClick={() => setShow((value) => !value)} aria-pressed={show}>
          <Icon name="eye" size={15} /> {show ? "Скрыть" : "Показать"}
        </button>
        <button type="submit" className="primary" disabled={busy || mismatch || !current || password.length < MIN_PASSWORD}>
          {busy ? <><span className="spinner sm" /> Меняю…</> : <><Icon name="lock" size={15} /> Сменить пароль</>}
        </button>
      </div>
    </form>
  );
}

function NotifySection() {
  const [prefs, setPrefs] = useState<NotifyPrefs | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    fetchNotifyPrefs().then(setPrefs).catch((exc) => setError(exc instanceof Error ? exc.message : "Не загрузилось"));
  }, []);

  const toggle = async (key: string, enabled: boolean) => {
    if (!prefs) return;
    const before = prefs;
    setPrefs({ ...prefs, categories: prefs.categories.map((c) => (c.key === key ? { ...c, enabled } : c)) });
    setSaving(key);
    try {
      setPrefs(await saveNotifyPrefs({ [key]: enabled }));
    } catch (exc) {
      setPrefs(before);
      toast.error(exc instanceof Error ? exc.message : "Не сохранилось");
    } finally {
      setSaving(null);
    }
  };

  const test = async () => {
    try {
      const result = await sendTestMail();
      (result.mail_configured ? toast.success : toast.warn)(result.detail);
    } catch (exc) {
      toast.error(exc instanceof Error ? exc.message : "Не получилось");
    }
  };

  return (
    <section className="profile-section" aria-labelledby="profile-notify">
      <h3 id="profile-notify"><Icon name="mail" size={16} /> Письма на почту</h3>
      {error && <Alert>{error}</Alert>}
      {!prefs && !error && <Spinner label="Загружаю…" />}
      {prefs && (
        <>
          {!prefs.mail_configured && (
            <Alert tone="warn">
              Почта на сервере пока не настроена — письма не уходят. Настройки сохранятся и заработают, когда её подключат.
            </Alert>
          )}
          {!prefs.email && <Alert tone="info">У вашей учётной записи нет почты — письма приходить не будут.</Alert>}
          {prefs.categories.length === 0 && <p className="hint">Для вашей роли писем с уведомлениями нет.</p>}
          <div className="stack profile-switches">
            {prefs.categories.map((category) => (
              <Switch
                key={category.key}
                label={category.label}
                checked={category.enabled}
                disabled={saving === category.key}
                onChange={(checked) => void toggle(category.key, checked)}
              />
            ))}
          </div>
          <p className="hint">
            Назначения приходят одной сводкой: назначили пачкой — одно письмо. Письма о пароле приходят всегда.
            {prefs.telegram_linked ? " Telegram подключён — туда приходит то же самое." : ""}
          </p>
          {prefs.can_test && (
            <div className="row profile-actions">
              <button type="button" className="ghost sm" onClick={() => void test()}>
                <Icon name="mail" size={15} /> Пробное письмо себе
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
