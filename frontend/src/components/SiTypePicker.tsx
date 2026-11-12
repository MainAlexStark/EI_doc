import { useEffect, useState } from "react";
import type { SiType } from "../api";
import { rememberSiType } from "../offline/sync";
import FifTypeSearch from "./FifTypeSearch";
import SiTypeForm, { type SiTypeDraft } from "./SiTypeForm";

export type PickedType = { id: number; label: string; needs_review?: boolean };

/**
 * Выбор типа СИ — общий для экрана поверок и сверки скана.
 *
 * В одном списке — заведённые типы и карточки из Госреестра (ФИФ): нажатие на
 * карточку — и тип заводится прямо здесь, поля заполняются из реестра, а шаблон
 * поверки агент готовит в фоне. Без связи ищем по локальной копии справочника.
 */
export default function SiTypePicker({
  value,
  onChange,
  initialQuery = "",
  initialDraft = null,
  inputId = "si-type",
  onDraftChange,
}: {
  value: PickedType | null;
  onChange: (picked: PickedType | null) => void;
  initialQuery?: string;
  /** Сразу открыть заведение типа с этими данными (тип известен из прошлой поверки, а у нас его нет). */
  initialDraft?: SiTypeDraft | null;
  inputId?: string;
  /** Идёт заведение нового типа — внешняя форма прячет свои кнопки, чтобы не путать. */
  onDraftChange?: (drafting: boolean) => void;
}) {
  const [draft, setDraft] = useState<SiTypeDraft | null>(initialDraft);

  useEffect(() => {
    onDraftChange?.(draft !== null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const handleCreated = (siType: SiType) => {
    void rememberSiType(siType);
    setDraft(null);
    onChange({ id: siType.id, label: siType.label, needs_review: siType.needs_review });
  };

  if (value) {
    return (
      <div className="field">
        <label>Тип счётчика</label>
        <div className="picked">
          <span>
            {value.label}
            {value.needs_review && <span className="pill warn">ждёт проверки метролога</span>}
          </span>
          <button
            type="button"
            onClick={() => onChange(null)}
          >
            Другой
          </button>
        </div>
      </div>
    );
  }

  if (draft) {
    return (
      <SiTypeForm
        title="Новый тип счётчика"
        initial={draft}
        onSaved={handleCreated}
        onCancel={() => setDraft(null)}
      />
    );
  }

  return (
    <FifTypeSearch
      label="Тип счётчика"
      inputId={inputId}
      initialQuery={initialQuery}
      onPickLocal={(item) => onChange({ id: item.si_type_id!, label: item.label, needs_review: item.needs_review })}
      onPickFif={setDraft}
    />
  );
}
