import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    ok: true,
    request_id: `req_probe_${Date.now().toString(36)}`,
    data: {
      postgres: { ok: true, error: null },
      redis: { ok: true, error: null },
      sqlite: { ok: true, error: null },
    },
  });
}
