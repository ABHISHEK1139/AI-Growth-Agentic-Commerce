import { NextResponse } from "next/server";

import { getServerDb } from "@/lib/serverDb";

/**
 * Web-tier datastore probe. The SQLite file this tier reads through
 * @/lib/serverDb is actually opened; PostgreSQL/Redis belong to the gateway
 * and are reported as unprobed here rather than hardcoded healthy — a
 * hardcoded ok:true would green-light a demo whose database is missing.
 */
export async function GET() {
  let sqlite: { ok: boolean; error: string | null };
  try {
    getServerDb().prepare("SELECT 1").get();
    sqlite = { ok: true, error: null };
  } catch (err) {
    sqlite = { ok: false, error: err instanceof Error ? err.message : "unreachable" };
  }
  const unprobed = { ok: false, error: "not probed by the web tier" };
  const ok = sqlite.ok;
  return NextResponse.json(
    {
      ok,
      request_id: `req_probe_${Date.now().toString(36)}`,
      data: {
        postgres: unprobed,
        redis: unprobed,
        sqlite,
      },
    },
    { status: ok ? 200 : 503 }
  );
}
