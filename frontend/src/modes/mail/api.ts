/** API панели «Почта» (/api/mail/…, apps.mailbox). */

import { ApiError, call, json, queryString, statusText } from "../../api";

export type MailFolder = "inbox" | "sent" | "archive" | "junk" | "trash" | "unread" | "flagged";

export type MailBox = {
  name: string;
  address: string;
  configured: boolean;
  unread: number;
  synced_at: string | null;
  error: string;
};

export type DnsRecord = { type: string; name: string; want: string; have: string; ok: boolean; note: string };
export type DnsCheck = { domain: string; records: DnsRecord[]; ok: boolean; error: string };

export type MailStatus = {
  enabled: boolean;
  boxes: MailBox[];
  unread: number;
  env_hint: string[];
  dns?: DnsCheck[];
};

export type MailRow = {
  id: number;
  box: string;
  folder: string;
  direction: "in" | "out";
  thread: number;
  who: string;
  from_addr: string;
  subject: string;
  preview: string;
  date: string;
  received_at: string;
  unread: boolean;
  flagged: boolean;
  has_attachments: boolean;
};

export type MailAttachment = { n: number; name: string; type: string; size: number };

export type MailMessage = MailRow & {
  from_name: string;
  to: string;
  cc: string;
  reply_to: string;
  text: string;
  has_html: boolean;
  remote_images: boolean;
  too_big: boolean;
  size: number;
  message_id: string;
  attachments: MailAttachment[];
  sending: null | { status: "pending" | "sent" | "failed"; error: string; attempts: number; sent_at: string | null };
  sent_by: string;
};

export type Compose = {
  boxes: { name: string; address: string }[];
  from: string;
  to: string;
  cc: string;
  subject: string;
  text: string;
  attachments: MailAttachment[];
};

export type ComposeMode = "new" | "reply" | "all" | "forward";

export const fetchMailStatus = (dns = false) => json<MailStatus>(`/api/mail/status/${dns ? "?dns=1" : ""}`);

export const syncMail = () => json<{ queued: boolean }>("/api/mail/sync/", { method: "POST" });

export const fetchMailList = (params: { box?: string; folder: MailFolder; q?: string; page?: number }) =>
  json<{ results: MailRow[]; page: number; has_more: boolean }>(`/api/mail/messages/?${queryString(params)}`);

export const fetchMailThread = (id: number) =>
  json<{ message: MailMessage; thread: MailMessage[] }>(`/api/mail/messages/${id}/`);

export const mailAction = (id: number, action: string) =>
  json<MailRow | undefined>(`/api/mail/messages/${id}/action/`, { method: "POST", body: JSON.stringify({ action }) });

export async function fetchMailBody(id: number, images: boolean): Promise<string> {
  const response = await call(`/api/mail/messages/${id}/body/${images ? "?images=1" : ""}`);
  if (!response.ok) throw new ApiError(statusText(response.status), response.status);
  return response.text();
}

export const fetchCompose = (reply?: number, mode?: ComposeMode) =>
  json<Compose>(`/api/mail/compose/?${queryString({ reply, mode: mode === "new" ? undefined : mode })}`);

export function sendMail(form: {
  from: string; to: string; cc: string; subject: string; text: string; files: File[]; reply?: number; forward?: number;
}) {
  const body = new FormData();
  for (const key of ["from", "to", "cc", "subject", "text"] as const) body.append(key, form[key]);
  if (form.reply) body.append("reply", String(form.reply));
  if (form.forward) body.append("forward", String(form.forward));
  for (const file of form.files) body.append("files", file, file.name);
  return json<MailMessage>("/api/mail/send/", { method: "POST", body });
}

export const attachmentUrl = (id: number, n: number) => `/api/mail/messages/${id}/att/${n}/`;
export const rawUrl = (id: number) => `/api/mail/messages/${id}/raw/`;
