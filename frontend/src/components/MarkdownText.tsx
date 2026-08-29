import { createElement, memo, useMemo, type ReactNode } from "react";
import { cx } from "@/lib/format";

type Block =
  | { kind: "code"; lang: string; text: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "hr" };

const INLINE_PATTERN_SOURCE =
  "(`[^`]+`|\\*\\*[^*]+\\*\\*|__[^_]+__|\\*[^*]+\\*|_[^_]+_|~~[^~]+~~|!\\[[^\\]]*\\]\\([^)]+\\)|\\[[^\\]]+\\]\\([^)]+\\))";

const HEADING_CLASS: Record<number, string> = {
  1: "mt-3 mb-1 text-lg font-semibold",
  2: "mt-3 mb-1 text-base font-semibold",
  3: "mt-2 mb-1 text-sm font-semibold",
  4: "mt-2 mb-0.5 text-sm font-medium",
  5: "mt-1 mb-0.5 text-sm font-medium",
  6: "mt-1 mb-0.5 text-sm font-medium text-muted",
};

function safeUrl(raw: string): string {
  try {
    const url = new URL(raw, window.location.href);
    if (url.protocol === "http:" || url.protocol === "https:") return url.href;
  } catch {
    // Ignore malformed URLs and keep the raw text renderer safe.
  }
  return "#";
}

function parseInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  const pattern = new RegExp(INLINE_PATTERN_SOURCE, "g");
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(<code key={key++} className="rounded-sm bg-surface-2 px-1 py-0.5 font-mono text-[0.9em]">{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(<strong key={key++} className="font-semibold">{parseInline(token.slice(2, -2))}</strong>);
    } else if (token.startsWith("~~")) {
      nodes.push(<del key={key++} className="text-muted">{parseInline(token.slice(2, -2))}</del>);
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key++}>{parseInline(token.slice(1, -1))}</em>);
    } else if (token.startsWith("_")) {
      nodes.push(<em key={key++}>{parseInline(token.slice(1, -1))}</em>);
    } else if (token.startsWith("![")) {
      const alt = token.slice(2, token.indexOf("]("));
      nodes.push(<span key={key++} className="text-muted">[{alt}]</span>);
    } else if (token.startsWith("[")) {
      const title = token.slice(1, token.indexOf("]("));
      const href = token.slice(token.indexOf("](") + 2, -1);
      nodes.push(
        <a
          key={key++}
          href={safeUrl(href)}
          target="_blank"
          rel="noreferrer"
          className="text-accent underline underline-offset-2"
        >
          {title}
        </a>
      );
    }
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

function isSpecialLine(line: string): boolean {
  return (
    /^```/.test(line) ||
    /^#{1,6}\s+/.test(line) ||
    /^\s*([-*_])\1{2,}\s*$/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^\s*(?:[-*+]|\d+[.)])\s+/.test(line)
  );
}

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (/^```/.test(line)) {
      const lang = line.slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1; // closing fence
      blocks.push({ kind: "code", lang, text: code.join("\n") });
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      blocks.push({ kind: "hr" });
      index += 1;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", text: quote.join("\n") });
      continue;
    }
    const unordered = /^\s*[-*+]\s+/.test(line);
    const ordered = /^\s*\d+[.)]\s+/.test(line);
    if (unordered || ordered) {
      const items: string[] = [];
      const itemPattern = unordered ? /^\s*[-*+]\s+/ : /^\s*\d+[.)]\s+/;
      while (index < lines.length && itemPattern.test(lines[index])) {
        items.push(lines[index].replace(itemPattern, ""));
        index += 1;
      }
      blocks.push(unordered ? { kind: "ul", items } : { kind: "ol", items });
      continue;
    }
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    const paragraph: string[] = [];
    while (
      index < lines.length &&
      lines[index].trim() !== "" &&
      !isSpecialLine(lines[index])
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
  }
  return blocks;
}

function renderBlock(block: Block, index: number): ReactNode {
  switch (block.kind) {
    case "code":
      return (
        <pre key={index} className="my-2 overflow-x-auto rounded-md bg-surface-2/70 p-2.5 font-mono text-xs leading-relaxed">
          <code>{block.text}</code>
        </pre>
      );
    case "heading": {
      const Tag = ["h1", "h2", "h3", "h4", "h5", "h6"][block.level - 1] ?? "h6";
      return createElement(
        Tag,
        {
          key: index,
          className: cx(
            HEADING_CLASS[block.level] ?? "mt-2 mb-1 text-sm font-semibold",
            "whitespace-pre-wrap break-words"
          ),
        },
        parseInline(block.text)
      );
    }
    case "paragraph":
      return (
        <p key={index} className="whitespace-pre-wrap break-words leading-relaxed">
          {parseInline(block.text)}
        </p>
      );
    case "ul":
      return (
        <ul key={index} className="my-1.5 list-disc space-y-0.5 pl-5 leading-relaxed">
          {block.items.map((item, itemIndex) => (
            <li key={itemIndex} className="whitespace-pre-wrap break-words">{parseInline(item)}</li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol key={index} className="my-1.5 list-decimal space-y-0.5 pl-5 leading-relaxed">
          {block.items.map((item, itemIndex) => (
            <li key={itemIndex} className="whitespace-pre-wrap break-words">{parseInline(item)}</li>
          ))}
        </ol>
      );
    case "quote":
      return (
        <blockquote key={index} className="my-1.5 border-l-2 border-muted/40 pl-3 text-muted">
          <p className="whitespace-pre-wrap break-words leading-relaxed">{parseInline(block.text)}</p>
        </blockquote>
      );
    case "hr":
      return <hr key={index} className="my-3 border-border" />;
    default:
      return null;
  }
}

export interface MarkdownTextProps {
  text: string;
  className?: string;
}

/** Small dependency-free Markdown renderer for assistant replies. */
export const MarkdownText = memo(function MarkdownText({ text, className }: MarkdownTextProps) {
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return (
    <div className={cx("break-words", className)}>
      {blocks.map(renderBlock)}
    </div>
  );
});
