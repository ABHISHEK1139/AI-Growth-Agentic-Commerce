/**
 * In-process sliding window for unauthenticated Next.js route handlers.
 *
 * This is not a substitute for gateway rate limits. It only caps a single
 * Node process so a browser tab cannot burn the configured model key.
 */

const buckets = new Map<string, number[]>();

export function isRateLimited(key: string, max: number, windowMs: number): boolean {
  const now = Date.now();
  const hits = (buckets.get(key) || []).filter((t) => now - t < windowMs);
  hits.push(now);
  buckets.set(key, hits);
  if (buckets.size > 10_000) {
    const cutoff = now - windowMs;
    for (const [k, times] of buckets) {
      const kept = times.filter((t) => t > cutoff);
      if (kept.length === 0) buckets.delete(k);
      else buckets.set(k, kept);
    }
  }
  return hits.length > max;
}

export function clientRateLimitKey(req: { headers: { get(name: string): string | null } }): string {
  return (req.headers.get("x-forwarded-for") || req.headers.get("x-real-ip") || "local")
    .split(",")[0]
    .trim();
}
