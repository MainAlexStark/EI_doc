/** Выбор модели как в OpenRouter: поиск по каталогу, контекст, цена, поддержка картинок. */

import { useEffect, useMemo, useRef, useState } from "react";
import type { ModelInfo } from "./api";

type Props = {
  models: ModelInfo[];
  value: string;
  onChange: (id: string) => void;
  compact?: boolean;
};

const FAVORITES_KEY = "ei_doc_assistant_favorite_models";

function loadFavorites(): string[] {
  try {
    return JSON.parse(localStorage.getItem(FAVORITES_KEY) || "[]");
  } catch {
    return [];
  }
}

export function shortModelName(id: string, models: ModelInfo[]): string {
  const found = models.find((m) => m.id === id);
  if (found) return found.name.replace(/^[^:]+:\s*/, "");
  return id.replace(/^(openrouter|yandex):/, "");
}

const price = (v: number | null) => (v == null ? "—" : v === 0 ? "бесплатно" : `$${v < 1 ? v.toFixed(2) : v.toFixed(1)}`);
const ctx = (n: number | null) => (n ? (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}K`) : "");

export default function ModelPicker({ models, value, onChange, compact }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [favorites, setFavorites] = useState<string[]>(loadFavorites);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const toggleFavorite = (id: string) => {
    const next = favorites.includes(id) ? favorites.filter((f) => f !== id) : [...favorites, id];
    setFavorites(next);
    try {
      localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
    } catch {
      /* приватный режим */
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? models.filter((m) => `${m.name} ${m.id}`.toLowerCase().includes(q)) : models;
    const fav = list.filter((m) => favorites.includes(m.id));
    const rest = list.filter((m) => !favorites.includes(m.id));
    return { fav, rest: rest.slice(0, q ? 200 : 80) };
  }, [models, query, favorites]);

  const row = (m: ModelInfo) => (
    <div
      key={m.id}
      className={`mp-row ${m.id === value ? "active" : ""}`}
      onClick={() => {
        onChange(m.id);
        setOpen(false);
      }}
    >
      <div className="mp-main">
        <span className="mp-name">{m.name}</span>
        <span className="mp-id">{m.id}</span>
      </div>
      <div className="mp-meta">
        {m.provider === "yandex" && <span className="badge ru">РФ</span>}
        {m.vision && <span className="badge" title="Видит картинки">👁</span>}
        {m.context_length ? <span title="Контекст">{ctx(m.context_length)}</span> : null}
        {m.price_in != null && <span title="Цена за 1М токенов: вход / выход">{price(m.price_in)} / {price(m.price_out)}</span>}
        <button
          className={`star ${favorites.includes(m.id) ? "on" : ""}`}
          title="В избранное"
          onClick={(e) => {
            e.stopPropagation();
            toggleFavorite(m.id);
          }}
        >
          ★
        </button>
      </div>
    </div>
  );

  return (
    <div className={`model-picker ${compact ? "compact" : ""}`} ref={ref}>
      <button className="mp-current" onClick={() => setOpen((o) => !o)} title={value}>
        {value ? shortModelName(value, models) : "Выберите модель"} ▾
      </button>
      {open && (
        <div className="mp-popover">
          <input autoFocus placeholder="Поиск модели: claude, gpt, gemini, qwen…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <div className="mp-list">
            {models.length === 0 && (
              <div className="muted pad">
                Нет доступных моделей. Задайте OPENROUTER_API_KEY или ключи Yandex AI Studio в .env.
              </div>
            )}
            {filtered.fav.length > 0 && <div className="mp-group">Избранное</div>}
            {filtered.fav.map(row)}
            {filtered.fav.length > 0 && filtered.rest.length > 0 && <div className="mp-group">Все модели</div>}
            {filtered.rest.map(row)}
          </div>
          <div className="mp-foot muted">Цена — за 1 млн токенов (вход / выход). 👁 — понимает фото и сканы.</div>
        </div>
      )}
    </div>
  );
}
