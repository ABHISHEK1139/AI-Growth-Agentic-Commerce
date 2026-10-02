import { NextRequest, NextResponse } from "next/server";
import { chatCompletionsEndpoint, checkOutboundUrl } from "@/lib/outboundUrl";
import { clientRateLimitKey, isRateLimited } from "@/lib/simpleRateLimit";

/** Cached per-URL so hammering this endpoint cannot exhaust the outbound pool. */
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 30;

export async function POST(req: NextRequest) {
  const startTime = Date.now();
  try {
    const clientKey = clientRateLimitKey(req);
    if (isRateLimited(`ai-test:${clientKey}`, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_MS)) {
      return NextResponse.json(
        { ok: false, error: "Too many connection tests. Please wait a moment and try again." },
        { status: 429 }
      );
    }

    const body = await req.json();
    const { baseUrl, apiKey, modelName = "default", providerId } = body;

    if (!baseUrl || typeof baseUrl !== "string") {
      return NextResponse.json(
        { ok: false, error: "Please provide a valid Base URL (e.g. http://localhost:11434/v1 or https://api.x.ai/v1)" },
        { status: 400 }
      );
    }

    // The base URL is caller-supplied and the request below is server-side, so
    // this endpoint is an SSRF primitive unless the target is validated first.
    const checked = checkOutboundUrl(baseUrl);
    if (!checked.ok) {
      return NextResponse.json({ ok: false, error: checked.reason }, { status: 400 });
    }
    const trimmedBase = checked.url.origin + checked.url.pathname.replace(/\/+$/, "");
    const endpoint = chatCompletionsEndpoint(trimmedBase);

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };

    if (apiKey && typeof apiKey === "string" && apiKey.trim()) {
      headers["Authorization"] = `Bearer ${apiKey.trim()}`;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);

    try {
      const pingRes = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({
          model: typeof modelName === "string" ? modelName.trim() : "default",
          messages: [{ role: "user", content: "hello" }],
          max_tokens: 5,
        }),
        signal: controller.signal,
        // A public host must not be able to bounce this request to a blocked
        // address after the URL check above has already passed.
        redirect: "manual",
      });

      clearTimeout(timeoutId);
      const latencyMs = Date.now() - startTime;

      if (!pingRes.ok) {
        await pingRes.text().catch(() => "");
        return NextResponse.json({
          ok: false,
          error: `Endpoint returned HTTP ${pingRes.status}.`,
          status: pingRes.status,
          latencyMs,
        });
      }

      const resJson = await pingRes.json().catch(() => ({}));
      return NextResponse.json({
        ok: true,
        latencyMs,
        message: `Successfully connected to ${modelName} (${latencyMs}ms)`,
        modelReceived: resJson.model || modelName,
      });
    } catch (fetchErr: any) {
      clearTimeout(timeoutId);
      const latencyMs = Date.now() - startTime;
      const isAbort = fetchErr.name === "AbortError";
      const isConnectionRefused =
        fetchErr.message?.includes("ECONNREFUSED") || fetchErr.message?.includes("fetch failed");

      let userMsg = "Failed to connect to endpoint.";
      if (isAbort) {
        userMsg = "Connection timed out after 8 seconds. Check if server is running.";
      } else if (isConnectionRefused) {
        userMsg =
          "Connection refused. If using Ollama, run 'ollama serve'. If using LM Studio, ensure 'Local Server' is started.";
      }

      return NextResponse.json({
        ok: false,
        error: userMsg,
        latencyMs,
      });
    }
  } catch (error: any) {
    return NextResponse.json(
      { ok: false, error: "The connection test could not be completed." },
      { status: 500 }
    );
  }
}
