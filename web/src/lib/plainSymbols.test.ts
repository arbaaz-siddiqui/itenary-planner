import { describe, expect, it } from "vitest";
import { plainSymbols } from "./plainSymbols";

describe("plainSymbols", () => {
  it("turns math-mode arrows into the character", () => {
    expect(plainSymbols(String.raw`Departure (AMD $\to$ DXB)`)).toBe("Departure (AMD → DXB)");
    expect(plainSymbols(String.raw`04:15 $\rightarrow$ 06:10`)).toBe("04:15 → 06:10");
  });

  it("leaves real dollar amounts alone", () => {
    expect(plainSymbols("costs $120 to $150")).toBe("costs $120 to $150");
  });
});
