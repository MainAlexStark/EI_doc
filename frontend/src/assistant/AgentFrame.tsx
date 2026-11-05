/** Вкладка «Ассистент» на agentkit: чат агента во фрейме + панель контекста EI_doc.
 *
 * Вход без второго логина: EI_doc подписывает короткий токен (/api/assistant/agent/),
 * и мы передаём его фрейму postMessage'ем — только на origin агента. Чат просит токен
 * сам ({type: "agentkit:need-token"}) при старте и когда старый истёк; свежий мы
 * присылаем и заранее, раз в час, чтобы разговор не прерывался входом.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import "./assistant.css";
import ContextPanel from "./ContextPanel";
import { fetchAgentLink } from "./api";

const PANEL_KEY = "ei_doc_agent_context_open";
const REFRESH_MS = 60 * 60 * 1000;

export default function AgentFrame({ url, origin, token }: { url: string; origin: string; token: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const tokenRef = useRef(token);
  const [panelOpen, setPanelOpen] = useState(() => {
    try {
      return localStorage.getItem(PANEL_KEY) === "1";
    } catch {
      return false;
    }
  });
  const rootRef = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(57);
  useLayoutEffect(() => {
    const measure = () => setTop(Math.max(0, Math.round(rootRef.current?.getBoundingClientRect().top ?? 57) + window.scrollY));
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(PANEL_KEY, panelOpen ? "1" : "0");
    } catch {
      /* ок */
    }
  }, [panelOpen]);

  useEffect(() => {
    const send = () => frame.current?.contentWindow?.postMessage({ type: "agentkit:token", token: tokenRef.current }, origin);
    const refresh = () =>
      fetchAgentLink()
        .then((l) => {
          if (l.enabled) tokenRef.current = l.token;
        })
        .catch(() => undefined)
        .finally(send);
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== origin || e.source !== frame.current?.contentWindow) return;
      if (e.data?.type === "agentkit:need-token") void refresh();
    };
    window.addEventListener("message", onMessage);
    const timer = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      window.removeEventListener("message", onMessage);
      window.clearInterval(timer);
    };
  }, [origin]);

  return (
    <div ref={rootRef} className={`agent-shell ${panelOpen ? "with-panel" : ""}`} style={{ ["--top" as string]: `${top}px` }}>
      <section className="agent-main">
        <div className="assistant-bar">
          <b className="assistant-title">Ассистент-метролог</b>
          <div className="spacer" />
          <button
            className={panelOpen ? "active-toggle" : ""}
            title="Файлы компании, общая память и правила — что ассистент знает о компании"
            onClick={() => setPanelOpen((o) => !o)}
          >
            📚 Контекст
          </button>
        </div>
        <iframe
          ref={frame}
          className="agent-frame"
          src={url}
          title="Ассистент-метролог"
          allow="clipboard-write"
          onLoad={() => frame.current?.contentWindow?.postMessage({ type: "agentkit:token", token: tokenRef.current }, origin)}
        />
      </section>
      {panelOpen && <ContextPanel agentMode models={[]} focusFileId={null} onClose={() => setPanelOpen(false)} />}
    </div>
  );
}
