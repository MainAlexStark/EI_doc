/** API режима «Закупки» (apps/logistics/api.py: документы, поиск, выжимка, план заходов). */

import { json, type ProcurementDocument, type TripBrief } from "../../api";

const send = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export type TextStatus = "pending" | "done" | "empty" | "failed";
export type DocumentRow = ProcurementDocument & {
  text_status: TextStatus;
  text_status_display: string;
  text_error: string;
  text_chars: number | null;
  text_extracted_at: string | null;
};

export const fetchDocuments = (procurementId: number) =>
  json<DocumentRow[]>(`/api/procurements/${procurementId}/documents/`);

export type DocumentText = {
  id: number;
  title: string;
  text_status: TextStatus;
  text_error: string;
  total: number;
  offset: number;
  text: string;
  next_offset: number | null;
  page: number | null;
};

export const fetchDocumentText = (id: number, offset = 0, maxChars = 40000) =>
  json<DocumentText>(`/api/procurement-documents/${id}/text/?offset=${offset}&max_chars=${maxChars}`);

export const extractDocument = (id: number) =>
  json<DocumentRow>(`/api/procurement-documents/${id}/extract/`, { method: "POST" });

export type SearchSnippet = { offset: number; page: number | null; text: string };
export type SearchResult = {
  document_id: number;
  title: string;
  kind: string;
  kind_display: string;
  hits: number;
  snippets: SearchSnippet[];
};
export type SearchResponse = {
  query: string;
  results: SearchResult[];
  unread: { id: number; title: string; text_status: TextStatus }[];
};

export const searchDocuments = (procurementId: number, q: string, signal?: AbortSignal) =>
  json<SearchResponse>(`/api/procurements/${procurementId}/search/?q=${encodeURIComponent(q)}`, { signal });

// --- выжимка ---------------------------------------------------------------
export type TextPoint = { text: string; source?: string };
export type SummaryInstrument = {
  name: string;
  type?: string;
  registry_number?: string;
  quantity: number;
  address?: string;
  note?: string;
  source?: string;
};
export type SummaryStage = { title: string; date_from?: string; date_to?: string; note?: string; source?: string };
export type SummaryContact = { name?: string; position?: string; phone?: string; email?: string; note?: string; source?: string };
export type Summary = {
  requirements: TextPoint[];
  instruments: SummaryInstrument[];
  stages: SummaryStage[];
  access: TextPoint[];
  contacts: SummaryContact[];
  penalties: TextPoint[];
  deadline: string;
  notes: string;
};
export type SummaryResponse = {
  procurement_id: number;
  summary: Partial<Summary>;
  saved_by: string;
  saved_at: string | null;
  via_assistant: boolean;
  sources: string[];
  sections: Record<string, string>;
};

export const emptySummary = (): Summary => ({
  requirements: [], instruments: [], stages: [], access: [], contacts: [], penalties: [], deadline: "", notes: "",
});

export const normalizeSummary = (raw: Partial<Summary> | null | undefined): Summary => ({ ...emptySummary(), ...(raw ?? {}) });

export const fetchSummary = (procurementId: number) =>
  json<SummaryResponse>(`/api/procurements/${procurementId}/summary/`);
export const saveSummary = (procurementId: number, summary: Summary) =>
  json<SummaryResponse>(`/api/procurements/${procurementId}/summary/`, send("PUT", { summary }));

// --- план заходов -----------------------------------------------------------
export type PlanStop = { address: string; quantity: number; instruments: string[] };
export type PlanDay = { date: string; quantity: number; stops: PlanStop[] };
export type PlanTrip = { number: number; date_start: string; date_end: string; quantity: number; days: PlanDay[] };
export type VisitPlan = {
  procurement_id: number;
  source: "summary" | "items";
  per_day: number;
  days_per_trip: number;
  instruments_total: number;
  addresses: number;
  trips: PlanTrip[];
  warnings: string[];
  created?: TripBrief[];
};
export type VisitPlanPayload = {
  per_day: number;
  days_per_trip: number;
  start?: string | null;
  apply?: boolean;
  lead_id?: number | null;
  member_ids?: number[];
};

export const planVisits = (procurementId: number, payload: VisitPlanPayload) =>
  json<VisitPlan>(`/api/procurements/${procurementId}/visit-plan/`, send("POST", payload));
