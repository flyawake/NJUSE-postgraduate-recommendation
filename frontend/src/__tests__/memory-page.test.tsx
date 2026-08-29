import { describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Memory } from "@/api/client";
import { MemoryPage } from "@/components/MemoryPage";
import { I18nProvider } from "@/lib/i18n";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      listMemories: vi.fn(),
      getMemorySettings: vi.fn(),
      setMemorySettings: vi.fn(),
      createMemory: vi.fn(),
      editMemory: vi.fn(),
      deleteMemory: vi.fn(),
      approveMemory: vi.fn(),
      rejectMemory: vi.fn(),
      resetMemories: vi.fn(),
    },
  };
});

import { api } from "@/api/client";

const candidate: Memory = {
  id: "memory-1",
  scope_type: "workspace",
  scope_key: "C:/repo",
  kind: "fact",
  title: "stack",
  content: "项目使用 FastAPI 和 React",
  status: "candidate",
  confirmation: "imported",
  source_conversation_id: "conv-a",
  source_turn_id: "turn-a",
  source_excerpt: null,
  supersedes_id: null,
  version: 1,
  normalized_hash: "hash",
  created_at: "2026-08-29T00:00:00+00:00",
  updated_at: "2026-08-29T00:00:00+00:00",
  last_used_at: null,
  use_count: 0,
  sources: [],
};

function renderPage(props: ComponentProps<typeof MemoryPage> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>
        <MemoryPage {...props} />
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe("Memory Center", () => {
  it("lists active memories and approves a candidate", async () => {
    const listMemories = api.listMemories as ReturnType<typeof vi.fn>;
    const getMemorySettings = api.getMemorySettings as ReturnType<typeof vi.fn>;
    const approveMemory = api.approveMemory as ReturnType<typeof vi.fn>;
    listMemories.mockResolvedValue({ items: [candidate], next_cursor: null });
    getMemorySettings.mockResolvedValue({ enabled: true, candidate_enabled: false, scope_type: "global", scope_key: "global" });
    approveMemory.mockImplementation(async () => ({ ...candidate, status: "confirmed" }));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("项目使用 FastAPI 和 React")).toBeInTheDocument();
    expect(screen.getByText(/conv-a/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "批准" }));
    await waitFor(() => expect(approveMemory).toHaveBeenCalledWith(candidate.id, expect.objectContaining({ expected_version: 1, idempotency_key: expect.any(String) })));
  });

  it("opens a source and requires confirmation before hard delete", async () => {
    const listMemories = api.listMemories as ReturnType<typeof vi.fn>;
    const getMemorySettings = api.getMemorySettings as ReturnType<typeof vi.fn>;
    const deleteMemory = api.deleteMemory as ReturnType<typeof vi.fn>;
    listMemories.mockResolvedValue({ items: [{ ...candidate, status: "confirmed" }], next_cursor: null });
    getMemorySettings.mockResolvedValue({ enabled: true, candidate_enabled: false, scope_type: "global", scope_key: "global" });
    deleteMemory.mockResolvedValue(undefined);
    const openSource = vi.fn();
    const user = userEvent.setup();
    renderPage({ onOpenSource: openSource });

    await user.click(await screen.findByRole("button", { name: /来源 conv-a/ }));
    expect(openSource).toHaveBeenCalledWith("conv-a", "turn-a");

    await user.click(screen.getByRole("button", { name: "删除" }));
    expect(deleteMemory).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "删除" });
    await user.click(dialog.querySelector("button.btn-primary")!);
    await waitFor(() => expect(deleteMemory).toHaveBeenCalledWith(candidate.id, expect.objectContaining({ expected_version: 1, idempotency_key: expect.any(String) })));
  });

  it("loads a bounded next page and updates a workspace override", async () => {
    const listMemories = api.listMemories as ReturnType<typeof vi.fn>;
    const getMemorySettings = api.getMemorySettings as ReturnType<typeof vi.fn>;
    const setMemorySettings = api.setMemorySettings as ReturnType<typeof vi.fn>;
    listMemories.mockImplementation(async (params?: { cursor?: string }) => params?.cursor
      ? { items: [{ ...candidate, id: "memory-2", content: "second page" }], next_cursor: null }
      : { items: [candidate], next_cursor: "next-page" });
    getMemorySettings.mockResolvedValue({ enabled: true, candidate_enabled: false, scope_type: "global", scope_key: "global" });
    setMemorySettings.mockResolvedValue({ enabled: false, candidate_enabled: false, scope_type: "workspace", scope_key: "C:/repo" });
    const user = userEvent.setup();
    renderPage({ defaultWorkspace: "C:/repo" });

    await user.click(await screen.findByRole("button", { name: "加载更多" }));
    expect(await screen.findByText("second page")).toBeInTheDocument();
    expect(listMemories).toHaveBeenCalledWith(expect.objectContaining({ cursor: "next-page", limit: 100 }));

    await user.selectOptions(screen.getByTestId("memory-mode-scope"), "workspace");
    await user.click(screen.getByTestId("memory-enabled"));
    await waitFor(() => expect(setMemorySettings).toHaveBeenCalledWith({ scope_type: "workspace", scope_key: "C:/repo", enabled: false }));
  });
});
