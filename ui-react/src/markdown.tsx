/**
 * Minimal markdown-ish renderer for chat replies — headers, bold/inline-code, fenced
 * code blocks, bullet/numbered lists, pipe tables, and links. No remark/react-markdown
 * dependency for a handful of block types the assistant/rag-agent LLMs already write
 * naturally. Rich charts/tables are real generative UI now (see chatGenerativeUi.tsx),
 * not a markdown convention — this renderer no longer special-cases a ```chart fence.
 */

import type { ReactNode } from "react";

const LINK_PATTERN = /\[([^\]]+)\]\(([^)\s]+)\)/g;

/** Only http(s)/mailto — never render a javascript: (or other) URI as a clickable href. */
function isSafeUrl(url: string): boolean {
  return /^(https?:|mailto:)/i.test(url) || url.startsWith("/") || url.startsWith("#");
}

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g).filter(Boolean);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={i}>{part.slice(1, -1)}</code>;
    }
    const link = LINK_PATTERN.exec(part);
    LINK_PATTERN.lastIndex = 0; // stateful global regex — reset after each use
    if (link && isSafeUrl(link[2])) {
      return (
        <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer">
          {link[1]}
        </a>
      );
    }
    return part;
  });
}

function renderTable(lines: string[], key: number): ReactNode {
  const rows = lines.map((line) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim()),
  );
  const [header, ...rest] = rows;
  const body = rest.filter((row) => !row.every((cell) => /^:?-+:?$/.test(cell)));
  return (
    <table className="chat-table" key={key}>
      <thead>
        <tr>
          {header.map((cell, i) => (
            <th key={i}>{renderInline(cell)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j}>{renderInline(cell)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Consecutive list items starting at `start`; an indented, non-marker line right
 * after an item continues it (markdown's lazy continuation), so a bullet hard-wrapped
 * across several lines stays one bullet instead of spilling into a paragraph. */
function collectListItems(lines: string[], start: number, marker: RegExp): { items: string[]; next: number } {
  const items: string[] = [];
  let i = start;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    if (marker.test(trimmed)) items.push(trimmed.replace(marker, ""));
    else if (items.length > 0 && trimmed !== "" && /^\s/.test(lines[i]) && !/^[-*]\s+|^\d+\.\s+/.test(trimmed)) items[items.length - 1] += ` ${trimmed}`;
    else break;
    i++;
  }
  return { items, next: i };
}

export function renderMarkdown(text: string): ReactNode {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i++;
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)/.exec(line.trim());
    if (heading) {
      const Tag = `h${heading[1].length}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      blocks.push(<Tag key={key++}>{renderInline(heading[2])}</Tag>);
      i++;
      continue;
    }

    if (line.trim().startsWith("```")) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      blocks.push(
        <pre className="chat-code" key={key++}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    if (line.trim().startsWith("|")) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        tableLines.push(lines[i]);
        i++;
      }
      blocks.push(
        <div className="chat-table-wrap" key={key++}>
          {renderTable(tableLines, key)}
        </div>,
      );
      continue;
    }

    if (/^[-*]\s+/.test(line.trim())) {
      const items = collectListItems(lines, i, /^[-*]\s+/);
      i = items.next;
      blocks.push(
        <ul key={key++}>
          {items.items.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\d+\.\s+/.test(line.trim())) {
      const items = collectListItems(lines, i, /^\d+\.\s+/);
      i = items.next;
      blocks.push(
        <ol key={key++}>
          {items.items.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^[-*]\s+|^\d+\.\s+|^```|^\||^#{1,6}\s+/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    blocks.push(<p key={key++}>{renderInline(paraLines.join(" "))}</p>);
  }

  return <>{blocks}</>;
}
