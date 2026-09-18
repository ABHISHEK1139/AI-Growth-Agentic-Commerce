/**
 * Exact monetary formatting for integer minor units (paise / cents).
 * Requirement 11.2, 36.5.
 *
 * All math stays in integers: the only division is by 100 with a remainder,
 * so there is no binary floating-point anywhere on this path.
 */

const CURRENCY_SYMBOLS: Record<string, string> = {
  INR: "₹",
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
};

export function formatMinorToMajor(amountMinor: number, currency: string = "INR"): string {
  if (!Number.isFinite(amountMinor)) return "—";
  const isNegative = amountMinor < 0;
  const abs = Math.abs(Math.round(amountMinor));
  const major = Math.floor(abs / 100);
  const minor = abs % 100;

  const formattedMajor = new Intl.NumberFormat("en-IN").format(major);
  const formattedMinor = minor.toString().padStart(2, "0");

  const symbol = CURRENCY_SYMBOLS[(currency || "INR").toUpperCase()] ?? "$";
  const sign = isNegative ? "-" : "";

  return `${sign}${symbol}${formattedMajor}.${formattedMinor}`;
}

/**
 * Whole major units (or a 2-decimal string) to integer minor units.
 * Rounds the decimal representation first so a value like 19.99 — which is
 * not exact in binary floating point — still converts to exactly 1999.
 */
export function majorToMinor(major: number | string): number {
  const n = typeof major === "string" ? Number(major) : major;
  if (!Number.isFinite(n) || n < 0) {
    throw new RangeError("majorToMinor expects a finite non-negative amount");
  }
  return Math.round(Number(n.toFixed(2)) * 100);
}
