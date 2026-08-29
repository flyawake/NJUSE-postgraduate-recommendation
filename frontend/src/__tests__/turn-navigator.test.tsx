import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Turn } from "@/api/client";
import { TurnNavigator } from "@/components/TurnNavigator";
import { I18nProvider } from "@/lib/i18n";

const turns = [
  {
    id: "turn-1",
    user_text: "优化侧栏布局",
    active: false,
    state: "completed",
    result: { final_text: "侧栏已调整为项目树结构" },
  },
  {
    id: "turn-2",
    user_text: "优化输入框",
    active: true,
    state: "running",
    result: null,
  },
] as unknown as Turn[];

describe("TurnNavigator", () => {
  it("shows every turn summary and jumps to the selected turn", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <TurnNavigator turns={turns} currentTurnId="turn-2" onSelect={onSelect} />
      </I18nProvider>
    );

    expect(screen.getByText("优化侧栏布局")).toBeInTheDocument();
    expect(screen.getByText("侧栏已调整为项目树结构")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByTestId("turn-tick-turn-1")).toHaveStyle({ width: "8px", opacity: "0.56" });
    expect(screen.getByTestId("turn-tick-turn-2")).toHaveStyle({ width: "8px", opacity: "1" });
    await user.hover(screen.getByRole("button", { name: "跳转到第 1 轮对话" }));
    expect(screen.getByTestId("turn-tick-turn-1")).toHaveStyle({ width: "32px", opacity: "1" });
    expect(screen.getByTestId("turn-tick-turn-2")).toHaveStyle({ width: "24px", opacity: "0.62" });
    await user.click(screen.getByTestId("turn-overview-turn-1"));
    expect(onSelect).toHaveBeenCalledWith("turn-1");
  });
});
