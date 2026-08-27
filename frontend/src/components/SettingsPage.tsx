import { useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Plus, Trash2 } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { Profile } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { InlineError } from "./InlineError";
import { ProfileForm } from "./ProfileForm";
import { CredentialField } from "./CredentialField";

function isProfileUsable(profile: Profile): boolean {
  return profile.credential.configured && profile.wire_api === "openai_chat_completions";
}

export function SettingsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"list" | "form">("list");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Profile | null>(null);

  const bootstrap = useQuery({ queryKey: ["bootstrap"], queryFn: api.bootstrap, staleTime: 30_000 });
  const profilesQuery = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles, staleTime: 5_000 });

  const profiles = profilesQuery.data ?? [];
  const presets = bootstrap.data?.provider_presets ?? [];
  const activeId = bootstrap.data?.active_profile_id ?? null;

  const editing = profiles.find((profile) => profile.id === selectedId) ?? null;
  const [pageError, setPageError] = useState<string | null>(null);

  const activateMutation = useMutation({
    mutationFn: (id: string) => api.activateProfile(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
    },
    onError: (err: unknown) => setPageError(err instanceof ApiError ? err.message : t("error.validation")),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteProfile(id),
    onSuccess: () => {
      setDeleteTarget(null);
      if (selectedId) setSelectedId(null);
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
    },
    onError: (err: unknown) => {
      setPageError(err instanceof ApiError ? err.message : t("error.validation"));
      setDeleteTarget(null);
    },
  });

  const openCreate = () => {
    setSelectedId(null);
    setTab("form");
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 lg:px-8">
      <header>
        <h1 className="text-lg font-semibold">{t("settings.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("settings.subtitle")}</p>
      </header>

      <Tabs.Root value={tab} onValueChange={(next) => setTab(next as "list" | "form")} data-testid="settings-tabs">
        <Tabs.List className="flex gap-1 border-b border-border">
          <Tabs.Trigger
            value="list"
            className="px-3 py-2 text-sm font-medium text-muted transition-colors duration-fast data-[state=active]:border-b-2 data-[state=active]:border-accent data-[state=active]:text-text"
          >
            {t("settings.profiles")}
          </Tabs.Trigger>
          <Tabs.Trigger
            value="form"
            className="px-3 py-2 text-sm font-medium text-muted transition-colors duration-fast data-[state=active]:border-b-2 data-[state=active]:border-accent data-[state=active]:text-text"
            onClick={openCreate}
          >
            {t("settings.create")}
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="list" className="pt-4">
          {profiles.length === 0 ? (
            <p className="text-sm text-muted">{t("settings.empty")}</p>
          ) : (
            <ul className="space-y-2">
              {profiles.map((profile) => (
                <li
                  key={profile.id}
                  className={selectedId === profile.id ? "rounded-lg border border-accent bg-accent-muted/40 p-3" : "card p-3"}
                  data-testid={`profile-row-${profile.id}`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 text-sm font-medium">
                        <span className="truncate">{profile.display_name}</span>
                        {profile.id === activeId ? (
                          <span className="rounded-sm bg-success-muted px-1.5 py-0.5 text-[11px] font-medium text-success">
                            {t("settings.active")}
                          </span>
                        ) : null}
                      </p>
                      <p className="mono text-xs text-faint">
                        {profile.id} · {profile.provider_id} · {profile.model}
                      </p>
                    </div>
                    <div className="flex items-center gap-1">
                      <span className={profile.credential.configured ? "text-xs text-success" : "text-xs text-warning"}>
                        {profile.credential.configured
                          ? profile.credential.source === "env"
                            ? t("settings.credential.envProvided")
                            : t("settings.credential.set")
                          : t("profile.missingCredential")}
                      </span>
                      <button
                        type="button"
                        className="btn-icon"
                        aria-label={t("settings.edit")}
                        title={t("settings.edit")}
                        onClick={() => {
                          setTab("list");
                          setSelectedId(profile.id);
                        }}
                      >
                        <Pencil aria-hidden size={14} />
                      </button>
                      {profile.id !== activeId ? (
                        <button
                          type="button"
                          className="btn-icon"
                          aria-label={t("settings.activate")}
                          title={t("settings.activate")}
                          onClick={() => activateMutation.mutate(profile.id)}
                        >
                          <Check aria-hidden size={14} />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="btn-icon"
                        aria-label={t("settings.delete")}
                        title={t("settings.delete")}
                        onClick={() => setDeleteTarget(profile)}
                      >
                        <Trash2 aria-hidden size={14} />
                      </button>
                    </div>
                  </div>
                  {!isProfileUsable(profile) ? (
                    <p className="mt-1.5 text-xs text-warning">{t("profile.unavailable")}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}

          {editing ? (
            <div className="mt-4 rounded-lg border border-border bg-surface p-4">
              <ProfileForm
                mode="edit"
                presets={presets}
                editing={{
                  id: editing.id,
                  provider_id: editing.provider_id,
                  display_name: editing.display_name,
                  base_url: editing.base_url,
                  model: editing.model,
                  credential_ref: editing.credential_ref ?? null,
                }}
                onSaved={() => undefined}
                onCancel={() => setSelectedId(null)}
              />              <div className="mt-4 border-t border-border pt-4">
                {editing.credential_ref ? (
                  <CredentialField profile={editing} />
                ) : (
                  <p className="text-xs text-faint">{t("form.credentialRefHint")}</p>
                )}
              </div>
            </div>
          ) : null}
        </Tabs.Content>

        <Tabs.Content value="form" className="pt-4">
          <div className="rounded-lg border border-border bg-surface p-4">
            <ProfileForm
              mode="create"
              presets={presets}
              onSaved={() => setTab("list")}
              onCancel={() => setTab("list")}
            />          </div>
        </Tabs.Content>
      </Tabs.Root>

      {pageError ? (
        <InlineError kind="configuration" message={pageError} onRetry={() => setPageError(null)} />
      ) : null}

      <AlertDialog.Root open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
          <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-5 shadow-md">
            <AlertDialog.Title className="text-base font-semibold">
              {t("settings.deleteTitle", { name: deleteTarget?.display_name ?? "" })}
            </AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm text-muted">
              {t("settings.deleteDescription")}
            </AlertDialog.Description>
            <div className="mt-4 flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <button type="button" className="btn-secondary">
                  {t("settings.cancel")}
                </button>
              </AlertDialog.Cancel>
              <AlertDialog.Action asChild>
                <button
                  type="button"
                  className="btn-danger"
                  onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
                >
                  <Trash2 aria-hidden size={14} />
                  {t("settings.confirmDelete")}
                </button>
              </AlertDialog.Action>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </div>
  );
}

export function AddProfileButton({ onClick }: { onClick: () => void }) {
  const { t } = useI18n();
  return (
    <button type="button" className="btn-secondary" onClick={onClick}>
      <Plus aria-hidden size={14} />
      {t("settings.create")}
    </button>
  );
}
