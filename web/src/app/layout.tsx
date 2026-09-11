import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Dubai Trip Planner",
  description: "AI travel agent for Dubai itineraries",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Prevents iOS Safari zooming the page when the composer gets focus.
  maximumScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#1a1a1c" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-full font-sans antialiased">{children}</body>
    </html>
  );
}
