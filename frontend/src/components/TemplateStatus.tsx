import { useCallback, useEffect, useState } from "react";
import { fetchTemplateJobs, startTemplateJob, type SiType, type TemplateJob } from "../api";
import Icon from "../ui/Icon";

const OPEN = new Set(["queued", "running"]);

/** Время попытки — чтобы повторный запуск с тем же итогом не выглядел как «ничего не произошло». */
function attemptTime(iso: string): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return d.toLocaleString("ru-RU", sameDay ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
    : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/**
 * Шаблон поверки нового типа — что сделал агент.
 *
 * После заведения типа из Госреестра EI_doc сам ставит агенту фоновую задачу: методика из ФГИС +
 * «Перечень применяемых эталонов» → черновик шаблона поверки (у ИО — и аттестации). Здесь видно,
 * идёт ли работа, что получилось, и можно попросить переделать с пожеланием.
 */
export default function TemplateStatus({ siType }: { siType: SiType }) {
  const [job, setJob] = useState<TemplateJob | null>(siType.template_job ?? null);
  const [comment, setComment] = useState("");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const templates = siType.templates ?? [];

  const refresh = useCallback(async () => {
    try {
      const jobs = await fetchTemplateJobs(siType.id);
      setJob(jobs[0] ?? null);
    } catch {
      /* статус — не главное на форме, тихо оставим прежний */
    }
  }, [siType.id]);

  useEffect(() => {
    if (!job || !OPEN.has(job.status)) return;
    const timer = setInterval(() => void refresh(), 8000);
    return () => clearInterval(timer);
  }, [job, refresh]);

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      setJob(await startTemplateJob(siType.id, comment.trim()));
      setAsking(false);
      setComment("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось запустить ассистента");
    } finally {
      setBusy(false);
    }
  };

  const running = job && OPEN.has(job.status);
  const template = job?.verification_template;
  const program = job?.attestation_program;

  return (
    <div className="fif-panel">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <b>
          <Icon name="sparkle" size={14} /> Шаблон поверки
        </b>
        {job && (
          <span className={`pill ${job.status === "done" ? "ok" : job.status === "failed" ? "warn" : "muted"}`}>
            {running ? "ассистент готовит…" : job.status_display}
          </span>
        )}
      </div>

      {templates.length > 0 ? (
        templates.map((t) => (
          <div className="sub" key={t.id}>
            {t.own ? "Свой шаблон" : "Общий шаблон семейства"}: «{t.title}»
            {t.needs_review ? " — ждёт проверки метролога («Шаблоны поверки»)" : " — проверен"}
            {t.kits.length > 0 ? `. Эталоны: ${t.kits.join("; ")}` : ". Эталоны из перечня не выбраны"}
          </div>
        ))
      ) : (
        !running && job?.status !== "skipped" && <div className="sub">Шаблона поверки для этого типа пока нет.</div>
      )}

      {running && (
        <div className="sub">
          Ассистент читает методику поверки и перечень эталонов — обычно несколько минут. Можно закрыть окно: черновик
          появится сам, а ход работы виден во вкладке «Ассистент».
        </div>
      )}
      {job && !running && job.summary && <blockquote style={{ whiteSpace: "pre-wrap" }}>{job.summary}</blockquote>}
      {job && !running && job.error && (
        <div className="hint warn-text">
          {job.error}
          {job.finished_at && <> (попытка {attemptTime(job.finished_at)})</>}
        </div>
      )}
      {!running && (template || program) && (
        <div className="sub">
          {template && <>Черновик поверки: «{template.title}». </>}
          {program && <>Черновик аттестации: «{program.title}» (вкладка «Аттестация ИО»).</>}
        </div>
      )}

      {!running && (
        asking ? (
          <div className="field" style={{ marginTop: 8 }}>
            <label htmlFor={`tj-${siType.id}`}>Что поправить (необязательно)</label>
            <textarea
              id={`tj-${siType.id}`}
              rows={3}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="например: добавь точку 25 % ВПИ; для КТ 2,5 бери набор с МО-05М"
            />
            <div className="actions">
              <button type="button" className="primary sm" disabled={busy} onClick={() => void start()}>
                {busy ? "Запускаю…" : "Отдать ассистенту"}
              </button>
              <button type="button" className="sm" onClick={() => setAsking(false)}>Отмена</button>
            </div>
          </div>
        ) : (
          <button type="button" className="link" onClick={() => setAsking(true)}>
            {template || templates.length > 0 ? "Переделать шаблон с пожеланием" : "Попросить ассистента сделать шаблон"}
          </button>
        )
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
