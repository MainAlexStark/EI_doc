/** API ассистента-метролога (/api/assistant/…). */

import { ApiError, call, json } from "../api";

export type Source = { kind: string; title: string; url?: string; ref?: string };
export type FileRef = { id: number; name: string; size: number; mime: string };

export type Action = {
  id: number;
  kind: string;
  title: string;
  payload: Record<string, unknown>;
  sources: string[];
  status: "pending" | "done" | "rejected" | "failed";
  result: Record<string, unknown>;
  error: string;
  decided_at: string | null;
};

export type Widget = { type: string; title?: string; [key: string]: unknown };

export type MessageUi = {
  label?: string;
  summary?: string;
  error?: boolean;
  args?: Record<string, unknown>;
  sources?: Source[];
  widgets?: Widget[];
  files?: FileRef[];
  action?: Action | null;
};

export type Attachment = {
  id: number;
  name: string;
  mime: string;
  size: number;
  origin: "user" | "agent";
  chars: number;
  extract_error: string;
  is_image: boolean;
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant" | "tool" | "event";
  content: string;
  created_at: string;
  tool_calls: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id: string;
  tool_name: string;
  ui: MessageUi;
  model: string;
  usage: Record<string, number>;
  run: number | null;
  attachments: Attachment[];
};

export type RunState = {
  id: number;
  status: string;
  activity: string;
  error: string;
  model: string;
  usage: Record<string, number>;
  active: boolean;
};

export type Conversation = {
  id: number;
  title: string;
  model: string;
  pinned: boolean;
  updated_at: string;
  created_at: string;
};

export type ConversationDetail = {
  conversation: Conversation;
  messages: ChatMessage[];
  active_run: RunState | null;
  last_run: RunState | null;
  partial: string | null;
};

export type ModelInfo = {
  id: string;
  name: string;
  provider: string;
  context_length: number | null;
  vision: boolean;
  price_in: number | null;
  price_out: number | null;
};

export const listConversations = (q = "") =>
  json<Conversation[]>(`/api/assistant/conversations/${q ? `?q=${encodeURIComponent(q)}` : ""}`);
export const createConversation = (model?: string) =>
  json<Conversation>("/api/assistant/conversations/", { method: "POST", body: JSON.stringify({ model }) });
export const getConversation = (id: number) => json<ConversationDetail>(`/api/assistant/conversations/${id}/`);
export const updateConversation = (id: number, patch: Partial<Conversation>) =>
  json<Conversation>(`/api/assistant/conversations/${id}/`, { method: "PATCH", body: JSON.stringify(patch) });
export const deleteConversation = (id: number) =>
  json<void>(`/api/assistant/conversations/${id}/`, { method: "DELETE" });

export const sendMessage = (id: number, content: string, attachment_ids: number[], model: string) =>
  json<{ message: ChatMessage; run: RunState }>(`/api/assistant/conversations/${id}/messages/`, {
    method: "POST",
    body: JSON.stringify({ content, attachment_ids, model }),
  });
export const regenerate = (id: number, model: string) =>
  json<{ run: RunState }>(`/api/assistant/conversations/${id}/regenerate/`, {
    method: "POST",
    body: JSON.stringify({ model }),
  });
export const getRun = (id: number, after: number) =>
  json<{ run: RunState; messages: ChatMessage[]; partial: string | null }>(`/api/assistant/runs/${id}/?after=${after}`);
export const cancelRun = (id: number) => json<RunState>(`/api/assistant/runs/${id}/cancel/`, { method: "POST" });
export const decideAction = (id: number, decision: "confirm" | "reject") =>
  json<Action>(`/api/assistant/actions/${id}/${decision}/`, { method: "POST" });

export async function uploadAttachment(file: File, conversation?: number): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  if (conversation) form.append("conversation", String(conversation));
  return json<Attachment>("/api/assistant/attachments/", { method: "POST", body: form });
}

async function blobOf(path: string): Promise<Blob> {
  const response = await call(path);
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* не JSON */
    }
    throw new ApiError(detail, response.status);
  }
  return response.blob();
}

const blobCache = new Map<string, Promise<string>>();

/** URL картинки/файла с авторизацией (через blob:). */
export function authedUrl(path: string): Promise<string> {
  if (!blobCache.has(path)) {
    blobCache.set(path, blobOf(path).then((blob) => URL.createObjectURL(blob)));
  }
  return blobCache.get(path)!;
}

export async function openFile(path: string, name: string, download = false): Promise<void> {
  const blob = await blobOf(path);
  const url = URL.createObjectURL(blob);
  if (download || !/pdf|image|text/.test(blob.type)) {
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } else {
    window.open(url, "_blank");
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export const attachmentPath = (id: number) => `/api/assistant/attachments/${id}/download/`;

export const fetchModels = () =>
  json<{ default: string; providers: Record<string, boolean>; models: ModelInfo[]; notice?: string }>("/api/assistant/models/");

// --- Контекст ---------------------------------------------------------------
export type KnowledgeFile = {
  id: number;
  title: string;
  category: string;
  category_label: string;
  description: string;
  scope: "company" | "personal";
  mode: "always" | "search" | "off";
  original_name: string;
  mime: string;
  chars: number;
  chunks: number | null;
  extract_error: string;
  updated_at: string;
  uploaded_by: string;
  can_edit: boolean;
  has_file: boolean;
  text_preview?: string;
};

export type KnowledgeListing = {
  files: KnowledgeFile[];
  categories: { value: string; label: string }[];
  always_chars: number;
  always_budget: number;
  can_edit_company: boolean;
};

export const fetchKnowledge = () => json<KnowledgeListing>("/api/assistant/knowledge/");
export const getKnowledgeFile = (id: number) => json<KnowledgeFile>(`/api/assistant/knowledge/${id}/`);
export async function uploadKnowledge(
  file: File | null,
  fields: { title?: string; category: string; mode: string; scope: string; description?: string; text?: string },
): Promise<KnowledgeFile> {
  const form = new FormData();
  if (file) form.append("file", file);
  Object.entries(fields).forEach(([key, value]) => value && form.append(key, value));
  return json<KnowledgeFile>("/api/assistant/knowledge/", { method: "POST", body: form });
}
export const updateKnowledge = (id: number, patch: Partial<KnowledgeFile> & { text?: string }) =>
  json<KnowledgeFile>(`/api/assistant/knowledge/${id}/`, { method: "PATCH", body: JSON.stringify(patch) });
export const deleteKnowledge = (id: number) => json<void>(`/api/assistant/knowledge/${id}/`, { method: "DELETE" });
export const searchKnowledge = (q: string) =>
  json<{ file_id: number; file: string; chunk: number; section: string; score: number; text: string }[]>(
    `/api/assistant/knowledge/search/?q=${encodeURIComponent(q)}`,
  );

export type MemoryItem = {
  id: number;
  scope: "company" | "personal";
  text: string;
  updated_at: string;
  source_conversation: number | null;
  can_edit: boolean;
};
export const fetchMemory = () => json<{ items: MemoryItem[]; can_edit_company: boolean }>("/api/assistant/memory/");
export const createMemory = (text: string, scope: string) =>
  json<MemoryItem>("/api/assistant/memory/", { method: "POST", body: JSON.stringify({ text, scope }) });
export const updateMemory = (id: number, text: string) =>
  json<MemoryItem>(`/api/assistant/memory/${id}/`, { method: "PATCH", body: JSON.stringify({ text }) });
export const deleteMemory = (id: number) => json<void>(`/api/assistant/memory/${id}/`, { method: "DELETE" });

export type McpServer = {
  id: number;
  name: string;
  title: string;
  url: string;
  description: string;
  enabled: boolean;
  disabled_tools: string[];
  has_auth: boolean;
};
export const fetchTools = () =>
  json<{
    servers: McpServer[];
    builtin: { name: string; label: string; description: string }[];
    web_search: boolean;
    can_edit: boolean;
  }>("/api/assistant/mcp/");
export const createMcp = (body: { name: string; url: string; title?: string; token?: string; description?: string }) =>
  json<McpServer>("/api/assistant/mcp/", { method: "POST", body: JSON.stringify(body) });
export const updateMcp = (id: number, patch: Partial<McpServer> & { token?: string }) =>
  json<McpServer>(`/api/assistant/mcp/${id}/`, { method: "PATCH", body: JSON.stringify(patch) });
export const deleteMcp = (id: number) => json<void>(`/api/assistant/mcp/${id}/`, { method: "DELETE" });
export const fetchMcpTools = (id: number, refresh = false) =>
  json<{ ok: boolean; error?: string; tools: { name: string; title: string; description: string; enabled: boolean }[] }>(
    `/api/assistant/mcp/${id}/tools/${refresh ? "?refresh=1" : ""}`,
  );

export type AssistantSettings = {
  default_model: string;
  extra_instructions: string;
  max_steps: number;
  always_budget_chars: number;
  can_edit: boolean;
};
export const fetchSettings = () => json<AssistantSettings>("/api/assistant/settings/");
export const updateSettings = (patch: Partial<AssistantSettings>) =>
  json<AssistantSettings>("/api/assistant/settings/", { method: "PATCH", body: JSON.stringify(patch) });
export const fetchUsage = () =>
  json<{ since: string; rows: { model: string; calls: number; tokens: number; cost: number }[] }>(
    "/api/assistant/usage/",
  );
