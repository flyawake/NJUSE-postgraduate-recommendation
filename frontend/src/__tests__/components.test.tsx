import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nProvider } from "@/lib/i18n";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { VerificationBadge } from "@/components/VerificationBadge";
import { verificationKind } from "@/components/VerificationBadge";
import { TaskComposer } from "@/components/TaskComposer";

function renderWithI18n(element: React.ReactElement) {
  return render(<I18nProvider>{element}</I18nProvider>);
}

describe("RunStatusBadge", () => {
  it("renders explicit labels for every state (not color only)", () => {
    const { container } = renderWithI18n(
      <div>
        <RunStatusBadge status="idle" />
        <RunStatusBadge status="running" />
        <RunStatusBadge status="completed" />
        <RunStatusBadge status="error" />
        <RunStatusBadge status="interrupted" />
      </div>
    );
    expect(container.textContent).toContain("空闲");
    expect(container.textContent).toContain("运行中");
    expect(container.textContent).toContain("已完成");
    expect(container.textContent).toContain("失败");
    expect(container.textContent).toContain("已取消");
  });
});

describe("VerificationBadge", () => {
  it("maps status values to explicit kinds", () => {
    expect(verificationKind("VERIFIED")).toBe("verified");
    expect(verificationKind("FAILED")).toBe("failed");
    expect(verificationKind("NOT_RUN")).toBe("not-run");
    expect(verificationKind("NOT_APPLICABLE")).toBe("not-applicable");
    expect(verificationKind(null)).toBe("not-applicable");
  });

  it("shows zh labels by default and distinguishes failed from verified", () => {
    const { container } = renderWithI18n(
      <div>
        <VerificationBadge status="verified" />
        <VerificationBadge status="failed" />
        <VerificationBadge status="not-run" />
        <VerificationBadge status="not-applicable" />
      </div>
    );
    const text = container.textContent ?? "";
    expect(text).toContain("验证通过");
    expect(text).toContain("验证失败");
    expect(text).toContain("尚未验证");
    expect(text).toContain("无需验证");
  });
});

describe("TaskComposer", () => {
  it("enables start when a task is present", () => {
    renderWithI18n(
      <TaskComposer
        task="fix the bug"
        onTaskChange={() => undefined}
        state="ready"
        onStart={() => undefined}
        onCancel={() => undefined}
      />
    );
    expect(screen.getByLabelText("开始运行")).toBeEnabled();
  });

  it("disables start when the task is empty", () => {
    renderWithI18n(
      <TaskComposer
        task=""
        onTaskChange={() => undefined}
        state="idle"
        onStart={() => undefined}
        onCancel={() => undefined}
      />
    );
    expect(screen.getByLabelText("开始运行")).toBeDisabled();
  });

  it("shows cancelling label while cancelling", () => {
    renderWithI18n(
      <TaskComposer
        task="t"
        onTaskChange={() => undefined}
        state="cancelling"
        onStart={() => undefined}
        onCancel={() => undefined}
      />
    );
    expect(screen.getByText("正在取消…")).toBeInTheDocument();
    expect(screen.getByLabelText("取消运行")).toBeDisabled();
  });
});
