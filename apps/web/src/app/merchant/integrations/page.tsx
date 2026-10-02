"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * The integrations screen is now the channels screen.
 *
 * The old `/merchant/integrations` page was a form that posted to
 * `/api/v1/connectors/register`, which fired a sync and discarded the result.
 * Connections were held in an in-memory registry, so they were lost on restart
 * and nothing recorded whether a sync had ever run. `/merchant/channels` reads
 * the persisted connections and the sync and order-push logs instead.
 *
 * The redirect keeps this path working rather than 404ing, because it is the URL
 * an operator was told to use.
 */
export default function IntegrationsRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/merchant/channels");
  }, [router]);

  return null;
}
