import { useCallback, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "@/lib/i18n";
import { WorkspaceField } from "@/components/WorkspaceField";
import { ToolEventGroup } from "@/components/ToolEventGroup";
import type { ToolGroup } from "@/lib/toolgroups";

vi.mock("@/api/client", () => {
  return {
    ApiError: class ApiError extends Error {
      code = "invalid_workspace";
      status = 400;
      field: string | null = "workspace";
    },
    api: {
      validateWorkspace: vi.fn(),
      pickWorkspace: vi.fn(),
    },
  };
});

import { api } from "@/api/client";

const mockedValidate = api.validateWorkspace as ReturnType<typeof vi.fn>;
const mockedPick = api.pickWorkspace as ReturnType<typeof vi.fn>;

function ControlledWorkspaceField() {
  const [value, setValue] = useState("");
  const onValidated = useCallback(() => undefined, []);
  return (
    <I18nProvider>
      <WorkspaceField value={value} onChange={setValue} onValidated={onValidated} />
    </I18nProvider>
  );
}

describe("WorkspaceField", () => {
  it("shows valid state after successful validation", async () => {
    mockedValidate.mockResolvedValue({ valid: true, resolved_path: "C:/repo", error: null });
    const user = userEvent.setup();
    render(<ControlledWorkspaceField />);
    await user.type(screen.getByLabelText("工作区"), "C:/repo");
    await waitFor(() => expect(screen.getByText("工作区可用")).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("shows invalid state with the server message", async () => {
    mockedValidate.mockResolvedValue({
      valid: false,
      resolved_path: null,
      error: { code: "invalid_workspace", message: "工作区不是目录", field: "workspace" },
    });
    const user = userEvent.setup();
    render(<ControlledWorkspaceField />);
    await user.type(screen.getByLabelText("工作区"), "C:/nope");
    await waitFor(() => expect(screen.getByText("工作区路径无效或不可访问")).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("fills the path from the native browse picker and validates it", async () => {
    mockedValidate.mockResolvedValue({ valid: true, resolved_path: "C:/picked", error: null });
    mockedPick.mockResolvedValue({
      cancelled: false,
      path: "C:/picked",
      error: null,
    });
    const user = userEvent.setup();
    render(<ControlledWorkspaceField />);
    await user.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("工作区")).toHaveValue("C:/picked"), {
      timeout: 3000,
    });
    await waitFor(() => expect(screen.getByText("工作区可用")).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("keeps the current value when the picker is cancelled", async () => {
    mockedPick.mockResolvedValue({ cancelled: true, path: null, error: null });
    const user = userEvent.setup();
    render(<ControlledWorkspaceField />);
    await user.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(mockedPick).toHaveBeenCalled());
    expect(screen.getByLabelText("工作区")).toHaveValue("");
  });

  it("shows a stable i18n message when the picker is unavailable", async () => {
    mockedPick.mockResolvedValue({
      cancelled: false,
      path: null,
      error: { code: "picker_unavailable", message: "无法打开系统文件夹", field: null },
    });
    const user = userEvent.setup();
    render(<ControlledWorkspaceField />);
    await user.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(
      () =>
        expect(
          screen.getByText("无法打开系统文件夹选择窗口，请手动输入路径")
        ).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });
});

describe("ToolEventGroup", () => {
  const group: ToolGroup = {
    key: "g1",
    items: [
      {
        callId: "a",
        name: "glob",
        argsSummary: '{"pattern":"**/*.py"}',
        status: "success",
        summary: "glob ok: path=.",
        step: 1,
        phase: "EXECUTING_TOOLS",
      },
      {
        callId: "b",
        name: "read_file",
        argsSummary: '{"path":"src/app.py"}',
        status: "success",
        summary: "read_file ok: path=src/app.py",
        step: 1,
        phase: "EXECUTING_TOOLS",
      },
    ],
    containsError: false,
    running: false,
    aborted: false,
  };

  it("collapses completed groups into one summary row", () => {
    render(
      <I18nProvider>
        <ToolEventGroup group={group} />
      </I18nProvider>
    );
    expect(screen.getByText("已完成 2 项操作")).toBeInTheDocument();
    expect(screen.queryByText("glob")).not.toBeInTheDocument();
  });

  it("expands groups containing errors", () => {
    render(
      <I18nProvider>
        <ToolEventGroup group={{ ...group, containsError: true }} />
      </I18nProvider>
    );
    expect(screen.getByText("glob")).toBeInTheDocument();
  });

  it("expands running groups", () => {
    render(
      <I18nProvider>
        <ToolEventGroup group={{ ...group, running: true }} />
      </I18nProvider>
    );
    expect(screen.getByText("正在执行 2 项操作")).toBeInTheDocument();
  });
});
