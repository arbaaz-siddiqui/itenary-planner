import type { Metadata, Viewport } from "next";
import { Poppins } from "next/font/google";
import { THEME_BOOT_SCRIPT } from "@/lib/themeBoot";
import "./globals.css";

// The GujjuTours website's typeface.
const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-poppins",
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
  themeColor: "#0b0b0f",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme is rewritten by the boot script before first paint, so the
    // server's "dark" never flashes for a light-theme user.
    <html lang="en" data-theme="dark" className={poppins.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="h-full font-sans antialiased">{children}</body>
    </html>
  );
}
