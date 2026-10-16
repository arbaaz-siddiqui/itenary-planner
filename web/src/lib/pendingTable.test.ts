import { describe, expect, it } from "vitest";
import { holdPendingTable } from "./pendingTable";

const intro = "Here are the top 5 flights:\n\n";

describe("holdPendingTable", () => {
  it("holds a header with no separator yet", () => {
    const r = holdPendingTable(`${intro}| Airline | Departure | Total for 2 Adults |`);
    expect(r).toEqual({ ready: "Here are the top 5 flights:", pending: true });
  });

  it("holds a separator that is still arriving", () => {
    const r = holdPendingTable(`${intro}| Airline | Total |\n| :--- | :-`);
    expect(r.pending).toBe(true);
  });

  it("holds a whole separator until a newline follows it", () => {
    expect(holdPendingTable(`${intro}| Airline | Total |\n| :--- | :--- |`).pending).toBe(true);
  });

  it("releases the table once rows start", () => {
    const text = `${intro}| Airline | Total |\n| :--- | :--- |\n| IndiGo | ₹32,242.87 |`;
    expect(holdPendingTable(text)).toEqual({ ready: text, pending: false });
  });

  it("leaves text without a trailing table alone", () => {
    const text = `${intro}| A | B |\n|---|---|\n| 1 | 2 |\n\nAll prices are for 2 adults.`;
    expect(holdPendingTable(text)).toEqual({ ready: text, pending: false });
  });
});
