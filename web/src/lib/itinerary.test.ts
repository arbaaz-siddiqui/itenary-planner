import { describe, expect, it } from "vitest";
import { dayHeading, toItinerary } from "./itinerary";

const plan = {
  start_date: "2026-11-06",
  adults: 2,
  total_inr: 30911.52,
  days: [
    {
      day_number: 1,
      date: "2026-11-06",
      label: "Arrival",
      items: [
        { start: "10:20", end: "11:20", title: "Airport pickup to Social Hotel", kind: "transfer", detail: "Zone 3", included: true },
        { start: "15:00", end: "20:00", title: "Dubai Desert Safari by Gujjutours", kind: "tour", detail: "BBQ, Sharing Transfer" },
        { start: "19:30", end: "", title: "Dinner (included in Dubai Desert Safari by Gujjutours)", kind: "meal", included: true },
      ],
    },
    {
      day_number: 2,
      date: "2026-11-07",
      label: "Departure",
      items: [
        { start: "08:00", end: "09:00", title: "Breakfast at Social Hotel", kind: "meal", detail: "Pay at the hotel" },
        { start: "15:25", end: "16:25", title: "Airport drop-off from Social Hotel", kind: "transfer" },
      ],
    },
  ],
  package_lines: [
    { item: "Hotel (Social Hotel)", date: "2026-11-06", amount_inr: 11281.23 },
    { item: "Dubai Desert Safari by Gujjutours", date: "2026-11-06", amount_inr: 5328.32, detail: "Sharing Transfer" },
    { item: "Airport pickup", date: "2026-11-06", amount_inr: 3245.69 },
    { item: "Airport drop-off", date: "2026-11-07", amount_inr: 3245.69 },
    { item: "UAE tourist visa", date: "2026-11-06", amount_inr: 7810.59 },
  ],
};

describe("toItinerary", () => {
  it("reads the days, the cost lines and the total", () => {
    const it = toItinerary(plan)!;
    expect(it.days).toHaveLength(2);
    expect(it.nights).toBe(1);
    expect(it.total).toBe(30911.52);
    expect(it.lines.map((l) => l.item)).toEqual([
      "Hotel (Social Hotel)",
      "Dubai Desert Safari by Gujjutours",
      "Airport pickup",
      "Airport drop-off",
      "UAE tourist visa",
    ]);
  });

  it("puts each paid part's own price on its step", () => {
    const [d1, d2] = toItinerary(plan)!.days;
    expect(d1.steps[0].price).toBe(3245.69);
    expect(d1.steps[1].price).toBe(5328.32);
    expect(d1.steps[2].price).toBeNull();
    expect(d1.steps[2].included).toBe(true);
    expect(d2.steps[1].price).toBe(3245.69);
  });

  it("is null for anything that is not a plan", () => {
    expect(toItinerary({})).toBeNull();
    expect(toItinerary(null)).toBeNull();
  });
});

describe("dayHeading", () => {
  it("names the day", () => {
    expect(dayHeading("2026-11-06")).toBe("Fri 6 Nov");
  });
});
