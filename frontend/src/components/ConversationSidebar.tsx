import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { Archive, ArchiveRestore, Loader2, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { api } from "@/api/client";
import type { Conversation } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { cx } from "@/lib/format";

export interface ConversationSidebarProps {
  selectedId?: string | null;
  onSelect?: (conversation: Conversation) => void;
  onCreate?: () => void;
  onDeleted?: (conversationId: string) => void;
}

function workspaceName(conversation: Conversation): string {
  const parts = conversation.workspace_path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? conversation.workspace_path;
}

export function ConversationSidebar({ selectedId, onSelect, onCreate, onDeleted }: ConversationSidebarProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = useInfiniteQuery({
    queryKey: ["conversations", { archived: showArchived, query }],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => api.listConversations({
      archived: showArchived,
      query: query || undefined,
      limit: 50,
      cursor: pageParam ?? undefined,
    }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? null,
    refetchInterval: 1_000,
  });
  const conversations = useMemo(
    () => list.data?.pages.flatMap((page) => page.items) ?? [],
    [list.data]
  );
  const groups = useMemo(() => {
    const grouped = new Map<string, Conversation[]>();
    for (const conversation of conversations) {
      const key = `${workspaceName(conversation)}\u0000${conversation.workspace_key}`;
      grouped.set(key, [...(grouped.get(key) ?? []), conversation]);
    }
    return [...grouped.entries()];
  }, [conversations]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
  };
  const lifecycle = useMutation({
    mutationFn: async ({ conversation, action }: { conversation: Conversation; action: "archive" | "restore" }) => action === "archive"
      ? api.archiveConversation(conversation.id, { expected_version: conversation.version, confirm: true })
      : api.unarchiveConversation(conversation.id, { expected_version: conversation.version, confirm: true }),
    onSuccess: refresh,
    onError: (error) => setActionError(error instanceof Error ? error.message : t("error.runFailure")),
  });
  const rename = useMutation({
    mutationFn: () => {
      if (!renameTarget) throw new Error(t("error.runFailure"));
      return api.renameConversation(renameTarget.id, {
        title: renameTitle.trim(),
        expected_version: renameTarget.version,
      });
    },
    onSuccess: async () => {
      setRenameTarget(null);
      await refresh();
    },
    onError: (error) => setActionError(error instanceof Error ? error.message : t("error.runFailure")),
  });
  const remove = useMutation({
    mutationFn: () => {
      if (!deleteTarget) throw new Error(t("error.runFailure"));
      return api.deleteConversation(deleteTarget.id, {
        expected_version: deleteTarget.version,
        confirm: true,
      });
    },
    onSuccess: async () => {
      const deletedId = deleteTarget?.id;
      setDeleteTarget(null);
      if (deletedId) onDeleted?.(deletedId);
      await refresh();
    },
    onError: (error) => setActionError(error instanceof Error ? error.message : t("error.runFailure")),
  });

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2" data-testid="conversation-sidebar">
      <button type="button" className="btn-primary flex h-9 items-center gap-2" onClick={onCreate} data-testid="new-conversation">
        <Plus aria-hidden size={16} />
        <span>{t("nav.newConversation")}</span>
      </button>
      <label className="relative">
        <Search aria-hidden size={14} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-faint" />
        <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("nav.searchConversations")} className="h-8 w-full rounded-md border border-border bg-surface-2 pl-7 pr-2 text-xs outline-none focus:border-accent" aria-label={t("nav.searchConversations")} />
      </label>
      <button type="button" className="btn-ghost justify-start text-xs" onClick={() => setShowArchived((value) => !value)}>
        {showArchived ? <ArchiveRestore aria-hidden size={14} /> : <Archive aria-hidden size={14} />}
        {showArchived ? t("nav.backToConversations") : t("nav.archivedConversations")}
      </button>
      {actionError ? <p className="px-1 text-xs text-danger" role="alert">{actionError}</p> : null}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        {groups.length === 0 ? (
          <p className="px-2 py-3 text-xs text-faint">{t("nav.noConversations")}</p>
        ) : groups.map(([key, items]) => (
          <section key={key}>
            <h2 className="truncate px-2 pb-1 text-[11px] font-medium text-faint">{key.split("\u0000")[0]}</h2>
            <div className="space-y-1">
              {items.map((conversation) => {
                const active = conversation.id === selectedId;
                const running = Boolean(conversation.latest_turn?.active);
                return (
                  <div key={conversation.id} className={cx("group flex items-center rounded-md", active ? "bg-accent-muted" : "hover:bg-surface-2")}>
                    <button type="button" className={cx("min-w-0 flex-1 px-2 py-1.5 text-left text-sm", active ? "text-accent" : "text-muted")} onClick={() => onSelect?.(conversation)} aria-current={active ? "page" : undefined} data-testid={`conversation-${conversation.id}`}>
                      <span className="flex items-center gap-1.5">
                        {running ? <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent" aria-label={t("status.running")} /> : null}
                        <span className="truncate font-medium">{conversation.title}</span>
                      </span>
                    </button>
                    <div className="flex shrink-0 pr-1 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
                      <button type="button" className="btn-icon h-7 w-7" aria-label={t("conversation.rename")} onClick={() => { setRenameTarget(conversation); setRenameTitle(conversation.title); setActionError(null); }}><Pencil aria-hidden size={13} /></button>
                      <button type="button" className="btn-icon h-7 w-7" aria-label={showArchived ? t("conversation.restore") : t("conversation.archive")} onClick={() => lifecycle.mutate({ conversation, action: showArchived ? "restore" : "archive" })}>{showArchived ? <ArchiveRestore aria-hidden size={13} /> : <Archive aria-hidden size={13} />}</button>
                      <button type="button" className="btn-icon h-7 w-7 text-danger" aria-label={t("conversation.delete")} onClick={() => { setDeleteTarget(conversation); setActionError(null); }}><Trash2 aria-hidden size={13} /></button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
        {list.hasNextPage ? (
          <button type="button" className="btn-secondary mx-auto flex text-xs" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
            {list.isFetchingNextPage ? <Loader2 aria-hidden size={13} className="animate-spin" /> : null}
            {t("conversation.loadMore")}
          </button>
        ) : null}
      </div>

      <Dialog.Root open={renameTarget !== null} onOpenChange={(open) => { if (!open) setRenameTarget(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(24rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-md outline-none">
            <div className="flex items-center justify-between">
              <Dialog.Title className="font-semibold">{t("conversation.rename")}</Dialog.Title>
              <Dialog.Close asChild><button type="button" className="btn-icon" aria-label={t("common.close")}><X aria-hidden size={15} /></button></Dialog.Close>
            </div>
            <input className="input mt-4 w-full" value={renameTitle} onChange={(event) => setRenameTitle(event.target.value)} autoFocus aria-label={t("conversation.rename")} />
            <div className="mt-4 flex justify-end gap-2">
              <Dialog.Close asChild><button type="button" className="btn-secondary">{t("common.cancel")}</button></Dialog.Close>
              <button type="button" className="btn-primary" disabled={!renameTitle.trim() || rename.isPending} onClick={() => rename.mutate()}>{t("common.save")}</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <AlertDialog.Root open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
          <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(26rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-4 shadow-md outline-none">
            <AlertDialog.Title className="font-semibold">{t("conversation.deleteConfirmTitle")}</AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm text-muted">{t("conversation.deleteConfirmDescription")}</AlertDialog.Description>
            <div className="mt-4 flex justify-end gap-2">
              <AlertDialog.Cancel asChild><button type="button" className="btn-secondary">{t("common.cancel")}</button></AlertDialog.Cancel>
              <button type="button" className="btn-danger" disabled={remove.isPending} onClick={() => remove.mutate()}>{t("conversation.delete")}</button>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </div>
  );
}
