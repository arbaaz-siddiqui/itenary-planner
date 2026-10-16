// "Build a package": the website's builder fields (services, city, date,
// nights, guests and rooms, hotel stars, nationality), turned into one plain
// request the agent plans and prices in Plan mode. Kept pure so it is tested.

export type PackageService = "hotel" | "tours" | "transfers" | "flights" | "meals" | "visa";

export const PACKAGE_SERVICES: { id: PackageService; label: string }[] = [
  { id: "hotel", label: "Hotel" },
  { id: "tours", label: "Tours" },
  { id: "transfers", label: "Airport transfers" },
  { id: "flights", label: "Flights" },
  { id: "meals", label: "Meals" },
  { id: "visa", label: "Visa" },
];

export interface PackageForm {
  services: PackageService[];
  city: string;
  /** yyyy-mm-dd */
  checkIn: string;
  nights: number;
  adults: number;
  childAges: number[];
  rooms: number;
  /** Hotel star ratings the customer accepts; empty means any. */
  stars: number[];
  /** Departure city, used only when flights are included. */
  from: string;
  nationality: string;
  notes: string;
}

export function defaultPackageForm(today: Date = new Date()): PackageForm {
  const start = new Date(today.getTime() + 14 * 86_400_000);
  return {
    services: ["hotel", "tours", "transfers"],
    city: "Dubai",
    checkIn: start.toISOString().slice(0, 10),
    nights: 3,
    adults: 2,
    childAges: [],
    rooms: 1,
    stars: [],
    from: "",
    nationality: "Indian",
    notes: "",
  };
}

/** What is still missing before the request can be sent, in the customer's words. */
export function packageFormErrors(f: PackageForm, today: Date = new Date()): string[] {
  const errors: string[] = [];
  if (!f.services.length) errors.push("Pick at least one service.");
  if (!f.city.trim()) errors.push("Enter a destination.");
  if (!f.checkIn) errors.push("Pick a travel date.");
  else if (f.checkIn < today.toISOString().slice(0, 10)) errors.push("The travel date is in the past.");
  if (!(f.nights >= 1 && f.nights <= 30)) errors.push("Nights must be between 1 and 30.");
  if (!(f.adults >= 1 && f.adults <= 9)) errors.push("Adults must be between 1 and 9.");
  if (f.childAges.some((a) => !(a >= 0 && a <= 17))) errors.push("Each child's age must be 0 to 17.");
  if (f.rooms < 1 || f.rooms > f.adults) errors.push("Rooms must be between 1 and the number of adults.");
  if (f.services.includes("flights") && !f.from.trim()) errors.push("Enter the city you fly from.");
  return errors;
}

function longDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

function plural(n: number, word: string, many = `${word}s`): string {
  return `${n} ${n === 1 ? word : many}`;
}

/** One request in plain words, as a travel desk would write it. */
export function buildPackageRequest(f: PackageForm): string {
  const party = [plural(f.adults, "adult")];
  if (f.childAges.length) {
    party.push(`${plural(f.childAges.length, "child", "children")} (age${f.childAges.length > 1 ? "s" : ""} ${f.childAges.join(", ")})`);
  }
  const parts: string[] = [];
  for (const s of f.services) {
    if (s === "hotel") {
      const stars = [...f.stars].sort((a, b) => a - b);
      parts.push(stars.length ? `a ${stars.join(" or ")} star hotel` : "a hotel");
    } else if (s === "tours") parts.push("tours for each day");
    else if (s === "transfers") parts.push("airport pickup and drop-off");
    else if (s === "flights") parts.push(`return flights from ${f.from.trim()}`);
    else if (s === "meals") parts.push("meals (restaurants)");
    else if (s === "visa") parts.push(`the tourist visa (${f.nationality.trim() || "Indian"} passport)`);
  }
  const lines = [
    `Build my own package: ${f.city.trim()}, arriving ${longDate(f.checkIn)}, ${plural(f.nights, "night")}, ` +
      `${party.join(" and ")}, ${plural(f.rooms, "room")}.`,
    `Include ${parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0]}.`,
    "Price every part live, give the day-by-day plan and the total for our group.",
  ];
  // Said outright: left unsaid, the agent filled the gaps from remembered
  // facts and added flights from the customer's home city and a visa.
  const left = PACKAGE_SERVICES.filter((s) => !f.services.includes(s.id)).map((s) => s.label.toLowerCase());
  if (left.length) {
    lines.splice(2, 0, `Do not add ${left.length > 1 ? `${left.slice(0, -1).join(", ")} or ${left[left.length - 1]}` : left[0]}.`);
  }
  if (f.notes.trim()) lines.push(`Also: ${f.notes.trim()}`);
  return lines.join(" ");
}
