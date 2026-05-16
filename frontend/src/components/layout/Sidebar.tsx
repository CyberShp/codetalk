"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FilePlus2,
  Settings,
  Wrench,
  Shield,
} from "lucide-react";
import type { ReactNode } from "react";

interface NavItem {
  label: string;
  href: string;
  icon: ReactNode;
}

const navItems: NavItem[] = [
  { label: "仪表盘", href: "/", icon: <LayoutDashboard size={18} /> },
  { label: "新建分析", href: "/tasks/new", icon: <FilePlus2 size={18} /> },
  { label: "覆盖率分析", href: "/coverage", icon: <Shield size={18} /> },
  { label: "工具状态", href: "/tools", icon: <Wrench size={18} /> },
  { label: "设置", href: "/settings", icon: <Settings size={18} /> },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname.startsWith(href);
}

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 h-screen w-56 bg-surface-container-low flex flex-col z-40 border-r border-outline-variant/30">
      {/* Logo */}
      <div className="px-5 pt-6 pb-4">
        <h1 className="font-display text-lg font-bold text-primary tracking-wider">
          CODETALK
        </h1>
        <p className="text-xs text-on-surface-variant mt-1">
          轻量代码分析平台
        </p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 space-y-0.5">
        {navItems.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                active
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
              }`}
            >
              {item.icon}
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-outline-variant/20">
        <p className="text-[10px] text-on-surface-variant/50">
          CodeTalk v1.0
        </p>
      </div>
    </aside>
  );
}
