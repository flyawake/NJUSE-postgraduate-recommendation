import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowUp, BookOpen, CircleStop, FileText, ListPlus, Paperclip, X, Zap } from "lucide-react";
import { api } from "@/api/client";
import type { Attachment, ChangeSet, FileChange, ToolEvent, Turn } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { useGestureSwap } from "@/lib/gesturePreference";
import { ActivityFeed } from "./ActivityFeed";
import { MemoryUsageSummary } from "./MemoryUsageSummary";
import { InlineError } from "./InlineError";
import { QueueDock } from "./QueueDock";
import { TurnChangeSummary } from "./TurnChangeSummary";
import { TurnNavigator } from "./TurnNavigator";
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

function reasoningEffortKey(conversationId: string): string {
  return `coding-agent-conversation-reasoning-effort:${conversationId}`;
}

function storedReasoningEffort(conversationId: string): string | null {
  try { return localStorage.getItem(reasoningEffortKey(conversationId)); }
  catch { return null; }
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
  const initialScrollConversationRef = useRef<string | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const preferenceSaveRef = useRef<Promise<void>>(Promise.resolve());
  const [currentTurnId, setCurrentTurnId] = useState<string | null>(null);
  const [task, setTask] = useState(() => {
    try { return sessionStorage.getItem(draftKey(conversationId)) ?? ""; } catch { return ""; }
  });
  const [reasoningEffort, setReasoningEffort] = useState(
    () => storedReasoningEffort(conversationId) ?? ""
  );
  const [effortTouched, setEffortTouched] = useState(
    () => storedReasoningEffort(conversationId) !== null
  );
  const [profileId, setProfileId] = useState<string | null>(() => {
    try { return localStorage.getItem(profileKey(conversationId)); } catch { return null; }
  });
  const [profileTouched, setProfileTouched] = useState(false);
  const [rememberOpen, setRememberOpen] = useState(false);
  const [rememberContent, setRememberContent] = useState("");
  const [rememberTitle, setRememberTitle] = useState("");
  const [rememberKind, setRememberKind] = useState("fact");
  const [composerError, setComposerError] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const previousConversationRef = useRef(conversationId);

  useEffect(() => {
    const previous = previousConversationRef.current;
    if (previous !== conversationId) {
      for (const item of pendingAttachments) void api.deleteAttachment(previous, item.id).catch(() => undefined);
      setPendingAttachments([]);
      previousConversationRef.current = conversationId;
    }
  }, [conversationId, pendingAttachments]);

  useEffect(() => {
    try { sessionStorage.setItem(draftKey(conversationId), task); } catch { /* best effort */ }
  }, [conversationId, task]);
  useEffect(() => {
    followingRef.current = true;
    initialScrollConversationRef.current = null;
    setCurrentTurnId(null);
    try { sessionStorage.setItem(followKey(conversationId), "1"); } catch { /* best effort */ }
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
  const persistReasoningEffort = useCallback((value: string | null) => {
    const request = preferenceSaveRef.current
      .catch(() => undefined)
      .then(() => api.updateConversationPreferences(conversationId, {
        reasoning_effort: value,
      }));
    preferenceSaveRef.current = request.then(
      (updated) => {
        queryClient.setQueryData(["conversation", conversationId], updated);
      },
      (error) => {
        setComposerError(error instanceof Error ? error.message : t("error.runFailure"));
      },
    );
  }, [conversationId, queryClient, t]);
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
    const stored = storedReasoningEffort(conversationId);
    setProfileTouched(false);
    setEffortTouched(stored !== null);
    setReasoningEffort(stored ?? "");
  }, [conversationId]);
  useEffect(() => {
    const conversation = conversationQuery.data;
    if (!conversation) return;
    const persisted = conversation.reasoning_effort;
    if (persisted !== null && persisted !== undefined) {
      setReasoningEffort(persisted);
      setEffortTouched(true);
      try { localStorage.setItem(reasoningEffortKey(conversationId), persisted); }
      catch { /* best effort */ }
      return;
    }
    const stored = storedReasoningEffort(conversationId);
    if (stored !== null) {
      setReasoningEffort(stored);
      setEffortTouched(true);
      persistReasoningEffort(stored);
      return;
    }
    setEffortTouched(false);
  }, [conversationId, conversationQuery.data, persistReasoningEffort]);
  useEffect(() => {
    if (profileTouched) return;
    let stored: string | null = null;
    try { stored = localStorage.getItem(profileKey(conversationId)); } catch { /* best effort */ }
    setProfileId(stored || conversationQuery.data?.profile_id || null);
  }, [conversationId, conversationQuery.data?.profile_id, profileTouched]);
  useEffect(() => {
    if (
      effortTouched ||
      (conversationQuery.data?.reasoning_effort !== null &&
        conversationQuery.data?.reasoning_effort !== undefined)
    ) return;
    setReasoningEffort(selectedProfile?.reasoning_effort ?? "");
  }, [
    selectedProfile?.id,
    selectedProfile?.reasoning_effort,
    conversationQuery.data?.reasoning_effort,
    effortTouched,
  ]);
  const turnsQuery = useQuery({
    queryKey: ["turns", conversationId],
    queryFn: () => api.listTurns(conversationId, { limit: 100 }),
    refetchInterval: (query) => query.state.data?.items.some((turn) => turn.active) ? 500 : false,
  });
  const turns = useMemo(() => turnsQuery.data?.items ?? [], [turnsQuery.data]);
  const orderedTurns = useMemo(() => [...turns].reverse(), [turns]);
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
    if (turnsQuery.isLoading || turns.length === 0 || initialScrollConversationRef.current === conversationId) return;
    let sourceTurn: string | null = null;
    try { sourceTurn = new URL(window.location.href).searchParams.get("turn"); } catch { /* use latest */ }
    initialScrollConversationRef.current = conversationId;
    if (sourceTurn && turns.some((turn) => turn.id === sourceTurn)) {
      setCurrentTurnId(sourceTurn);
      return;
    }
    setCurrentTurnId(turns[0].id);
    followingRef.current = true;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (element) element.scrollTop = element.scrollHeight;
    }));
  }, [conversationId, turns, turnsQuery.isLoading]);

  useEffect(() => () => {
    if (scrollFrameRef.current !== null) cancelAnimationFrame(scrollFrameRef.current);
  }, []);

  const selectTurn = useCallback((turnId: string) => {
    const target = document.getElementById(`turn-${turnId}`);
    if (!target) return;
    const newest = turns[0]?.id === turnId;
    followingRef.current = newest;
    setCurrentTurnId(turnId);
    try { sessionStorage.setItem(followKey(conversationId), newest ? "1" : "0"); } catch { /* best effort */ }
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.focus({ preventScroll: true });
  }, [conversationId, turns]);

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
    mutationFn: ({ content, key, attachmentIds }: { content: string; key: string; attachmentIds: string[] }) => api.startTurn(conversationId, { content, attachment_ids: attachmentIds, idempotency_key: key, profile_id: profileId ?? null, reasoning_effort: reasoningEffort || null }),
    onSuccess: async () => {
      setTask("");
      setPendingAttachments([]);
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

  const addFiles = async (files: File[]) => {
    if (activeTurn || conversationQuery.data?.state !== "active" || files.length === 0) return;
    setComposerError(null);
    let count = pendingAttachments.length;
    let total = pendingAttachments.reduce((sum, item) => sum + item.size_bytes, 0);
    for (const file of files) {
      if (count >= 4) {
        setComposerError(t("attachment.tooMany"));
        break;
      }
      if (file.size > 10 * 1024 * 1024 || total + file.size > 20 * 1024 * 1024) {
        setComposerError(t("attachment.tooLarge"));
        break;
      }
      setUploadingCount((value) => value + 1);
      try {
        const uploaded = await api.uploadAttachment(conversationId, file);
        setPendingAttachments((current) => [...current, uploaded]);
        count += 1;
        total += uploaded.size_bytes;
      } catch (error) {
        setComposerError(error instanceof Error && error.message ? error.message : t("attachment.uploadFailed"));
        break;
      } finally {
        setUploadingCount((value) => Math.max(0, value - 1));
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeAttachment = async (attachment: Attachment) => {
    setPendingAttachments((current) => current.filter((item) => item.id !== attachment.id));
    try { await api.deleteAttachment(conversationId, attachment.id); }
    catch (error) { setComposerError(error instanceof Error ? error.message : t("attachment.removeFailed")); }
  };

  const submitTask = (mode: "start" | "queue" | "steer") => {
    const content = task.trim();
    if ((!content && pendingAttachments.length === 0) || conversationQuery.data?.state !== "active") return;
    const rememberMatch = /^\/remember(?:\s+(.+))?$/i.exec(content);
    if (rememberMatch) {
      setRememberContent((rememberMatch[1] ?? "").trim());
      setRememberTitle("");
      setRememberKind("fact");
      setRememberOpen(true);
      return;
    }
    if (activeTurn) {
      if (!content) return;
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
    startMutation.mutate({
      content,
      key: newIdempotencyKey(),
      attachmentIds: pendingAttachments.map((item) => item.id),
    });
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
      localStorage.removeItem(reasoningEffortKey(conversationId));
    } catch { /* best effort */ }
    persistReasoningEffort(null);
  };

  const handleReasoningEffortChange = (value: string) => {
    setReasoningEffort(value);
    setEffortTouched(true);
    try { localStorage.setItem(reasoningEffortKey(conversationId), value); }
    catch { /* best effort */ }
    persistReasoningEffort(value);
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
    <div className="relative flex h-full min-h-0 flex-col bg-transparent">
      <header className="glass-panel shrink-0 border-b border-border/65 px-4 py-3">
        <div className="mx-auto max-w-[56rem]">
          <h1 className="truncate text-[14px] font-semibold tracking-[-0.018em]">{conversation?.title ?? ""}</h1>
          <p className="mono mt-0.5 truncate text-[11px] text-faint">{conversation?.workspace_path ?? ""}</p>
        </div>
      </header>
      <TurnNavigator turns={orderedTurns} currentTurnId={currentTurnId} onSelect={selectTurn} />
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
          if (scrollFrameRef.current !== null) cancelAnimationFrame(scrollFrameRef.current);
          scrollFrameRef.current = requestAnimationFrame(() => {
            const viewportTop = element.getBoundingClientRect().top;
            const anchor = viewportTop + Math.min(220, element.clientHeight * 0.35);
            let closestId: string | null = null;
            let closestDistance = Number.POSITIVE_INFINITY;
            for (const turnElement of element.querySelectorAll<HTMLElement>("[data-turn-id]")) {
              const distance = Math.abs(turnElement.getBoundingClientRect().top - anchor);
              if (distance < closestDistance) {
                closestDistance = distance;
                closestId = turnElement.dataset.turnId ?? null;
              }
            }
            if (closestId) setCurrentTurnId(closestId);
          });
        }}
      >
        {turns.length === 0 ? (
          <div className="flex h-full min-h-[16rem] items-center justify-center px-6">
            <p className="max-w-md text-center text-sm leading-7 text-faint">{t("conversation.noTurns")}</p>
          </div>
        ) : (
          <div className="mx-auto max-w-[56rem] px-4 sm:px-6">
            {orderedTurns.map((turn) => (
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
      <div className="shrink-0 bg-gradient-to-t from-surface via-surface/95 to-transparent px-3 pb-3 pt-5 sm:px-5 sm:pb-5">
        <div className="mx-auto max-w-[52rem]">
          {composerError ? <InlineError kind="validation" message={composerError} /> : null}
          {startMutation.isError ? <InlineError kind="run_failure" message={startMutation.error instanceof Error ? startMutation.error.message : t("error.runFailure")} /> : null}
          {(inboxQuery.data?.items?.length ?? 0) > 0 ? (
            <QueueDock conversationId={conversationId} snapshot={inboxQuery.data} />
          ) : null}
          <div
            className="composer-shell p-2.5"
            onDragOver={(event) => {
              if (!activeTurn && event.dataTransfer.types.includes("Files")) event.preventDefault();
            }}
            onDrop={(event) => {
              if (activeTurn) return;
              event.preventDefault();
              void addFiles(Array.from(event.dataTransfer.files));
            }}
          >
            {pendingAttachments.length > 0 || uploadingCount > 0 ? (
              <div className="flex flex-wrap gap-2 px-2 pb-2" data-testid="pending-attachments">
                {pendingAttachments.map((attachment) => (
                  <AttachmentChip
                    key={attachment.id}
                    attachment={attachment}
                    conversationId={conversationId}
                    onRemove={() => void removeAttachment(attachment)}
                    disabled={startMutation.isPending}
                  />
                ))}
                {uploadingCount > 0 ? <span className="rounded-md border border-border px-2 py-1 text-xs text-muted">{t("attachment.uploading")}</span> : null}
              </div>
            ) : null}
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
              onPaste={(event) => {
                if (activeTurn) return;
                const files = Array.from(event.clipboardData.items)
                  .filter((item) => item.kind === "file")
                  .map((item) => item.getAsFile())
                  .filter((item): item is File => item !== null);
                if (files.length > 0) {
                  event.preventDefault();
                  void addFiles(files);
                }
              }}
              className="min-h-[5rem] w-full resize-y bg-transparent px-2 py-1.5 text-[15px] leading-6 outline-none placeholder:text-faint"
              placeholder={activeTurn ? t("conversation.runningDraftHint") : t("composer.placeholder")}
              aria-label={t("composer.label")}
              data-testid="conversation-task-input"
            />
            <div className="flex flex-wrap items-center justify-end gap-1 px-1 pt-1.5">
              <input
                ref={fileInputRef}
                type="file"
                className="sr-only"
                multiple
                accept="image/png,image/jpeg,image/gif,image/webp,.pdf,.txt,.md,.csv,.tsv,.json,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.rtf"
                onChange={(event) => void addFiles(Array.from(event.target.files ?? []))}
                data-testid="conversation-file-input"
              />
              <button
                type="button"
                className="composer-action"
                disabled={Boolean(activeTurn) || uploadingCount > 0 || pendingAttachments.length >= 4 || conversation?.state !== "active"}
                onClick={() => fileInputRef.current?.click()}
                aria-label={t("attachment.add")}
                title={activeTurn ? t("attachment.busyHint") : t("attachment.add")}
                data-testid="conversation-attach"
              >
                <Paperclip aria-hidden size={15} />
                <span className="hidden sm:inline">{t("attachment.add")}</span>
              </button>
              <label htmlFor="conversation-model" className="mr-auto flex min-w-0 items-center text-xs text-muted">
                <span className="sr-only">{t("composer.model")}</span>
                <select
                  id="conversation-model"
                  className="composer-control w-auto"
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
                <label htmlFor="conversation-reasoning-effort" className="flex items-center text-xs text-muted">
                  <span className="sr-only">{t("composer.reasoningEffort")}</span>
                  <select
                    id="conversation-reasoning-effort"
                    className="composer-control w-auto"
                    value={reasoningEffort}
                    onChange={(event) => handleReasoningEffortChange(event.target.value)}
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
                className="composer-action"
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
                <span className="hidden sm:inline">{t("remember.open")}</span>
              </button>
              {activeTurn ? (
                <>
                  {task.trim() ? (
                    <>
                      <button
                        type="button"
                        className="composer-action bg-accent text-accent-fg hover:!bg-accent-hover hover:!text-accent-fg"
                        disabled={enqueueMutation.isPending}
                        onClick={() => submitTask("queue")}
                        data-testid="conversation-queue"
                      >
                        <ListPlus aria-hidden size={15} />
                        {enqueueMutation.isPending ? t("composer.starting") : t("composer.queue")}
                      </button>
                      <button
                        type="button"
                        className="composer-action"
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
                    className="composer-submit composer-stop"
                    disabled={cancelMutation.isPending}
                    onClick={() => cancelMutation.mutate(activeTurn.id)}
                    data-testid="conversation-stop"
                    aria-label={t("composer.cancel")}
                    title={cancelMutation.isPending ? t("composer.cancelling") : t("composer.stopCompact")}
                  >
                    <CircleStop aria-hidden size={17} />
                    <span className="sr-only">{cancelMutation.isPending ? t("composer.cancelling") : t("composer.stopCompact")}</span>
                  </button>
                </>
              ) : (
                <button type="button" className="composer-submit" disabled={(!task.trim() && pendingAttachments.length === 0) || uploadingCount > 0 || startMutation.isPending || conversation?.state !== "active"} onClick={() => submitTask("start")} data-testid="conversation-start" title={startMutation.isPending ? t("composer.starting") : t("composer.start")}>
                  <ArrowUp aria-hidden size={18} className={startMutation.isPending ? "animate-pulse" : ""} />
                  <span className="sr-only">{startMutation.isPending ? t("composer.starting") : t("composer.start")}</span>
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

function formatAttachmentSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KiB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function AttachmentChip({
  attachment,
  conversationId,
  onRemove,
  disabled,
}: {
  attachment: Attachment;
  conversationId: string;
  onRemove: () => void;
  disabled: boolean;
}) {
  const { t } = useI18n();
  const image = attachment.kind === "image";
  return (
    <span className="flex max-w-[18rem] items-center gap-2 rounded-lg border border-border bg-surface-elevated px-2 py-1.5 text-xs">
      {image ? (
        <img className="h-8 w-8 rounded object-cover" src={api.attachmentUrl(conversationId, attachment.id)} alt="" />
      ) : <FileText aria-hidden size={16} className="shrink-0 text-muted" />}
      <span className="min-w-0">
        <span className="block truncate font-medium">{attachment.filename}</span>
        <span className="block text-[10px] text-faint">{formatAttachmentSize(attachment.size_bytes)}</span>
      </span>
      <button type="button" className="rounded p-1 text-muted hover:bg-surface-hover hover:text-fg" onClick={onRemove} disabled={disabled} aria-label={`${t("attachment.remove")} ${attachment.filename}`}>
        <X aria-hidden size={14} />
      </button>
    </span>
  );
}

function TurnAttachments({ conversationId, attachments }: { conversationId: string; attachments: Attachment[] }) {
  if (attachments.length === 0) return null;
  return (
    <div className="mb-3 flex flex-wrap gap-2 px-1" data-testid="turn-attachments">
      {attachments.map((attachment) => (
        <a
          key={attachment.id}
          href={api.attachmentUrl(conversationId, attachment.id)}
          target="_blank"
          rel="noreferrer"
          className="group flex max-w-[20rem] items-center gap-2 rounded-lg border border-border bg-surface-elevated p-2 text-xs hover:border-accent/50"
        >
          {attachment.kind === "image" ? (
            <img className="h-12 w-12 rounded object-cover" src={api.attachmentUrl(conversationId, attachment.id)} alt={attachment.filename} />
          ) : <FileText aria-hidden size={18} className="text-muted" />}
          <span className="min-w-0">
            <span className="block truncate font-medium group-hover:text-accent">{attachment.filename}</span>
            <span className="text-[10px] text-faint">{formatAttachmentSize(attachment.size_bytes)}</span>
          </span>
        </a>
      ))}
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
    <article id={`turn-${turn.id}`} className="scroll-mt-4 border-b border-border py-5" data-testid={`turn-${turn.id}`} data-turn-id={turn.id} tabIndex={-1}>
      <TurnAttachments conversationId={conversationId} attachments={turn.attachments ?? []} />
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
