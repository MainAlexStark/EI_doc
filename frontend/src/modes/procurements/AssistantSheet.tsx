/** Ассистент рядом с карточкой закупки: новый чат встроенного ассистента с готовым заданием.
 *
 * Как в шаблонах (TemplateStudio): ChatView на свежем разговоре, первое сообщение уходит само.
 * Инструменты ei_procurement_* предлагают запись карточкой «Подтвердить» — человек видит, что
 * сохранится. Закрыли лист — карточка закупки перечитывает выжимку и заходы. */

import { useEffect, useState } from "react";
import "../../assistant/assistant.css";
import ChatView from "../../assistant/ChatView";
import { createConversation, fetchModels, updateConversation, type ModelInfo } from "../../assistant/api";
import { Sheet } from "../../ui/Sheet";
import { Alert, Spinner } from "../../ui/controls";

export default function AssistantSheet({
  open, title, subtitle, prompt, onClose,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  /** Задание ассистенту — первое сообщение чата. */
  prompt: string;
  onClose: () => void;
}) {
  const [conversation, setConversation] = useState<number | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError("");
    setConversation(null);
    (async () => {
      try {
        const info = await fetchModels();
        if (cancelled) return;
        setModels(info.models);
        setDefaultModel(info.default);
        const created = await createConversation(info.default);
        if (cancelled) return;
        void updateConversation(created.id, { title }).catch(() => undefined);
        setConversation(created.id);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "Ассистент недоступен");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, title]);

  return (
    <Sheet open={open} onClose={onClose} title={title} subtitle={subtitle} size="lg" className="pr-assistant-sheet">
      {error && <Alert>{error} — откройте раздел «Ассистент» или заполните вручную.</Alert>}
      {!error && conversation == null && <Spinner label="Подключаю ассистента…" />}
      {conversation != null && (
        <div className="pr-assistant-chat">
          <ChatView
            key={conversation}
            conversationId={conversation}
            models={models}
            defaultModel={defaultModel}
            onChanged={() => undefined}
            onOpenContextFile={() => undefined}
            initialPrompt={prompt}
          />
        </div>
      )}
    </Sheet>
  );
}
