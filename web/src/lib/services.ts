import {
  BedDouble,
  CarFront,
  FileCheck2,
  Map,
  Package,
  Plane,
  UtensilsCrossed,
  type LucideIcon,
} from "lucide-react";

/** The seven things GujjuTours sells, as the sidebar and composer show them. */
export type ServiceId =
  | "restaurants"
  | "flights"
  | "tours"
  | "packages"
  | "hotels"
  | "transfers"
  | "visa";

export interface Service {
  id: ServiceId;
  label: string;
  icon: LucideIcon;
  /** Starter prompts shown on the home screen once the service is picked. */
  starters: string[];
}

export const SERVICES: Service[] = [
  {
    id: "restaurants",
    label: "Restaurants",
    icon: UtensilsCrossed,
    starters: [
      "Veg restaurants in Dubai",
      "Jain-friendly thali places in Karama",
      "Family dinner near Burj Khalifa",
    ],
  },
  {
    id: "flights",
    label: "Flights",
    icon: Plane,
    starters: [
      "Flights from Ahmedabad to Dubai next month",
      "Cheapest return flight Mumbai to Dubai",
      "Direct flights from Delhi to Dubai",
    ],
  },
  {
    id: "tours",
    label: "Tours",
    icon: Map,
    starters: [
      "Desert safari options with private transfer",
      "Burj Khalifa tickets for 2 adults",
      "Things to do in Dubai with kids",
    ],
  },
  {
    id: "packages",
    label: "Packages",
    icon: Package,
    starters: [
      "5-night Dubai holiday package for a family of 4",
      "Dubai honeymoon package",
      "Plan a 3-day Dubai trip for me",
    ],
  },
  {
    id: "hotels",
    label: "Hotels",
    icon: BedDouble,
    starters: [
      "4-star hotels near Dubai Mall",
      "Family hotels in Deira with breakfast",
      "Budget hotels in Bur Dubai",
    ],
  },
  {
    id: "transfers",
    label: "Transfers",
    icon: CarFront,
    starters: [
      "Airport transfer from DXB to my hotel",
      "Private car for 6 people from the airport",
      "Shared transfer to Downtown Dubai",
    ],
  },
  {
    id: "visa",
    label: "Visa",
    icon: FileCheck2,
    starters: [
      "Dubai tourist visa for Indian passport holders",
      "Documents needed for a UAE visa",
      "Oman tourist visa",
    ],
  },
];

export const serviceById = (id: ServiceId | null | undefined) =>
  SERVICES.find((s) => s.id === id) ?? null;

/** Quick-start chips on the home screen before any service is picked. */
export const QUICK_STARTS = [
  "Plan a Dubai trip",
  "Veg restaurants in Dubai",
  "Flights Ahmedabad to Dubai",
  "Visa for Oman",
  "Airport transfer",
];

/** "humble.shah@x.com" -> "Humble". The account has no display name yet. */
export function firstNameFrom(email: string | null | undefined): string {
  const local = (email ?? "").split("@")[0] ?? "";
  const first = local.split(/[._\-+0-9]/).find(Boolean) ?? "";
  return first ? first[0].toUpperCase() + first.slice(1).toLowerCase() : "";
}
