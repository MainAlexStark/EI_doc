import { useState, type FormEvent } from "react";
import { login } from "../api";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      onSuccess();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="login" onSubmit={submit}>
      <h1>EI_doc</h1>
      <p className="sub">Журнал учёта поверочных работ</p>

      {error && <div className="error">{error}</div>}

      <div className="field">
        <label htmlFor="login-email">Почта</label>
        <input
          id="login-email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="login-password">Пароль</label>
        <input
          id="login-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>

      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Входим…" : "Войти"}
      </button>
    </form>
  );
}
