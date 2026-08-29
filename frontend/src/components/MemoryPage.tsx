import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, Pencil, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import { api } from "@/api/client";
import type { Memory, MemorySettings } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/errorText";
import { InlineError } from "./InlineError";

const SCOPES = ["global", "workspace", "conversation"] as const;
const KINDS = ["preference", "fact", "decision", "procedure"] as const;
const STATUSES = ["confirmed", "candidate", "superseded", "rejected"] as const;

function newMemoryIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

interface MemoryForm {
  scopeType: string;
  scopeKey: string;
  kind: string;
  title: string;
  content: string;
}

const emptyForm: MemoryForm = {
  scopeType: "workspace",
  scopeKey: "",
  kind: "fact",
  title: "",
  content: "",
};

export interface MemoryPageProps {
  defaultWorkspace?: string;
  onOpenSource?: (conversationId: string, turnId?: string | null) => void;
}

export function MemoryPage({ defaultWorkspace = "", onOpenSource }: MemoryPageProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [scopeType, setScopeType] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>("confirmed");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<MemoryForm>(emptyForm);
  const [editTarget, setEditTarget] = useState<Memory | null>(null);
  const [editContent, setEditContent] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Memory | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetScopeKey, setResetScopeKey] = useState("global");
  const [modeScopeType, setModeScopeType] = useState("global");
  const [modeScopeKey, setModeScopeKey] = useState("global");
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["memories"] });
    await queryClient.invalidateQueries({ queryKey: ["memory-settings"] });
  };

  const list = useInfiniteQuery({
    queryKey: ["memories", scopeType, status, query],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.listMemories({
      scope_type: scopeType,
      status: status,
      query: query || undefined,
      limit: 100,
      cursor: pageParam,
    }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const settings = useQuery<MemorySettings>({
    queryKey: ["memory-settings", modeScopeType, modeScopeKey],
    queryFn: () => api.getMemorySettings(modeScopeType, modeScopeType === "global" ? "global" : modeScopeKey.trim()),
    enabled: modeScopeType === "global" || Boolean(modeScopeKey.trim()),
  });

  const create = useMutation({
    mutationFn: () => api.createMemory({
      scope_type: form.scopeType,
      scope_key: form.scopeKey.trim() || "global",
      kind: form.kind,
      title: form.title.trim() || undefined,
      content: form.content.trim(),
      source_conversation_id: form.scopeType === "conversation" ? form.scopeKey.trim() || null : null,
      source_turn_id: null,
      idempotency_key: newMemoryIdempotencyKey(),
    }),
    onSuccess: async () => {
      setCreateOpen(false);
      setForm(emptyForm);
      setError(null);
      await refresh();
    },
    onError: (err) => setError(apiErrorText(err, t)),
  });

  const saveEdit = useMutation({
    mutationFn: () => {
      if (!editTarget) throw new Error("missing target");
      return api.editMemory(editTarget.id, {
        content: editContent,
        kind: editTarget.kind,
        title: editTarget.title,
        expected_version: editTarget.version,
        idempotency_key: newMemoryIdempotencyKey(),
      });
    },
    onSuccess: async () => {
      setEditTarget(null);
      setError(null);
      await refresh();
    },
    onError: async (err) => { setError(apiErrorText(err, t)); await refresh(); },
  });

  const remove = useMutation({
    mutationFn: (memory: Memory) => api.deleteMemory(memory.id, { expected_version: memory.version, idempotency_key: newMemoryIdempotencyKey() }),
    onSuccess: async () => {
      setError(null);
      await refresh();
    },
    onError: async (err) => { setError(apiErrorText(err, t)); await refresh(); },
  });

  const approve = useMutation({
    mutationFn: (memory: Memory) => api.approveMemory(memory.id, { expected_version: memory.version, idempotency_key: newMemoryIdempotencyKey() }),
    onSuccess: async () => {
      setError(null);
      await refresh();
    },
    onError: async (err) => { setError(apiErrorText(err, t)); await refresh(); },
  });

  const reject = useMutation({
    mutationFn: (memory: Memory) => api.rejectMemory(memory.id, { expected_version: memory.version, idempotency_key: newMemoryIdempotencyKey() }),
    onSuccess: async () => {
      setError(null);
      await refresh();
    },
    onError: async (err) => { setError(apiErrorText(err, t)); await refresh(); },
  });

  const reset = useMutation({
    mutationFn: async () => {
      const scope = scopeType ?? "global";
      const key = scope === "global" ? "global" : resetScopeKey.trim();
      if (!key) throw new Error(t("memory.scopeKey"));
      const snapshot = await api.getMemorySettings(scope, key);
      return api.resetMemories({ scope_type: scope, scope_key: key, confirm: true, idempotency_key: newMemoryIdempotencyKey(), expected_scope_version: snapshot.scope_version });
    },
    onSuccess: async () => {
      setError(null);
      await refresh();
    },
    onError: async (err) => { setError(apiErrorText(err, t)); await refresh(); },
  });

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => api.setMemorySettings({ scope_type: modeScopeType, scope_key: modeScopeType === "global" ? "global" : modeScopeKey.trim(), enabled }),
    onSuccess: async () => {
      await refresh();
    },
    onError: (err) => setError(apiErrorText(err, t)),
  });

  const toggleCandidate = useMutation({
    mutationFn: (enabled: boolean) => api.setMemorySettings({ scope_type: "global", scope_key: "global", enabled: true, candidate_enabled: enabled }),
    onSuccess: async () => {
      await refresh();
    },
    onError: (err) => setError(apiErrorText(err, t)),
  });

  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data]);
  const enabled = settings.data?.enabled ?? true;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="memory-page">
      <div className="shrink-0 border-b border-border bg-surface/95 px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold tracking-[-0.025em]">{t("memory.title")}</h1>
          <select className="input ml-auto h-8 w-auto text-xs" value={modeScopeType} onChange={(event) => { const next = event.target.value; setModeScopeType(next); setModeScopeKey(next === "global" ? "global" : next === "workspace" ? defaultWorkspace : ""); }} aria-label={t("memory.scope")} data-testid="memory-mode-scope">
            {SCOPES.map((scope) => <option key={scope} value={scope}>{t(`memory.scope.${scope}`)}</option>)}
          </select>
          {modeScopeType !== "global" ? (
            <input className="input h-8 w-48 text-xs" value={modeScopeKey} onChange={(event) => setModeScopeKey(event.target.value)} placeholder={t("memory.scopeKey")} aria-label={t("memory.scopeKey")} data-testid="memory-mode-scope-key" />
          ) : null}
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={enabled} disabled={modeScopeType !== "global" && !modeScopeKey.trim()} onChange={(event) => toggleEnabled.mutate(event.target.checked)} data-testid="memory-enabled" />
            <span>{t("memory.enabled")}</span>
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={settings.data?.candidate_enabled ?? false} onChange={(event) => toggleCandidate.mutate(event.target.checked)} data-testid="memory-candidate-enabled" />
            <span>{t("memory.candidateEnabled")}</span>
          </label>
          <button type="button" className="btn-secondary" onClick={() => { setForm({ ...emptyForm, scopeKey: defaultWorkspace }); setCreateOpen(true); }} data-testid="memory-create">
            <Plus aria-hidden size={15} /> {t("memory.create")}
          </button>
          <button type="button" className="btn-secondary" onClick={() => { setResetOpen(true); setResetScopeKey(scopeType === "global" ? "global" : ""); }} disabled={reset.isPending} data-testid="memory-reset">
            <RefreshCw aria-hidden size={15} /> {t("memory.reset")}
          </button>
        </div>
        <div className="mx-auto mt-3 flex max-w-6xl flex-wrap items-center gap-2">
          <label className="relative min-w-[16rem] flex-1">
            <Search aria-hidden size={14} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-faint" />
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("memory.searchPlaceholder")} className="input h-8 pl-7 text-sm" aria-label={t("memory.searchPlaceholder")} data-testid="memory-search" />
          </label>
          <select className="input h-8 w-auto text-sm" value={scopeType ?? ""} onChange={(event) => setScopeType(event.target.value || undefined)} aria-label={t("memory.scope")} data-testid="memory-scope-filter">
            <option value="">{t("memory.scope.all")}</option>
            {SCOPES.map((scope) => <option key={scope} value={scope}>{t(`memory.scope.${scope}`)}</option>)}
          </select>
          <select className="input h-8 w-auto text-sm" value={status ?? ""} onChange={(event) => setStatus(event.target.value || undefined)} aria-label={t("memory.status")} data-testid="memory-status-filter">
            <option value="">{t("memory.status.all")}</option>
            {STATUSES.map((item) => <option key={item} value={item}>{t(`memory.status.${item}`)}</option>)}
          </select>
        </div>
      </div>

      {error ? <div className="px-4 pt-3"><InlineError kind="validation" message={error} /></div> : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
        {list.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-muted"><Loader2 aria-hidden size={14} className="animate-spin" /> {t("common.loading")}</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-faint">{t("memory.empty")}</p>
        ) : (
          <div className="mx-auto max-w-6xl space-y-2.5">
            {items.map((memory) => (
              <article key={memory.id} className="rounded-lg border border-border bg-surface p-4 shadow-sm" data-testid={`memory-${memory.id}`}>
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                      <span className="font-medium text-text">{memory.title || t(`memory.kind.${memory.kind}`)}</span>
                      <span className="rounded bg-surface-2 px-1.5 py-0.5">{t(`memory.kind.${memory.kind}`)}</span>
                      <span className="rounded bg-surface-2 px-1.5 py-0.5">{t(`memory.scope.${memory.scope_type}`)}</span>
                      <span className="rounded bg-surface-2 px-1.5 py-0.5">{t(`memory.status.${memory.status}`)}</span>
                      {memory.source_conversation_id ? (
                        <button
                          type="button"
                          className="truncate underline decoration-dotted underline-offset-2 hover:text-text"
                          title={`${memory.source_conversation_id}/${memory.source_turn_id ?? ""}`}
                          onClick={() => onOpenSource?.(memory.source_conversation_id!, memory.source_turn_id)}
                        >
                          {t("memory.source")} {memory.source_conversation_id.slice(0, 8)}
                        </button>
                      ) : null}
                    </div>
                    <p className="mt-1 whitespace-pre-wrap break-words text-sm">{memory.content}</p>
                    <p className="mt-1 text-[11px] text-faint">{t("memory.updatedAt")} {memory.updated_at}</p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {memory.status === "candidate" ? (
                      <>
                        <button type="button" className="btn-secondary h-7 px-2 text-xs" onClick={() => approve.mutate(memory)} disabled={approve.isPending}>{t("memory.approve")}</button>
                        <button type="button" className="btn-secondary h-7 px-2 text-xs" onClick={() => reject.mutate(memory)} disabled={reject.isPending}>{t("memory.reject")}</button>
                      </>
                    ) : null}
                    {memory.status === "confirmed" || memory.status === "candidate" ? (
                      <button type="button" className="btn-icon h-7 w-7" aria-label={t("memory.edit")} onClick={() => { setEditTarget(memory); setEditContent(memory.content); setError(null); }}>
                        <Pencil aria-hidden size={14} />
                      </button>
                    ) : null}
                    <button type="button" className="btn-icon h-7 w-7 text-danger" aria-label={t("memory.delete")} onClick={() => setDeleteTarget(memory)} disabled={remove.isPending}>
                      <Trash2 aria-hidden size={14} />
                    </button>
                  </div>
                </div>
              </article>
            ))}
            {list.hasNextPage ? (
              <button type="button" className="btn-secondary mx-auto" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage} data-testid="memory-load-more">
                {list.isFetchingNextPage ? t("common.loading") : t("memory.loadMore")}
              </button>
            ) : null}
          </div>
        )}
      </div>

      <Dialog.Root open={createOpen} onOpenChange={setCreateOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(34rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-md outline-none">
            <div className="flex items-center justify-between">
              <Dialog.Title className="font-semibold">{t("memory.create")}</Dialog.Title>
              <Dialog.Close asChild><button type="button" className="btn-icon" aria-label={t("common.close")}><X aria-hidden size={15} /></button></Dialog.Close>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <label className="text-xs text-muted">
                <span>{t("memory.scope")}</span>
                <select className="input mt-1 h-8 w-full text-sm" value={form.scopeType} onChange={(event) => { const next = event.target.value; setForm({ ...form, scopeType: next, scopeKey: next === "global" ? "global" : next === "workspace" ? defaultWorkspace : "" }); }}>
                  {SCOPES.map((scope) => <option key={scope} value={scope}>{t(`memory.scope.${scope}`)}</option>)}
                </select>
              </label>
              <label className="text-xs text-muted">
                <span>{t("memory.scopeKey")}</span>
                <input className="input mt-1 h-8 w-full text-sm" value={form.scopeKey} onChange={(event) => setForm({ ...form, scopeKey: event.target.value })} placeholder="global / C:\repo / conversation-id" />
              </label>
              <label className="text-xs text-muted">
                <span>{t("memory.kind")}</span>
                <select className="input mt-1 h-8 w-full text-sm" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
                  {KINDS.map((kind) => <option key={kind} value={kind}>{t(`memory.kind.${kind}`)}</option>)}
                </select>
              </label>
              <label className="text-xs text-muted">
                <span>{t("memory.fieldTitle")}</span>
                <input className="input mt-1 h-8 w-full text-sm" value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
              </label>
            </div>
            <label className="mt-2 block text-xs text-muted">
              <span>{t("memory.content")}</span>
              <textarea className="input mt-1 min-h-[8rem] w-full text-sm" value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild><button type="button" className="btn-secondary">{t("common.cancel")}</button></Dialog.Close>
              <button type="button" className="btn-primary" disabled={!form.content.trim() || create.isPending} onClick={() => create.mutate()}>{t("common.save")}</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(26rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-md outline-none">
            <Dialog.Title className="font-semibold">{t("memory.delete")}</Dialog.Title>
            <p className="mt-2 text-sm text-muted">{t("memory.deleteHint")}</p>
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild><button type="button" className="btn-secondary">{t("common.cancel")}</button></Dialog.Close>
              <button type="button" className="btn-primary" disabled={remove.isPending} onClick={() => { if (deleteTarget) remove.mutate(deleteTarget, { onSuccess: () => setDeleteTarget(null) }); }}>{t("memory.delete")}</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={editTarget !== null} onOpenChange={(open) => { if (!open) setEditTarget(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(34rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-md outline-none">
            <Dialog.Title className="font-semibold">{t("memory.edit")}</Dialog.Title>
            <textarea className="input mt-3 min-h-[8rem] w-full text-sm" value={editContent} onChange={(event) => setEditContent(event.target.value)} />
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild><button type="button" className="btn-secondary">{t("common.cancel")}</button></Dialog.Close>
              <button type="button" className="btn-primary" disabled={!editContent.trim() || saveEdit.isPending} onClick={() => saveEdit.mutate()}>{t("common.save")}</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={resetOpen} onOpenChange={setResetOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(26rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-md outline-none">
            <Dialog.Title className="font-semibold">{t("memory.reset")}</Dialog.Title>
            <p className="mt-2 text-sm text-muted">{t("memory.resetHint")}</p>
            <label className="mt-3 block text-xs text-muted">
              <span>{t("memory.scopeKey")}</span>
              <input className="input mt-1 h-8 w-full text-sm" value={resetScopeKey} onChange={(event) => setResetScopeKey(event.target.value)} placeholder="global / C:\repo / conversation-id" />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild><button type="button" className="btn-secondary">{t("common.cancel")}</button></Dialog.Close>
              <button type="button" className="btn-primary" disabled={!resetScopeKey.trim() || reset.isPending} onClick={() => reset.mutate()}>{t("memory.reset")}</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
