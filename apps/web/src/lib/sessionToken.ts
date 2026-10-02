/**
 * Edge-runtime verification of this project's session token.
 *
 * The token is a hand-rolled compact JWS: base64url header, base64url payload,
 * base64url HMAC-SHA256, separated by dots. `packages/security/tokens.py` issues
 * and verifies it; this is the JavaScript equivalent, so the middleware can
 * answer "is this a merchant session?" without a network call on every
 * navigation.
 *
 * Why it exists
 * -------------
 * The middleware originally asked the API (`GET /api/v1/auth/me`) on every
 * navigation. That endpoint is rate limited, and a console page makes several API
 * calls per navigation, so a burst earned a 429 -- and the gate read that as
 * "signed out", logging an operator out of a valid session and sending them to a
 * sign-in form that then told them their password was wrong.
 *
 * The *signature* is checked here; the *authority* stays with the API. Every
 * endpoint calls `require_roles` regardless of what the middleware decides, so a
 * role this file invents grants nothing. A tampered or expired token fails the
 * same check it would fail server-side, because it is the same algorithm with the
 * same secret.
 *
 * What it does NOT do
 * -------------------
 * Consult a database, check revocation, or know whether an operator was disabled
 * after the token was minted. All three are the API's job. The window that leaves
 * -- a token issued before a revocation, valid until it expires -- is the window
 * every JWT deployment has, and the session TTL bounds it.
 */

const SESSION_PAYLOAD_TYPE = "session";

export type TokenVerdict =
  | { kind: "valid"; role: string }
  | { kind: "invalid" }
  | { kind: "unknown" };

function base64UrlToBytes(value: string): Uint8Array | null {
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  } catch {
    return null;
  }
}

function toBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  // Indexed rather than spread: a 32-byte HMAC is fine either way, but an
  // iterated `Uint8Array` needs `downlevelIteration`, and the indexed form is
  // not measurably different at this size.
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decodeSegment(segment: string): Record<string, unknown> | null {
  const bytes = base64UrlToBytes(segment);
  if (!bytes) return null;
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (typeof parsed !== "object" || parsed === null) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Verify the signature, the token type, and the expiry. Returns the role.
 *
 * `unknown` means "this build cannot tell" -- no secret configured -- which is
 * distinct from `invalid`, and the middleware treats them differently. The
 * difference is a legitimate operator's experience: `unknown` fails closed to
 * sign-in, `invalid` also fails closed, but only one of them is worth an alert.
 */
export async function verifySessionToken(
  token: string,
  secret: string | undefined
): Promise<TokenVerdict> {
  if (!secret) return { kind: "unknown" };
  if (typeof token !== "string" || token.length === 0 || token.length > 8192) {
    return { kind: "invalid" };
  }

  const parts = token.split(".");
  if (parts.length !== 3) return { kind: "invalid" };
  const [header, payload, signature] = parts;

  const headerClaims = decodeSegment(header);
  const payloadClaims = decodeSegment(payload);
  if (!headerClaims || !payloadClaims) return { kind: "invalid" };

  // The algorithm, and the token *type*.
  //
  // `typ` is checked on the **payload**, not the header. The header carries
  // `{"alg":"HS256","typ":"JWT"}` for every token this project issues; the
  // payload carries `"typ":"session"` or `"typ":"access"`. Reading the header
  // compares `"JWT"` against `"session"`, fails every token, and redirects every
  // signed-in operator to the sign-in page -- which is what this check did before
  // it was corrected.
  if (headerClaims.alg !== "HS256") return { kind: "invalid" };
  if (payloadClaims.typ !== SESSION_PAYLOAD_TYPE) return { kind: "invalid" };

  const expected = await hmacSha256(`${header}.${payload}`, secret);
  if (!constantTimeEquals(expected, signature)) return { kind: "invalid" };

  const exp = payloadClaims.exp;
  if (typeof exp !== "number") return { kind: "invalid" };
  // `iat` is deliberately not checked. The signature is already verified, so a
  // future `iat` is clock skew rather than forgery -- and refusing on skew would
  // log out every operator whose browser runs ahead of the server.
  if (exp * 1000 <= Date.now()) return { kind: "invalid" };

  const role = payloadClaims.role;
  if (typeof role !== "string") return { kind: "invalid" };

  return { kind: "valid", role };
}

async function hmacSha256(message: string, secret: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return toBase64Url(signature);
}

/**
 * Length-independent comparison.
 *
 * A plain `!==` on a signature leaks its prefix through timing, and a prefix is
 * enough to make forging cheap. On a length mismatch both operands are still
 * walked, so the early return is not itself the signal.
 */
function constantTimeEquals(a: string, b: string): boolean {
  if (a.length !== b.length) {
    let sink = 0;
    const length = Math.max(a.length, b.length, 1);
    for (let i = 0; i < length; i++) {
      sink |= (a.charCodeAt(i % (a.length || 1)) || 0) ^ (b.charCodeAt(i % (b.length || 1)) || 0);
    }
    return false && sink === -1;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}
