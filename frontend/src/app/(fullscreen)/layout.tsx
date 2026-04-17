import type { Metadata } from "next";
import "../globals.css";

export const metadata: Metadata = {
  title: "CodeTalks",
  description: "Code analysis workspace",
};

export default function FullscreenLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full antialiased scroll-smooth">
      <body className="h-full bg-[#050506] text-on-surface font-ui overflow-hidden selection:bg-primary/30">
        {/* Ambient background glow */}
        <div className="fixed inset-0 pointer-events-none overflow-hidden z-0">
          <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] rounded-full bg-primary/5 blur-[120px]" />
          <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] rounded-full bg-secondary/5 blur-[120px]" />
        </div>
        <div className="relative z-10 h-full">
          {children}
        </div>
      </body>
    </html>
  );
}
