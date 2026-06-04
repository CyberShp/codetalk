import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";

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
        <Sidebar />
        <main className="p-4 md:ml-56 md:p-6 min-h-screen">{children}</main>
      </body>
    </html>
  );
}
