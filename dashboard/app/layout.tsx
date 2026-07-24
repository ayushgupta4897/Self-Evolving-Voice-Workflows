import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Swarm Evolution — Live Workflow Evolution",
  description:
    "Graph diff, population board and fitness trend for the self-evolving voice workflow.",
};

/**
 * Fonts are loaded with a plain <link> rather than next/font on purpose:
 * next/font fetches at build time and a venue with no network would fail the
 * build outright. A <link> that cannot resolve degrades to the system stack
 * and the page still renders.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;700&family=Geist+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
