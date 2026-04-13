import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";
import TopBar from "@/components/layout/TopBar";

export const metadata: Metadata = {
  title: "CodeTalks — Code Analysis Platform",
  description: "Orchestrate open-source code analysis tools with rich visualization",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-surface text-on-surface font-ui">
        <Sidebar />
        <TopBar />
        <main className="ml-64 mt-14 p-6">{children}</main>
      </body>
    </html>
  );
}
