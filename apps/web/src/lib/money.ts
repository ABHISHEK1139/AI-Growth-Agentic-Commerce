/**
 * Exact monetary formatting for integer minor units (paise / cents).
 * Requirement 11.2, 36.5.
 */

export function formatMinorToMajor(amountMinor: number, currency: string = "INR"): string {
  const isNegative = amountMinor < 0;
  const abs = Math.abs(Math.round(amountMinor));
  const major = Math.floor(abs / 100);
  const minor = abs % 100;

  const formattedMajor = new Intl.NumberFormat("en-IN").format(major);
  const formattedMinor = minor.toString().padStart(2, "0");

  const symbol = currency === "INR" ? "₹" : "$";
  const sign = isNegative ? "-" : "";

  if (minor === 0) {
    return `${sign}${symbol}${formattedMajor}`;
  }
  return `${sign}${symbol}${formattedMajor}.${formattedMinor}`;
}

export function majorToMinor(major: number): number {
  return Math.round(major * 100);
}
