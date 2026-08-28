import { StrictMode, useCallback, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "@/lib/i18n";
import { WorkspaceField } from "@/components/WorkspaceField";
import { resetWorkspaceValidationCache } from "@/lib/workspaceValidation";

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

afterEach(() => {
  resetWorkspaceValidationCache();
  vi.clearAllMocks();
});

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

  it("does not revalidate when callback identity changes or the same normalised path returns", async () => {
    mockedValidate.mockResolvedValue({ valid: true, resolved_path: "C:/repo", error: null });
    const renderField = (callbackVersion: number, value = " C:\\repo\\ ") => (
      <I18nProvider>
        <WorkspaceField value={value} onChange={() => undefined} onValidated={() => callbackVersion} />
      </I18nProvider>
    );
    const { rerender } = render(renderField(1));
    await waitFor(() => expect(mockedValidate).toHaveBeenCalledTimes(1), { timeout: 3000 });

    rerender(renderField(2, "C:/repo/"));
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(mockedValidate).toHaveBeenCalledTimes(1);
  });

  it("drops a stale response after the path changes", async () => {
    let resolveFirst!: (value: { valid: boolean; resolved_path: string; error: null }) => void;
    const first = new Promise<{ valid: boolean; resolved_path: string; error: null }>((resolve) => {
      resolveFirst = resolve;
    });
    mockedValidate
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({ valid: true, resolved_path: "C:/second", error: null });
    const { rerender } = render(
      <I18nProvider>
        <WorkspaceField value="C:/first" onChange={() => undefined} />
      </I18nProvider>
    );
    await waitFor(() => expect(mockedValidate).toHaveBeenCalledTimes(1), { timeout: 3000 });
    rerender(
      <I18nProvider>
        <WorkspaceField value="C:/second" onChange={() => undefined} />
      </I18nProvider>
    );
    await waitFor(() => expect(mockedValidate).toHaveBeenCalledTimes(2), { timeout: 3000 });
    await waitFor(() => expect(screen.getByText("工作区可用")).toBeInTheDocument(), { timeout: 3000 });
    resolveFirst({ valid: true, resolved_path: "C:/first", error: null });
    await Promise.resolve();
    expect(screen.getByDisplayValue("C:/second")).toBeInTheDocument();
  });

  it("keeps a newer same-path request owned when an aborted predecessor settles late", async () => {
    let resolveFirst!: (value: { valid: boolean; resolved_path: string; error: null }) => void;
    let resolveSecond!: (value: { valid: boolean; resolved_path: string; error: null }) => void;
    const first = new Promise<{ valid: boolean; resolved_path: string; error: null }>((resolve) => {
      resolveFirst = resolve;
    });
    const second = new Promise<{ valid: boolean; resolved_path: string; error: null }>((resolve) => {
      resolveSecond = resolve;
    });
    mockedValidate.mockReturnValueOnce(first).mockReturnValueOnce(second);
    const fixedField = (
      <I18nProvider>
        <WorkspaceField value="C:/same" onChange={() => undefined} />
      </I18nProvider>
    );

    const firstMount = render(fixedField);
    await waitFor(() => expect(mockedValidate).toHaveBeenCalledTimes(1), { timeout: 3000 });
    firstMount.unmount();

    render(fixedField);
    await waitFor(() => expect(mockedValidate).toHaveBeenCalledTimes(2), { timeout: 3000 });

    // This transport double deliberately ignores AbortSignal. Its late
    // completion must not remove or cache the second request's map entry.
    resolveFirst({ valid: true, resolved_path: "C:/stale", error: null });
    await Promise.resolve();
    render(fixedField);
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(mockedValidate).toHaveBeenCalledTimes(2);

    resolveSecond({ valid: true, resolved_path: "C:/same", error: null });
    await waitFor(() => expect(screen.getAllByText("工作区可用")).toHaveLength(2), {
      timeout: 3000,
    });
  });

  it("does not cache a transport failure and retries exactly once in StrictMode", async () => {
    mockedValidate
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockResolvedValueOnce({ valid: true, resolved_path: "C:/stable", error: null });
    const user = userEvent.setup();
    render(
      <StrictMode>
        <ControlledWorkspaceField />
      </StrictMode>
    );

    await user.type(screen.getByLabelText("工作区"), "C:/stable");
    await waitFor(() => expect(screen.getByTestId("workspace-retry")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(mockedValidate).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId("workspace-retry"));
    await waitFor(() => expect(screen.getByText("工作区可用")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(mockedValidate).toHaveBeenCalledTimes(2);
  });
});
