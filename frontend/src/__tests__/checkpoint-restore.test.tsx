import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationView } from "@/components/ConversationView";
import { I18nProvider } from "@/lib/i18n";
import { ApiError } from "@/api/client";

const mocks = vi.hoisted(() => ({
  listTurns: vi.fn(),
  previewCheckpoint: vi.fn(),
  restoreCheckpoint: vi.fn(),
}));

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      getConversation: vi.fn(async () => ({
        id: "conversation",
        title: "checkpoint",
        title_source: "user",
        workspace_path: "E:/workspace",
        workspace_key: "workspace",
        state: "active",
        version: 1,
        created_at: "2026-09-01T00:00:00Z",
        last_activity_at: "2026-09-01T00:00:00Z",
        profile_id: null,
        reasoning_effort: null,
        command_policy: "ask",
        latest_turn: null,
        archived_at: null,
      })),
      listProfiles: vi.fn(async () => []),
      listTurns: mocks.listTurns,
      getInbox: vi.fn(async () => ({ queue_version: 1, items: [], recent_events: [] })),
      getTurnChanges: vi.fn(async (_conversationId, turnId) => ({
        id: `changes-${turnId}`,
        conversation_id: "conversation",
        turn_id: turnId,
        status: "final",
        additions: 0,
        deletions: 0,
        file_count: 0,
        coverage: "complete",
        finalized_at: "2026-09-01T00:00:00Z",
        files: [],
      })),
      getTurnEvents: vi.fn(async () => []),
      getStreamSnapshot: vi.fn(async () => ({ checkpoints: [] })),
      getTurnMemoryUsage: vi.fn(async () => []),
      previewCheckpoint: mocks.previewCheckpoint,
      restoreCheckpoint: mocks.restoreCheckpoint,
    },
  };
});

vi.mock("@/lib/sse", () => ({
  subscribeToConversationEvents: vi.fn(() => new Promise<void>(() => undefined)),
  subscribeToInbox: vi.fn(() => new Promise<void>(() => undefined)),
}));

const turns = [
  {
    id: "turn-new",
    conversation_id: "conversation",
    ordinal: 2,
    state: "success",
    timeline_state: "active",
    run_id: "run-new",
    user_text: "new",
    created_at: "2026-09-01T00:01:00Z",
    active: false,
    result: { final_text: "new done" },
    attachments: [],
  },
  {
    id: "turn-old",
    conversation_id: "conversation",
    ordinal: 1,
    state: "success",
    timeline_state: "active",
    run_id: "run-old",
    user_text: "old",
    created_at: "2026-09-01T00:00:00Z",
    active: false,
    result: { final_text: "old done" },
    attachments: [],
  },
];

describe("checkpoint restore UI", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTurns.mockResolvedValue({ items: turns, next_cursor: null });
    mocks.previewCheckpoint.mockResolvedValue({
      conversation_id: "conversation",
      target_turn_id: "turn-old",
      target_ordinal: 1,
      future_turn_count: 1,
      file_count: 2,
      create_count: 0,
      modify_count: 1,
      delete_count: 1,
      restorable: true,
      coverage: "complete",
      affected_files: ["src/app.ts", "created.txt"],
      blockers: [],
      warnings: ["checkpoint_restores_captured_workspace_files_only"],
    });
  });

  it("previews the restore and reuses its idempotency key after a lost response", async () => {
    let attempt = 0;
    mocks.restoreCheckpoint.mockImplementation(async (_conversationId, _turnId, _input) => {
      attempt += 1;
      if (attempt === 1) throw new ApiError("transport_error", "", 0);
      mocks.listTurns.mockResolvedValue({ items: [turns[1]], next_cursor: null });
      return {
        operation_id: "restore-operation",
        conversation_id: "conversation",
        target_turn_id: "turn-old",
        state: "completed",
        superseded_turn_count: 1,
        restored_file_count: 2,
        completed_at: "2026-09-01T00:02:00Z",
      };
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <I18nProvider><ConversationView conversationId="conversation" /></I18nProvider>
      </QueryClientProvider>
    );

    await user.click(await screen.findByTestId("checkpoint-restore-turn-old"));
    expect(await screen.findByText("将回退 1 个后续轮次，影响 2 个文件。")).toBeInTheDocument();
    const confirm = screen.getByTestId("checkpoint-confirm");
    await user.click(confirm);
    await waitFor(() => expect(mocks.restoreCheckpoint).toHaveBeenCalledTimes(1));
    await screen.findByText("无法连接本地服务");
    await user.click(screen.getByTestId("checkpoint-confirm"));
    await waitFor(() => expect(mocks.restoreCheckpoint).toHaveBeenCalledTimes(2));
    expect(mocks.restoreCheckpoint.mock.calls[0][2].idempotency_key).toBe(
      mocks.restoreCheckpoint.mock.calls[1][2].idempotency_key
    );
    await waitFor(() => {
      expect(screen.queryByText("回到第 1 轮结束时？")).not.toBeInTheDocument();
    });
  });
});
