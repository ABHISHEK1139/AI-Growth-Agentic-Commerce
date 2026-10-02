import { NextResponse } from "next/server";
import { buildCapabilityDocument } from "@/lib/capabilityDocument";
import { getMerchantRules } from "@/lib/merchantRules";

/**
 * `/.well-known/agent-capability.json` — the discovery document an external AI
 * agent reads before it buys anything.
 *
 * This path is not under `/api/*`, so the `api/[...path]` fallback never
 * reached it and the route 404'd: the merchant console's "do the three
 * discovery routes agree?" panel was comparing two documents and one empty
 * response, and reporting agreement. The builder is shared with
 * `/api/v1/capability`, which is the only thing that makes the three-surface
 * comparison mean anything.
 */
export async function GET() {
  return NextResponse.json(buildCapabilityDocument(getMerchantRules()), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}
