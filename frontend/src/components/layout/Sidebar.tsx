"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { label: "Dashboard", href: "/dashboard", icon: "⬡" },
  { label: "Tasks", href: "/tasks", icon: "◈" },
  { label: "Tools", href: "/tools", icon: "⬢" },
  { label: "Assets", href: "/assets", icon: "◇" },
  { label: "Settings", href: "/settings", icon: "⚙" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 h-screen w-64 bg-surface-container-low flex flex-col z-40">
      {/* Logo */}
      <div className="p-6 pb-4">
        <h1 className="font-display text-xl font-bold text-primary tracking-wider">
          CODETALKS
        </h1>
        <p className="text-xs text-on-surface-variant mt-1 tracking-widest uppercase">
          Code Analysis Platform
        </p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 space-y-1">
        {navItems.map((item) => {
          const isActive = pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-4 py-2.5 rounded-md text-sm font-ui transition-colors ${
                isActive
                  ? "bg-surface-container-high text-primary border-l-2 border-primary"
                  : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
              }`}
            >
              <span className="text-base">{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      {/* New Analysis Button */}
      <div className="p-4">
        <Link
          href="/tasks?new=true"
          className="block w-full py-2.5 text-center text-sm font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_12px_rgba(164,230,255,0.2)] transition-shadow"
        >
          New Analysis
        </Link>
      </div>
    </aside>
  );
}
