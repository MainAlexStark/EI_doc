/** Виджеты, которые ассистент показывает в чате (инструмент ui_show и protocol_draft). */

import { useMemo, useState } from "react";
import type { Widget } from "./api";

type Props = { widget: Widget; onSend: (text: string) => void };

const str = (v: unknown) => (v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

export default function WidgetView({ widget, onSend }: Props) {
  const title = widget.title ? <div className="w-title">{str(widget.title)}</div> : null;
  switch (widget.type) {
    case "table":
      return <div className="widget">{title}<TableWidget columns={arr<string>(widget.columns)} rows={arr<unknown[]>(widget.rows)} /></div>;
    case "cards":
      return <div className="widget">{title}<Cards items={arr<Record<string, unknown>>(widget.items)} onSend={onSend} /></div>;
    case "kv":
      return (
        <div className="widget">
          {title}
          <dl className="w-kv">
            {arr<Record<string, unknown>>(widget.rows).map((row, i) => (
              <div key={i}>
                <dt>{str(row.label)}</dt>
                <dd>
                  {str(row.value)}
                  {row.source ? <span className="w-src">{str(row.source)}</span> : null}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      );
    case "chart":
      return <div className="widget">{title}<Chart widget={widget} /></div>;
    case "steps":
      return (
        <div className="widget">
          {title}
          <ol className="w-steps">
            {arr<Record<string, unknown>>(widget.items).map((item, i) => (
              <li key={i} className={`st-${str(item.status) || "todo"}`}>
                <b>{str(item.title)}</b>
                {item.detail ? <span>{str(item.detail)}</span> : null}
              </li>
            ))}
          </ol>
        </div>
      );
    case "choices":
      return (
        <div className="widget">
          {widget.question ? <div className="w-title">{str(widget.question)}</div> : title}
          <div className="w-choices">
            {arr<unknown>(widget.options).map((option, i) => (
              <button key={i} onClick={() => onSend(str(option))}>{str(option)}</button>
            ))}
          </div>
        </div>
      );
    case "callout":
      return (
        <div className={`widget w-callout tone-${str(widget.tone) || "info"}`}>
          {title}
          <div>{str(widget.text)}</div>
        </div>
      );
    case "compare":
      return (
        <div className="widget">
          {title}
          <div className="w-table-wrap">
            <table className="w-table">
              <thead>
                <tr>
                  <th />
                  {arr<string>(widget.columns).map((c, i) => <th key={i}>{str(c)}</th>)}
                </tr>
              </thead>
              <tbody>
                {arr<Record<string, unknown>>(widget.rows).map((row, i) => (
                  <tr key={i} className={row.diff ? "diff" : ""}>
                    <th>{str(row.label)}</th>
                    {arr<unknown>(row.values).map((v, j) => <td key={j}>{str(v)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    case "facts":
      return <div className="widget">{title}<Facts rows={arr<Record<string, string>>(widget.rows)} /></div>;
    case "html":
      return (
        <div className="widget">
          {title}
          <iframe
            className="w-html"
            sandbox="allow-scripts"
            srcDoc={str(widget.html)}
            style={{ height: Number(widget.height) || 360 }}
            title={str(widget.title) || "виджет"}
          />
        </div>
      );
    default:
      return <pre className="widget">{JSON.stringify(widget, null, 2)}</pre>;
  }
}

function TableWidget({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const [filter, setFilter] = useState("");
  const shown = useMemo(() => {
    let out = rows.map((r) => (Array.isArray(r) ? r : [r]));
    if (filter) {
      const q = filter.toLowerCase();
      out = out.filter((r) => r.some((c) => str(c).toLowerCase().includes(q)));
    }
    if (sort) {
      out = [...out].sort((a, b) => {
        const x = str(a[sort.col]);
        const y = str(b[sort.col]);
        const nx = Number(x.replace(",", "."));
        const ny = Number(y.replace(",", "."));
        const cmp = !Number.isNaN(nx) && !Number.isNaN(ny) ? nx - ny : x.localeCompare(y, "ru");
        return cmp * sort.dir;
      });
    }
    return out;
  }, [rows, sort, filter]);

  const copyCsv = () => {
    const lines = [columns, ...shown].map((r) => r.map((c) => `"${str(c).replace(/"/g, '""')}"`).join(";"));
    void navigator.clipboard?.writeText(lines.join("\n"));
  };

  return (
    <>
      {rows.length > 8 && (
        <div className="w-tools">
          <input placeholder="Фильтр…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <span className="muted">{shown.length} из {rows.length}</span>
          <button className="link" onClick={copyCsv}>Копировать для Excel</button>
        </div>
      )}
      <div className="w-table-wrap">
        <table className="w-table">
          <thead>
            <tr>
              {columns.map((c, i) => (
                <th
                  key={i}
                  onClick={() => setSort((s) => (s?.col === i ? { col: i, dir: s.dir === 1 ? -1 : 1 } : { col: i, dir: 1 }))}
                >
                  {str(c)} {sort?.col === i ? (sort.dir === 1 ? "▲" : "▼") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i}>
                {columns.map((_, j) => <td key={j}>{str(row[j])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Cards({ items, onSend }: { items: Record<string, unknown>[]; onSend: (text: string) => void }) {
  return (
    <div className="w-cards">
      {items.map((item, i) => {
        const fields = (item.fields && typeof item.fields === "object" ? item.fields : {}) as Record<string, unknown>;
        return (
          <div className="w-card" key={i}>
            <div className="w-card-head">
              <b>{str(item.title)}</b>
              {item.badge ? <span className="badge">{str(item.badge)}</span> : null}
            </div>
            {item.subtitle ? <div className="muted">{str(item.subtitle)}</div> : null}
            {Object.keys(fields).length > 0 && (
              <dl className="w-kv small">
                {Object.entries(fields).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{str(v)}</dd>
                  </div>
                ))}
              </dl>
            )}
            <div className="w-card-actions">
              {item.url ? <a href={str(item.url)} target="_blank" rel="noreferrer">Открыть ↗</a> : null}
              <button className="link" onClick={() => onSend(str(item.choose_text) || `Выбираю: ${str(item.title)}`)}>
                Выбрать
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  confirmed: "✅ подтверждено",
  derived: "🔎 выведено",
  missing: "❓ нет данных",
};

function Facts({ rows }: { rows: Record<string, string>[] }) {
  const missing = rows.filter((r) => r.status === "missing").length;
  return (
    <>
      {missing > 0 && <div className="w-callout tone-warn">Не заполнено: {missing}. Без этих данных протокол не подписывать.</div>}
      <div className="w-table-wrap">
        <table className="w-table">
          <thead>
            <tr><th>Поле</th><th>Значение</th><th>Откуда</th><th>Статус</th></tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={`fact-${r.status}`}>
                <td>{r.field}</td>
                <td>{r.value || "—"}</td>
                <td>{r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.source || r.url}</a> : r.source}</td>
                <td className="nowrap">{STATUS_LABEL[r.status] ?? r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

const PALETTE = ["#0e6e75", "#c77d1f", "#5b5fc7", "#2c6b4c", "#9e362e", "#6e7a7e"];

function Chart({ widget }: { widget: Widget }) {
  const labels = arr<unknown>(widget.labels).map(str);
  const series = arr<{ name?: string; values?: unknown[] }>(widget.series).map((s) => ({
    name: str(s.name),
    values: arr<unknown>(s.values).map((v) => Number(String(v).replace(",", ".")) || 0),
  }));
  const kind = widget.kind === "line" ? "line" : "bar";
  const W = 640;
  const H = 240;
  const pad = { l: 44, r: 12, t: 12, b: 42 };
  const all = series.flatMap((s) => s.values);
  const max = Math.max(0, ...all);
  const min = Math.min(0, ...all);
  const span = max - min || 1;
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - (v - min) / span);
  const step = (W - pad.l - pad.r) / Math.max(labels.length, 1);
  const ticks = [min, min + span / 2, max];
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-chart" role="img">
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end">{Number(t.toFixed(2))}</text>
          </g>
        ))}
        {labels.map((l, i) => (
          <text key={i} x={pad.l + step * (i + 0.5)} y={H - pad.b + 16} textAnchor="middle">
            {l.length > 12 ? `${l.slice(0, 11)}…` : l}
          </text>
        ))}
        {series.map((s, si) =>
          kind === "bar" ? (
            s.values.map((v, i) => {
              const bw = (step * 0.7) / series.length;
              const x = pad.l + step * i + step * 0.15 + bw * si;
              return <rect key={`${si}-${i}`} x={x} width={bw} y={Math.min(y(v), y(0))} height={Math.abs(y(v) - y(0))} fill={PALETTE[si % PALETTE.length]} rx={2}><title>{`${s.name} ${labels[i]}: ${v}`}</title></rect>;
            })
          ) : (
            <polyline
              key={si}
              fill="none"
              stroke={PALETTE[si % PALETTE.length]}
              strokeWidth={2}
              points={s.values.map((v, i) => `${pad.l + step * (i + 0.5)},${y(v)}`).join(" ")}
            />
          ),
        )}
      </svg>
      {series.length > 1 && (
        <div className="w-legend">
          {series.map((s, i) => (
            <span key={i}><i style={{ background: PALETTE[i % PALETTE.length] }} />{s.name}</span>
          ))}
        </div>
      )}
      {widget.unit ? <div className="muted small">{str(widget.unit)}</div> : null}
    </div>
  );
}
