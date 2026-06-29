import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";
import PremiumAtmosphere from "@/components/motion/PremiumAtmosphere";
import AIThreadMiniDock from "@/components/ai/AIThreadMiniDock";

export const metadata: Metadata = {
  title: "CodeTalk — 轻量代码分析平台",
  description: "编排开源代码分析工具，生成结构化分析报告",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full bg-surface text-on-surface font-ui">
        <PremiumAtmosphere />
        <Sidebar />
        <main className="ct-page-shell relative z-10 min-h-screen p-4 md:p-6">{children}</main>
        <AIThreadMiniDock />
      </body>
    </html>
  );
}
