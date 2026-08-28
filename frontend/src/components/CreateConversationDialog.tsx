import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { api } from "@/api/client";
import type { Conversation, Profile } from "@/api/client";
import { apiErrorText } from "@/lib/errorText";
import { useI18n } from "@/lib/i18n";
import { InlineError } from "./InlineError";
import { ProfileSelector } from "./ProfileSelector";
import { WorkspaceField } from "./WorkspaceField";

interface CreateConversationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialWorkspace: string;
  initialProfileId: string | null;
  activeProfileId: string | null;
  profiles: Profile[];
  onCreated: (conversation: Conversation) => void;
}

export function CreateConversationDialog({
  open,
  onOpenChange,
  initialWorkspace,
  initialProfileId,
  activeProfileId,
  profiles,
  onCreated,
}: CreateConversationDialogProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [workspaceValid, setWorkspaceValid] = useState(false);
  const [profileId, setProfileId] = useState<string | null>(initialProfileId);

  useEffect(() => {
    if (!open) return;
    setWorkspace(initialWorkspace);
    setWorkspaceValid(false);
    setProfileId(initialProfileId);
  }, [open, initialWorkspace, initialProfileId]);

  const create = useMutation({
    mutationFn: () => api.createConversation({
      workspace: workspace.trim(),
      profile_id: profileId ?? activeProfileId ?? null,
      title: null,
    }),
    onSuccess: async (conversation) => {
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      onCreated(conversation);
      onOpenChange(false);
    },
  });

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(42rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-5 shadow-md outline-none">
          <div className="flex items-center justify-between">
            <Dialog.Title className="text-base font-semibold">{t("conversation.createTitle")}</Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" className="btn-icon" aria-label={t("common.close")}>
                <X aria-hidden size={16} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="mt-1 text-sm text-muted">
            {t("conversation.createDescription")}
          </Dialog.Description>
          <div className="mt-4 space-y-3">
            <WorkspaceField
              id="conversation-workspace"
              value={workspace}
              onChange={(value) => {
                setWorkspace(value);
                setWorkspaceValid(false);
              }}
              onValidated={(resolved) => setWorkspaceValid(resolved !== null)}
            />
            <ProfileSelector
              id="conversation-profile"
              profiles={profiles}
              activeProfileId={activeProfileId}
              value={profileId}
              onChange={setProfileId}
            />
            {create.isError ? (
              <InlineError kind="validation" message={apiErrorText(create.error, t)} />
            ) : null}
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Dialog.Close asChild>
              <button type="button" className="btn-secondary">{t("common.cancel")}</button>
            </Dialog.Close>
            <button
              type="button"
              className="btn-primary"
              disabled={!workspaceValid || create.isPending}
              onClick={() => create.mutate()}
              data-testid="confirm-create-conversation"
            >
              {create.isPending ? t("common.loading") : t("nav.newConversation")}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
