import { useState } from "react";
import type { Turn } from "@/api/client";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

export interface TurnNavigatorProps {
  turns: Turn[];
  currentTurnId: string | null;
  onSelect: (turnId: string) => void;
}

function turnSummary(turn: Turn, status: string): string {
  const finalText = turn.result?.final_text ? String(turn.result.final_text).trim() : "";
  return finalText || status;
}

/** Compact turn rail which expands into a readable conversation index. */
export function TurnNavigator({ turns, currentTurnId, onSelect }: TurnNavigatorProps) {
  const { t } = useI18n();
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  if (turns.length === 0) return null;
  const matchedCurrentIndex = turns.findIndex((turn) => turn.id === currentTurnId);
  const currentIndex = matchedCurrentIndex >= 0 ? matchedCurrentIndex : turns.length - 1;

  const lineStyle = (index: number) => {
    const focusIndex = hoveredIndex ?? currentIndex;
    const distance = Math.abs(index - focusIndex);
    if (hoveredIndex === null) {
      return {
        width: "8px",
        opacity: Math.max(0.16, [1, 0.56, 0.36, 0.25, 0.19][distance] ?? 0.16),
      };
    }
    return {
      width: `${[32, 24, 17, 12, 9][distance] ?? 7}px`,
      opacity: Math.max(0.15, [1, 0.62, 0.4, 0.28, 0.2][distance] ?? 0.15),
    };
  };

  return (
    <nav
      className="group absolute left-1.5 top-1/2 z-30 hidden -translate-y-1/2 md:block"
      aria-label={t("conversation.turnNavigator")}
      data-testid="turn-navigator"
      onMouseLeave={() => setHoveredIndex(null)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHoveredIndex(null);
      }}
    >
      <div className="relative flex max-h-[62vh] items-center">
        <div className="relative z-10 flex w-10 flex-col items-center gap-1.5 rounded-full py-3">
          {turns.map((turn, index) => {
            const active = turn.id === currentTurnId;
            return (
              <button
                key={turn.id}
                type="button"
                className="flex h-2.5 w-10 items-center justify-start pl-1.5"
                onClick={() => onSelect(turn.id)}
                onMouseEnter={() => setHoveredIndex(index)}
                onFocus={() => setHoveredIndex(index)}
                aria-label={t("conversation.turnJump", { index: index + 1 })}
                aria-current={active ? "location" : undefined}
              >
                <span
                  className="turn-rail-line shrink-0"
                  style={lineStyle(index)}
                  data-testid={`turn-tick-${turn.id}`}
                />
              </button>
            );
          })}
        </div>

        <div className="pointer-events-none absolute left-10 top-1/2 w-[min(25rem,calc(100vw-22rem))] -translate-x-1 -translate-y-1/2 opacity-0 transition-[opacity,transform] duration-normal group-hover:pointer-events-auto group-hover:translate-x-0 group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:translate-x-0 group-focus-within:opacity-100">
          <div className="glass-panel max-h-[62vh] overflow-y-auto rounded-[16px] border p-2 shadow-md">
            <p className="px-2 pb-1.5 pt-1 text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
              {t("conversation.turnNavigator")}
            </p>
            <div className="space-y-1">
              {turns.map((turn, index) => {
                const active = turn.id === currentTurnId;
                const status = t(turn.active ? "status.running" : turn.state === "error" ? "status.error" : turn.state === "interrupted" ? "status.interrupted" : "status.completed");
                return (
                  <button
                    key={turn.id}
                    type="button"
                    className={cx(
                      "block w-full rounded-[11px] px-3 py-2.5 text-left transition-colors",
                      active ? "bg-accent-muted" : "hover:bg-surface-2/70"
                    )}
                    onClick={() => onSelect(turn.id)}
                    onMouseEnter={() => setHoveredIndex(index)}
                    onFocus={() => setHoveredIndex(index)}
                    data-testid={`turn-overview-${turn.id}`}
                  >
                    <span className="block truncate text-[13px] font-medium text-text">
                      {turn.user_text || t("conversation.turnJump", { index: index + 1 })}
                    </span>
                    <span className="mt-1 block overflow-hidden text-ellipsis whitespace-nowrap text-[11px] text-faint">
                      {turnSummary(turn, status)}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </nav>
  );
}
