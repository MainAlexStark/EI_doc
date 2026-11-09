import { useCallback, useEffect, useRef, useState } from "react";
import { fetchFifStatus, type FifStatus } from "../api";

/**
 * Индикатор «РСТ»: отвечает ли Госреестр (ФИФ) и каким путём — через MCP-сервер РСТ или напрямую.
 *
 * Обычно ничего не спрашивает у ФГИС сам: сервер отдаёт исход последних поисков. Нажатие —
 * «Проверить сейчас»: по одному запросу каждым путём. ``signal`` меняется, когда поиск сообщил о
 * сбое, — тогда индикатор перечитывает статус.
 */
export default function RstStatus({ signal, compact }: { signal?: unknown; compact?: boolean }) {
  const [status, setStatus] = useState<FifStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [failed, setFailed] = useState("");
  const [open, setOpen] = useState(false);

  const load = useCallback(async (probe: boolean) => {
    setChecking(true);
    setFailed("");
    try {
      setStatus(await fetchFifStatus(probe));
    } catch (exc) {
      setFailed(exc instanceof Error ? exc.message : "сервер EI_doc не ответил");
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load, signal]);

  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const tone = checking && !status ? "muted" : failed || !status?.ok ? "bad" : "ok";
  const route = status?.routes.find((r) => r.key === status.via);
  const label = checking && !status
    ? "РСТ: проверяю…"
    : failed
      ? "РСТ: нет ответа от EI_doc"
      : status?.ok
        ? `РСТ на связи${compact ? "" : ` · ${status.via === "mcp" ? "через MCP" : "напрямую"}`}${route?.ms != null && !compact ? ` · ${(route.ms / 1000).toFixed(1).replace(".", ",")} с` : ""}`
        : "РСТ не отвечает";

  return (
    <span ref={box} className="rst-status" style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        className={`pill ${tone}`}
        style={{ border: 0, cursor: "pointer" }}
        aria-expanded={open}
        title="Связь с Госреестром (ФИФ, РСТ). Нажмите — подробности и проверка"
        onClick={() => setOpen((v) => !v)}
      >
        {label}
      </button>
      {open && (
        <div
          className="card"
          role="dialog"
          style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 30, width: 320, padding: 12, fontSize: 13, background: "var(--surface)", boxShadow: "0 8px 28px rgba(0,0,0,.14)" }}
        >
          <b>Связь с Госреестром</b>
          {failed && <div className="hint warn-text">{failed}</div>}
          {status?.routes.map((r) => (
            <div key={r.key} style={{ marginTop: 8 }}>
              <span className={`pill ${r.ok ? "ok" : r.ok === false ? "bad" : "muted"}`}>{r.label}</span>{" "}
              {r.ok ? `на связи${r.ms != null ? `, ${(r.ms / 1000).toFixed(1).replace(".", ",")} с` : ""}` : r.ok === false ? "нет связи" : "не проверялся"}
              {r.error && <div className="sub" style={{ wordBreak: "break-word" }}>{r.error}</div>}
              {r.checked_at && (
                <div className="sub">проверено {new Date(r.checked_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</div>
              )}
            </div>
          ))}
          {status && !status.ok && (
            <p className="hint">
              ФГИС отвечает только российским адресам. Если не отвечают оба пути — проверьте MCP-сервер РСТ (RST_MCP_URL,
              токен) или попробуйте позже.
            </p>
          )}
          <div className="actions" style={{ marginTop: 10 }}>
            <button type="button" className="sm" disabled={checking} onClick={() => void load(true)}>
              {checking ? "Проверяю…" : "Проверить сейчас"}
            </button>
          </div>
        </div>
      )}
    </span>
  );
}
