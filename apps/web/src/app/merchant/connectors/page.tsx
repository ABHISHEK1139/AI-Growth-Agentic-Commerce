"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * `/merchant/connectors` is an alias for `/merchant/channels`.
 *
 * Both names were reachable but only the redirect target carried a real console.
 * "Connectors" is the backend term; "channels" is what an operator is doing, and
 * the screen is now the one at `/merchant/channels`.
 */
export default function MerchantConnectorsPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/merchant/channels");
  }, [router]);

  return null;
}
