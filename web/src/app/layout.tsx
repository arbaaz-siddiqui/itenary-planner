import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Plus_Jakarta_Sans } from "next/font/google";
import { THEME_BOOT_SCRIPT } from "@/lib/themeBoot";
import "./globals.css";

// Text: Plus Jakarta Sans, open and friendly at small sizes, with a proper
// rupee sign (U+20B9). Headings: Bricolage Grotesque, a characterful
// grotesque for display sizes. Both carry the rupee sign, so prices never
// fall back to a system face mid-number.
const sans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans-face",
  display: "swap",
});
const display = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
  variable: "--font-display-face",
  display: "swap",
});

export const metadata: Metadata = {
  title: "GujjuTours AI Travel Agent",
  description: "Plan Dubai trips, flights, hotels, tours, transfers and visas with the GujjuTours AI travel agent",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Prevents iOS Safari zooming the page when the composer gets focus.
  maximumScale: 1,
  // Edge to edge on notched phones; the layout pads with safe-area insets.
  viewportFit: "cover",
  themeColor: "#0b0b0f",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme is rewritten by the boot script before first paint, so the
    // server's "dark" never flashes for a light-theme user.
    <html lang="en" data-theme="dark" className={`${sans.variable} ${display.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="h-full font-sans antialiased">{children}</body>
    </html>
  );
}
