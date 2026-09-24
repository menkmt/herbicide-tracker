"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

/**
 * Sends one beacon per page view to the site's own /api/track.
 *
 * No cookie, no identifier, no script from anyone else: the path and the
 * referrer are all that leave the browser, and the query string is dropped
 * before sending so a searched address is never recorded.
 */
export function Track() {
  const pathname = usePathname();

  useEffect(() => {
    if (!pathname || pathname.startsWith("/api/")) return;
    if (navigator.webdriver) return;
    const payload = JSON.stringify({ path: pathname, referrer: document.referrer || null });
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/track", new Blob([payload], { type: "application/json" }));
      } else {
        fetch("/api/track", { method: "POST", body: payload, keepalive: true,
                              headers: { "Content-Type": "application/json" } }).catch(() => {});
      }
    } catch {
      // Never let measurement affect the page.
    }
  }, [pathname]);

  return null;
}
