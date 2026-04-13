"use client";

export default function TopBar() {
  return (
    <header className="fixed top-0 left-64 right-0 h-14 bg-surface-container/60 backdrop-blur-xl z-30 flex items-center justify-between px-6">
      {/* Search */}
      <div className="flex-1 max-w-md">
        <input
          type="text"
          placeholder="Search projects, tasks..."
          className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-1.5 rounded-md outline-none placeholder:text-on-surface-variant/40 focus:ring-1 focus:ring-primary-container"
        />
      </div>

      {/* Status */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-secondary shadow-[0_0_6px_rgba(236,255,227,0.4)]" />
          <span className="text-xs text-on-surface-variant">System Online</span>
        </div>
        <div className="w-8 h-8 rounded-md bg-surface-container-high flex items-center justify-center text-on-surface-variant text-sm">
          CT
        </div>
      </div>
    </header>
  );
}
