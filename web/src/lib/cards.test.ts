import { describe, expect, it } from "vitest";
import { formatInr, idOf, imagesOf, kindOf, mergeImages, partyTotalNote, titleOf, withRowTotal } from "./cards";

describe("kindOf", () => {
  it("trusts the group kind first", () => {
    expect(kindOf({ name: "x" }, "restaurants")).toBe("restaurant");
    expect(kindOf({ name: "x" }, "transfers")).toBe("transfer");
    expect(kindOf({ name: "x" }, "visa")).toBe("visa");
  });

  it("falls back to the item's own keys", () => {
    expect(kindOf({ restaurant_id: 2 })).toBe("restaurant");
    expect(kindOf({ hotel_name: "Social Hotel" })).toBe("hotel");
    expect(kindOf({ airline: "Air India", outbound: {} })).toBe("flight");
    expect(kindOf({ package_id: 91 })).toBe("package");
    expect(kindOf({ vehicle_name: "Toyota Hiace" })).toBe("transfer");
    expect(kindOf({ visa_type: "30 Days" })).toBe("visa");
    expect(kindOf({ tour_id: 53632 })).toBe("tour");
  });
});

describe("formatting and identity", () => {
  it("formats rupees with Indian grouping and paise only when present", () => {
    expect(formatInr(13290.34)).toBe("₹13,290.34");
    expect(formatInr(130790)).toBe("₹1,30,790");
    expect(formatInr(null)).toBeNull();
  });

  it("collects images without repeats", () => {
    expect(imagesOf({ image_url: "a", image_urls: ["a", "b", 3] })).toEqual(["a", "b"]);
  });

  it("builds stable ids and titles per kind", () => {
    expect(idOf({ restaurant_id: 2 }, "restaurant")).toBe("restaurant-2");
    expect(idOf({ tour_id: 53632, option_id: 222565 }, "tour")).toBe("tour-53632-222565");
    expect(titleOf({ vehicle_name: "Toyota Hiace" }, "transfer")).toBe("Toyota Hiace");
  });
});

describe("withRowTotal / partyTotalNote", () => {
  const visa = { visa_type: "30 Days Single Entry Tourist Visa", price_per_person_inr: 7753.36 };

  it("keeps a party total the table row carried", () => {
    const item = withRowTotal(visa, "₹17,713.08");
    expect(partyTotalNote(item)).toBe("₹17,713.08 for your party");
  });

  it("adds nothing when the row shows the per-person price itself", () => {
    expect(partyTotalNote(withRowTotal(visa, "₹7,753.36"))).toBeNull();
    expect(partyTotalNote(withRowTotal(visa, null))).toBeNull();
  });
});

describe("mergeImages", () => {
  it("prefers the loaded gallery, which already holds the cover", () => {
    expect(mergeImages(["a.webp"], ["b.webp", "c.webp", "b.webp"])).toEqual(["b.webp", "c.webp"]);
  });
  it("keeps the card's photo until the gallery arrives", () => {
    expect(mergeImages(["a.webp"], [])).toEqual(["a.webp"]);
  });
});
