import { describe, expect, it } from "vitest";
import { refersTo, sameName, normaliseName, shortenedFrom, tableRowNames } from "./optionMatch";

describe("refersTo", () => {
  it("matches the agent's shortened restaurant names", () => {
    const full = "Khandani Rajdhani - Indian Restaurant in Karama";
    expect(refersTo("Khandani Rajdhani", full)).toBe(true);
    expect(refersTo("Khandani Rajdhani - Karama", full)).toBe(true);
    expect(refersTo("**Khandani Rajdhani**", full)).toBe(true);
  });

  it("does not link different restaurants by one shared word", () => {
    expect(shortenedFrom("Rangoli", "Rangoli Restaurant")).toBe(false);
    expect(refersTo("Thali Restaurant", "Kathhiyawadi Thali Restaurant")).toBe(true);
    expect(refersTo("Aamras Thali", "Kathhiyawadi Thali Restaurant")).toBe(false);
  });

  it("keeps the prefix rule's minimum length", () => {
    expect(sameName(normaliseName("Veg"), normaliseName("Veganesha"))).toBe(false);
  });
});

describe("tableRowNames", () => {
  it("reads first cells and skips the separator row", () => {
    const md = "| Restaurant | Price |\n| :--- | :--- |\n| **Rangoli** | ₹445.50 |\n";
    expect(tableRowNames(md)).toEqual(["Restaurant", "**Rangoli**"]);
  });
});
