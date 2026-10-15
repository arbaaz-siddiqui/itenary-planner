import { BedDouble, CarFront, Plane, Ticket } from "lucide-react";

// What the agent books, in the order a trip comes together, each arriving
// from its own side (left, bottom, right, top) in its own colour, taken in
// order from the home gradient: blue, violet, magenta, sunset orange.
const ICONS = [
  { Icon: Plane, x: "-90%", y: "0", color: "#2b7fc8" },
  { Icon: BedDouble, x: "0", y: "90%", color: "#8c5fc6" },
  { Icon: Ticket, x: "90%", y: "0", color: "#e2477c" },
  { Icon: CarFront, x: "0", y: "-90%", color: "#f26a2e" },
];

/**
 * The working indicator: travel icons taking turns, each gliding into the
 * centre from a different side and fading where it landed. No badge or
 * frame, just the icons. Pure CSS (see `.travel-loader` in globals.css), so it
 * keeps moving while the main thread is busy rendering a reply. With reduced
 * motion it holds still on the plane.
 */
export function TravelLoader({ size = "sm", label }: { size?: "sm" | "lg"; label?: string }) {
  const box = size === "lg" ? "h-12 w-12" : "h-6 w-6";
  const icon = size === "lg" ? 22 : 15;
  return (
    <span
      role={label ? "status" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={`travel-loader relative inline-grid shrink-0 place-items-center ${box}`}
      style={{ "--n": ICONS.length } as React.CSSProperties}
    >
      {ICONS.map(({ Icon, x, y, color }, i) => (
        <Icon
          key={i}
          size={icon}
          strokeWidth={1.9}
          className="travel-loader-icon absolute"
          color={color}
          style={{ "--i": i, "--from-x": x, "--from-y": y } as React.CSSProperties}
        />
      ))}
    </span>
  );
}
