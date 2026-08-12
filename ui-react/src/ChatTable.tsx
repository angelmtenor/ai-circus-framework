import { useState } from "react";

export type ChatTableRow = Record<string, string | number | null>;

/**
 * Interactive table for the render_table generative-UI action (see
 * chatGenerativeUi.tsx) — sortable/scrollable, unlike the plain-text markdown
 * tables markdown.tsx already renders for prose replies.
 */
export function ChatTable({ title, columns, rows }: { title?: string; columns: string[]; rows: ChatTableRow[] }) {
  const [sort, setSort] = useState<{ column: string; dir: 1 | -1 } | null>(null);

  const sortedRows = sort
    ? [...rows].sort((a, b) => {
        const av = a[sort.column];
        const bv = b[sort.column];
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * sort.dir;
        return String(av ?? "").localeCompare(String(bv ?? "")) * sort.dir;
      })
    : rows;

  function toggleSort(column: string) {
    setSort((s) => (s?.column === column ? { column, dir: s.dir === 1 ? -1 : 1 } : { column, dir: 1 }));
  }

  return (
    <div className="chat-table-wrap">
      {title && <div className="chat-table-title">{title}</div>}
      <table className="chat-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col} onClick={() => toggleSort(col)}>
                {col}
                {sort?.column === col && <span>{sort.dir === 1 ? " ▲" : " ▼"}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col}>{row[col] ?? ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
