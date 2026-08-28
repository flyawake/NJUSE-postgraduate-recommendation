import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleStop, Send } from "lucide-react";
import { api } from "@/api/client";
import type { ChangeSet, FileChange, ToolEvent, Turn } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { ActivityFeed } from "./ActivityFeed";
import { InlineError } from "./InlineError";
import { TurnChangeSummary } from "./TurnChangeSummary";
import { subscribeToConversationEvents } from "@/lib/sse";

export interface ConversationViewProps {
  conversationId: string;
  onOpenArtifact?: (turn: Turn, file: FileChange, files: FileChange[]) => void;
}

function draftKey(conversationId: string): string {
  return `coding-agent-conversation-draft:${conversationId}`;
}

function scrollKey(conversationId: string): string {
  return `coding-agent-conversation-scroll:${conversationId}`;
}

function followKey(conversationId: string): string {
  return `coding-agent-conversation-follow:${conversationId}`;
}

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

export function mergeEventTail(
  current: ToolEvent[],
  incoming: ToolEvent[],
  limit = 2_000
): ToolEvent[] {
  const byId = new Map<number, ToolEvent>();
  for (const item of current) byId.set(item.id, item);
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => a.id - b.id).slice(-limit);
}

export function ConversationView({ conversationId, onOpenArtifact }: ConversationViewProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followingRef = useRef(true);
  const [task, setTask] = useState(() => {
    try { return sessionStorage.getItem(draftKey(conversationId)) ?? ""; } catch { return ""; }
  });

  useEffect(() => {
    try { sessionStorage.setItem(draftKey(conversationId), task); } catch { /* best effort */ }
  }, [conversationId, task]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    try {
      followingRef.current = sessionStorage.getItem(followKey(conversationId)) !== "0";
      element.scrollTop = Number(sessionStorage.getItem(scrollKey(conversationId)) ?? 0);
    } catch { /* best effort */ }
  }, [conversationId]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element || typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(() => {
      if (!followingRef.current) return;
      requestAnimationFrame(() => { element.scrollTop = element.scrollHeight; });
    });
    observer.observe(element, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [conversationId]);

  const conversationQuery = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api.getConversation(conversationId),
    refetchInterval: 1_000,
  });
  const profilesQuery = useQuery({
    queryKey: ["profiles"],
    queryFn: api.listProfiles,
    staleTime: 5_000,
  });
  const turnsQuery = useQuery({
    queryKey: ["turns", conversationId],
    queryFn: () => api.listTurns(conversationId, { limit: 100 }),
    refetchInterval: (query) => query.state.data?.items.some((turn) => turn.active) ? 500 : false,
  });
  const turns = useMemo(() => turnsQuery.data?.items ?? [], [turnsQuery.data]);
  const terminalKey = turns.filter((turn) => !turn.active).map((turn) => `${turn.id}:${turn.state}`).join("|");
  const changeSets = useQuery({
    queryKey: ["turn-change-sets", conversationId, terminalKey],
    queryFn: async () => {
      const entries = await Promise.all(turns.map(async (turn) => {
        if (turn.active || turn.state === "rejected") return [turn.id, null] as const;
        try { return [turn.id, await api.getTurnChanges(conversationId, turn.id)] as const; }
        catch { return [turn.id, null] as const; }
      }));
      return Object.fromEntries(entries) as Record<string, ChangeSet | null>;
    },
    enabled: turns.length > 0,
  });
  const activeTurn = turns.find((turn) => turn.active) ?? null;

  const startMutation = useMutation({
    mutationFn: ({ content, key }: { content: string; key: string }) => api.startTurn(conversationId, { content, idempotency_key: key }),
    onSuccess: async () => {
      setTask("");
      followingRef.current = true;
      try { sessionStorage.setItem(followKey(conversationId), "1"); } catch { /* best effort */ }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["turns", conversationId] }),
        queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
    },
  });
  const cancelMutation = useMutation({
    mutationFn: (turnId: string) => api.cancelTurn(conversationId, turnId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["turns", conversationId] }),
  });

  const submitTask = () => {
    const content = task.trim();
    if (!content || startMutation.isPending || activeTurn || conversationQuery.data?.state !== "active") return;
    followingRef.current = true;
    try { sessionStorage.setItem(followKey(conversationId), "1"); } catch { /* best effort */ }
    startMutation.mutate({ content, key: newIdempotencyKey() });
  };

  const conversation = conversationQuery.data;
  const defaultThinkOpen = Boolean(
    profilesQuery.data?.find((profile) => profile.id === conversation?.profile_id)
      ?.show_reasoning
  );
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="border-b border-border px-4 py-3">
        <h1 className="truncate text-base font-semibold">{conversation?.title ?? ""}</h1>
        <p className="truncate text-xs text-muted">{conversation?.workspace_path ?? ""}</p>
      </header>
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto"
        onScroll={(event) => {
          const element = event.currentTarget;
          followingRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
          try {
            sessionStorage.setItem(scrollKey(conversationId), String(element.scrollTop));
            sessionStorage.setItem(followKey(conversationId), followingRef.current ? "1" : "0");
          } catch { /* best effort */ }
        }}
      >
        {turns.length === 0 ? (
          <div><p className="py-10 text-center text-sm text-faint">{t("conversation.noTurns")}</p></div>
        ) : (
          <div className="mx-auto max-w-[54rem] px-4">
            {[...turns].reverse().map((turn) => (
              <ConversationTurn
                key={turn.id}
                conversationId={conversationId}
                turn={turn}
                changeSet={changeSets.data?.[turn.id] ?? null}
                defaultThinkOpen={defaultThinkOpen}
                onOpenArtifact={onOpenArtifact}
              />
            ))}
          </div>
        )}
      </div>
      <div className="shrink-0 border-t border-border bg-surface/95 p-3">
        <div className="mx-auto max-w-[54rem]">
          {startMutation.isError ? <InlineError kind="run_failure" message={startMutation.error instanceof Error ? startMutation.error.message : t("error.runFailure")} /> : null}
          <div className="rounded-lg border border-border bg-surface p-2 shadow-sm">
            <textarea
              value={task}
              onChange={(event) => setTask(event.target.value)}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                  event.preventDefault();
                  submitTask();
                }
              }}
              className="min-h-[4rem] w-full resize-y bg-transparent p-2 text-sm outline-none"
              placeholder={activeTurn ? t("conversation.runningDraftHint") : t("composer.placeholder")}
              aria-label={t("composer.label")}
              data-testid="conversation-task-input"
            />
            <div className="flex justify-end border-t border-border pt-2">
              {activeTurn ? (
                <button type="button" className="btn-danger" disabled={cancelMutation.isPending} onClick={() => cancelMutation.mutate(activeTurn.id)}>
                  <CircleStop aria-hidden size={15} />
                  {cancelMutation.isPending ? t("composer.cancelling") : t("composer.cancel")}
                </button>
              ) : (
                <button type="button" className="btn-primary" disabled={!task.trim() || startMutation.isPending || conversation?.state !== "active"} onClick={submitTask} data-testid="conversation-start">
                  <Send aria-hidden size={15} />
                  {startMutation.isPending ? t("composer.starting") : t("composer.start")}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConversationTurn({
  conversationId,
  turn,
  changeSet,
  defaultThinkOpen,
  onOpenArtifact,
}: {
  conversationId: string;
  turn: Turn;
  changeSet: ChangeSet | null;
  defaultThinkOpen: boolean;
  onOpenArtifact?: (turn: Turn, file: FileChange, files: FileChange[]) => void;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [items, setItems] = useState<ToolEvent[]>([]);
  const afterSeq = useRef(0);
  useEffect(() => {
    setItems([]);
    afterSeq.current = 0;
  }, [conversationId, turn.id]);
  const events = useQuery({
    queryKey: ["turn-event-tail", conversationId, turn.id],
    queryFn: () => api.getTurnEvents(conversationId, turn.id, { afterSeq: afterSeq.current, limit: 500 }),
    refetchInterval: turn.active ? 500 : false,
  });
  const streamSnapshot = useQuery({
    queryKey: ["turn-stream-snapshot", conversationId, turn.id],
    queryFn: () => api.getStreamSnapshot(conversationId, turn.id),
    refetchInterval: turn.active ? 500 : false,
    staleTime: turn.active ? 0 : Number.POSITIVE_INFINITY,
  });
  const eventData = events.data;
  const refetchEvents = events.refetch;
  useEffect(() => {
    const batch = eventData ?? [];
    if (batch.length === 0) return;
    afterSeq.current = Math.max(
      afterSeq.current,
      ...batch.map((event) => event.id)
    );
    setItems((current) => mergeEventTail(current, batch));
    if (batch.length === 500) queueMicrotask(() => void refetchEvents());
  }, [eventData, refetchEvents]);
  useEffect(() => {
    if (!turn.active) return;
    let disposed = false;
    const controller = new AbortController();
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      if (disposed) return;
      subscribeToConversationEvents(conversationId, turn.id, {
        lastEventId: afterSeq.current || undefined,
        signal: controller.signal,
        onEvent: (message) => {
          if (message.event === "hello" || message.event === "end") return;
          if (message.event === "reset") {
            afterSeq.current = 0;
            setItems([]);
            queryClient.invalidateQueries({
              queryKey: ["turn-stream-snapshot", conversationId, turn.id],
            });
            return;
          }
          const event = message.data as ToolEvent;
          if (!event || typeof event.id !== "number") return;
          afterSeq.current = Math.max(afterSeq.current, event.id);
          setItems((current) => mergeEventTail(current, [event]));
          if (event.kind === "run_finished") {
            queryClient.invalidateQueries({ queryKey: ["turns", conversationId] });
            queryClient.invalidateQueries({
              queryKey: ["turn-stream-snapshot", conversationId, turn.id],
            });
          }
        },
        onError: () => {
          if (disposed) return;
          reconnectTimer = setTimeout(connect, 1_500);
        },
      }).catch(() => {
        if (disposed) return;
        reconnectTimer = setTimeout(connect, 1_500);
      });
    };
    connect();
    return () => {
      disposed = true;
      controller.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [conversationId, turn.id, turn.active, queryClient]);
  return (
    <article className="border-b border-border py-3" data-testid={`turn-${turn.id}`}>
      <ActivityFeed
        retainedEvents={items}
        eventBatch={items}
        resetVersion={turn.ordinal}
        eventVersion={items.at(-1)?.id ?? 0}
        task={turn.user_text}
        state={turn.active ? "running" : "terminal"}
        sseStatus="idle"
        terminalText={turn.result?.final_text ? String(turn.result.final_text) : null}
        verificationStatus={turn.result?.verification_status ? String(turn.result.verification_status) : null}
        streamSnapshot={streamSnapshot.data?.checkpoints}
        defaultThinkOpen={defaultThinkOpen}
        embedded
      />
      {!turn.active && !turn.result?.final_text && turn.state === "interrupted" ? (
        <p className="mb-2 px-1 text-xs text-warning" role="status">{t("conversation.turnInterrupted")}</p>
      ) : null}
      {!turn.active && !turn.result?.final_text && turn.state === "error" ? (
        <p className="mb-2 px-1 text-xs text-danger" role="status">{t("conversation.turnError")}</p>
      ) : null}
      {turn.state === "rejected" ? (
        <p className="mb-2 px-1 text-xs text-danger" role="status">{t("conversation.turnRejected")}</p>
      ) : null}
      {!turn.active && turn.state !== "rejected" ? (
        <TurnChangeSummary changeSet={changeSet} onOpenFile={(file) => onOpenArtifact?.(turn, file, changeSet?.files ?? [])} />
      ) : null}
    </article>
  );
}
