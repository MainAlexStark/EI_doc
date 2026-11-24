/** Режим «Почта» — общие ящики фирмы на Timeweb (apps.mailbox), как «Почта» в openseller.
 *
 * Слева — ящики, папки, поиск и список; справа — переписка целиком. HTML письма
 * показывается в <iframe sandbox srcdoc> без скриптов (CSP в самом документе),
 * внешние картинки — только по кнопке. Ответ, «ответить всем», пересылка и новое
 * письмо — окно ComposeModal; отправка идёт SMTP ящика, копия — в «Отправленные».
 * Адрес #/mail/<id> открывает письмо (ссылки из уведомлений). */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { downloadFile } from "../../api";
import Icon from "../../ui/Icon";
import { Modal, confirmDialog } from "../../ui/Modal";
import { SkeletonList } from "../../ui/Skeleton";
import toast from "../../ui/Toast";
import { Alert, EmptyState, SearchField } from "../../ui/controls";
import { useModeAction } from "../../ui/modeActions";
import type { ModeProps } from "../types";
import {
  attachmentUrl, fetchCompose, fetchMailBody, fetchMailList, fetchMailStatus, fetchMailThread, mailAction,
  rawUrl, sendMail, syncMail,
  type Compose, type ComposeMode, type MailFolder, type MailMessage, type MailRow, type MailStatus,
} from "./api";
import "./mail.css";

const FOLDERS: { value: MailFolder; label: string; icon: "inbox" | "eye" | "flag" | "send" | "box" | "ban" | "trash" }[] = [
  { value: "inbox", label: "Входящие", icon: "inbox" },
  { value: "unread", label: "Непрочитанные", icon: "eye" },
  { value: "flagged", label: "Важные", icon: "flag" },
  { value: "sent", label: "Отправленные", icon: "send" },
  { value: "archive", label: "Архив", icon: "box" },
  { value: "junk", label: "Спам", icon: "ban" },
  { value: "trash", label: "Корзина", icon: "trash" },
];

const openIdFromHash = (): number | null => {
  const m = window.location.hash.match(/^#\/mail\/(\d+)/);
  return m ? Number(m[1]) : null;
};

const setHash = (id: number | null) =>
  window.history.replaceState(null, "", id ? `#/mail/${id}` : "#/mail");

function mailDate(value: string): string {
  const d = new Date(value);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  return d.toLocaleDateString("ru-RU");
}

const fullDate = (value: string) =>
  new Date(value).toLocaleString("ru-RU", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });

function humanSize(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} КБ`;
  return `${(n / 1024 / 1024).toFixed(1)} МБ`;
}

const initial = (name: string) => (name.match(/[\p{L}\p{N}]/u)?.[0] ?? "?").toUpperCase();

export default function MailMode({ refreshCounts }: ModeProps) {
  const [status, setStatus] = useState<MailStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [box, setBox] = useState("");
  const [folder, setFolder] = useState<MailFolder>("inbox");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<MailRow[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<number | null>(openIdFromHash);
  const [syncing, setSyncing] = useState(false);
  const [compose, setCompose] = useState<{ mode: ComposeMode; source?: MailMessage } | null>(null);
  const [dnsOpen, setDnsOpen] = useState(false);
  const [version, setVersion] = useState(0);

  useModeAction("mail", { new: () => setCompose({ mode: "new" }) });

  const loadStatus = useCallback(() => {
    fetchMailStatus()
      .then((s) => {
        setStatus(s);
        setStatusError("");
      })
      .catch((e: Error) => setStatusError(e.message));
  }, []);

  useEffect(() => {
    loadStatus();
    const timer = window.setInterval(() => {
      loadStatus();
      setVersion((v) => v + 1);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [loadStatus]);

  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const onHash = () => setOpenId(openIdFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Список: первая страница при смене ящика/папки/поиска, тихое обновление — по version.
  const listKey = `${box}|${folder}|${query}`;
  const lastKey = useRef("");
  useEffect(() => {
    if (!status?.enabled) {
      setLoading(false);
      return;
    }
    const fresh = lastKey.current !== listKey;
    lastKey.current = listKey;
    if (fresh) {
      setLoading(true);
      setPage(1);
    }
    let cancelled = false;
    fetchMailList({ box: box || undefined, folder, q: query || undefined, page: 1 })
      .then((data) => {
        if (cancelled) return;
        setRows((prev) => (fresh || page === 1 ? data.results : mergeFirstPage(prev, data.results)));
        if (fresh) setHasMore(data.has_more);
      })
      .catch((e: Error) => !cancelled && toast.error(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listKey, version, status?.enabled]);

  const loadMore = () => {
    const next = page + 1;
    fetchMailList({ box: box || undefined, folder, q: query || undefined, page: next })
      .then((data) => {
        setRows((prev) => [...prev, ...data.results.filter((r) => !prev.some((p) => p.id === r.id))]);
        setPage(next);
        setHasMore(data.has_more);
      })
      .catch((e: Error) => toast.error(e.message));
  };

  const open = (id: number | null) => {
    setOpenId(id);
    setHash(id);
    if (id) setRows((prev) => prev.map((r) => (r.id === id ? { ...r, unread: false } : r)));
  };

  const changed = () => {
    setVersion((v) => v + 1);
    loadStatus();
    refreshCounts();
  };

  const doSync = () => {
    setSyncing(true);
    syncMail()
      .then(() => window.setTimeout(changed, 2500))
      .catch((e: Error) => toast.error(e.message))
      .finally(() => window.setTimeout(() => setSyncing(false), 2500));
  };

  if (statusError) return <div className="mx-pad"><Alert>{statusError}</Alert></div>;
  if (!status) return <div className="mx-pad"><SkeletonList rows={6} label="Открываю почту…" /></div>;
  if (!status.enabled) return <MailDisabled status={status} />;

  const boxOptions = [
    { value: "", label: "Все ящики", count: status.unread },
    ...status.boxes.filter((b) => b.configured).map((b) => ({ value: b.name, label: b.address, count: b.unread })),
  ];
  const boxErrors = status.boxes.filter((b) => b.error);

  return (
    <div className={`split mx ${openId ? "has-detail" : ""}`}>
      <div className="split-list">
        <div className="split-list-head">
          <div className="mx-toolbar">
            {boxOptions.length > 2 ? (
              <select className="mx-box" value={box} onChange={(e) => setBox(e.target.value)} aria-label="Ящик">
                {boxOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}{o.count ? ` · ${o.count}` : ""}</option>
                ))}
              </select>
            ) : (
              <b className="mx-box-one">{boxOptions[1]?.label}</b>
            )}
            <button type="button" className="icon-btn" title="Проверить почту" aria-label="Проверить почту" onClick={doSync} disabled={syncing}>
              <Icon name="refresh" size={17} className={syncing ? "spin" : ""} />
            </button>
            <button type="button" className="icon-btn" title="Записи домена (DNS)" aria-label="Записи домена" onClick={() => setDnsOpen(true)}>
              <Icon name="shield" size={17} />
            </button>
            <button type="button" className="primary sm mx-compose-btn" onClick={() => setCompose({ mode: "new" })}>
              <Icon name="pen" size={15} /> Написать
            </button>
          </div>
          <div className="mx-folders" role="tablist" aria-label="Папки">
            {FOLDERS.map((f) => (
              <button
                key={f.value} type="button" role="tab" aria-selected={folder === f.value}
                className={folder === f.value ? "active" : ""}
                onClick={() => setFolder(f.value)}
              >
                <Icon name={f.icon} size={14} />
                <span>{f.label}</span>
                {f.value === "inbox" && !!(box ? status.boxes.find((b) => b.name === box)?.unread : status.unread) && (
                  <span className="seg-count">{box ? status.boxes.find((b) => b.name === box)?.unread : status.unread}</span>
                )}
              </button>
            ))}
          </div>
          <SearchField value={q} onChange={setQ} placeholder="Тема, адрес или текст" />
          {boxErrors.map((b) => (
            <Alert key={b.name} tone="warn">{b.address}: {b.error}</Alert>
          ))}
        </div>
        <div className="split-list-body">
          {loading ? (
            <SkeletonList rows={7} label="Загружаю письма…" />
          ) : rows.length === 0 ? (
            <EmptyState icon="mail" title={query ? "Ничего не нашлось" : "Писем нет"} compact>
              {query ? "Попробуйте другое слово или папку." : "Новые письма появятся здесь — почта проверяется раз в минуту."}
            </EmptyState>
          ) : (
            <div className="mail-list" role="listbox" aria-label="Письма">
              {rows.map((r) => (
                <button
                  key={r.id} type="button" role="option" aria-selected={r.id === openId}
                  className={`mail-item mx-item ${r.id === openId ? "active" : ""} ${r.unread ? "is-unread" : ""}`}
                  onClick={() => open(r.id)}
                >
                  {r.unread && <span className="unread" aria-label="не прочитано" />}
                  <span className="mail-top">
                    <b>{r.direction === "out" ? `Кому: ${r.who}` : r.who || r.from_addr}</b>
                    {r.flagged && <Icon name="flag" size={12} className="mx-flag" />}
                    {r.has_attachments && <Icon name="paperclip" size={12} />}
                    <span className="mail-date">{mailDate(r.date)}</span>
                  </span>
                  <span className="sub">{r.subject || "(без темы)"}</span>
                  <span className="mail-meta">{r.preview}</span>
                </button>
              ))}
              {hasMore && (
                <button type="button" className="ghost mx-more" onClick={loadMore}>Показать ещё</button>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="split-detail" tabIndex={-1}>
        <div className="split-detail-inner mx-detail">
          {openId ? (
            <Thread
              key={openId}
              id={openId}
              onBack={() => open(null)}
              onChanged={changed}
              onGone={() => {
                open(null);
                changed();
              }}
              onCompose={(mode, source) => setCompose({ mode, source })}
            />
          ) : (
            <EmptyState icon="mail" title="Выберите письмо">
              Слева — письма {box ? status.boxes.find((b) => b.name === box)?.address : "всех ящиков фирмы"}.
            </EmptyState>
          )}
        </div>
      </div>
      {compose && (
        <ComposeModal
          mode={compose.mode}
          source={compose.source}
          defaultBox={box}
          onClose={() => setCompose(null)}
          onSent={(msg) => {
            setCompose(null);
            toast.success("Письмо поставлено в отправку", { description: msg.to });
            changed();
          }}
        />
      )}
      {dnsOpen && <DnsModal onClose={() => setDnsOpen(false)} />}
    </div>
  );
}

function mergeFirstPage(prev: MailRow[], first: MailRow[]): MailRow[] {
  const ids = new Set(first.map((r) => r.id));
  return [...first, ...prev.slice(first.length).filter((r) => !ids.has(r.id))];
}

// ------------------------------------------------------------------ выключено
function MailDisabled({ status }: { status: MailStatus }) {
  return (
    <div className="mx-pad">
      <EmptyState icon="mail" title="Почта фирмы ещё не подключена">
        Кабинет читает ящики Timeweb по IMAP и отвечает с них же — MX и сами ящики остаются как есть.
        Впишите на сервере в <code>.env</code> и перезапустите web, worker и beat:
      </EmptyState>
      <pre className="mx-env">{status.env_hint.join("\n")}</pre>
      {status.boxes.some((b) => !b.configured) && (
        <Alert tone="warn">
          Нет пароля для: {status.boxes.filter((b) => !b.configured).map((b) => b.address).join(", ")}
        </Alert>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ переписка
function Thread({
  id, onBack, onChanged, onGone, onCompose,
}: {
  id: number;
  onBack: () => void;
  onChanged: () => void;
  onGone: () => void;
  onCompose: (mode: ComposeMode, source: MailMessage) => void;
}) {
  const [data, setData] = useState<{ message: MailMessage; thread: MailMessage[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    fetchMailThread(id)
      .then((d) => {
        setData(d);
        onChanged();
      })
      .catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(load, [load]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <SkeletonList rows={3} label="Открываю письмо…" />;
  const msg = data.message;

  const act = async (action: string, label: string) => {
    if (action === "delete" && !(await confirmDialog({ title: "Удалить насовсем?", text: "Письмо удалится и из ящика на сервере.", ok: "Удалить", danger: true }))) {
      return;
    }
    setBusy(true);
    try {
      await mailAction(msg.id, action);
      toast.success(label);
      if (["archive", "spam", "trash", "inbox", "delete", "unread"].includes(action)) onGone();
      else load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const inTrash = msg.folder === "trash";
  const away = ["archive", "junk", "trash"].includes(msg.folder);

  return (
    <div className="mx-thread">
      <div className="mx-thread-bar">
        <button type="button" className="ghost mx-back" onClick={onBack}>
          <Icon name="chevronLeft" size={16} /> Письма
        </button>
        <div className="mx-actions">
          <button type="button" className="btn sm" onClick={() => onCompose("reply", msg)} disabled={busy}><Icon name="undo" size={14} /> Ответить</button>
          <button type="button" className="btn sm" onClick={() => onCompose("all", msg)} disabled={busy}>Всем</button>
          <button type="button" className="btn sm" onClick={() => onCompose("forward", msg)} disabled={busy}><Icon name="arrowRight" size={14} /> Переслать</button>
          <span className="mx-sep" />
          <button type="button" className="icon-btn" title={msg.flagged ? "Снять «важное»" : "Важное"} aria-label="Важное" disabled={busy}
            onClick={() => act(msg.flagged ? "unflag" : "flag", msg.flagged ? "Снято «важное»" : "Отмечено важным")}>
            <Icon name="flag" size={16} className={msg.flagged ? "mx-flag" : ""} />
          </button>
          {msg.direction === "in" && (
            <button type="button" className="icon-btn" title="Отметить непрочитанным" aria-label="Непрочитанным" disabled={busy}
              onClick={() => act("unread", "Отмечено непрочитанным")}><Icon name="eye" size={16} /></button>
          )}
          {away ? (
            <button type="button" className="icon-btn" title="Вернуть во входящие" aria-label="Во входящие" disabled={busy}
              onClick={() => act("inbox", "Вернулось во входящие")}><Icon name="inbox" size={16} /></button>
          ) : (
            <button type="button" className="icon-btn" title="В архив" aria-label="В архив" disabled={busy}
              onClick={() => act("archive", "Письмо в архиве")}><Icon name="box" size={16} /></button>
          )}
          {msg.folder !== "junk" && msg.direction === "in" && (
            <button type="button" className="icon-btn" title="Это спам" aria-label="Спам" disabled={busy}
              onClick={() => act("spam", "Перенесено в спам")}><Icon name="ban" size={16} /></button>
          )}
          {inTrash ? (
            <button type="button" className="icon-btn danger" title="Удалить насовсем" aria-label="Удалить насовсем" disabled={busy}
              onClick={() => act("delete", "Письмо удалено")}><Icon name="trash" size={16} /></button>
          ) : (
            <button type="button" className="icon-btn" title="В корзину" aria-label="В корзину" disabled={busy}
              onClick={() => act("trash", "Перенесено в корзину")}><Icon name="trash" size={16} /></button>
          )}
          <button type="button" className="icon-btn" title="Исходник .eml" aria-label="Скачать исходник"
            onClick={() => downloadFile(rawUrl(msg.id), `mail-${msg.id}.eml`).catch((e: Error) => toast.error(e.message))}>
            <Icon name="download" size={16} />
          </button>
        </div>
      </div>
      <h2 className="mx-subject">{msg.subject || "(без темы)"}</h2>
      {data.thread.map((m) => (
        <MessageCard key={m.id} m={m} current={m.id === msg.id} last={m.id === data.thread[data.thread.length - 1].id} />
      ))}
    </div>
  );
}

function MessageCard({ m, current, last }: { m: MailMessage; current: boolean; last: boolean }) {
  const [expanded, setExpanded] = useState(current || last);
  const who = m.from_name || m.from_addr;
  return (
    <article className={`mx-card ${expanded ? "" : "collapsed"} ${m.direction === "out" ? "out" : ""}`}>
      <header className="mx-card-head" onClick={() => setExpanded((v) => !v)}>
        <span className="mx-avatar" aria-hidden="true">{initial(who)}</span>
        <div className="mx-card-who">
          <b>{who}</b> {m.from_name && <span className="muted">&lt;{m.from_addr}&gt;</span>}
          {expanded ? (
            <div className="mx-card-to muted">
              Кому: {m.to}{m.cc ? ` · Копия: ${m.cc}` : ""}{m.sent_by ? ` · отправил(а) ${m.sent_by}` : ""}
            </div>
          ) : (
            <div className="mx-card-to muted">{m.preview}</div>
          )}
        </div>
        <time className="mail-date">{fullDate(m.date)}</time>
      </header>
      {expanded && (
        <>
          {m.sending && m.sending.status !== "sent" && (
            <Alert tone={m.sending.status === "failed" ? "error" : "info"}>
              {m.sending.status === "failed"
                ? `Не отправлено: ${m.sending.error || "сервер отказал"}`
                : m.sending.error
                  ? `Отправляется, повтор (попыток: ${m.sending.attempts}): ${m.sending.error}`
                  : "Отправляется…"}
            </Alert>
          )}
          {m.too_big && (
            <Alert tone="warn">Письмо больше лимита — здесь только заголовки. Целиком — в веб-почте Timeweb.</Alert>
          )}
          {m.has_html ? <HtmlBody m={m} /> : <pre className="mx-text">{m.text}</pre>}
          {m.attachments.length > 0 && (
            <div className="mx-atts">
              {m.attachments.map((a) => (
                <button key={a.n} type="button" className="mx-att"
                  onClick={() => downloadFile(attachmentUrl(m.id, a.n), a.name).catch((e: Error) => toast.error(e.message))}>
                  <Icon name="paperclip" size={14} />
                  <span className="mx-att-name">{a.name}</span>
                  <span className="muted">{humanSize(a.size)}</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </article>
  );
}

/** Высота рамки — по оценке из текста письма: в песочнице без allow-same-origin
 *  документ письма недоступен странице, измерить его нельзя (и не нужно). Рамку
 *  можно растянуть за угол, внутри — своя прокрутка. */
function frameHeight(m: MailMessage): number {
  const lines = (m.text || "").split("\n").reduce((n, line) => n + 1 + Math.floor(line.length / 90), 0);
  return Math.min(Math.max(lines * 22 + 80, 220), 900);
}

/** HTML письма в песочнице: sandbox без allow-scripts и без allow-same-origin —
 *  у документа «пустой» origin, скрипты не запускаются, к кабинету и JWT доступа нет;
 *  allow-popups — чтобы ссылки открывались в новой вкладке. CSP — внутри документа. */
function HtmlBody({ m }: { m: MailMessage }) {
  const [images, setImages] = useState(false);
  const [doc, setDoc] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    fetchMailBody(m.id, images)
      .then((html) => !cancelled && setDoc(html))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [m.id, images]);

  return (
    <>
      {m.remote_images && !images && (
        <div className="mx-images">
          <Icon name="eye" size={14} /> Внешние картинки скрыты — по ним отправитель узнаёт, что письмо открыли.
          <button type="button" className="btn sm" onClick={() => setImages(true)}>Показать картинки</button>
        </div>
      )}
      {error ? (
        <Alert>{error}</Alert>
      ) : doc === null ? (
        <SkeletonList rows={2} label="Загружаю текст…" />
      ) : (
        <div className="mx-frame-wrap" style={{ height: frameHeight(m) }}>
          <iframe
            title="Текст письма"
            className="mx-frame"
            sandbox="allow-popups allow-popups-to-escape-sandbox"
            referrerPolicy="no-referrer"
            srcDoc={doc}
          />
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ новое письмо
function ComposeModal({
  mode, source, defaultBox, onClose, onSent,
}: {
  mode: ComposeMode;
  source?: MailMessage;
  defaultBox: string;
  onClose: () => void;
  onSent: (msg: MailMessage) => void;
}) {
  const [form, setForm] = useState<Compose | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [showCc, setShowCc] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchCompose(source?.id, mode)
      .then((c) => {
        if (mode === "new" && defaultBox && c.boxes.some((b) => b.name === defaultBox)) c.from = defaultBox;
        setForm(c);
        setShowCc(!!c.cc);
      })
      .catch((e: Error) => setError(e.message));
  }, [mode, source?.id, defaultBox]);

  const total = useMemo(
    () => files.reduce((s, f) => s + f.size, 0) + (form?.attachments ?? []).reduce((s, a) => s + a.size, 0),
    [files, form],
  );
  const title = { new: "Новое письмо", reply: "Ответ", all: "Ответ всем", forward: "Пересылка" }[mode];
  const set = (patch: Partial<Compose>) => setForm((f) => (f ? { ...f, ...patch } : f));

  const submit = async () => {
    if (!form) return;
    setSending(true);
    setError("");
    try {
      const msg = await sendMail({
        from: form.from, to: form.to, cc: form.cc, subject: form.subject, text: form.text, files,
        reply: mode === "reply" || mode === "all" ? source?.id : undefined,
        forward: mode === "forward" ? source?.id : undefined,
      });
      onSent(msg);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      size="lg"
      footer={
        <>
          <span className="muted mx-total">{total ? `Вложения: ${humanSize(total)} из 20 МБ` : ""}</span>
          <button type="button" className="ghost" onClick={onClose}>Отмена</button>
          <button type="button" className="primary" onClick={submit} disabled={!form || sending || total > 20 << 20}>
            <Icon name="send" size={15} /> {sending ? "Отправляю…" : "Отправить"}
          </button>
        </>
      }
    >
      {!form ? (
        error ? <Alert>{error}</Alert> : <SkeletonList rows={3} label="Готовлю письмо…" />
      ) : (
        <form className="mx-compose" onSubmit={(e) => { e.preventDefault(); void submit(); }}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void submit(); }}>
          {error && <Alert onClose={() => setError("")}>{error}</Alert>}
          <label>
            <span>От</span>
            <select value={form.from} onChange={(e) => set({ from: e.target.value })}>
              {form.boxes.map((b) => <option key={b.name} value={b.name}>{b.address}</option>)}
            </select>
          </label>
          <label>
            <span>Кому</span>
            <span className="mx-row">
              <input value={form.to} onChange={(e) => set({ to: e.target.value })} placeholder="адрес@пример.ru, …" autoFocus={mode !== "reply"} />
              {!showCc && <button type="button" className="ghost sm" onClick={() => setShowCc(true)}>Копия</button>}
            </span>
          </label>
          {showCc && (
            <label>
              <span>Копия</span>
              <input value={form.cc} onChange={(e) => set({ cc: e.target.value })} />
            </label>
          )}
          <label>
            <span>Тема</span>
            <input value={form.subject} onChange={(e) => set({ subject: e.target.value })} />
          </label>
          <textarea
            className="mx-body"
            value={form.text}
            onChange={(e) => set({ text: e.target.value })}
            rows={14}
            autoFocus={mode === "reply"}
            onFocus={(e) => { if (mode !== "new" && mode !== "forward") e.currentTarget.setSelectionRange(0, 0); }}
          />
          <div className="mx-atts">
            {form.attachments.map((a) => (
              <span key={`src-${a.n}`} className="mx-att static"><Icon name="paperclip" size={14} />
                <span className="mx-att-name">{a.name}</span><span className="muted">{humanSize(a.size)}</span></span>
            ))}
            {files.map((f, i) => (
              <span key={`${f.name}-${i}`} className="mx-att static"><Icon name="paperclip" size={14} />
                <span className="mx-att-name">{f.name}</span><span className="muted">{humanSize(f.size)}</span>
                <button type="button" className="icon-btn" aria-label="Убрать" onClick={() => setFiles((list) => list.filter((_, j) => j !== i))}>
                  <Icon name="x" size={13} />
                </button>
              </span>
            ))}
            <label className="btn sm mx-attach">
              <Icon name="paperclip" size={14} /> Прикрепить
              <input type="file" multiple hidden onChange={(e) => {
                const picked = Array.from(e.target.files ?? []);
                setFiles((list) => [...list, ...picked]);
                e.target.value = "";
              }} />
            </label>
          </div>
          {total > 20 << 20 && <Alert tone="warn">Вложения больше 20 МБ — так много за раз не отправить.</Alert>}
        </form>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ DNS
function DnsModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<MailStatus | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetchMailStatus(true).then(setStatus).catch((e: Error) => setError(e.message));
  }, []);
  return (
    <Modal open onClose={onClose} title="Записи домена" subtitle="Чтобы письма с ящиков не попадали в спам. Ничего не меняем — только проверяем." size="lg">
      {error && <Alert>{error}</Alert>}
      {!status && !error && <SkeletonList rows={4} label="Проверяю DNS…" />}
      {status?.dns?.map((d) => (
        <div key={d.domain} className="mx-dns">
          <h3>{d.domain} {d.ok ? <span className="pill ok">всё на месте</span> : <span className="pill warn">есть что поправить</span>}</h3>
          {d.error && <Alert tone="warn">{d.error}</Alert>}
          <table className="mx-dns-table">
            <tbody>
              {d.records.map((r) => (
                <tr key={`${r.type}-${r.name}`}>
                  <td><Icon name={r.ok ? "checkCircle" : "alert"} size={16} className={r.ok ? "mx-ok" : "mx-bad"} /></td>
                  <td><b>{r.type}</b> <code>{r.name}</code><div className="muted">{r.note}</div></td>
                  <td>
                    <div><span className="muted">нужно:</span> <code>{r.want}</code></div>
                    <div><span className="muted">сейчас:</span> <code>{r.have || "—"}</code></div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <p className="muted">Правятся в панели Timeweb → Домены → DNS. MX менять не нужно: кабинет читает ящики Timeweb по IMAP.</p>
    </Modal>
  );
}
