"use client";

import { useEffect, useState } from "react";

export default function PremiumAtmosphere() {
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  if (reducedMotion) return null;

  return (
    <div className="ct-atmosphere" aria-hidden="true">
      <div className="ct-atmosphere__grid" />
      <div className="ct-atmosphere__halo ct-atmosphere__halo-a" />
      <div className="ct-atmosphere__halo ct-atmosphere__halo-b" />
      <div className="ct-atmosphere__grain" />
    </div>
  );
}
