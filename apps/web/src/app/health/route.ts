import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    status: "ok",
    service: "agentpay-web",
    timestamp: new Date().toISOString(),
  });
}
