import * as Select from "@radix-ui/react-select";
import { Check, ChevronDown, Loader2, TriangleAlert } from "lucide-react";
import type { Profile } from "@/api/client";
import { useI18n } from "@/lib/i18n";

export type ProfileState = "loading" | "ready" | "missing-credential" | "unavailable";

export interface ProfileSelectorProps {
  profiles: Profile[] | undefined;
  activeProfileId: string | null;
  value: string | null;
  onChange: (profileId: string | null) => void;
  disabled?: boolean;
  id?: string;
}

function profileState(profile: Profile, activeProfileId: string | null): ProfileState {
  if (profile.id === activeProfileId) {
    return profile.credential?.configured ? "ready" : "missing-credential";
  }
  return profile.credential?.configured ? "ready" : "missing-credential";
}

/** Radix Select wrapper: loading / ready / missing-credential / unavailable. */
export function ProfileSelector({
  profiles,
  activeProfileId,
  value,
  onChange,
  disabled,
  id,
}: ProfileSelectorProps) {
  const { t } = useI18n();
  const loading = profiles === undefined;
  const selected = profiles?.find((profile) => profile.id === value) ?? null;

  return (
    <div>
      <label htmlFor={id ?? "profile"} className="sr-only">
        {t("profile.label")}
      </label>
      <Select.Root
        value={value ?? "inherit"}
        onValueChange={(next) => onChange(next === "inherit" ? null : next)}
        disabled={disabled || loading}
      >
        <Select.Trigger
          id={id ?? "profile"}
          className="input flex items-center justify-between gap-2 text-left"
          aria-label={t("profile.label")}
        >
          <span className="flex min-w-0 items-center gap-1.5 truncate">
            {loading ? <Loader2 size={14} className="animate-spin text-faint" aria-hidden /> : null}
            {selected ? (
              <>
                <span className="truncate">{selected.display_name}</span>
                <span className="mono text-xs text-faint">{selected.model}</span>
                {!selected.credential?.configured ? (
                  <TriangleAlert size={13} className="text-warning" aria-label={t("profile.missingCredential")} />
                ) : null}
              </>
            ) : (
              <span className="text-muted">{t("profile.select.placeholder")}</span>
            )}
          </span>
          <Select.Icon>
            <ChevronDown size={14} className="text-faint" aria-hidden />
          </Select.Icon>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            position="popper"
            sideOffset={4}
            className="z-50 max-h-72 min-w-[16rem] overflow-auto rounded-md border border-border bg-surface p-1 shadow-md"
          >
            <Select.Viewport>
              <Select.Item
                value="inherit"
                className="flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-2"
              >
                <Select.ItemIndicator>
                  <Check size={14} className="text-accent" aria-hidden />
                </Select.ItemIndicator>
                <span className="flex-1">{t("profile.inheritActive")}</span>
                <span className="mono text-xs text-faint">
                  {activeProfileId ? activeProfileId : "–"}
                </span>
              </Select.Item>
              {profiles?.map((profile) => {
                const state = profileState(profile, activeProfileId);
                return (
                  <Select.Item
                    key={profile.id}
                    value={profile.id}
                    className="flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-2"
                  >
                    <Select.ItemIndicator>
                      <Check size={14} className="text-accent" aria-hidden />
                    </Select.ItemIndicator>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{profile.display_name}</span>
                      <span className="mono block text-xs text-faint">
                        {profile.provider_id} · {profile.model}
                      </span>
                    </span>
                    {state === "missing-credential" ? (
                      <span className="rounded-sm bg-warning-muted px-1.5 py-0.5 text-[11px] font-medium text-warning">
                        {t("profile.missingCredential")}
                      </span>
                    ) : null}
                  </Select.Item>
                );
              })}
              {profiles && profiles.length === 0 ? (
                <div className="px-2 py-3 text-sm text-muted">{t("profile.noneConfigured")}</div>
              ) : null}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  );
}
