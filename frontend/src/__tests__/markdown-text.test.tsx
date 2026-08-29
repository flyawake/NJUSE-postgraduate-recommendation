import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownText } from "@/components/MarkdownText";

describe("MarkdownText", () => {
  it("renders headings, lists, bold and inline code instead of raw Markdown", () => {
    render(
      <MarkdownText
        text={"# 标题\n\n- 第一项\n- **加粗项**\n\n运行 `npm test` 验证。\n"}
      />
    );
    expect(screen.getByRole("heading", { name: "标题" })).toBeInTheDocument();
    expect(screen.getByText("第一项")).toBeInTheDocument();
    expect(screen.getByText("加粗项")).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("**加粗项**");
  });

  it("renders fenced code blocks as preformatted text", () => {
    render(
      <MarkdownText text={"```\nconst a = 1;\n```\n"} />
    );
    expect(screen.getByText("const a = 1;")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("```");
  });

  it("keeps plain paragraphs readable", () => {
    render(<MarkdownText text={"普通回复内容"} />);
    expect(screen.getByText("普通回复内容")).toBeInTheDocument();
  });
});
