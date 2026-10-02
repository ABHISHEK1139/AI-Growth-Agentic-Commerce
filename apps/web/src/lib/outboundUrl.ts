/**
 * Guard for caller-supplied upstream URLs.
 *
 * `POST /api/ai/test-connection` and `POST /api/grok/chat` both accept a
 * `baseUrl` from the request body and issue a server-side request to it. With
 * no validation that is a full SSRF primitive: anyone who can reach this
 * origin can make the server fetch the cloud metadata service
 * (169.254.169.254), RFC1918 hosts, or anything else only the server can
 * reach, and read part of the response back.
 *
 * Loopback stays allowed on purpose: Ollama and LM Studio listen on the
 * operator's own machine and are the documented purpose of these endpoints.
 * Every other non-routable range is refused.
 *
 * Server-only. Never import this from a client component.
 */

export type OutboundUrlCheck =
  | { ok: true; url: URL }
  | { ok: false; reason: string };

/** Cloud metadata services reachable by hostname rather than IP literal. */
const BLOCKED_HOSTNAMES = new Set([
  "metadata.google.internal",
  "metadata.goog",
  "metadata",
  "instance-data",
  "instance-data.ec2.internal",
]);

const LOOPBACK_HOSTNAMES = new Set(["localhost", "ip6-localhost", "ip6-loopback"]);

/**
 * True for IPv4 literals that are not publicly routable.
 * 127.0.0.0/8 is deliberately *not* matched — local Ollama/LM Studio are the
 * documented use case for this endpoint.
 */
function isBlockedIpv4(host: string): boolean {
  const match = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!match) return false;
  const a = Number(match[1]);
  const b = Number(match[2]);
  if (a === 0) return true;                              // 0.0.0.0/8 "this host"
  if (a === 10) return true;                             // RFC1918
  if (a === 100 && b >= 64 && b <= 127) return true;     // CGNAT 100.64.0.0/10
  if (a === 169 && b === 254) return true;                // link-local, incl. 169.254.169.254
  if (a === 172 && b >= 16 && b <= 31) return true;       // RFC1918
  if (a === 192 && b === 0) return true;                  // 192.0.0.0/24, 192.0.2.0/24
  if (a === 192 && b === 168) return true;                // RFC1918
  if (a === 198 && (b === 18 || b === 19)) return true;   // 198.18.0.0/15 benchmarking
  if (a >= 224) return true;                              // multicast + reserved
  return false;
}

function isBlockedIpv6(host: string): boolean {
  const h = host.replace(/^\[/, "").replace(/\]$/, "").toLowerCase();
  if (h === "::1" || h === "0:0:0:0:0:0:0:1") return false; // loopback is allowed
  if (h === "::" || h === "0:0:0:0:0:0:0:0") return true;
  if (h.startsWith("::ffff:")) return isBlockedIpv4(h.slice(7));
  if (/^f[cd][0-9a-f]{2}:/.test(h)) return true;            // fc00::/7 unique-local
  if (/^fe[89ab][0-9a-f]:/.test(h)) return true;           // fe80::/10 link-local
  return false;
}

/**
 * Validate a caller-supplied absolute http(s) URL for server-side use.
 * Returns the parsed URL on success, or a message safe to show the caller.
 */
export function checkOutboundUrl(raw: unknown): OutboundUrlCheck {
  if (typeof raw !== "string" || !raw.trim()) {
    return { ok: false, reason: "A base URL is required." };
  }

  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return { ok: false, reason: "The base URL is not a valid absolute URL." };
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, reason: "Only http and https base URLs are supported." };
  }
  if (url.username || url.password) {
    return { ok: false, reason: "Credentials in the base URL are not supported." };
  }

  const host = url.hostname.toLowerCase();
  if (!host) {
    return { ok: false, reason: "The base URL is missing a host." };
  }
  if (BLOCKED_HOSTNAMES.has(host)) {
    return { ok: false, reason: "That host is not an allowed inference endpoint." };
  }
  if (LOOPBACK_HOSTNAMES.has(host) || host.endsWith(".localhost")) {
    return { ok: true, url };
  }
  if (isBlockedIpv4(host) || isBlockedIpv6(host)) {
    return {
      ok: false,
      reason:
        "Private, link-local, and cloud-metadata addresses are blocked. Only a loopback address (for Ollama / LM Studio) or a public endpoint host is allowed.",
    };
  }

  return { ok: true, url };
}

/**
 * Append `/chat/completions` unless the caller already pointed at it.
 * Callers must have run {@link checkOutboundUrl} on the base first.
 */
export function chatCompletionsEndpoint(base: string): string {
  const trimmed = base.trim().replace(/\/+$/, "");
  return trimmed.endsWith("/chat/completions") ? trimmed : `${trimmed}/chat/completions`;
}

/**
 * `fetch` options for a validated upstream: a hard timeout so a hung provider
 * cannot pin a route handler open, and no redirect following so a public host
 * cannot bounce the request to a blocked address.
 */
export function guardedFetchInit(timeoutMs: number): RequestInit {
  return { redirect: "manual", signal: AbortSignal.timeout(timeoutMs) };
}
