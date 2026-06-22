"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Archive,
  Settings,
  Wrench,
  Shield,
  BookOpen,
  FolderOpen,
  Workflow,
} from "lucide-react";
import type { ReactNode } from "react";

interface NavItem {
  label: string;
  href: string;
  icon: ReactNode;
}

const navItems: NavItem[] = [
  { label: "工作台", href: "/", icon: <LayoutDashboard size={18} /> },
  { label: "工作空间", href: "/workspaces", icon: <FolderOpen size={18} /> },
  { label: "Agent Workbench", href: "/workbench", icon: <Workflow size={18} /> },
  { label: "DeepWiki", href: "/deepwiki", icon: <BookOpen size={18} /> },
  { label: "历史任务", href: "/tasks", icon: <Archive size={18} /> },
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
    <aside className="sticky top-0 w-full bg-surface-container-low flex flex-col z-40 border-b border-outline-variant/70 shadow-sm md:fixed md:left-0 md:top-0 md:h-screen md:w-56 md:border-r md:border-b-0">
      {/* Logo */}
      <div className="px-4 pt-4 pb-3 md:px-5 md:pt-6 md:pb-4">
        <h1 className="font-display text-lg font-bold text-primary tracking-wider">
          CODETALK
        </h1>
        <p className="text-xs text-on-surface-variant mt-1">
          轻量代码分析平台
        </p>
      </div>

      {/* Navigation */}
      <nav className="px-3 pb-3 flex gap-1 overflow-x-auto md:flex-1 md:block md:space-y-0.5 md:pb-0">
        {navItems.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex shrink-0 items-center gap-2 md:gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                active
                  ? "bg-primary text-on-primary font-medium shadow-sm"
                  : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              }`}
            >
              {item.icon}
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="hidden md:block px-5 py-4 border-t border-outline-variant/60 bg-surface-container-lowest">
        <p className="text-[10px] text-on-surface-variant/70">
          CodeTalk v1.0
        </p>
      </div>
    </aside>
  );
}
