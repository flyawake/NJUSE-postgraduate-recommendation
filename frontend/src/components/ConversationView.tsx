import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { BookOpen, CircleStop, ListPlus, Send, Zap } from "lucide-react";
import { api } from "@/api/client";
import type { ChangeSet, FileChange, ToolEvent, Turn } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { useGestureSwap } from "@/lib/gesturePreference";
import { ActivityFeed } from "./ActivityFeed";
import { MemoryUsageSummary } from "./MemoryUsageSummary";
import { InlineError } from "./InlineError";
import { QueueDock } from "./QueueDock";
import { TurnChangeSummary } from "./TurnChangeSummary";
import { subscribeToConversationEvents, subscribeToInbox } from "@/lib/sse";

export interface ConversationViewProps {
  conversationId: string;
  onOpenArtifact?: (turn: Turn, file: FileChange, files: FileChange[]) => void;
  onOpenMemorySource?: (conversationId: string, turnId?: string | null) => void;
}

function draftKey(conversationId: string): string {
  return `coding-agent-conversation-draft:${conversationId}`;
}

function profileKey(conversationId: string): string {
  return `coding-agent-conversation-profile:${conversationId}`;
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

export function ConversationView({ conversationId, onOpenArtifact, onOpenMemorySource }: ConversationViewProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [gestureSwap] = useGestureSwap();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followingRef = useRef(true);
  const [task, setTask] = useState(() => {
    try { return sessionStorage.getItem(draftKey(conversationId)) ?? ""; } catch { return ""; }
  });
  const [reasoningEffort, setReasoningEffort] = useState("");
  const [effortTouched, setEffortTouched] = useState(false);
  const [profileId, setProfileId] = useState<string | null>(() => {
    try { return localStorage.getItem(profileKey(conversationId)); } catch { return null; }
  });
  const [profileTouched, setProfileTouched] = useState(false);
  const [rememberOpen, setRememberOpen] = useState(false);
  const [rememberContent, setRememberContent] = useState("");
  const [rememberTitle, setRememberTitle] = useState("");
  const [rememberKind, setRememberKind] = useState("fact");
  const [composerError, setComposerError] = useState<string | null>(null);

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
  const selectedProfile = profilesQuery.data?.find(
    (item) => item.id === (profileId ?? conversationQuery.data?.profile_id)
  ) ?? null;
  const availableProfiles = profilesQuery.data?.filter((profile) => profile.credential?.configured) ?? [];
  useEffect(() => {
    setProfileTouched(false);
    setEffortTouched(false);
    setReasoningEffort("");
  }, [conversationId]);
  useEffect(() => {
    if (profileTouched) return;
    let stored: string | null = null;
    try { stored = localStorage.getItem(profileKey(conversationId)); } catch { /* best effort */ }
    setProfileId(stored || conversationQuery.data?.profile_id || null);
  }, [conversationId, conversationQuery.data?.profile_id, profileTouched]);
  useEffect(() => {
    if (effortTouched) return;
    setReasoningEffort(selectedProfile?.reasoning_effort ?? "");
  }, [selectedProfile?.id, selectedProfile?.reasoning_effort, effortTouched]);
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

  useEffect(() => {
    let sourceTurn: string | null = null;
    try { sourceTurn = new URL(window.location.href).searchParams.get("turn"); } catch { return; }
    if (!sourceTurn || !turns.some((turn) => turn.id === sourceTurn)) return;
    requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(`[data-testid="turn-${sourceTurn}"]`);
      target?.scrollIntoView({ block: "center" });
      target?.focus({ preventScroll: true });
    });
  }, [conversationId, turns]);

  const inboxQuery = useQuery({
    queryKey: ["inbox", conversationId],
    queryFn: () => api.getInbox(conversationId),
    refetchInterval: activeTurn ? 700 : false,
  });

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      if (disposed) return;
      subscribeToInbox(conversationId, {
        signal: controller.signal,
        onEvent: (message) => {
          if (
            message.event === "inbox_snapshot" ||
            message.event === "inbox_changed"
          ) {
            queryClient.invalidateQueries({ queryKey: ["inbox", conversationId] });
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
  }, [conversationId, queryClient]);

  const enqueueMutation = useMutation({
    mutationFn: ({
      content,
      mode,
      key,
    }: {
      content: string;
      mode: "queue" | "steer";
      key: string;
    }) =>
      api.enqueueInbox(conversationId, {
        content,
        mode,
        idempotency_key: key,
        profile_id: profileId ?? null,
        reasoning_effort: reasoningEffort || null,
      }),
    onSuccess: async () => {
      setTask("");
    },
    // A version conflict has no optimistic local source of truth.  Refetch
    // on both success and failure so the Host snapshot always wins.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["inbox", conversationId] }),
  });

  const startMutation = useMutation({
    mutationFn: ({ content, key }: { content: string; key: string }) => api.startTurn(conversationId, { content, idempotency_key: key, profile_id: profileId ?? null, reasoning_effort: reasoningEffort || null }),
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
  const saveMemory = useMutation({
    mutationFn: () => {
      if (!conversation || !conversation.workspace_key) {
        throw new Error(t("memory.scopeKey"));
      }
      return api.createMemory({
        scope_type: "workspace",
        scope_key: conversation.workspace_key,
        kind: rememberKind,
        title: rememberTitle.trim() || undefined,
        content: rememberContent.trim(),
        source_conversation_id: conversationId,
        source_turn_id: null,
        idempotency_key: newIdempotencyKey(),
      });
    },
    onSuccess: () => {
      setRememberOpen(false);
      setTask("");
      setRememberContent("");
      setRememberTitle("");
      setRememberKind("fact");
      queryClient.invalidateQueries({ queryKey: ["memories"] });
    },
    onError: (error) => setComposerError(error instanceof Error ? error.message : t("error.runFailure")),
  });

  const cancelMutation = useMutation({
    mutationFn: (turnId: string) => api.cancelTurn(conversationId, turnId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["turns", conversationId] }),
  });

  const submitTask = (mode: "start" | "queue" | "steer") => {
    const content = task.trim();
    if (!content || conversationQuery.data?.state !== "active") return;
    const rememberMatch = /^\/remember(?:\s+(.+))?$/i.exec(content);
    if (rememberMatch) {
      setRememberContent((rememberMatch[1] ?? "").trim());
      setRememberTitle("");
      setRememberKind("fact");
      setRememberOpen(true);
      return;
    }
    if (activeTurn) {
      if (mode === "start" || mode === "queue") {
        if (enqueueMutation.isPending) return;
        enqueueMutation.mutate({ content, mode: "queue", key: newIdempotencyKey() });
      } else {
        if (enqueueMutation.isPending) return;
        enqueueMutation.mutate({ content, mode: "steer", key: newIdempotencyKey() });
      }
      return;
    }
    if (mode !== "start" || startMutation.isPending) return;
    followingRef.current = true;
    try { sessionStorage.setItem(followKey(conversationId), "1"); } catch { /* best effort */ }
    startMutation.mutate({ content, key: newIdempotencyKey() });
  };

  const handleMessageProfileChange = (value: string) => {
    const next = value || null;
    setProfileId(next);
    setProfileTouched(true);
    const nextProfile = profilesQuery.data?.find((profile) => profile.id === next) ?? null;
    setEffortTouched(false);
    setReasoningEffort(nextProfile?.reasoning_effort ?? "");
    try {
      if (next) localStorage.setItem(profileKey(conversationId), next);
      else localStorage.removeItem(profileKey(conversationId));
    } catch { /* best effort */ }
  };

  const conversation = conversationQuery.data;
  const defaultThinkOpen = Boolean(selectedProfile?.show_reasoning);
  const supportsReasoningEffort = Boolean(selectedProfile && (
    selectedProfile.wire_api === "openai_responses" ||
    (selectedProfile.wire_api === "openai_chat_completions" && ["openai", "deepseek"].includes(selectedProfile.provider_id))
  ));
  const effortOptions = selectedProfile?.provider_id === "deepseek"
    ? ["", "low", "medium", "high", "max"]
    : ["", "low", "medium", "high"];
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
                onOpenMemorySource={onOpenMemorySource}
              />
            ))}
          </div>
        )}
      </div>
      <div className="shrink-0 border-t border-border bg-surface/95 p-3">
        <div className="mx-auto max-w-[54rem]">
          {composerError ? <InlineError kind="validation" message={composerError} /> : null}
          {startMutation.isError ? <InlineError kind="run_failure" message={startMutation.error instanceof Error ? startMutation.error.message : t("error.runFailure")} /> : null}
          {(inboxQuery.data?.items?.length ?? 0) > 0 ? (
            <QueueDock conversationId={conversationId} snapshot={inboxQuery.data} />
          ) : null}
          <div className="rounded-lg border border-border bg-surface p-2 shadow-sm">
            <textarea
              value={task}
              onChange={(event) => setTask(event.target.value)}
              onKeyDown={(event) => {
                if (event.shiftKey && event.key === "Enter") return;
                if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                  event.preventDefault();
                  submitTask(activeTurn ? (gestureSwap ? "queue" : "steer") : "start");
                  return;
                }
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submitTask(activeTurn ? (gestureSwap ? "steer" : "queue") : "start");
                }
              }}
              className="min-h-[4rem] w-full resize-y bg-transparent p-2 text-sm outline-none"
              placeholder={activeTurn ? t("conversation.runningDraftHint") : t("composer.placeholder")}
              aria-label={t("composer.label")}
              data-testid="conversation-task-input"
            />
            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-2">
              <label htmlFor="conversation-model" className="mr-auto flex items-center gap-1.5 text-xs text-muted">
                <span>{t("composer.model")}</span>
                <select
                  id="conversation-model"
                  className="input h-8 w-auto max-w-[15rem] py-1 text-xs"
                  value={profileId ?? ""}
                  onChange={(event) => handleMessageProfileChange(event.target.value)}
                  disabled={availableProfiles.length === 0}
                  data-testid="conversation-model"
                >
                  <option value="">{t("composer.model.placeholder")}</option>
                  {availableProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.display_name} · {profile.model}
                    </option>
                  ))}
                </select>
              </label>
              {supportsReasoningEffort ? (
                <label htmlFor="conversation-reasoning-effort" className="flex items-center gap-1.5 text-xs text-muted">
                  <span>{t("composer.reasoningEffort")}</span>
                  <select
                    id="conversation-reasoning-effort"
                    className="input h-8 w-auto py-1 text-xs"
                    value={reasoningEffort}
                    onChange={(event) => {
                      setReasoningEffort(event.target.value);
                      setEffortTouched(true);
                    }}
                    data-testid="conversation-reasoning-effort"
                  >
                    {effortOptions.map((value) => (
                      <option key={value || "default"} value={value}>
                        {value ? t(`composer.reasoningEffort.${value}`) : t("composer.reasoningEffort.default")}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <button
                type="button"
                className="btn-secondary"
                disabled={!task.trim()}
                onClick={() => {
                  setRememberContent(task.trim());
                  setRememberTitle("");
                  setRememberKind("fact");
                  setRememberOpen(true);
                }}
                data-testid="conversation-remember"
                aria-label={t("remember.open")}
              >
                <BookOpen aria-hidden size={15} />
                {t("remember.open")}
              </button>
              {activeTurn ? (
                <>
                  {task.trim() ? (
                    <>
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={enqueueMutation.isPending}
                        onClick={() => submitTask("queue")}
                        data-testid="conversation-queue"
                      >
                        <ListPlus aria-hidden size={15} />
                        {enqueueMutation.isPending ? t("composer.starting") : t("composer.queue")}
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={enqueueMutation.isPending || cancelMutation.isPending}
                        onClick={() => submitTask("steer")}
                        data-testid="conversation-steer"
                      >
                        <Zap aria-hidden size={15} />
                        {t("composer.steer")}
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    className="btn-danger"
                    disabled={cancelMutation.isPending}
                    onClick={() => cancelMutation.mutate(activeTurn.id)}
                    data-testid="conversation-stop"
                    aria-label={t("composer.cancel")}
                  >
                    <CircleStop aria-hidden size={15} />
                    {cancelMutation.isPending ? t("composer.cancelling") : t("composer.stopCompact")}
                  </button>
                </>
              ) : (
                <button type="button" className="btn-primary" disabled={!task.trim() || startMutation.isPending || conversation?.state !== "active"} onClick={() => submitTask("start")} data-testid="conversation-start">
                  <Send aria-hidden size={15} />
                  {startMutation.isPending ? t("composer.starting") : t("composer.start")}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      <Dialog.Root open={rememberOpen} onOpenChange={setRememberOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(34rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-md outline-none">
            <div className="flex items-center justify-between">
              <Dialog.Title className="font-semibold">{t("remember.title")}</Dialog.Title>
              <Dialog.Close asChild>
                <button type="button" className="btn-icon" aria-label={t("common.close")}>
                  <BookOpen aria-hidden size={15} />
                </button>
              </Dialog.Close>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <label className="text-xs text-muted">
                <span>{t("memory.kind")}</span>
                <select className="input mt-1 h-8 w-full text-sm" value={rememberKind} onChange={(event) => setRememberKind(event.target.value)}>
                  <option value="preference">{t("memory.kind.preference")}</option>
                  <option value="fact">{t("memory.kind.fact")}</option>
                  <option value="decision">{t("memory.kind.decision")}</option>
                  <option value="procedure">{t("memory.kind.procedure")}</option>
                </select>
              </label>
              <label className="text-xs text-muted">
                <span>{t("memory.fieldTitle")}</span>
                <input className="input mt-1 h-8 w-full text-sm" value={rememberTitle} onChange={(event) => setRememberTitle(event.target.value)} />
              </label>
            </div>
            <label className="mt-2 block text-xs text-muted">
              <span>{t("memory.content")}</span>
              <textarea className="input mt-1 min-h-[8rem] w-full text-sm" value={rememberContent} onChange={(event) => setRememberContent(event.target.value)} />
            </label>
            <p className="mt-2 text-[11px] text-faint">{t("remember.scopeHint")}</p>
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild><button type="button" className="btn-secondary">{t("common.cancel")}</button></Dialog.Close>
              <button type="button" className="btn-primary" disabled={!rememberContent.trim() || saveMemory.isPending} onClick={() => saveMemory.mutate()} data-testid="remember-confirm">
                {t("remember.save")}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function ConversationTurn({
  conversationId,
  turn,
  changeSet,
  defaultThinkOpen,
  onOpenArtifact,
  onOpenMemorySource,
}: {
  conversationId: string;
  turn: Turn;
  changeSet: ChangeSet | null;
  defaultThinkOpen: boolean;
  onOpenArtifact?: (turn: Turn, file: FileChange, files: FileChange[]) => void;
  onOpenMemorySource?: (conversationId: string, turnId?: string | null) => void;
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
    <article className="border-b border-border py-3" data-testid={`turn-${turn.id}`} tabIndex={-1}>
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
      <MemoryUsageSummary conversationId={conversationId} turnId={turn.id} onOpenSource={onOpenMemorySource} />
      {items.some((event) => event.kind === "steer_delivered") ? (
        <p className="mb-2 px-1 text-xs text-accent" role="status" data-testid="steer-caption">
          {t("conversation.steerCaption")}
        </p>
      ) : null}
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
