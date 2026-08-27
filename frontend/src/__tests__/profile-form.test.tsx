import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { ProviderPreset } from "@/api/client";
import { I18nProvider } from "@/lib/i18n";
import { ProfileForm } from "@/components/ProfileForm";

const presets: ProviderPreset[] = [
  {
    provider_id: "openai",
    display_name: "OpenAI",
    default_base_url: "https://api.openai.com/v1",
    default_model: "gpt-test",
    note: "test",
  },
];

function renderForm(onSaved = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <ProfileForm mode="create" presets={presets} onSaved={onSaved} />
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe("ProfileForm credential readiness", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("does not submit a profile without a credential ref", async () => {
    const create = vi.spyOn(api, "createProfile");
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByRole("radio", { name: /OpenAI/ }));
    await user.type(screen.getByLabelText("名称"), "主模型");
    await user.click(screen.getByRole("button", { name: "保存 profile" }));

    expect(screen.getByText("请填写凭据引用；没有引用的 profile 无法解析凭据。")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });

  it("submits after the ref is supplied", async () => {
    const create = vi.spyOn(api, "createProfile").mockResolvedValue({
      id: "openai-main",
      provider_id: "openai",
      display_name: "主模型",
      wire_api: "openai_chat_completions",
      base_url: "https://api.openai.com/v1",
      model: "gpt-test",
      credential_ref: "openai",
      credential: { configured: false, source: null, writable: true },
    });
    const onSaved = vi.fn();
    const user = userEvent.setup();
    renderForm(onSaved);

    await user.click(screen.getByRole("radio", { name: /OpenAI/ }));
    await user.type(screen.getByLabelText("名称"), "主模型");
    await user.type(screen.getByLabelText("凭据引用"), "openai");
    await user.click(screen.getByRole("button", { name: "保存 profile" }));

    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    expect(onSaved).toHaveBeenCalledWith("openai-main");
  });

  it("repairs a legacy profile whose credential ref is empty", async () => {
    const legacy = {
      id: "legacy",
      provider_id: "openai",
      display_name: "Legacy profile",
      wire_api: "openai_chat_completions",
      base_url: "https://api.openai.com/v1",
      model: "gpt-test",
      credential_ref: null,
      credential: { configured: false, source: null, writable: false },
    };
    const repaired = {
      ...legacy,
      credential_ref: "openai-main",
      credential: { configured: false, source: null, writable: true },
    };
    const update = vi.spyOn(api, "updateProfile").mockResolvedValue(repaired);
    const onSaved = vi.fn();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          <ProfileForm
            mode="edit"
            presets={presets}
            editing={legacy}
            onSaved={onSaved}
          />
        </I18nProvider>
      </QueryClientProvider>
    );

    await user.type(screen.getByLabelText("凭据引用"), "openai-main");
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(update.mock.calls[0][1].credential_ref).toBe("openai-main");
    expect(onSaved).toHaveBeenCalledWith("legacy");
  });
});
