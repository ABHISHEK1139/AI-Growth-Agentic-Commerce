/**
 * Presentation helpers for catalog records.
 *
 * Nothing here computes a monetary value. Formatting of money is
 * `formatMinorToMajor` from `@/lib/money` and nowhere else; this module only
 * labels, normalises text, and translates a route slug into the category
 * identifier the catalog actually stores.
 */

import type {
  CatalogProduct,
  CatalogSourceName,
  ExploreOffer,
  PricingSource,
} from "./types";

// ---------------------------------------------------------------------------
// Provenance labels
// ---------------------------------------------------------------------------

/**
 * How a price came to exist. Shown wherever a price is shown.
 *
 * `OfferV1.pricing_source` has exactly two values, and neither of them is
 * "observed on another site". Saying so where the buyer can read it is the point:
 * a figure generated inside a band is not a market price and must not be mistaken
 * for one.
 */
export function pricingSourceLabel(source: PricingSource | null | undefined): string {
  switch (source) {
    case "merchant_configured":
      return "Merchant-configured price";
    case "synthetic_band_random":
      return "Generated demo price";
    default:
      return "Price provenance not reported";
  }
}

export function pricingSourceDetail(source: PricingSource | null | undefined): string {
  switch (source) {
    case "merchant_configured":
      return "Set by the merchant in their own catalog record. Not scraped from any marketplace.";
    case "synthetic_band_random":
      return "Generated inside a configured price band for demonstration. Not a market price and not scraped from any marketplace.";
    default:
      return "This record did not state how its price was set.";
  }
}

/** Which catalog answered (`apps/api/catalog_source.py`). */
export function catalogSourceLabel(source: CatalogSourceName | null | undefined): string {
  switch (source) {
    case "postgresql":
      return "Published catalog";
    case "seed_fixture":
      return "Seed import artifacts";
    default:
      return "Catalog source not reported";
  }
}

export function catalogSourceDetail(source: CatalogSourceName | null | undefined): string {
  switch (source) {
    case "postgresql":
      return "Answered by the merchant's published catalog in PostgreSQL.";
    case "seed_fixture":
      return "The published catalog was unreachable, so this answer came from the seed import artifacts. The filter semantics are identical; the record set is smaller.";
    default:
      return "This response did not name the catalog that answered it.";
  }
}

// ---------------------------------------------------------------------------
// Category slugs
// ---------------------------------------------------------------------------

/**
 * Route slug to the `category_id` the catalog stores.
 *
 * The two vocabularies genuinely differ: the routes were written as plurals
 * (`/category/laptops`) and the catalog stores the singular subcategory the
 * importer wrote (`laptop`, `smartphone`, `monitor`, `audio`,
 * `computer_accessory` -- see `data/seed/catalog/products.jsonl` and
 * `services/offers/seed.py`). This map is the translation and nothing more; it
 * invents no category. A slug that is absent from it has no catalog category, and
 * the category screen says that rather than searching for a value that cannot
 * match.
 */
export const CATEGORY_SLUG_TO_ID: Record<string, string> = {
  laptops: "laptop",
  laptop: "laptop",
  phones: "smartphone",
  smartphones: "smartphone",
  smartphone: "smartphone",
  monitors: "monitor",
  monitor: "monitor",
  audio: "audio",
  headphones: "audio",
  accessories: "computer_accessory",
  keyboards: "computer_accessory",
  computer_accessory: "computer_accessory",
  cameras: "camera",
  camera: "camera",
  appliances: "appliance",
  appliance: "appliance",
  electronics: "home_electronics",
  home_electronics: "home_electronics",
  phone_accessory: "phone_accessory",
  phone_accessories: "phone_accessory",
};

/** Display wording for a route slug. Falls back to the slug itself. */
export const CATEGORY_SLUG_TITLE: Record<string, string> = {
  laptops: "Laptops",
  laptop: "Laptops",
  phones: "Smartphones",
  smartphones: "Smartphones",
  smartphone: "Smartphones",
  monitors: "Monitors & Displays",
  monitor: "Monitors & Displays",
  audio: "Audio & Headphones",
  headphones: "Audio & Headphones",
  keyboards: "Keyboards & Accessories",
  keyboard: "Keyboards & Accessories",
  accessories: "Computer Accessories",
  computer_accessory: "Keyboards & Accessories",
  cameras: "Cameras & Optics",
  camera: "Cameras & Optics",
  appliances: "Appliances & Smart Home",
  appliance: "Appliances",
  electronics: "Home Electronics",
  home_electronics: "Home Electronics",
  phone_accessory: "Phone Accessories",
  phone_accessories: "Phone Accessories",
};

export function categoryIdForSlug(slug: string): string | null {
  return CATEGORY_SLUG_TO_ID[slug.toLowerCase()] ?? null;
}

export function categoryTitleForSlug(slug: string): string {
  return CATEGORY_SLUG_TITLE[slug.toLowerCase()] ?? slug;
}

const KNOWN_BRANDS = [
  "Apple", "Dell", "Lenovo", "HP", "ASUS", "Acer", "MSI", "Samsung", "Sony",
  "Keychron", "Logitech", "Google", "Nothing", "OnePlus", "Xiaomi", "Redmi",
  "LG", "BenQ", "Bose", "Sennheiser", "NuPhy", "Chuwi", "Infinix", "boAt",
  "Noise", "Realme", "Motorola", "Whirlpool", "Bosch", "Philips", "Panasonic",
  "Corsair", "Razer", "SteelSeries", "HyperX", "Anker", "Belkin", "TP-Link",
  "SanDisk", "Crucial", "Western Digital", "Seagate", "Kingston", "Intel", "AMD", "NVIDIA"
];

const EXCLUDED_BRAND_TOKENS = new Set([
  "the", "a", "an", "new", "pro", "ultra", "mini", "pack", "set", "lot",
  "wireless", "wired", "portable", "premium", "smart", "super", "digital",
  "usb", "hdmi", "cable", "adapter", "case", "cover", "sleeve", "bag",
  "laptop", "laptops", "phone", "phones", "smartphone", "smartphones",
  "monitor", "monitors", "audio", "headphones", "accessory", "accessories",
  "computer_accessory", "appliance", "appliances", "electronics", "home_electronics"
]);

/**
 * Resolves an accurate, human-readable brand/company name.
 * 1. Checks specs for non-generic brand/manufacturer.
 * 2. If missing or generic, extracts known brand from title.
 * 3. Never returns raw DB category slugs like "laptop" or "computer_accessory".
 */
export function resolveBrand(
  specs?: Record<string, unknown> | null,
  title?: string | null,
  category?: string | null
): string {
  if (specs && typeof specs === "object") {
    const rawBrand = specs.brand || specs.Brand || specs.manufacturer || specs.Manufacturer;
    if (typeof rawBrand === "string" && rawBrand.trim().length > 0) {
      const trimmed = rawBrand.trim();
      const lower = trimmed.toLowerCase();
      if (lower !== "generic" && !lower.includes("unknown") && !EXCLUDED_BRAND_TOKENS.has(lower)) {
        return trimmed;
      }
    }
  }

  if (title && typeof title === "string") {
    const cleanTitle = title.trim();

    for (const b of KNOWN_BRANDS) {
      const regex = new RegExp(`(^|[\\s(—–-])${b}([\\s)™®—–-]|$)`, "i");
      if (regex.test(cleanTitle)) {
        return b;
      }
    }

    const firstWordMatch = cleanTitle.match(/^([A-Za-z0-9&'+.-]{2,15})\b/);
    if (firstWordMatch) {
      const candidate = firstWordMatch[1];
      if (!EXCLUDED_BRAND_TOKENS.has(candidate.toLowerCase())) {
        return candidate;
      }
    }
  }

  if (category && typeof category === "string") {
    const titleFromSlug = categoryTitleForSlug(category);
    if (titleFromSlug && !EXCLUDED_BRAND_TOKENS.has(titleFromSlug.toLowerCase())) {
      return titleFromSlug;
    }
  }

  return "Verified Brand";
}

// ---------------------------------------------------------------------------
// Specifications
// ---------------------------------------------------------------------------

/** A specification row ready to render: a label and an already-formatted value. */
export interface SpecRow {
  key: string;
  label: string;
  value: string;
}

const SPEC_LABELS: Record<string, string> = {
  brand: "Brand",
  model_number: "Model",
  color: "Color",
  memory_gb: "Memory",
  storage_gb: "Storage",
  weight_grams: "Weight",
  dimensions_mm: "Dimensions",
  length_mm: "Length",
  width_mm: "Width",
  height_mm: "Height",
};

const SPEC_UNITS: Record<string, string> = {
  memory_gb: "GB",
  storage_gb: "GB",
  weight_grams: "g",
  length_mm: "mm",
  width_mm: "mm",
  height_mm: "mm",
};

function titleCase(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

/**
 * Turn a raw specifications object into rows.
 *
 * Expands dimensions objects, flags, brands, and hardware specs into clean
 * user-facing labels and values.
 */
export function specRows(specs: Record<string, unknown> | null | undefined): SpecRow[] {
  if (!specs) return [];
  const rows: SpecRow[] = [];
  Object.keys(specs).forEach((key) => {
    const raw = specs[key];
    if (raw === null || raw === undefined || raw === "") return;
    let value = "";
    if (key === "dimensions_mm" && typeof raw === "object" && raw !== null) {
      const dim = raw as Record<string, number>;
      const l = dim.length_mm;
      const w = dim.width_mm;
      const h = dim.height_mm;
      if (l != null && w != null && h != null) {
        rows.push({
          key: "dimensions_mm",
          label: "Dimensions",
          value: `${Math.round(l)} × ${Math.round(w)} × ${Math.round(h)} mm`,
        });
        return;
      }
    } else if (key === "flags" && typeof raw === "object" && raw !== null) {
      const flags = raw as Record<string, boolean>;
      Object.entries(flags).forEach(([fKey, fVal]) => {
        if (typeof fVal === "boolean") {
          rows.push({
            key: `flag_${fKey}`,
            label: titleCase(fKey),
            value: fVal ? "Yes" : "No",
          });
        }
      });
      return;
    } else if (typeof raw === "number") {
      const unit = SPEC_UNITS[key];
      value = unit ? `${new Intl.NumberFormat("en-IN").format(raw)} ${unit}` : String(raw);
    } else if (typeof raw === "boolean") {
      value = raw ? "Yes" : "No";
    } else if (typeof raw === "string") {
      value = raw;
    } else if (Array.isArray(raw)) {
      value = raw.map((entry) => String(entry)).join(", ");
    } else {
      return;
    }
    rows.push({ key, label: SPEC_LABELS[key] ?? titleCase(key), value });
  });
  return rows;
}

/**
 * A one-line specification summary for a card, built from the most descriptive keys.
 */
export function specSummary(specs: Record<string, unknown> | null | undefined): string {
  const rows = specRows(specs);
  const priorityKeys = ["brand", "memory_gb", "storage_gb", "color", "dimensions_mm", "weight_grams"];
  const summaryRows = priorityKeys
    .map((k) => rows.find((r) => r.key === k))
    .filter((r): r is SpecRow => r != null);
  
  const chosen = summaryRows.length > 0 ? summaryRows.slice(0, 3) : rows.slice(0, 3);
  return chosen.map((row) => `${row.label}: ${row.value}`).join(" · ");
}

// ---------------------------------------------------------------------------
// Text normalisation
// ---------------------------------------------------------------------------

/**
 * The product description as paragraphs.
 *
 * The column holds whatever the importer wrote: the seed artifacts write a list
 * of strings, another writer may hold one string. Both are accepted; anything
 * else yields no paragraphs and the screen says the record carries no
 * description.
 */
export function descriptionParagraphs(description: unknown): string[] {
  if (typeof description === "string") {
    const trimmed = description.trim();
    return trimmed ? [trimmed] : [];
  }
  if (Array.isArray(description)) {
    return description
      .filter((entry): entry is string => typeof entry === "string")
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
  }
  return [];
}

/** Usable image URLs from a product record, in the order the catalog gave them. */
export function productImageUrls(product: CatalogProduct | null | undefined): string[] {
  if (!product || !Array.isArray(product.images)) return [];
  const urls: string[] = [];
  product.images.forEach((image) => {
    const url = image?.source_url || (image as any)?.url;
    if (typeof url === "string" && url.trim()) urls.push(url.trim());
  });
  return urls;
}

/** Whether an offer expiry has already passed, judged against a supplied clock. */
export function isExpired(expiresAt: string | null | undefined, nowMs: number): boolean {
  if (!expiresAt) return false;
  const parsed = Date.parse(expiresAt);
  return Number.isNaN(parsed) ? false : parsed <= nowMs;
}

/** A date-time as plain readable text, or null when it cannot be parsed. */
export function readableInstant(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return null;
  return new Date(parsed).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Stock wording that never overstates: the figure is the offer's own. */
export function stockLabel(available: number): string {
  if (available <= 0) return "Out of stock";
  if (available === 1) return "1 unit left";
  return `${available} in stock`;
}

/** A short, honest label for an explore offer with no image. */
export const MISSING_IMAGE_NOTE = "No catalog image";

export function offerImageUrl(offer: ExploreOffer | null | undefined): string | null {
  const url = offer?.image_url;
  return typeof url === "string" && url.trim() ? url.trim() : null;
}
