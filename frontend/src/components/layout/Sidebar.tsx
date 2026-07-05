"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChevronDown,
  LayoutDashboard,
  Settings,
  Shield,
  FolderOpen,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  Workflow,
} from "lucide-react";
import type { ReactNode } from "react";

interface NavItem {
  label: string;
  href: string;
  icon: ReactNode;
}

interface OrchestrationChild {
  label: string;
  href: string;
}

const navItems: NavItem[] = [
  { label: "工作台", href: "/", icon: <LayoutDashboard size={18} /> },
  { label: "工作空间", href: "/workspaces", icon: <FolderOpen size={18} /> },
  { label: "AI 线程", href: "/ai", icon: <MessageSquareText size={18} /> },
  { label: "覆盖率分析", href: "/coverage", icon: <Shield size={18} /> },
  { label: "设置", href: "/settings", icon: <Settings size={18} /> },
];

const orchestrationChildren: OrchestrationChild[] = [
  { label: "运行驾驶舱", href: "/workbench" },
  { label: "工作流设计", href: "/workbench/designer" },
  { label: "语义库", href: "/workbench/semantic" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/workbench") return pathname === "/workbench";
  return pathname.startsWith(href);
}

const NAV_COLLAPSED_KEY = "codetalk.nav.collapsed";

export default function Sidebar() {
  const pathname = usePathname();
  const transitionTimerRef = useRef<number | null>(null);
  const [userCollapsed, setUserCollapsed] = useState<boolean | null>(null);
  const [isSwitching, setIsSwitching] = useState(false);
  const collapsed = userCollapsed ?? pathname.startsWith("/ai");

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const saved = window.localStorage.getItem(NAV_COLLAPSED_KEY);
      if (saved !== null) {
        setUserCollapsed(saved === "true");
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.navCollapsed = collapsed ? "true" : "false";
  }, [collapsed]);

  useEffect(() => {
    return () => {
      if (transitionTimerRef.current !== null) {
        window.clearTimeout(transitionTimerRef.current);
      }
    };
  }, []);

  const toggleCollapsed = () => {
    if (transitionTimerRef.current !== null) {
      window.clearTimeout(transitionTimerRef.current);
    }
    setIsSwitching(true);
    setUserCollapsed((current) => {
      const resolved = current ?? pathname.startsWith("/ai");
      const nextValue = !resolved;
      window.localStorage.setItem(NAV_COLLAPSED_KEY, String(nextValue));
      document.documentElement.dataset.navCollapsed = nextValue ? "true" : "false";
      return nextValue;
    });
    transitionTimerRef.current = window.setTimeout(() => {
      setIsSwitching(false);
      transitionTimerRef.current = null;
    }, 360);
  };

  const orchestrationGroup = (
    <div
      className={`ct-app-sidebar__group ${pathname.startsWith("/workbench") ? "is-open" : ""}`}
    >
      <Link
        href="/workbench"
        className={`ct-app-sidebar__link ct-app-sidebar__group-root ${
          pathname.startsWith("/workbench") ? "is-active" : ""
        }`}
        title={collapsed ? "智能体编排" : undefined}
        aria-expanded={pathname.startsWith("/workbench")}
      >
        {pathname.startsWith("/workbench") && (
          <>
            <span className="ct-app-sidebar__active-bar" aria-hidden="true" />
            <span className="ct-app-sidebar__active-glow" aria-hidden="true" />
          </>
        )}
        <span className="ct-app-sidebar__icon" aria-hidden="true">
          <Workflow size={18} />
        </span>
        <span className="ct-app-sidebar__label">智能体编排</span>
        <ChevronDown
          className="ct-app-sidebar__group-chevron"
          size={14}
          aria-hidden="true"
        />
      </Link>
      {!collapsed && pathname.startsWith("/workbench") && (
        <div className="ct-app-sidebar__children" aria-label="智能体编排子导航">
          {orchestrationChildren.map((child) => {
            const active = pathname === child.href;
            return (
              <Link
                key={child.href}
                href={child.href}
                className={`ct-app-sidebar__child ${active ? "is-active" : ""}`}
                aria-current={active ? "page" : undefined}
              >
                {child.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );

  return (
    <aside className={`ct-app-sidebar ${collapsed ? "is-collapsed" : ""} ${isSwitching ? "is-switching" : ""}`}>
      {/* Logo */}
      <div className="ct-app-sidebar__brand">
        <span className="ct-app-sidebar__shine" aria-hidden="true" />
        <div className="ct-app-sidebar__mark" aria-hidden="true">C</div>
        <div className="ct-app-sidebar__brand-text">
          <h1>CODETALK</h1>
          <p>轻量代码分析平台</p>
        </div>
        <button
          type="button"
          className="ct-app-sidebar__collapse"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "展开 CodeTalk 导航" : "折叠 CodeTalk 导航"}
          title={collapsed ? "展开导航" : "折叠导航"}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="ct-app-sidebar__nav" aria-label="CodeTalk 主导航">
        {navItems.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <Fragment key={item.href}>
              <Link
                href={item.href}
                className={`ct-app-sidebar__link ${active ? "is-active" : ""}`}
                title={collapsed ? item.label : undefined}
              >
                {active && (
                  <>
                    <span className="ct-app-sidebar__active-bar" aria-hidden="true" />
                    <span className="ct-app-sidebar__active-glow" aria-hidden="true" />
                  </>
                )}
                <span className="ct-app-sidebar__icon" aria-hidden="true">
                  {item.icon}
                </span>
                <span className="ct-app-sidebar__label">{item.label}</span>
              </Link>
              {item.href === "/workspaces" ? orchestrationGroup : null}
            </Fragment>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="ct-app-sidebar__footer">
        <span className="ct-app-sidebar__status" aria-hidden="true" />
        <p>CodeTalk v1.0</p>
      </div>
    </aside>
  );
}
