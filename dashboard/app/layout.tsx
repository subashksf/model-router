import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Model Router",
  description: "Cost attribution and routing analytics",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
