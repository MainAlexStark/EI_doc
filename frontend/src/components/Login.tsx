import { useState, type FormEvent } from "react";
import { login } from "../api";
import Icon from "../ui/Icon";
import { Alert } from "../ui/controls";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email.trim(), password);
      onSuccess();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login" onSubmit={submit}>
        <div className="brand-mark lg">ЕИ</div>
        <h1>Вход в EI_doc</h1>
        <p className="sub">Журнал поверочных работ ООО «Единица Измерения»</p>

        {error && <Alert>{error}</Alert>}

        <div className="field">
          <label htmlFor="login-email">Почта</label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            inputMode="email"
            required
            autoFocus
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
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button
              type="button"
              className={`icon-btn sm ${showPassword ? "active" : ""}`}
              onClick={() => setShowPassword((shown) => !shown)}
              aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
              title={showPassword ? "Скрыть пароль" : "Показать пароль"}
            >
              <Icon name="eye" size={16} />
            </button>
          </div>
        </div>

        <button className="primary" type="submit" disabled={busy}>
          {busy ? <><span className="spinner sm" style={{ borderTopColor: "#fff" }} /> Входим…</> : "Войти"}
        </button>
        <div className="login-foot">Нет доступа — обратитесь к руководителю</div>
      </form>
    </div>
  );
}
