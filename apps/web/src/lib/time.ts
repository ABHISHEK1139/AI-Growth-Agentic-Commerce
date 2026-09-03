/**
 * Server-time-anchored offset measurement and countdown calculation.
 * Requirement 29.6, 36.7.
 */

export function calculateRemainingSeconds(
  targetIsoString: string,
  serverOffsetMs: number = 0
): number {
  const targetMs = new Date(targetIsoString).getTime();
  const nowMs = Date.now() + serverOffsetMs;
  const diffSec = Math.floor((targetMs - nowMs) / 1000);
  return Math.max(0, diffSec);
}

export function formatCountdown(totalSeconds: number): string {
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

/**
 * Offset between the server clock and this browser's clock, in milliseconds.
 *
 * `serverDateMs` is the instant the server produced the response, read from the
 * HTTP `Date` header (the API exposes no time field in any body, so the header is
 * the only anchor available without changing the API). `clientReceivedAtMs` is
 * `Date.now()` sampled the moment that response was handled.
 *
 * The result is added to `Date.now()` by `calculateRemainingSeconds`, so a
 * browser whose clock is wrong by hours still counts down against server time.
 * Resolution is one second plus one network leg, which is why expiry remains
 * enforced server-side: this offset makes the countdown honest, it does not make
 * the client the authority.
 */
export function serverOffsetMs(
  serverDateMs: number | null | undefined,
  clientReceivedAtMs: number
): number {
  if (serverDateMs === null || serverDateMs === undefined || Number.isNaN(serverDateMs)) {
    return 0;
  }
  return serverDateMs - clientReceivedAtMs;
}

/** True when `targetIsoString` is at or before the server-anchored present. */
export function hasExpired(targetIsoString: string, offsetMs: number = 0): boolean {
  const targetMs = new Date(targetIsoString).getTime();
  if (Number.isNaN(targetMs)) return false;
  return targetMs - (Date.now() + offsetMs) <= 0;
}
