import { describe, expect, it } from "vitest";
import { toCardRefs } from "./cardRefs";

const flight = (price: number, dep = "09:00") => ({
  airline: "Gulf Air",
  price_inr: price,
  outbound: { flight_numbers: ["65", "512"], departure_time: dep },
});

describe("toCardRefs", () => {
  it("shows the same flight at the same fare once", () => {
    expect(toCardRefs([flight(30000), flight(30000)], "flights")).toHaveLength(1);
  });

  it("keeps the same flight at a different fare or time", () => {
    expect(toCardRefs([flight(30000), flight(31000), flight(30000, "21:00")], "flights")).toHaveLength(3);
  });

  it("never merges non-flight results that share a name", () => {
    const t = { vehicle_name: "Toyota Hiace", price_inr: 2536.72 };
    expect(toCardRefs([t, { ...t, price_inr: 14435.59 }], "transfers")).toHaveLength(2);
  });
});
