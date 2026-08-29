import { useQuery } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { api } from "@/api/client";
import { useI18n } from "@/lib/i18n";

export function MemoryUsageSummary({
  conversationId,
  turnId,
  onOpenSource,
}: {
  conversationId: string;
  turnId: string;
  onOpenSource?: (conversationId: string, turnId?: string | null) => void;
}) {
  const { t } = useI18n();
  const usage = useQuery({
    queryKey: ["memory-usage", conversationId, turnId],
    queryFn: () => api.getTurnMemoryUsage(conversationId, turnId),
    staleTime: 30_000,
  });
  const items = usage.data ?? [];
  if (items.length === 0) return null;
  return (
    <section className="mt-2 rounded-md border border-border/70 bg-surface-2/50 p-2 text-xs" data-testid="memory-usage-summary">
      <div className="flex items-center gap-1.5 font-medium text-muted">
        <BookOpen aria-hidden size={13} />
        <span>{t("memory.usageTitle")}</span>
      </div>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={`${item.turn_id}-${item.entry_id}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-muted">
            <span className="text-text">{item.title || (item.kind ? t(`memory.kind.${item.kind}`) : item.entry_id.slice(0, 8))}</span>
            {item.kind ? <span className="rounded bg-surface-2 px-1.5 py-0.5">{t(`memory.kind.${item.kind}`)}</span> : null}
            {item.scope_type ? <span className="rounded bg-surface-2 px-1.5 py-0.5">{t(`memory.scope.${item.scope_type}`)}</span> : null}
            {item.source_conversation_id ? (
              <button
                type="button"
                className="truncate underline decoration-dotted underline-offset-2 hover:text-text"
                title={`${item.source_conversation_id}/${item.source_turn_id ?? ""}`}
                onClick={() => onOpenSource?.(item.source_conversation_id!, item.source_turn_id)}
              >
                {t("memory.source")} {item.source_conversation_id.slice(0, 8)}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
