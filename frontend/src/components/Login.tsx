import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { checkResetToken, forgotPassword, login, resetPassword } from "../api";
import Icon from "../ui/Icon";
import { Alert } from "../ui/controls";

const LAST_EMAIL = "ei_doc_last_email";

/** Токен из письма «Сброс пароля». Ссылка — /#reset=<токен>: фрагмент не уходит
 *  на сервер и не попадает в логи Caddy/gunicorn; старый вид /?reset= тоже понимаем.
 *  Читаем один раз при загрузке модуля и сразу убираем из адреса (history.replaceState):
 *  токен одноразовый, в истории, закладках и роутинге кабинета ему не место —
 *  дальше он живёт только в памяти вкладки. */
const pendingReset = (() => {
  try {
    const url = new URL(window.location.href);
    const fromHash = new URLSearchParams(url.hash.replace(/^#\/?/, "")).get("reset") ?? "";
    const fromQuery = url.searchParams.get("reset") ?? "";
    const token = fromHash || fromQuery;
    if (!token) return "";
    url.searchParams.delete("reset");
    const hash = fromHash ? "" : url.hash;
    window.history.replaceState(null, "", url.pathname + url.search + hash);
    return token;
  } catch {
    return "";
  }
})();
let resetToken = pendingReset;

/** Открыта ссылка сброса пароля (App выходит из текущего входа, чтобы показать форму). */
export const hasResetToken = () => resetToken !== "";
const resetTokenFromUrl = () => resetToken;
const dropResetFromUrl = () => {
  resetToken = "";
};

type Screen = "login" | "forgot" | "reset";

/** Вход в кабинет (дизайн-система v2). Почта запоминается — на телефоне в поле её
 *  каждый раз набирать неудобно; при нажатом Caps Lock — подсказка у пароля.
 *  Здесь же «Забыли пароль?» и новый пароль по ссылке из письма (как в openseller). */
export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [screen, setScreen] = useState<Screen>(() => (resetTokenFromUrl() ? "reset" : "login"));
  const [notice, setNotice] = useState("");
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

  if (screen === "forgot") {
    return <ForgotScreen initialEmail={email} onBack={() => setScreen("login")} />;
  }
  if (screen === "reset") {
    return (
      <ResetScreen
        onBack={() => {
          dropResetFromUrl();
          setScreen("forgot");
        }}
        onDone={(doneEmail, detail) => {
          dropResetFromUrl();
          if (doneEmail) setEmail(doneEmail);
          setPassword("");
          setNotice(detail);
          setScreen("login");
        }}
      />
    );
  }

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
        <Brand />
        <h1 id="login-title">Вход</h1>
        <p className="sub">Выезды, приёмки, протоколы поверки — в одном месте.</p>

        {notice && !error && <Alert tone="ok">{notice}</Alert>}
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
          <button type="button" className="link-btn" onClick={() => setScreen("forgot")}>Забыли пароль?</button>
          <a href="/schetchiki/" target="_blank" rel="noopener">Сайт фирмы</a>
        </div>
      </form>
    </div>
  );
}

function Brand() {
  return (
    <div className="brand-row">
      <div className="brand-mark lg" aria-hidden="true">ЕИ</div>
      <div>
        <b>EI_doc</b>
        <span>ООО «Единица Измерения», Киров</span>
      </div>
    </div>
  );
}

/** «Забыли пароль?» — ответ одинаковый, есть такой адрес или нет. Без почты на сервере — честно об этом. */
function ForgotScreen({ initialEmail, onBack }: { initialEmail: string; onBack: () => void }) {
  const [email, setEmail] = useState(initialEmail);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await forgotPassword(email.trim());
      setResult({ ok: data.mail_configured, text: data.detail });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не получилось — попробуйте ещё раз");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login" onSubmit={submit} aria-labelledby="forgot-title">
        <Brand />
        <h1 id="forgot-title">{result?.ok ? "Проверьте почту" : "Сброс пароля"}</h1>
        {!result && <p className="sub">Пришлём ссылку, по которой можно задать новый пароль.</p>}
        {error && <Alert>{error}</Alert>}
        {result && <Alert tone={result.ok ? "ok" : "warn"}>{result.text}</Alert>}
        {!result && (
          <>
            <div className="field">
              <label htmlFor="forgot-email">Почта</label>
              <input id="forgot-email" type="email" autoComplete="username" inputMode="email" required autoFocus
                value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
            <button className="primary" type="submit" disabled={busy}>
              {busy ? <><span className="spinner sm" /> Отправляем…</> : <>Прислать ссылку <Icon name="mail" size={16} /></>}
            </button>
          </>
        )}
        <div className="login-foot">
          <button type="button" className="link-btn login-back" onClick={onBack}>← Ко входу</button>
          <span>Нет доступа к почте — напишите руководителю.</span>
        </div>
      </form>
    </div>
  );
}

/** Новый пароль по ссылке из письма: сначала проверяем, жива ли ссылка, потом форма. */
function ResetScreen({ onBack, onDone }: { onBack: () => void; onDone: (email: string, detail: string) => void }) {
  const [token] = useState(resetTokenFromUrl);
  const [state, setState] = useState<"checking" | "form" | "bad">("checking");
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    checkResetToken(token)
      .then((data) => {
        if (!alive) return;
        setAccount(data.email ?? "");
        setState(data.ok ? "form" : "bad");
        if (!data.ok) setError(data.detail ?? "");
      })
      .catch((exc) => {
        if (!alive) return;
        setState("bad");
        setError(exc instanceof Error ? exc.message : "Ссылка не сработала");
      });
    return () => {
      alive = false;
    };
  }, [token]);

  const mismatch = password2.length > 0 && password !== password2;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError("");
    try {
      const data = await resetPassword(token, password, password2);
      onDone(data.email, data.detail);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не получилось сохранить пароль");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login" onSubmit={submit} aria-labelledby="reset-title">
        <Brand />
        <h1 id="reset-title">{state === "bad" ? "Ссылка не сработала" : "Новый пароль"}</h1>
        {state === "checking" && <p className="sub"><span className="spinner sm" /> Проверяем ссылку…</p>}
        {state === "form" && <p className="sub">Для {account}. После смены остальные входы закроются.</p>}
        {error && <Alert tone={state === "bad" ? "warn" : "error"}>{error}</Alert>}
        {state === "form" && (
          <>
            <input type="email" autoComplete="username" value={account} readOnly hidden />
            <div className="field">
              <label htmlFor="reset-password">Новый пароль</label>
              <input id="reset-password" type={show ? "text" : "password"} autoComplete="new-password" required
                minLength={8} autoFocus value={password} onChange={(event) => setPassword(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="reset-password2">Ещё раз</label>
              <input id="reset-password2" type={show ? "text" : "password"} autoComplete="new-password" required
                value={password2} onChange={(event) => setPassword2(event.target.value)} aria-invalid={mismatch || undefined} />
              {mismatch && <span className="hint warn">Пароли не совпадают</span>}
            </div>
            <label className="switch-row">
              <span className="switch-text"><span>Показать пароль</span></span>
              <input type="checkbox" role="switch" className="switch" checked={show} onChange={(event) => setShow(event.target.checked)} />
            </label>
            <button className="primary" type="submit" disabled={busy || mismatch || password.length < 8}>
              {busy ? <><span className="spinner sm" /> Сохраняем…</> : <>Сохранить пароль <Icon name="arrowRight" size={16} /></>}
            </button>
          </>
        )}
        <div className="login-foot">
          {state === "bad" ? (
            <button type="button" className="link-btn" onClick={onBack}>Запросить новую ссылку</button>
          ) : (
            <span>Ссылка действует час и срабатывает один раз.</span>
          )}
        </div>
      </form>
    </div>
  );
}
