import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { ProviderPreset } from "@/api/client";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/errorText";
import { cx } from "@/lib/format";
import { providerUrlError } from "@/lib/validate";
import { InlineError } from "./InlineError";

export type ProfileFormMode = "create" | "edit";

export interface ProfileFormProps {
  mode: ProfileFormMode;
  /** create mode: presets to choose from; edit mode: the fixed provider_id. */
  presets: ProviderPreset[];
  editing?: {
    id: string;
    provider_id: string;
    display_name: string;
    base_url: string;
    model: string;
    credential_ref: string | null;
    wire_api?: string;
    reasoning_mode?: string;
    show_reasoning?: boolean;
  };
  onSaved: (profileId: string) => void;
  onCancel?: () => void;
}

interface FieldErrors {
  providerId?: string;
  displayName?: string;
  baseUrl?: string;
  model?: string;
  credentialRef?: string;
}

/**
 * Profile form: name -> URL -> model -> credential ref, real-time validation
 * with errors next to fields. Contract states: create/edit/dirty/saving/error/saved.
 */
export function ProfileForm({ mode, presets, editing, onSaved, onCancel }: ProfileFormProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [providerId, setProviderId] = useState(editing?.provider_id ?? "");
  const [displayName, setDisplayName] = useState(editing?.display_name ?? "");
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? "");
  const [model, setModel] = useState(editing?.model ?? "");
  const [credentialRef, setCredentialRef] = useState(editing?.credential_ref ?? "");
  const [wireApi, setWireApi] = useState(editing?.wire_api ?? "openai_chat_completions");
  const [reasoningMode, setReasoningMode] = useState(editing?.reasoning_mode ?? "auto");
  const [showReasoning, setShowReasoning] = useState(editing?.show_reasoning ?? false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const effectiveProvider = mode === "create"
    ? providerId
    : (editing?.provider_id ?? providerId);
  const supportsVisibleReasoning = wireApi === "openai_responses"
    || effectiveProvider !== "openai";

  const dirty = useMemo(
    () =>
      displayName !== (editing?.display_name ?? "") ||
      baseUrl !== (editing?.base_url ?? "") ||
      model !== (editing?.model ?? "") ||
      credentialRef !== (editing?.credential_ref ?? "") ||
      wireApi !== (editing?.wire_api ?? "openai_chat_completions") ||
      reasoningMode !== (editing?.reasoning_mode ?? "auto") ||
      showReasoning !== (editing?.show_reasoning ?? false) ||
      (mode === "create" && providerId !== ""),
    [mode, displayName, baseUrl, model, credentialRef, wireApi, reasoningMode, showReasoning, providerId, editing]
  );

  const saveMutation = useMutation({
    mutationFn: () => {
      const input = {
        provider_id: mode === "create" ? providerId : (editing?.provider_id ?? providerId),
        display_name: displayName.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        credential_ref: credentialRef.trim() ? credentialRef.trim() : null,
        wire_api: wireApi,
        reasoning_mode: reasoningMode,
        show_reasoning: showReasoning,
      };
      return mode === "create"
        ? api.createProfile(input)
        : api.updateProfile(editing?.id ?? "", input);
    },
    onSuccess: (profile) => {
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      onSaved(profile.id);
    },
    onError: (error: unknown) => {
      setSubmitError(apiErrorText(error, t, "error.validation"));
    },
  });

  const validate = (): boolean => {
    const next: FieldErrors = {};
    if (mode === "create" && !providerId) next.providerId = t("form.providerId.help");
    if (mode === "create" && providerId && !/^(openai|deepseek|custom)$/.test(providerId)) {
      next.providerId = t("form.providerId.help");
    }
    if (!displayName.trim()) next.displayName = t("error.validation");
    const urlError = providerUrlError(baseUrl);
    if (urlError) next.baseUrl = t("error.validation");
    if (!model.trim()) next.model = t("error.validation");
    if (!credentialRef.trim()) next.credentialRef = t("form.credentialRefRequired");
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        setSubmitError(null);
        if (validate()) saveMutation.mutate();
      }}
      data-testid="profile-form"
    >
      {saved ? (
        <p className="text-xs font-medium text-success" role="status">
          {t("settings.saved")} · {t("settings.idNote")}
        </p>
      ) : null}

      {mode === "create" ? (
        <div>
          <fieldset>
            <legend className="mb-1.5 text-sm font-medium">{t("settings.providerCard.title")}</legend>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3" data-testid="provider-cards">
              {presets.map((preset) => {
                const selected = providerId === preset.provider_id;
                return (
                  <button
                    type="button"
                    key={preset.provider_id}
                    role="radio"
                    aria-checked={selected}
                    onClick={() => {
                      setProviderId(preset.provider_id);
                      if (preset.provider_id === "deepseek" && wireApi === "openai_responses") {
                        setWireApi("openai_chat_completions");
                      }
                      if (!baseUrl.trim() && preset.default_base_url) setBaseUrl(preset.default_base_url);
                      if (!model.trim() && preset.default_model) setModel(preset.default_model);
                    }}
                    className={cx(
                      "rounded-md border px-3 py-2.5 text-left transition-colors duration-fast",
                      selected ? "border-accent bg-accent-muted" : "border-border bg-surface hover:bg-surface-2"
                    )}
                  >
                    <span className="block text-sm font-medium">
                      {t(`settings.providerCard.${preset.provider_id}`)}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted">
                      {t(`settings.providerCard.${preset.provider_id}Note`)}
                    </span>
                  </button>
                );
              })}
            </div>
          </fieldset>
          {errors.providerId ? <FieldError text={errors.providerId} /> : null}
        </div>
      ) : (
        <p className="text-sm text-muted">
          <span className="mono">{editing?.id}</span> · {editing?.provider_id}
        </p>
      )}

      <Field
        id="form-display-name"
        label={t("form.displayName")}
        value={displayName}
        onChange={setDisplayName}
        placeholder={t("form.displayName.placeholder")}
        error={errors.displayName}
      />
      <Field
        id="form-base-url"
        label={t("form.baseUrl")}
        value={baseUrl}
        onChange={setBaseUrl}
        placeholder={t("form.baseUrl.placeholder")}
        error={errors.baseUrl}
        hint={providerUrlError(baseUrl) ? t("error.validation") : undefined}
      />
      <Field
        id="form-model"
        label={t("form.model")}
        value={model}
        onChange={setModel}
        placeholder={t("form.model.placeholder")}
        error={errors.model}
      />
      <Field
        id="form-credential-ref"
        label={t("form.credentialRef")}
        value={credentialRef}
        onChange={setCredentialRef}
        placeholder={t("form.credentialRef.placeholder")}
        error={errors.credentialRef}
        hint={t("form.credentialRefHint")}
      />

      <div>
        <label htmlFor="form-wire-api" className="mb-1 block text-sm font-medium">
          {t("form.wireApi")}
        </label>
        <select
          id="form-wire-api"
          className="input"
          value={wireApi}
          onChange={(event) => {
            const next = event.target.value;
            setWireApi(next);
            if (next === "openai_chat_completions") {
              if (effectiveProvider === "openai" && reasoningMode === "visible") {
                setReasoningMode("auto");
              }
            }
          }}
        >
          <option value="openai_chat_completions">{t("form.wireApi.chat")}</option>
          <option value="openai_responses" disabled={effectiveProvider === "deepseek"}>
            {t("form.wireApi.responses")}
          </option>
        </select>
        <p className="mt-1 text-xs text-faint">{t("form.wireApiHint")}</p>
      </div>

      <div>
        <label htmlFor="form-reasoning-mode" className="mb-1 block text-sm font-medium">
          {t("form.reasoningMode")}
        </label>
        <select
          id="form-reasoning-mode"
          className="input"
          value={reasoningMode}
          onChange={(event) => setReasoningMode(event.target.value)}
        >
          <option value="auto">{t("form.reasoningMode.auto")}</option>
          <option value="off">{t("form.reasoningMode.off")}</option>
          <option value="visible" disabled={!supportsVisibleReasoning}>
            {t("form.reasoningMode.visible")}
          </option>
        </select>
      </div>
      <label className="flex items-center gap-2 text-sm text-muted">
        <input
          type="checkbox"
          checked={showReasoning}
          onChange={(event) => setShowReasoning(event.target.checked)}
        />
        {t("form.showReasoning")}
      </label>

      <p className="text-xs text-faint">{t("settings.wireApiNote")}</p>

      {submitError ? <InlineError kind="validation" message={submitError} /> : null}

      <div className="flex items-center gap-2">
        <button type="submit" className="btn-primary" disabled={saveMutation.isPending || !dirty}>
          {saveMutation.isPending ? t("settings.saving") : mode === "create" ? t("form.save") : t("settings.savedChanged")}
        </button>
        {onCancel ? (
          <button type="button" className="btn-secondary" onClick={onCancel}>
            {t("settings.cancel")}
          </button>
        ) : null}
      </div>
      <p className="text-xs text-faint">{t("form.idNote")}</p>
    </form>
  );
}

function FieldError({ id, text }: { id?: string; text: string }) {
  return (
    <p id={id} className="mt-1 text-xs text-danger" role="alert">
      {text}
    </p>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  placeholder,
  error,
  hint,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  error?: string;
  hint?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        className={cx("input", error && "border-danger")}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
        spellCheck={false}
        autoComplete="off"
      />
      {error ? (
        <FieldError id={`${id}-error`} text={error} />
      ) : hint ? (
        <p className="mt-1 text-xs text-faint">{hint}</p>
      ) : null}
    </div>
  );
}
