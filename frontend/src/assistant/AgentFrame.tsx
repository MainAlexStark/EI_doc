/** Вкладка «Ассистент» на agentkit: чат агента во фрейме на всю рабочую область.
 *
 * Вход без второго логина: EI_doc подписывает короткий токен (/api/assistant/agent/),
 * и мы передаём его фрейму postMessage'ем — только на origin агента. Чат просит токен
 * сам ({type: "agentkit:need-token"}) при старте и когда старый истёк; свежий мы
 * присылаем и заранее, раз в час, чтобы разговор не прерывался входом.
 *
 * Файлы компании, общая память и правила — в отдельном окне «Контекст» (кнопка в шапке),
 * а не панелью сбоку: чату нужна вся ширина.
 */

import { useEffect, useRef, useState } from "react";
import "./assistant.css";
import Icon from "../ui/Icon";
import { PageActions } from "../ui/controls";
import ContextWindow from "./ContextPanel";
import { fetchAgentLink } from "./api";

const REFRESH_MS = 60 * 60 * 1000;

export default function AgentFrame({ url, origin, token }: { url: string; origin: string; token: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const tokenRef = useRef(token);
  const [contextOpen, setContextOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);

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
    <div className="agent-shell">
      <PageActions>
        <button
          type="button"
          aria-label="Контекст ассистента"
          title="Файлы компании, общая память и правила — что ассистент знает о компании"
          onClick={() => setContextOpen(true)}
        >
          <Icon name="book" size={16} />
          <span className="hide-phone">Контекст</span>
        </button>
      </PageActions>
      {!loaded && <div className="agent-loading"><span className="spinner" /> Подключаю ассистента…</div>}
      <iframe
        ref={frame}
        className="agent-frame"
        src={url}
        title="Ассистент-метролог"
        allow="clipboard-write"
        onLoad={() => {
          setLoaded(true);
          frame.current?.contentWindow?.postMessage({ type: "agentkit:token", token: tokenRef.current }, origin);
        }}
      />
      <ContextWindow open={contextOpen} onClose={() => setContextOpen(false)} agentMode models={[]} focusFileId={null} />
    </div>
  );
}
