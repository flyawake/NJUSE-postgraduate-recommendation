import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/lib/i18n";
import { ConversationView } from "@/components/ConversationView";

const mocks = vi.hoisted(() => ({
  getConversation: vi.fn(),
  listProfiles: vi.fn(),
  listTurns: vi.fn(),
  getInbox: vi.fn(),
  uploadAttachment: vi.fn(),
  startTurn: vi.fn(),
  deleteAttachment: vi.fn(),
}));

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      getConversation: mocks.getConversation,
      listProfiles: mocks.listProfiles,
      listTurns: mocks.listTurns,
      getInbox: mocks.getInbox,
      uploadAttachment: mocks.uploadAttachment,
      startTurn: mocks.startTurn,
      deleteAttachment: mocks.deleteAttachment,
    },
  };
});

vi.mock("@/lib/sse", () => ({
  subscribeToConversationEvents: vi.fn(() => new Promise<void>(() => undefined)),
  subscribeToInbox: vi.fn(() => new Promise<void>(() => undefined)),
}));

function conversation(id: string) {
  return {
    id,
    title: id,
    title_source: "user",
    workspace_path: "E:/workspace",
    workspace_key: "workspace",
    state: "active",
    version: 1,
    created_at: "2026-08-29T00:00:00Z",
    last_activity_at: "2026-08-29T00:00:00Z",
    profile_id: "profile-1",
    latest_turn: null,
    archived_at: null,
  };
}

describe("conversation reasoning preference", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    mocks.getConversation.mockImplementation(async (id: string) => conversation(id));
    mocks.listProfiles.mockResolvedValue([{
      id: "profile-1",
      provider_id: "openai",
      display_name: "OpenAI",
      wire_api: "openai_responses",
      base_url: "https://example.test/v1",
      model: "test-model",
      reasoning_effort: "medium",
      reasoning_mode: "auto",
      show_reasoning: false,
      credential_ref: null,
      credential: { configured: true, source: "local_file", writable: true },
    }]);
    mocks.listTurns.mockResolvedValue({ items: [], next_cursor: null });
    mocks.getInbox.mockResolvedValue({ queue_version: 1, items: [], recent_events: [] });
    mocks.uploadAttachment.mockResolvedValue({
      id: "attachment-1",
      filename: "screen.png",
      media_type: "image/png",
      kind: "image",
      size_bytes: 12,
    });
    mocks.startTurn.mockResolvedValue({
      id: "turn-1",
      conversation_id: "conversation-a",
      ordinal: 1,
      state: "starting",
      run_id: "run-1",
      user_text: "",
      created_at: "2026-08-29T00:00:00Z",
      active: true,
      attachments: [],
    });
    mocks.deleteAttachment.mockResolvedValue(undefined);
  });

  it("restores an independently selected effort after switching conversations", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    const view = (id: string) => (
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ConversationView key={id} conversationId={id} />
        </I18nProvider>
      </QueryClientProvider>
    );

    const { rerender } = render(view("conversation-a"));
    const firstEffort = await screen.findByTestId("conversation-reasoning-effort");
    await waitFor(() => expect(firstEffort).toHaveValue("medium"));
    await user.selectOptions(firstEffort, "high");

    rerender(view("conversation-b"));
    const secondEffort = await screen.findByTestId("conversation-reasoning-effort");
    await waitFor(() => expect(secondEffort).toHaveValue("medium"));
    await user.selectOptions(secondEffort, "low");

    rerender(view("conversation-a"));
    await waitFor(() => {
      expect(screen.getByTestId("conversation-reasoning-effort")).toHaveValue("high");
    });
    expect(localStorage.getItem("coding-agent-conversation-reasoning-effort:conversation-b")).toBe("low");
  });

  it("uploads an image and sends its reference with an attachment-only turn", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ConversationView conversationId="conversation-a" />
        </I18nProvider>
      </QueryClientProvider>
    );
    await screen.findByTestId("conversation-file-input");
    const file = new File(["image-bytes"], "screen.png", { type: "image/png" });
    await user.upload(screen.getByTestId("conversation-file-input"), file);
    await screen.findByText("screen.png");
    expect(screen.getByTestId("conversation-start")).toBeEnabled();
    await user.click(screen.getByTestId("conversation-start"));
    await waitFor(() => expect(mocks.startTurn).toHaveBeenCalled());
    expect(mocks.startTurn.mock.calls[0][1]).toMatchObject({
      content: "",
      attachment_ids: ["attachment-1"],
    });
  });

  it("removes a pending attachment through the server API", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <I18nProvider><ConversationView conversationId="conversation-a" /></I18nProvider>
      </QueryClientProvider>
    );
    const file = new File(["image-bytes"], "screen.png", { type: "image/png" });
    await user.upload(await screen.findByTestId("conversation-file-input"), file);
    await user.click(await screen.findByRole("button", { name: /移除附件 screen.png/ }));
    await waitFor(() => expect(mocks.deleteAttachment).toHaveBeenCalledWith("conversation-a", "attachment-1"));
    expect(screen.queryByText("screen.png")).not.toBeInTheDocument();
  });

  it("shows an upload error without adding a chip", async () => {
    mocks.uploadAttachment.mockRejectedValueOnce(new Error("不支持的附件"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <I18nProvider><ConversationView conversationId="conversation-a" /></I18nProvider>
      </QueryClientProvider>
    );
    const file = new File(["image-bytes"], "bad.png", { type: "image/png" });
    await user.upload(await screen.findByTestId("conversation-file-input"), file);
    await screen.findByText("不支持的附件");
    expect(screen.queryByTestId("pending-attachments")).not.toBeInTheDocument();
  });
});
