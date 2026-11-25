import { useEffect, useState } from "react";
import { fetchFifCard, type FifCard } from "../api";
import Icon from "../ui/Icon";
import { Alert, Spinner } from "../ui/controls";
import { FifDocumentButtons } from "./FifDocuments";
import type { SiTypeDraft } from "./SiTypeForm";
import { MpiHero } from "./MpiBadge";

function ruDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString("ru-RU") : "";
}

/**
 * Карточка утверждённого типа из Госреестра (ФИФ) — справка для работы: модификации,
 * изготовитель, срок свидетельства, МПИ, методика поверки и описание типа.
 *
 * Ничего не заводит само: «Завести в систему» отдаёт черновик формы типа наружу,
 * а «уже заведено у нас» — ссылки на свои типы с этим номером.
 */
export default function FifTypeCard({
  ident,
  query = "",
  onAdd,
  onOpenLocal,
}: {
  /** mit_uuid или номер в Госреестре. */
  ident: string;
  /** Что искали — по нему форма подберёт модификацию. */
  query?: string;
  onAdd?: (draft: SiTypeDraft) => void;
  onOpenLocal?: (id: number) => void;
}) {
  const [card, setCard] = useState<FifCard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setCard(null);
    setError("");
    fetchFifCard(ident)
      .then((data) => !cancelled && setCard(data))
      .catch((exc) => !cancelled && setError(exc instanceof Error ? exc.message : "Госреестр не ответил"));
    return () => {
      cancelled = true;
    };
  }, [ident]);

  if (error) return <Alert>{error}</Alert>;
  if (!card) return <Spinner />;

  const validity =
    card.is_valid === false ? "истекло" : card.valid_to ? `до ${ruDate(card.valid_to)}` : card.is_valid ? "действует" : "—";

  return (
    <div className="fif-card">
      <MpiHero mpi={card.mpi} />
      {card.title && <p className="hint" style={{ marginTop: 0 }}>{card.title}</p>}
      <div className="kv">
        <div><span className="k">Номер в Госреестре</span><span className="v cell-mono">{card.registry_number}</span></div>
        {card.notation && <div><span className="k">Обозначение</span><span className="v">{card.notation}</span></div>}
        <div><span className="k">Изготовитель</span><span className="v">{card.manufacturer || "не указан"}</span></div>
        <div>
          <span className="k">Свидетельство</span>
          <span className={`v ${card.is_valid === false ? "warn-text" : ""}`}>{validity}</span>
        </div>
        {card.variants.length > 0 && (
          <div>
            <span className="k">Модификации</span>
            <span className="v">{card.variants.join(", ")}</span>
          </div>
        )}
      </div>

      {card.documents.length > 0 && (
        <>
          <div className="field-label">Документы</div>
          <FifDocumentButtons documents={card.documents} />
        </>
      )}

      {card.already_added.length > 0 && (
        <div className="pick-list" style={{ marginTop: 14 }}>
          <div className="pick-head">Уже в системе</div>
          {card.already_added.map((t) => (
            <button type="button" key={t.id} className="pick-item" disabled={!onOpenLocal} onClick={() => onOpenLocal?.(t.id)}>
              <b>{t.name}</b>
              {t.needs_review && <span className="pill muted">ждёт проверки</span>}
            </button>
          ))}
        </div>
      )}

      {onAdd && (
        <div className="actions" style={{ marginTop: 16 }}>
          <button
            type="button"
            className="primary"
            onClick={() =>
              onAdd({
                name: card.notation || card.title,
                registry_number: card.registry_number,
                manufacturer: card.manufacturer,
                interval_months: card.mpi[0]?.months ?? null,
                fif_uuid: card.uuid,
                query,
              })
            }
          >
            <Icon name="plus" size={15} /> {card.already_added.length ? "Завести ещё модификацию" : "Завести в систему"}
          </button>
        </div>
      )}
    </div>
  );
}
