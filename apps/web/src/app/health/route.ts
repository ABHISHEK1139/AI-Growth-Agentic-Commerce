import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    ok: true,
    status: "ok",
    service: "agentpay-web",
    request_id: `req_${Date.now().toString(36)}`,
    timestamp: new Date().toISOString(),
    data: {
      service: "agentpay-web",
      env: process.env.NODE_ENV || "production",
      payment_provider: "razorpay",
      model_provider: process.env.MODEL_NAME || "gemini-2.5-flash",
    },
  });
}
