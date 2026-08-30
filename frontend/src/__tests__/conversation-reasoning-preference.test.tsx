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
  updateConversationPreferences: vi.fn(),
  updateConversationCommandPolicy: vi.fn(),
  preferences: new Map<string, string | null>(),
  commandPolicies: new Map<string, "ask" | "allow" | "deny">(),
  uploadAttachment: vi.fn(),
  startTurn: vi.fn(),
  deleteAttachment: vi.fn(),
  listTurnPermissions: vi.fn(),
  resolveTurnPermission: vi.fn(),
  getTurnEvents: vi.fn(),
  getStreamSnapshot: vi.fn(),
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
      updateConversationPreferences: mocks.updateConversationPreferences,
      updateConversationCommandPolicy: mocks.updateConversationCommandPolicy,
      uploadAttachment: mocks.uploadAttachment,
      startTurn: mocks.startTurn,
      deleteAttachment: mocks.deleteAttachment,
      listTurnPermissions: mocks.listTurnPermissions,
      resolveTurnPermission: mocks.resolveTurnPermission,
      getTurnEvents: mocks.getTurnEvents,
      getStreamSnapshot: mocks.getStreamSnapshot,
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
    reasoning_effort: mocks.preferences.get(id) ?? null,
    command_policy: mocks.commandPolicies.get(id) ?? "ask",
    latest_turn: null,
    archived_at: null,
  };
}

describe("conversation reasoning preference", () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.preferences.clear();
    mocks.commandPolicies.clear();
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
    mocks.updateConversationPreferences.mockImplementation(
      async (id: string, input: { reasoning_effort?: string | null }) => {
        mocks.preferences.set(id, input.reasoning_effort ?? null);
        return conversation(id);
      },
    );
    mocks.updateConversationCommandPolicy.mockImplementation(
      async (id: string, input: { command_policy: "ask" | "allow" | "deny" }) => {
        mocks.commandPolicies.set(id, input.command_policy);
        return conversation(id);
      },
    );
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
    mocks.listTurnPermissions.mockResolvedValue([]);
    mocks.resolveTurnPermission.mockResolvedValue(undefined);
    mocks.getTurnEvents.mockResolvedValue([]);
    mocks.getStreamSnapshot.mockResolvedValue({ checkpoints: [] });
  });

  it("restores an independently selected effort after switching conversations", async () => {
    const user = userEvent.setup();
    const view = (client: QueryClient, id: string) => (
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ConversationView key={id} conversationId={id} />
        </I18nProvider>
      </QueryClientProvider>
    );

    const firstClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const firstRender = render(view(firstClient, "conversation-a"));
    const firstEffort = await screen.findByTestId("conversation-reasoning-effort");
    await waitFor(() => expect(firstEffort).toHaveValue("medium"));
    await user.selectOptions(firstEffort, "high");
    await waitFor(() => expect(mocks.preferences.get("conversation-a")).toBe("high"));

    firstRender.rerender(view(firstClient, "conversation-b"));
    const secondEffort = await screen.findByTestId("conversation-reasoning-effort");
    await waitFor(() => expect(secondEffort).toHaveValue("medium"));
    await user.selectOptions(secondEffort, "low");
    await waitFor(() => expect(mocks.preferences.get("conversation-b")).toBe("low"));

    firstRender.unmount();
    localStorage.clear();
    const restartedClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const restartedRender = render(view(restartedClient, "conversation-a"));
    await waitFor(() => {
      expect(screen.getByTestId("conversation-reasoning-effort")).toHaveValue("high");
    });
    restartedRender.rerender(view(restartedClient, "conversation-b"));
    await waitFor(() => {
      expect(screen.getByTestId("conversation-reasoning-effort")).toHaveValue("low");
    });
  });

  it("persists an independent command policy for every conversation", async () => {
    const user = userEvent.setup();
    const view = (client: QueryClient, id: string) => (
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ConversationView key={id} conversationId={id} />
        </I18nProvider>
      </QueryClientProvider>
    );

    const firstClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const firstRender = render(view(firstClient, "conversation-a"));
    const firstPolicy = await screen.findByTestId("conversation-command-policy");
    await waitFor(() => expect(firstPolicy).toBeEnabled());
    expect(firstPolicy).toHaveValue("ask");
    await user.selectOptions(firstPolicy, "allow");
    await screen.findByTestId("command-policy-allow-warning");
    expect(mocks.updateConversationCommandPolicy).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("command-policy-allow-cancel"));
    expect(firstPolicy).toHaveValue("ask");
    expect(mocks.commandPolicies.get("conversation-a")).toBeUndefined();

    await user.selectOptions(firstPolicy, "allow");
    await screen.findByTestId("command-policy-allow-warning");
    await user.click(screen.getByTestId("command-policy-allow-confirm"));
    await waitFor(() => expect(mocks.commandPolicies.get("conversation-a")).toBe("allow"));

    firstRender.rerender(view(firstClient, "conversation-b"));
    const secondPolicy = await screen.findByTestId("conversation-command-policy");
    await waitFor(() => {
      expect(secondPolicy).toBeEnabled();
      expect(secondPolicy).toHaveValue("ask");
    });
    await user.selectOptions(secondPolicy, "deny");
    await waitFor(() => expect(mocks.commandPolicies.get("conversation-b")).toBe("deny"));

    firstRender.unmount();
    localStorage.clear();
    const restartedClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const restartedRender = render(view(restartedClient, "conversation-a"));
    await waitFor(() => {
      expect(screen.getByTestId("conversation-command-policy")).toHaveValue("allow");
    });
    restartedRender.rerender(view(restartedClient, "conversation-b"));
    await waitFor(() => {
      expect(screen.getByTestId("conversation-command-policy")).toHaveValue("deny");
    });
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

  it("blocks on a host-command permission dialog and resolves one-time approval", async () => {
    const activeTurn = {
      id: "turn-active",
      conversation_id: "conversation-a",
      ordinal: 1,
      state: "running",
      run_id: "run-active",
      user_text: "verify",
      created_at: "2026-08-29T00:00:00Z",
      active: true,
      attachments: [],
    };
    const permission = {
      id: "permission-1",
      conversation_id: "conversation-a",
      turn_id: "turn-active",
      call_id: "call-1",
      tool_name: "run_command",
      executable: "python.exe",
      argv: ["python.exe", "-c", "print('ok')"],
      cwd: ".",
      purpose: "verify",
      capabilities: ["start_host_process"],
      created_at: 1,
    };
    mocks.listTurns.mockResolvedValue({ items: [activeTurn], next_cursor: null });
    mocks.listTurnPermissions.mockResolvedValue([permission]);
    mocks.resolveTurnPermission.mockImplementation(async () => {
      mocks.listTurnPermissions.mockResolvedValue([]);
      return { ...permission, decision: "allow" };
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <I18nProvider><ConversationView conversationId="conversation-a" /></I18nProvider>
      </QueryClientProvider>
    );

    await screen.findByTestId("command-permission-dialog");
    expect(screen.getByText(/print\('ok'\)/)).toBeInTheDocument();
    await user.click(screen.getByTestId("command-permission-allow"));
    await waitFor(() => expect(mocks.resolveTurnPermission).toHaveBeenCalledWith(
      "conversation-a", "turn-active", "permission-1", "allow"
    ));
  });
});
