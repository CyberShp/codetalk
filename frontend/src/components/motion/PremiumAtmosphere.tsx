"use client";

import { useEffect } from "react";

export default function PremiumAtmosphere() {
  useEffect(() => {
    const root = document.documentElement;
    let raf = 0;

    const handlePointerMove = (event: PointerEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        root.style.setProperty("--ct-pointer-x", `${event.clientX}px`);
        root.style.setProperty("--ct-pointer-y", `${event.clientY}px`);
      });
    };

    window.addEventListener("pointermove", handlePointerMove, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", handlePointerMove);
    };
  }, []);

  return (
    <div className="ct-atmosphere" aria-hidden="true">
      <div className="ct-atmosphere__grid" />
      <div className="ct-atmosphere__halo ct-atmosphere__halo-a" />
      <div className="ct-atmosphere__halo ct-atmosphere__halo-b" />
      <div className="ct-atmosphere__ribbon ct-atmosphere__ribbon-a" />
      <div className="ct-atmosphere__ribbon ct-atmosphere__ribbon-b" />
      <div className="ct-atmosphere__lux ct-atmosphere__lux-a" />
      <div className="ct-atmosphere__lux ct-atmosphere__lux-b" />
      <div className="ct-atmosphere__lux-line ct-atmosphere__lux-line-a" />
      <div className="ct-atmosphere__lux-line ct-atmosphere__lux-line-b" />
      <div className="ct-atmosphere__beam ct-atmosphere__beam-a" />
      <div className="ct-atmosphere__beam ct-atmosphere__beam-b" />
      <div className="ct-atmosphere__spotlight" />
      <div className="ct-atmosphere__grain" />
    </div>
  );
}
