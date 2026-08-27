import { describe, expect, it } from "vitest";
import zhCN from "@/i18n/zh-CN";
import enUS from "@/i18n/en-US";

type Dict = Record<string, unknown>;

function collect(value: Dict, prefix = "", out: Array<[string, string]> = []): Array<[string, string]> {
  for (const [key, item] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (typeof item === "string") out.push([next, item]);
    else if (typeof item === "object" && item !== null) {
      collect(item as Dict, next, out);
    }
  }
  return out;
}

describe("i18n resource completeness", () => {
  it("zh-CN and en-US expose exactly the same keys", () => {
    const zh = collect(zhCN as Dict);
    const en = collect(enUS as Dict);
    expect(zh.map(([key]) => key).sort()).toEqual(en.map(([key]) => key).sort());
  });

  it("no resource value is empty", () => {
    for (const entries of [collect(zhCN as Dict), collect(enUS as Dict)]) {
      for (const [, value] of entries) {
        expect(value.trim().length).toBeGreaterThan(0);
      }
    }
  });
});
