"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Consolidated Merchant Policy Route.
 *
 * This route previously duplicated `/merchant/policy`. All merchant policy bounds,
 * multi-surface agreement checks, and rule projections are consolidated under
 * `/merchant/policy`. This route seamlessly redirects callers to the canonical screen.
 */
export default function PoliciesRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/merchant/policy");
  }, [router]);

  return null;
}
