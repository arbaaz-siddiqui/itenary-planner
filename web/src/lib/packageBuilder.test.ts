import { describe, expect, it } from "vitest";
import { buildPackageRequest, defaultPackageForm, packageFormErrors } from "./packageBuilder";

const today = new Date("2026-10-16T09:00:00Z");

describe("buildPackageRequest", () => {
  it("writes the website's builder fields as one plain request", () => {
    const f = { ...defaultPackageForm(today), checkIn: "2026-10-23", stars: [5, 4] };
    expect(buildPackageRequest(f)).toBe(
      "Build my own package: Dubai, arriving 23 October 2026, 3 nights, 2 adults, 1 room. " +
        "Include a 4 or 5 star hotel, tours for each day and airport pickup and drop-off. " +
        "Do not add flights, meals or visa. " +
        "Price every part live, give the day-by-day plan and the total for our group.",
    );
  });

  it("names children with their ages, flights, visa and notes", () => {
    const f = {
      ...defaultPackageForm(today),
      checkIn: "2026-12-02",
      nights: 1,
      adults: 2,
      childAges: [5, 9],
      services: ["flights", "visa"] as const,
      from: "Ahmedabad",
      notes: "Vegetarian meals",
    };
    expect(buildPackageRequest({ ...f, services: [...f.services] })).toBe(
      "Build my own package: Dubai, arriving 2 December 2026, 1 night, 2 adults and 2 children (ages 5, 9), 1 room. " +
        "Include return flights from Ahmedabad and the tourist visa (Indian passport). " +
        "Do not add hotel, tours, airport transfers or meals. " +
        "Price every part live, give the day-by-day plan and the total for our group. Also: Vegetarian meals",
    );
  });
});

describe("packageFormErrors", () => {
  it("accepts the defaults", () => {
    expect(packageFormErrors(defaultPackageForm(today), today)).toEqual([]);
  });

  it("says what is missing in plain words", () => {
    const f = { ...defaultPackageForm(today), services: [], checkIn: "2026-10-01", rooms: 3, adults: 2 };
    expect(packageFormErrors(f, today)).toEqual([
      "Pick at least one service.",
      "The travel date is in the past.",
      "Rooms must be between 1 and the number of adults.",
    ]);
  });

  it("needs a departure city only when flights are in", () => {
    const f = { ...defaultPackageForm(today), services: ["flights" as const] };
    expect(packageFormErrors(f, today)).toEqual(["Enter the city you fly from."]);
  });
});
