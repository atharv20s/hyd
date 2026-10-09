import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HYD PS2 | Made for you. Made for Hyderabad.",
  description: "Your Hyderabad growth desk: local business discovery, thoughtful outreach, communication, and voice assistants. AI-assisted, human-approved.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
