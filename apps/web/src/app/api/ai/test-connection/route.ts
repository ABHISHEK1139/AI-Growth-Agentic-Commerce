import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const startTime = Date.now();
  try {
    const body = await req.json();
    const { baseUrl, apiKey, modelName = "default", providerId } = body;

    if (!baseUrl || typeof baseUrl !== "string") {
      return NextResponse.json(
        { ok: false, error: "Please provide a valid Base URL (e.g. http://localhost:11434/v1 or https://api.x.ai/v1)" },
        { status: 400 }
      );
    }

    const trimmedBase = baseUrl.trim().replace(/\/$/, "");
    const endpoint = trimmedBase.endsWith("/chat/completions")
      ? trimmedBase
      : `${trimmedBase}/chat/completions`;

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
          model: modelName.trim(),
          messages: [{ role: "user", content: "hello" }],
          max_tokens: 5,
        }),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);
      const latencyMs = Date.now() - startTime;

      if (!pingRes.ok) {
        const errText = await pingRes.text();
        return NextResponse.json({
          ok: false,
          error: `Endpoint returned HTTP ${pingRes.status}: ${errText.slice(0, 200)}`,
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

      let userMsg = fetchErr.message || "Failed to connect to endpoint";
      if (isAbort) {
        userMsg = "Connection timed out after 8 seconds. Check if server is running.";
      } else if (isConnectionRefused) {
        userMsg = `Connection refused at ${trimmedBase}. If using Ollama, run 'ollama serve'. If using LM Studio, ensure 'Local Server' is started.`;
      }

      return NextResponse.json({
        ok: false,
        error: userMsg,
        latencyMs,
      });
    }
  } catch (error: any) {
    return NextResponse.json(
      { ok: false, error: error?.message || "Internal test error" },
      { status: 500 }
    );
  }
}
