import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, RotateCcw, Trash2, Zap } from "lucide-react";
import { api } from "@/api/client";
import type { InboxItem, InboxSnapshot } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";

export interface QueueDockProps {
  conversationId: string;
  snapshot?: InboxSnapshot | null;
}

const ACTIVE_STATES = new Set(["queued", "steer_pending", "claimed", "blocked"]);
const WINDOW_SIZE = 50;

export function QueueDock({ conversationId, snapshot }: QueueDockProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [windowStart, setWindowStart] = useState(0);
  const items = useMemo(
    () => (snapshot?.items ?? []).filter((item) => ACTIVE_STATES.has(item.state)),
    [snapshot]
  );
  const version = snapshot?.queue_version ?? 1;

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["inbox", conversationId] });
  };

  const removeMutation = useMutation({
    mutationFn: (item: InboxItem) =>
      api.removeInbox(conversationId, item.id, { expected_version: item.version }),
    onSettled: invalidate,
  });
  const steerMutation = useMutation({
    mutationFn: (item: InboxItem) =>
      api.steerInbox(conversationId, item.id, { expected_version: item.version }),
    onSettled: invalidate,
  });
  const retryMutation = useMutation({
    mutationFn: (item: InboxItem) =>
      api.retryInbox(conversationId, item.id, { expected_version: item.version }),
    onSettled: invalidate,
  });
  const editMutation = useMutation({
    mutationFn: ({ item, content }: { item: InboxItem; content: string }) =>
      api.editInbox(conversationId, item.id, {
        content,
        expected_version: item.version,
      }),
    onSettled: invalidate,
  });
  const reorderMutation = useMutation({
    mutationFn: (orderedIds: string[]) =>
      api.reorderInbox(conversationId, {
        ordered_ids: orderedIds,
        expected_queue_version: version,
      }),
    onSettled: invalidate,
  });

  if (items.length === 0) return null;

  const maxWindowStart = Math.max(0, Math.floor((items.length - 1) / WINDOW_SIZE) * WINDOW_SIZE);
  const currentStart = expanded ? Math.min(windowStart, maxWindowStart) : 0;

  const move = (index: number, direction: -1 | 1) => {
    const next = [...items];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    reorderMutation.mutate(next.map((item) => item.id));
  };

  const visible = expanded
    ? items.slice(currentStart, currentStart + WINDOW_SIZE)
    : items.slice(0, 3);
  const extraCount = items.length - visible.length;

  return (
    <div className="mb-2 rounded-lg border border-border bg-surface-2/60 p-2" data-testid="queue-dock">
      <div className="flex items-center justify-between gap-2 px-1">
        <button
          type="button"
          className="text-xs font-medium text-muted"
          onClick={() => {
            setExpanded((value) => !value);
            setWindowStart(0);
          }}
          aria-expanded={expanded}
        >
          {t("queue.title", { count: items.length })}
        </button>
        <span className="text-xs text-faint">{t("queue.hint")}</span>
      </div>
      <ul className="mt-1 space-y-1">
        {visible.map((item, localIndex) => {
          const index = currentStart + localIndex;
          return (
          <li
            key={item.id}
            className={cx(
              "flex items-center gap-2 rounded-md border border-border/60 bg-surface px-2 py-1.5",
              item.state === "steer_pending" && "border-accent/40 bg-accent-muted/30"
            )}
            data-testid={`queue-item-${item.id}`}
          >
            <span className="w-5 shrink-0 text-center text-xs text-faint">{index + 1}</span>
            <button
              type="button"
              className="min-w-0 flex-1 truncate text-left text-sm"
              title={t("queue.edit")}
              onClick={() => {
                const content = window.prompt(t("queue.editPrompt"), item.content);
                if (content && content.trim()) {
                  editMutation.mutate({ item, content: content.trim() });
                }
              }}
            >
              {item.content}
            </button>
            <span
              className={cx(
                "shrink-0 rounded px-1.5 py-0.5 text-[10px]",
                item.state === "steer_pending"
                  ? "bg-accent-muted text-accent"
                  : item.state === "claimed"
                    ? "bg-warning-muted text-warning"
                  : item.state === "blocked"
                    ? "bg-danger-muted text-danger"
                    : "bg-surface-2 text-muted"
              )}
            >
              {item.state === "steer_pending"
                ? t("queue.steerPending")
                : item.state === "claimed"
                  ? t("queue.claimed")
                : item.state === "blocked"
                  ? t("queue.blocked")
                  : t("queue.queued")}
            </span>
            <button
              type="button"
              className="btn-icon"
              onClick={() => move(index, -1)}
              disabled={index === 0}
              aria-label={t("queue.moveUp")}
            >
              <ChevronUp aria-hidden size={13} />
            </button>
            <button
              type="button"
              className="btn-icon"
              onClick={() => move(index, 1)}
              disabled={index === items.length - 1}
              aria-label={t("queue.moveDown")}
            >
              <ChevronDown aria-hidden size={13} />
            </button>
            {item.state !== "steer_pending" ? (
              <button
                type="button"
                className="btn-icon text-accent"
                onClick={() => steerMutation.mutate(item)}
                aria-label={t("queue.steer")}
              >
                <Zap aria-hidden size={13} />
              </button>
            ) : (
              <Zap aria-hidden size={13} className="text-accent" />
            )}
            {item.state === "blocked" ? (
              <button
                type="button"
                className="btn-icon text-warning"
                onClick={() => retryMutation.mutate(item)}
                aria-label={t("queue.retry")}
              >
                <RotateCcw aria-hidden size={13} />
              </button>
            ) : null}
            <button
              type="button"
              className="btn-icon text-danger"
              onClick={() => {
                if (window.confirm(t("queue.deleteConfirm"))) {
                  removeMutation.mutate(item);
                }
              }}
              aria-label={t("queue.delete")}
            >
              <Trash2 aria-hidden size={13} />
            </button>
          </li>
          );
        })}
      </ul>
      {expanded && items.length > WINDOW_SIZE ? (
        <div className="mt-1 flex items-center justify-between gap-2">
          <button
            type="button"
            className="text-xs text-muted"
            onClick={() => setWindowStart((start) => Math.max(0, start - WINDOW_SIZE))}
            disabled={currentStart === 0}
            aria-label={t("queue.previousWindow")}
          >
            {t("queue.previousWindow")}
          </button>
          <span className="text-xs text-faint">
            {t("queue.window", { start: currentStart + 1, end: currentStart + visible.length, count: items.length })}
          </span>
          <button
            type="button"
            className="text-xs text-muted"
            onClick={() => setWindowStart((start) => Math.min(maxWindowStart, start + WINDOW_SIZE))}
            disabled={currentStart >= maxWindowStart}
            aria-label={t("queue.nextWindow")}
          >
            {t("queue.nextWindow")}
          </button>
        </div>
      ) : null}
      {extraCount > 0 || expanded ? (
        <button
          type="button"
          className="mt-1 w-full text-center text-xs text-muted"
          onClick={() => {
            setExpanded((value) => !value);
            setWindowStart(0);
          }}
        >
          {expanded ? t("queue.showLess") : t("queue.showMore", { count: extraCount })}
        </button>
      ) : null}
    </div>
  );
}
