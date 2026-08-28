import { memo } from "react";
import { cx } from "@/lib/format";

export interface DiffViewerProps {
  lines: string[];
  className?: string;
  mode?: "diff" | "text";
}

/**
 * Read-only lightweight unified diff renderer. Lines are rendered as text
 * nodes; no HTML injection is possible because React escapes text content.
 */
export const DiffViewer = memo(function DiffViewer({ lines, className, mode = "diff" }: DiffViewerProps) {
  return (
    <div
      className={cx("overflow-auto rounded-md border border-border bg-surface text-xs", className)}
      data-testid="diff-viewer"
    >
      <table className="w-full border-collapse font-mono">
        <tbody>
          {lines.map((line, index) => {
            const kind = mode === "diff" && line.startsWith("+") && !line.startsWith("+++") ? "add" : mode === "diff" && line.startsWith("-") && !line.startsWith("---") ? "del" : "ctx";
            return (
              <tr key={`${index}-${line}`} className={cx(kind === "add" && "bg-success/10", kind === "del" && "bg-danger/10")}>
                <td className="select-none border-r border-border/60 px-2 text-right text-faint">
                  {index + 1}
                </td>
                <td className="whitespace-pre px-2 text-text">{line}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
});
