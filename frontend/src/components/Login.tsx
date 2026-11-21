import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { login } from "../api";
import Icon from "../ui/Icon";
import { Alert } from "../ui/controls";

const LAST_EMAIL = "ei_doc_last_email";

/** Вход в кабинет (дизайн-система v2). Почта запоминается — на телефоне в поле её
 *  каждый раз набирать неудобно; при нажатом Caps Lock — подсказка у пароля. */
export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState(() => {
    try {
      return localStorage.getItem(LAST_EMAIL) ?? "";
    } catch {
      return "";
    }
  });
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [caps, setCaps] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.app = "cabinet";
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email.trim(), password);
      try {
        localStorage.setItem(LAST_EMAIL, email.trim());
      } catch {
        /* приватный режим */
      }
      onSuccess();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  };

  const onKey = (event: KeyboardEvent<HTMLInputElement>) => setCaps(event.getModifierState?.("CapsLock") ?? false);

  return (
    <div className="login-page">
      <form className="login" onSubmit={submit} aria-labelledby="login-title">
        <div className="brand-row">
          <div className="brand-mark lg" aria-hidden="true">ЕИ</div>
          <div>
            <b>EI_doc</b>
            <span>ООО «Единица Измерения», Киров</span>
          </div>
        </div>
        <h1 id="login-title">Вход</h1>
        <p className="sub">Выезды, приёмки, протоколы поверки — в одном месте.</p>

        {error && <Alert>{error}</Alert>}

        <div className="field">
          <label htmlFor="login-email">Почта</label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            inputMode="email"
            required
            autoFocus={!email}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="login-password">Пароль</label>
          <div className="password-wrap">
            <input
              id="login-password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              autoFocus={!!email}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              onKeyUp={onKey}
              onKeyDown={onKey}
              aria-describedby={caps ? "login-caps" : undefined}
            />
            <button
              type="button"
              className={`icon-btn sm ${showPassword ? "active" : ""}`}
              onClick={() => setShowPassword((shown) => !shown)}
              aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
              aria-pressed={showPassword}
              title={showPassword ? "Скрыть пароль" : "Показать пароль"}
            >
              <Icon name="eye" size={16} />
            </button>
          </div>
          {caps && (
            <div className="caps-hint" id="login-caps" role="status">
              <Icon name="alert" size={14} /> Включён Caps Lock
            </div>
          )}
        </div>

        <button className="primary" type="submit" disabled={busy}>
          {busy ? <><span className="spinner sm" /> Входим…</> : <>Войти <Icon name="arrowRight" size={16} /></>}
        </button>
        <div className="login-foot">
          <span>Нет доступа или забыли пароль — напишите руководителю.</span>
          <a href="/o-nas/" target="_blank" rel="noopener">Сайт фирмы</a>
        </div>
      </form>
    </div>
  );
}
