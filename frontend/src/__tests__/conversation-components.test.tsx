import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { FileChange } from "@/api/client";
import { ArtifactPreviewPane } from "@/components/ArtifactPreviewPane";
import { TurnChangeSummary } from "@/components/TurnChangeSummary";
import { I18nProvider } from "@/lib/i18n";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: { ...actual.api, getFilePreview: vi.fn() },
  };
});

import { api } from "@/api/client";

const file: FileChange = {
  id: "change-1",
  relative_path: "src/app.py",
  old_relative_path: null,
  change_type: "modified",
  source: "tool_confirmed",
  before_blob_id: "a",
  after_blob_id: "b",
  before_sha: "a",
  after_sha: "b",
  additions: 1,
  deletions: 1,
  binary: false,
  preview_status: "available",
  warnings: [],
};

function wrapper(element: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>{element}</I18nProvider>
    </QueryClientProvider>
  );
}

describe("turn change review", () => {
  it("renders product wording and opens the selected historical file", async () => {
    const open = vi.fn();
    const user = userEvent.setup();
    wrapper(
      <TurnChangeSummary
        changeSet={{
          id: "set",
          conversation_id: "conversation",
          turn_id: "turn",
          status: "final",
          additions: 1,
          deletions: 1,
          file_count: 1,
          coverage: "incomplete",
          finalized_at: null,
          files: [file],
        }}
        onOpenFile={open}
      />
    );
    expect(screen.getByText(/改动了 1 个文件/)).toBeInTheDocument();
    expect(screen.getByText("部分命令产生的文件变化可能未被完整识别。")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("coverage");
    await user.click(screen.getByTestId("change-file-src/app.py"));
    expect(open).toHaveBeenCalledWith(file);
  });

  it("defaults modified files to diff and can fetch current mode", async () => {
    const mockedPreview = api.getFilePreview as ReturnType<typeof vi.fn>;
    mockedPreview.mockImplementation(async (_c, _t, _f, mode) => ({
      change_id: file.id,
      relative_path: file.relative_path,
      change_type: file.change_type,
      mode: String(mode),
      lines: mode === "current" ? ["current"] : ["- old", "+ new"],
      additions: 1,
      deletions: 1,
      truncated: false,
      binary: false,
      before_sha: "a",
      after_sha: "b",
      current_sha: "b",
      diverged: false,
      error: null,
    }));
    const user = userEvent.setup();
    wrapper(
      <ArtifactPreviewPane
        conversationId="conversation"
        turnId="turn"
        file={file}
        files={[file]}
      />
    );
    await waitFor(() => expect(mockedPreview).toHaveBeenCalledWith("conversation", "turn", "change-1", "diff"));
    expect(screen.getByRole("tab", { name: "差异" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "当前文件" }));
    await waitFor(() => expect(mockedPreview).toHaveBeenCalledWith("conversation", "turn", "change-1", "current"));
    expect(await screen.findByText("current")).toBeInTheDocument();
  });
});
