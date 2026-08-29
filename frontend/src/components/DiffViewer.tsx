import { Fragment, memo, useMemo, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

export interface DiffViewerProps {
  lines: string[];
  className?: string;
  mode?: "diff" | "text";
  filePath?: string;
  /** Complete after-snapshot used only to reveal folded unchanged ranges. */
  fullLines?: string[];
}

type CodeRow = {
  key: string;
  kind: "add" | "del" | "ctx" | "meta";
  text: string;
  oldLine?: number;
  newLine?: number;
};

type FoldRow = {
  key: string;
  kind: "fold";
  count: number;
  oldStart: number;
  newStart: number;
};

type ViewerRow = CodeRow | FoldRow;

const HUNK_HEADER = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;
const SYNTAX_PATTERN = /(<!--[\s\S]*?-->|\/\*.*?\*\/|\/\/.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|<\/?[A-Za-z][A-Za-z0-9:-]*|\b(?:const|let|var|function|return|if|else|for|while|switch|case|break|continue|class|interface|type|extends|implements|import|from|export|default|async|await|try|catch|finally|throw|new|def|lambda|yield|with|as|in|is|not|and|or|True|False|None|public|private|protected|static|void|int|float|str|bool|self|this|DOCTYPE)\b|\b(?:0x[\da-fA-F]+|\d+(?:\.\d+)?)\b|\b[A-Za-z_:][A-Za-z0-9_:.-]*(?=\s*[=:]))/gm;

function syntaxClass(token: string): string {
  if (token.startsWith("//") || token.startsWith("/*") || token.startsWith("<!--")) return "syntax-comment";
  if (/^["'`]/.test(token)) return "syntax-string";
  if (/^<\/?/.test(token)) return "syntax-tag";
  if (/^(?:0x[\da-fA-F]+|\d)/.test(token)) return "syntax-number";
  if (/^(?:const|let|var|function|return|if|else|for|while|switch|case|break|continue|class|interface|type|extends|implements|import|from|export|default|async|await|try|catch|finally|throw|new|def|lambda|yield|with|as|in|is|not|and|or|True|False|None|public|private|protected|static|void|int|float|str|bool|self|this|DOCTYPE)$/.test(token)) return "syntax-keyword";
  return "syntax-attribute";
}

function highlightLine(text: string, filePath: string): ReactNode[] {
  if (/\.(?:py|rb|sh|ya?ml|toml)$/i.test(filePath) && text.trimStart().startsWith("#")) {
    return [<span key="comment" className="syntax-comment">{text}</span>];
  }
  const pattern = new RegExp(SYNTAX_PATTERN.source, SYNTAX_PATTERN.flags);
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    nodes.push(<span key={key++} className={syntaxClass(match[0])}>{match[0]}</span>);
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function buildDiffRows(lines: string[], fullLines?: string[]): ViewerRow[] {
  const rows: ViewerRow[] = [];
  let oldCursor = 1;
  let newCursor = 1;
  let foldIndex = 0;

  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    if (raw.startsWith("---") || raw.startsWith("+++")) continue;
    const hunk = raw.match(HUNK_HEADER);
    if (hunk) {
      const oldStart = Number(hunk[1]);
      const newStart = Number(hunk[3]);
      const hidden = Math.max(0, newStart - newCursor);
      if (hidden > 0) {
        rows.push({ key: `fold-${foldIndex++}`, kind: "fold", count: hidden, oldStart: oldCursor, newStart: newCursor });
      }
      oldCursor = oldStart;
      newCursor = newStart;
      continue;
    }

    if (raw.startsWith("+") && !raw.startsWith("+++")) {
      rows.push({ key: `line-${index}`, kind: "add", text: raw.slice(1), newLine: newCursor });
      newCursor += 1;
    } else if (raw.startsWith("-") && !raw.startsWith("---")) {
      rows.push({ key: `line-${index}`, kind: "del", text: raw.slice(1), oldLine: oldCursor });
      oldCursor += 1;
    } else if (raw.startsWith(" ")) {
      rows.push({ key: `line-${index}`, kind: "ctx", text: raw.slice(1), oldLine: oldCursor, newLine: newCursor });
      oldCursor += 1;
      newCursor += 1;
    } else {
      rows.push({ key: `line-${index}`, kind: "meta", text: raw });
    }
  }

  if (fullLines && newCursor <= fullLines.length) {
    rows.push({ key: `fold-${foldIndex}`, kind: "fold", count: fullLines.length - newCursor + 1, oldStart: oldCursor, newStart: newCursor });
  }
  return rows;
}

/** Read-only, syntax-coloured line diff with collapsible unchanged ranges. */
export const DiffViewer = memo(function DiffViewer({ lines, className, mode = "diff", filePath = "", fullLines }: DiffViewerProps) {
  const { t } = useI18n();
  const [expandedFolds, setExpandedFolds] = useState<Set<string>>(() => new Set());
  const rows = useMemo<ViewerRow[]>(() => mode === "diff"
    ? buildDiffRows(lines, fullLines)
    : lines.map((text, index) => ({ key: `text-${index}`, kind: "ctx", text, newLine: index + 1 })), [fullLines, lines, mode]);

  const toggleFold = (key: string) => {
    setExpandedFolds((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const renderCodeRow = (row: CodeRow) => (
    <tr
      key={row.key}
      className={cx(
        "border-l-2",
        row.kind === "add" && "border-success bg-success-muted",
        row.kind === "del" && "border-danger bg-danger-muted",
        row.kind === "ctx" && "border-transparent",
        row.kind === "meta" && "border-transparent bg-surface-2 text-faint"
      )}
      data-line-kind={row.kind}
    >
      <td className={cx("w-5 select-none px-1 text-center", row.kind === "add" && "text-success", row.kind === "del" && "text-danger", row.kind !== "add" && row.kind !== "del" && "text-faint")}>
        {row.kind === "add" ? "+" : row.kind === "del" ? "−" : ""}
      </td>
      {mode === "diff" ? <td className="w-10 select-none border-r border-border/60 px-1.5 text-right tabular-nums text-faint">{row.oldLine ?? ""}</td> : null}
      <td className="w-10 select-none border-r border-border/60 px-1.5 text-right tabular-nums text-faint">{row.newLine ?? ""}</td>
      <td className="whitespace-pre px-2.5 py-px text-text"><code>{highlightLine(row.text, filePath)}</code></td>
    </tr>
  );

  return (
    <div className={cx("overflow-auto rounded-md border border-border bg-surface text-[12px] leading-5", className)} data-testid="diff-viewer">
      <table className="w-full border-collapse font-mono">
        <tbody>
          {rows.map((row) => {
            if (row.kind !== "fold") return renderCodeRow(row);
            const expanded = expandedFolds.has(row.key);
            const hiddenLines = fullLines?.slice(row.newStart - 1, row.newStart - 1 + row.count) ?? [];
            return (
              <Fragment key={row.key}>
                <tr className="bg-surface-2/80">
                  <td colSpan={mode === "diff" ? 4 : 3}>
                    <button
                      type="button"
                      className="flex h-8 w-full items-center gap-2 px-2 text-left text-[11px] text-muted transition-colors hover:bg-accent-muted hover:text-text"
                      onClick={() => toggleFold(row.key)}
                      aria-expanded={expanded}
                      disabled={hiddenLines.length === 0}
                    >
                      <ChevronRight aria-hidden size={13} className={cx("transition-transform", expanded && "rotate-90")} />
                      <span>{t("preview.unmodifiedLines", { count: row.count })}</span>
                    </button>
                  </td>
                </tr>
                {expanded ? hiddenLines.map((text, index) => renderCodeRow({
                  key: `${row.key}-expanded-${index}`,
                  kind: "ctx",
                  text,
                  oldLine: row.oldStart + index,
                  newLine: row.newStart + index,
                })) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
});
