import { describe, expect, it } from "vitest";
import { formatInr, idOf, imagesOf, kindOf, titleOf } from "./cards";

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
