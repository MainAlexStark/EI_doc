/** Режим 7 «Шаблоны поверки СИ» — пошаговый поток:
 *  1) тип в Госреестре (ФИФ) → 2) документы (методика, описание типа, свои файлы) → 3) студия: чат с
 *  ассистентом рядом с живым протоколом → 4) «Продолжить»: проверка метрологом и публикация — по типу
 *  создаются протоколы. Без открытого шаблона — список: фильтр по статусу, поиск, «на проверке».
 *
 *  Действия (ui/modeActions): new, open-type (id типа СИ), open-template (id шаблона поверки),
 *  open-workspace (id рабочего места). Адрес #/si-templates/<id типа СИ> открывает тип сразу. */

import { useCallback, useEffect, useMemo, useState } from "react";
import "./si-templates.css";
import TemplateStudio, { STUDIO_STEPS, type PendingHandoff } from "../../assistant/TemplateStudio";
import { fetchModels, listWorkspaces, openWorkspace, type ModelInfo, type TemplateWorkspace } from "../../assistant/api";
import type { VerificationTemplate } from "../../api";
import { useModeAction } from "../../ui/modeActions";
import { useHotkey } from "../../ui/Hotkeys";
import { toast } from "../../ui/Toast";
import { Alert } from "../../ui/controls";
import { withViewTransition } from "../../ui/transition";
import type { ModeProps } from "../types";
import { allTemplates, dropTemplatesCache } from "./api";
import NewTemplate, { type OpenRequest, type TypePick } from "./NewTemplate";
import ReviewSheet from "./ReviewSheet";
import Stepper from "./Stepper";
import TemplateList, { buildRows, type ListRow } from "./TemplateList";

type View = { kind: "list" } | { kind: "new"; step: 0 | 1; pick: TypePick | null } | { kind: "studio"; id: number };

const HASH = "#/si-templates";
function siTypeFromHash(): number | null {
  const match = window.location.hash.match(/^#\/(?:si-templates|protocol-templates)\/(\d+)/);
  return match ? Number(match[1]) : null;
}

export default function SiTemplatesMode({ isReviewer, go, refreshCounts }: ModeProps) {
  const [view, setViewRaw] = useState<View>({ kind: "list" });
  const [workspaces, setWorkspaces] = useState<TemplateWorkspace[]>([]);
  const [templates, setTemplates] = useState<VerificationTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openError, setOpenError] = useState("");
  const [opening, setOpening] = useState(false);
  const [review, setReview] = useState<number | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [handoffs, setHandoffs] = useState<Record<number, PendingHandoff>>({});

  const setView = (next: View) => withViewTransition(() => setViewRaw(next));

  const load = useCallback(async (fresh = false) => {
    setError("");
    try {
      const [ws, all] = await Promise.all([
        listWorkspaces(),
        allTemplates(fresh).catch(() => [] as VerificationTemplate[]),
      ]);
      setWorkspaces(ws);
      setTemplates(all);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const changed = useCallback(() => {
    dropTemplatesCache();
    void load(true);
    refreshCounts();
  }, [load, refreshCounts]);

  useEffect(() => {
    void load();
    fetchModels()
      .then((d) => {
        setModels(d.models);
        setDefaultModel(d.default);
      })
      .catch(() => undefined);
  }, [load]);

  const open = useCallback(
    async (request: { si_type?: number; fif_uuid?: string; name?: string }, handoff?: PendingHandoff) => {
      setOpening(true);
      setOpenError("");
      try {
        const ws = await openWorkspace(request);
        if (handoff && (handoff.files.length || handoff.note)) setHandoffs((h) => ({ ...h, [ws.id]: handoff }));
        setReview(null);
        setView({ kind: "studio", id: ws.id });
        void load(true);
        refreshCounts();
      } catch (e) {
        const message = (e as Error).message;
        setOpenError(message);
        toast.error("Не удалось открыть шаблон", { description: message });
      } finally {
        setOpening(false);
      }
    },
    [load, refreshCounts],
  );

  // Пришли по ссылке «Создать шаблон протокола» для конкретного типа (с экрана поверки, из справочника).
  useEffect(() => {
    const fromHash = () => {
      const siType = siTypeFromHash();
      if (siType) {
        window.history.replaceState(null, "", HASH);
        void open({ si_type: siType });
      }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, [open]);

  const startNew = () => {
    setOpenError("");
    setView({ kind: "new", step: 0, pick: null });
  };

  useModeAction("si-templates", {
    new: startNew,
    "open-type": (id) => typeof id === "number" && void open({ si_type: id }),
    "open-template": (id) => typeof id === "number" && setReview(id),
    "open-workspace": (id) => typeof id === "number" && setView({ kind: "studio", id }),
  });

  useHotkey("esc", () => setView({ kind: "list" }), { enabled: view.kind === "new" && !opening, description: "К списку шаблонов", group: "Шаблоны поверки" });

  const rows = useMemo(() => buildRows(workspaces, templates), [workspaces, templates]);

  const openRow = (row: ListRow) => {
    if (row.workspace) setView({ kind: "studio", id: row.workspace.id });
    else if (row.template) setReview(row.template.id);
  };

  let body;
  if (view.kind === "studio") {
    body = (
      <TemplateStudio
        key={view.id}
        id={view.id}
        models={models}
        defaultModel={defaultModel}
        isReviewer={isReviewer}
        handoff={handoffs[view.id] ?? null}
        onHandoffDone={() => setHandoffs((h) => {
          const next = { ...h };
          delete next[view.id];
          return next;
        })}
        onChanged={changed}
        onBack={() => setView({ kind: "list" })}
        onStepBack={() => setView({ kind: "new", step: 0, pick: null })}
        go={go}
      />
    );
  } else if (view.kind === "new") {
    body = (
      <div className="si-page">
        <Stepper
          steps={STUDIO_STEPS}
          current={view.step}
          onPick={(i) => setView({ kind: "new", step: i as 0 | 1, pick: view.pick })}
        />
        {openError && <Alert onClose={() => setOpenError("")}>Не удалось открыть: {openError}</Alert>}
        <NewTemplate
          workspaces={workspaces}
          step={view.step}
          pick={view.pick}
          onStep={(step) => setView({ kind: "new", step, pick: view.pick })}
          onPick={(pick) => setViewRaw({ kind: "new", step: view.step, pick })}
          onOpenWorkspace={(id) => setView({ kind: "studio", id })}
          onStart={(request: OpenRequest) =>
            void open({ si_type: request.si_type, fif_uuid: request.fif_uuid, name: request.name }, { files: request.files, note: request.note })
          }
          opening={opening}
        />
        <div className="si-page-foot">
          <button type="button" className="link" onClick={() => setView({ kind: "list" })}>Отмена — к списку шаблонов</button>
        </div>
      </div>
    );
  } else {
    body = (
      <div className="si-page">
        <TemplateList
          rows={rows}
          loading={loading}
          error={error}
          isReviewer={isReviewer}
          onRetry={() => {
            setLoading(true);
            void load(true);
          }}
          onNew={startNew}
          onOpen={openRow}
        />
      </div>
    );
  }

  return (
    <div className="si-mode">
      {body}
      <ReviewSheet
        templateId={review}
        isReviewer={isReviewer}
        onClose={() => setReview(null)}
        onChanged={changed}
        onOpenStudio={(siTypeId) => void open({ si_type: siTypeId })}
      />
    </div>
  );
}
