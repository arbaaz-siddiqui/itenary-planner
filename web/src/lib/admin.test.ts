import { describe, expect, it } from "vitest";
import {
  axisTicks,
  barPercents,
  failureRate,
  factRows,
  formatCount,
  fullNameOf,
  groupByService,
  pageCount,
  prettyJson,
  rangeLabel,
  relativeTime,
  satisfaction,
  searchQuery,
  searchSummary,
  serviceOfTool,
  shortDay,
  sparkPath,
  toolLabel,
} from "./admin";

describe("services", () => {
  it("maps tools to the service the customer used", () => {
    expect(serviceOfTool("search_flights")).toBe("Flights");
    expect(serviceOfTool("get_hotel_reviews")).toBe("Hotels");
    expect(serviceOfTool("search_airport_transfer_dubai")).toBe("Transfers");
    expect(serviceOfTool("get_visa_info")).toBe("Visa");
    expect(serviceOfTool("list_packages")).toBe("Packages");
    expect(serviceOfTool("plan_itinerary_tool")).toBe("Planning");
    expect(serviceOfTool("lookup_entity")).toBe("Other");
  });

  it("groups searches per service, biggest first, Other last", () => {
    const out = groupByService([
      { tool: "lookup_entity", searches: 50, failed: 0 },
      { tool: "search_hotels", searches: 3, failed: 1 },
      { tool: "get_hotel_info", searches: 2, failed: 0 },
      { tool: "search_flights", searches: 4, failed: 2 },
    ]);
    expect(out).toEqual([
      { service: "Hotels", searches: 5, failed: 1 },
      { service: "Flights", searches: 4, failed: 2 },
      { service: "Other", searches: 50, failed: 0 },
    ]);
  });

  it("labels known tools and makes unknown ones readable", () => {
    expect(toolLabel("search_tours")).toBe("Tour search");
    expect(toolLabel("compute_remaining_budget_tool")).toBe("Compute remaining budget");
    expect(toolLabel("")).toBe("Unknown");
  });
});

describe("charts", () => {
  it("scales bars to the largest value", () => {
    expect(barPercents([0, 5, 10])).toEqual([0, 50, 100]);
    expect(barPercents([0, 0])).toEqual([0, 0]);
    expect(barPercents([])).toEqual([]);
  });

  it("draws a sparkline inside the box", () => {
    const d = sparkPath([0, 10], 100, 20, 0);
    expect(d).toBe("M0.0 20.0 L100.0 0.0");
    expect(sparkPath([], 100, 20)).toBe("");
    expect(sparkPath([3], 10, 10, 0)).toBe("M0.0 0.0 L10.0 0.0");
  });

  it("picks axis ticks that never crowd", () => {
    expect(axisTicks(3)).toEqual([0, 1, 2]);
    const t = axisTicks(30, 6);
    expect(t[0]).toBe(0);
    expect(t[t.length - 1]).toBe(29);
    expect(t.length).toBeLessThanOrEqual(6);
    expect(axisTicks(0)).toEqual([]);
  });

  it("formats days, rates and counts", () => {
    expect(shortDay("2026-11-07")).toBe("7 Nov");
    expect(failureRate(0, 0)).toBe("0%");
    expect(failureRate(40, 1)).toBe("2.5%");
    expect(failureRate(10, 5)).toBe("50%");
    expect(satisfaction(3, 1)).toBe("75%");
    expect(satisfaction(0, 0)).toBeNull();
    expect(formatCount(1234567)).toBe("12,34,567");
  });
});

describe("people and time", () => {
  it("uses the name, else the email's local part", () => {
    expect(fullNameOf({ first_name: "Ada", last_name: "Lovelace" })).toBe("Ada Lovelace");
    expect(fullNameOf({ first_name: "", last_name: "", email: "x.y@z.com" })).toBe("x.y");
    expect(fullNameOf({})).toBe("Unnamed");
  });

  it("says how long ago in plain words", () => {
    const now = new Date("2026-11-17T12:00:00Z");
    expect(relativeTime(null, now)).toBe("Never");
    expect(relativeTime("2026-11-17T11:59:30Z", now)).toBe("Just now");
    expect(relativeTime("2026-11-17T11:30:00Z", now)).toBe("30 min ago");
    expect(relativeTime("2026-11-17T07:00:00Z", now)).toBe("5 h ago");
    expect(relativeTime("2026-11-16T10:00:00Z", now)).toBe("Yesterday");
    expect(relativeTime("2026-11-13T10:00:00Z", now)).toBe("4 days ago");
    expect(relativeTime("2026-10-01T10:00:00Z", now)).toMatch(/Oct 2026/);
  });

  it("lists remembered facts with known ones first", () => {
    expect(factRows({ diet: "Veg", zebra: 1, origin_city: "Pune", empty: "" })).toEqual([
      { label: "Home city", value: "Pune" },
      { label: "Diet", value: "Veg" },
      { label: "Zebra", value: "1" },
    ]);
  });
});

describe("paging and filters", () => {
  it("counts pages and ranges", () => {
    expect(pageCount(0, 25)).toBe(1);
    expect(pageCount(51, 25)).toBe(3);
    expect(rangeLabel(2, 25, 51)).toBe("26-50 of 51");
    expect(rangeLabel(1, 25, 0)).toBe("0 results");
  });

  it("builds the searches query without empty filters", () => {
    expect(searchQuery({})).toBe("");
    expect(searchQuery({ tool: "search_hotels", q: "  dubai ", errors_only: true, page: 1 })).toBe(
      "?tool=search_hotels&q=dubai&errors_only=true",
    );
    expect(searchQuery({ from: "2026-11-01", to: "2026-11-17", page: 3 })).toBe(
      "?from=2026-11-01&to=2026-11-17&page=3",
    );
  });

  it("summarises a search in one line", () => {
    expect(
      searchSummary({ origin: "Mumbai", destination: "Dubai", start_date: "2026-12-10", party: "2 adults" }),
    ).toBe("Mumbai to Dubai, 2026-12-10, 2 adults");
    expect(searchSummary({ origin: null, destination: null, start_date: null, party: null })).toBe("No details");
    expect(
      searchSummary({ origin: null, destination: null, start_date: "2026-12-10", party: null, args: { hotel_name: "Address Sky View" } }),
    ).toBe("To Address Sky View, 2026-12-10");
  });

  it("pretty-prints JSON and leaves other text alone", () => {
    expect(prettyJson('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyJson("not json")).toBe("not json");
    expect(prettyJson("x".repeat(10), 4)).toBe("xxxx\n…");
  });
});
