import { useCallback, useEffect, useState } from "react";
import {
  arshinSubmissionAction,
  createArshinSubmission,
  downloadFile,
  fetchArshinQueue,
  fetchArshinSubmission,
  fetchArshinSubmissions,
  setArshinItemResult,
  type ArshinItem,
  type ArshinQueueRow,
  type ArshinSubmission,
} from "../api";

const dateRu = (value: string | null) => (value ? new Date(value).toLocaleDateString("ru-RU") : "—");

/**
 * Отправка в Аршин: готовые протоколы → пакет XML → загрузка в ЛК ФИФ (человеком)
 * → сверка с открытым реестром забирает номера записей сама (раз в два часа
 * или по кнопке). Отказ ФИФ по строке возвращает протокол в очередь.
 */
export default function Arshin() {
  const [queue, setQueue] = useState<ArshinQueueRow[]>([]);
  const [cipher, setCipher] = useState("");
  const [subs, setSubs] = useState<ArshinSubmission[]>([]);
  const [open, setOpen] = useState<ArshinSubmission | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const [q, list] = await Promise.all([fetchArshinQueue(), fetchArshinSubmissions()]);
      setQueue(q.protocols);
      setCipher(q.sign_cipher);
      setSubs(list);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось загрузить очередь Аршин");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не получилось");
    } finally {
      setBusy(false);
    }
  };

  const ready = queue.filter((row) => row.problems.length === 0);
  const blocked = queue.filter((row) => row.problems.length > 0);

  const build = () =>
    run(async () => {
      const result = await createArshinSubmission(ready.map((row) => row.protocol));
      setMessage(
        `Пакет №${result.submission.id}: ${result.submission.total} протоколов` +
          (result.refused.length ? `, не вошло ${result.refused.length}` : ""),
      );
      setOpen(result.submission);
      await load();
    });

  const act = (sub: ArshinSubmission, action: "uploaded" | "cancel" | "reconcile") =>
    run(async () => {
      const result = await arshinSubmissionAction(sub.id, action);
      const fresh = result.submission ?? result;
      if (action === "reconcile") setMessage(`Сверка: опубликовано ${result.accepted ?? 0}, ждут ${result.waiting ?? 0}`);
      setOpen(fresh);
      await load();
    });

  const resolveItem = (item: ArshinItem, accepted: boolean) =>
    run(async () => {
      if (accepted) {
        const number = window.prompt(`Номер записи в ФИФ для ${item.number}`, "");
        if (!number) return;
        await setArshinItemResult(item.id, { accepted: true, fif_record_number: number });
      } else {
        const reason = window.prompt(`Почему ФИФ отклонил ${item.number}?`, "");
        if (reason === null) return;
        await setArshinItemResult(item.id, { accepted: false, reason });
      }
      if (open) setOpen(await fetchArshinSubmission(open.id));
      await load();
    });

  return (
    <div className="arshin">
      {error && <div className="error">{error}</div>}
      {message && <div className="notice"><span>{message}</span></div>}
      {!cipher && (
        <div className="notice">
          <span>Не задан шифр знака поверки (ARSHIN_SIGN_CIPHER в .env) — пакет не соберётся.</span>
        </div>
      )}

      <div className="scope">
        <h3>Готово к отправке: {ready.length}</h3>
        <div className="who">Подписанные протоколы, которых ещё нет в ФИФ</div>
        <div className="actions">
          <button className="primary" disabled={busy || ready.length === 0} onClick={() => void build()}>
            Собрать пакет XML
          </button>
        </div>
        {blocked.length > 0 && (
          <div className="breaks">
            Не готовы ({blocked.length}):
            <ul>
              {blocked.slice(0, 30).map((row) => (
                <li key={row.protocol}>
                  {row.number} · {row.si_name} № {row.serial_number} — {row.problems.join("; ")}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="scopes">
        {subs.map((sub) => (
          <div className="scope" key={sub.id}>
            <h3>
              Пакет №{sub.id} · {sub.status_display}
            </h3>
            <div className="who">
              {dateRu(sub.created_at)} · {sub.total} шт.
              {sub.uploaded_at ? ` · загружен ${dateRu(sub.uploaded_at)}` : ""}
              {sub.reconciled_at ? ` · сверка ${dateRu(sub.reconciled_at)}` : ""}
            </div>
            <div className="counts">
              {sub.counts.pending ? <span className="pill muted">в пакете: {sub.counts.pending}</span> : null}
              {sub.counts.sent ? <span className="pill muted">ждут ФИФ: {sub.counts.sent}</span> : null}
              {sub.counts.accepted ? <span className="pill ok">опубликовано: {sub.counts.accepted}</span> : null}
              {sub.counts.rejected ? <span className="pill">отклонено: {sub.counts.rejected}</span> : null}
            </div>
            <div className="actions">
              <button disabled={busy} onClick={() => void run(() => downloadFile(`/api/arshin/submissions/${sub.id}/xml/`, `arshin-${sub.id}.xml`))}>
                Скачать XML
              </button>
              {sub.status === "generated" && (
                <>
                  <button className="primary" disabled={busy} onClick={() => void act(sub, "uploaded")}>
                    Загружен в ЛК ФИФ
                  </button>
                  <button disabled={busy} onClick={() => void act(sub, "cancel")}>
                    Отменить
                  </button>
                </>
              )}
              {sub.status === "uploaded" && (
                <button disabled={busy} onClick={() => void act(sub, "reconcile")}>
                  Сверить с ФИФ
                </button>
              )}
              <button disabled={busy} onClick={() => void run(async () => setOpen(open?.id === sub.id ? null : await fetchArshinSubmission(sub.id)))}>
                {open?.id === sub.id ? "Скрыть" : "Строки"}
              </button>
            </div>
            {open?.id === sub.id && open.items && (
              <div className="preview">
                <ul>
                  {open.items.map((item) => (
                    <li key={item.id}>
                      {item.number} · {item.si_name} № {item.serial_number} — {item.status_display}
                      {item.fif_record_number ? ` · ${item.fif_record_number}` : ""}
                      {item.error ? ` · ${item.error}` : ""}
                      {item.status === "sent" && (
                        <>
                          {" "}
                          <button className="link" disabled={busy} onClick={() => void resolveItem(item, true)}>принят</button>
                          {" "}
                          <button className="link" disabled={busy} onClick={() => void resolveItem(item, false)}>отклонён</button>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
