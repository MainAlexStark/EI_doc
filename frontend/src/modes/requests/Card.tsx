/** Карточка заявки справа (на телефоне — в листе снизу). Только показ и кнопки действий:
 *  что делать с заявкой, решает Inbox. */

import type { HubRequest } from "../../api";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import toast from "../../ui/Toast";
import { STATUS, SOURCE, mapHref, money, telHref, whenLine } from "./util";

export type CardAction = "assign" | "confirm" | "reject" | "spam" | "delete";

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} скопирован`, { description: text, duration: 2500 });
  } catch {
    toast.error("Не удалось скопировать", { description: "Браузер не дал доступ к буферу обмена" });
  }
}

export function CardActions({ request, busy, onAction }: { request: HubRequest; busy?: boolean; onAction: (a: CardAction) => void }) {
  const actionable = request.status === "new" || request.status === "routed";
  // Отклонённую или спам можно стереть насовсем (тест, явный мусор) — если по ней не было нарядов.
  if (request.status === "rejected" || request.status === "spam") {
    return (
      <div className="rq-actions">
        <span className="grow" />
        <button type="button" className="ghost danger" disabled={busy} onClick={() => onAction("delete")}>
          <Icon name="trash" size={15} /> Удалить насовсем
        </button>
      </div>
    );
  }
  if (!actionable) return null;
  return (
    <div className="rq-actions">
      <button type="button" className="primary" disabled={busy} onClick={() => onAction("confirm")}>
        <Icon name="check" size={16} /> Подтвердить <Kbd className="on-accent kbd-hint">C</Kbd>
      </button>
      <button type="button" disabled={busy} onClick={() => onAction("assign")}>
        <Icon name="user" size={16} /> {request.suggested_employee ? "Другой исполнитель" : "Назначить"} <Kbd className="kbd-hint">A</Kbd>
      </button>
      <span className="grow" />
      <button type="button" className="ghost" disabled={busy} onClick={() => onAction("reject")}>
        Отклонить <Kbd className="kbd-hint">X</Kbd>
      </button>
      <button type="button" className="ghost danger" disabled={busy} onClick={() => onAction("spam")}>
        <Icon name="ban" size={15} /> Спам <Kbd className="kbd-hint">S</Kbd>
      </button>
    </div>
  );
}

export default function RequestCard({ request, busy, onAction, showActions = true }: {
  request: HubRequest;
  busy?: boolean;
  onAction: (a: CardAction) => void;
  showActions?: boolean;
}) {
  const status = STATUS[request.status] ?? { label: request.status_display || request.status, tone: "muted" };
  const total = request.items.reduce((sum, item) => sum + Number(item.subtotal), 0);
  const when = whenLine(request);
  const created = new Date(request.created_at).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

  return (
    <article className="rq-card" aria-label={`Заявка №${request.id}`}>
      <header className="rq-card-head">
        <div className="rq-card-titles">
          <div className="rq-card-meta">
            <span className="data">№{request.id}</span> · {created} · {SOURCE[request.source] ?? request.source}
          </div>
          <h2>{request.contact_name || "Без имени"}</h2>
        </div>
        <span className={`pill ${status.tone}`}>{status.label}</span>
      </header>

      <div className="rq-contacts">
        {request.contact_phone && (
          <div className="rq-contact">
            <a className="rq-contact-main data" href={telHref(request.contact_phone)}>
              <Icon name="phone" size={16} /> {request.contact_phone}
            </a>
            <button type="button" className="icon-btn sm" title="Скопировать телефон" aria-label="Скопировать телефон" onClick={() => void copy(request.contact_phone, "Телефон")}>
              <Icon name="copy" size={14} />
            </button>
          </div>
        )}
        {request.contact_email && (
          <div className="rq-contact">
            <a className="rq-contact-main" href={`mailto:${request.contact_email}`}><Icon name="mail" size={16} /> {request.contact_email}</a>
          </div>
        )}
        <div className="rq-contact">
          <a className="rq-contact-main" href={mapHref(request.address)} target="_blank" rel="noreferrer" title="Открыть на Яндекс Картах">
            <Icon name="mapPin" size={16} />
            <span>{request.address || "адрес не указан"}{request.district ? <span className="muted"> · {request.district}</span> : null}</span>
          </a>
          {request.address && (
            <button type="button" className="icon-btn sm" title="Скопировать адрес" aria-label="Скопировать адрес" onClick={() => void copy(request.address, "Адрес")}>
              <Icon name="copy" size={14} />
            </button>
          )}
        </div>
      </div>

      {!request.is_address_confirmed && request.address && (
        <p className="rq-note signal-text"><Icon name="alert" size={14} /> Адрес введён вручную — район мог определиться неточно.</p>
      )}

      <dl className="rq-facts">
        <div><dt>Когда</dt><dd>{when || <span className="muted">не выбрано — согласовать по телефону</span>}{request.is_priority_slot && <span className="pill ok plain rq-inline">приоритетное время</span>}</dd></div>
        <div>
          <dt>Исполнитель</dt>
          <dd>
            {request.assigned_employee || request.suggested_employee || <span className="muted">не подобран</span>}
            {!request.assigned_employee && request.suggested_employee && <span className="muted"> — подобран по району</span>}
          </dd>
        </div>
        {request.reject_reason && <div><dt>Причина отказа</dt><dd>{request.reject_reason}</dd></div>}
        {!request.consent_given && <div><dt>Согласие на ПДн</dt><dd className="fail-text">нет</dd></div>}
        {request.updated_by && request.updated_at && (
          <div><dt>Последний изменил</dt><dd className="muted">{request.updated_by} · {new Date(request.updated_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</dd></div>
        )}
      </dl>

      <h3 className="rq-h">Что поверить</h3>
      <ul className="lines">
        {request.items.map((item) => (
          <li key={item.id}>
            <span className="grow">{item.family}</span>
            <span className="num muted data">{item.quantity} × {money(item.unit_price)} ₽</span>
            <span className="num data rq-sum">{money(item.subtotal)} ₽</span>
          </li>
        ))}
        {request.items.length === 0 && <li className="muted">Типы приборов не выбраны</li>}
        {(request.estimated_price || request.items.length > 0) && (
          <li className="total">
            <span className="grow">Итого{Number(request.discount_percent) > 0 ? ` со скидкой ${Number(request.discount_percent)} %` : ""}</span>
            <span className="num data">≈ {money(request.estimated_price ?? total)} ₽</span>
          </li>
        )}
      </ul>

      {(request.si_description || request.comment) && (
        <>
          <h3 className="rq-h">Комментарий заявителя</h3>
          <div className="quote">{[request.si_description, request.comment].filter(Boolean).join("\n\n")}</div>
        </>
      )}

      {showActions && <CardActions request={request} busy={busy} onAction={onAction} />}
    </article>
  );
}
