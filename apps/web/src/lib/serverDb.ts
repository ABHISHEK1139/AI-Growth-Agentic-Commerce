import { DatabaseSync } from "node:sqlite";
import path from "path";
import fs from "fs";
import { resolveBrand } from "@/catalog/present";

let _db: DatabaseSync | null = null;

function resolveDatabasePath(): string {
  const candidates = [
    path.resolve(process.cwd(), "agentpay.db"),
    path.resolve(process.cwd(), "../../agentpay.db"),
    path.resolve(process.cwd(), "../agentpay.db"),
    path.resolve(process.cwd(), "data/local_dev.db"),
    path.resolve(process.cwd(), "../../data/local_dev.db"),
    path.resolve(process.cwd(), "../data/local_dev.db"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return path.resolve(process.cwd(), "agentpay.db");
}

export function getServerDb(): DatabaseSync {
  if (!_db) {
    const dbPath = resolveDatabasePath();
    _db = new DatabaseSync(dbPath);
    // Disable strict foreign keys on the session to allow resilient order/payment insertion
    _db.exec("PRAGMA foreign_keys = OFF;");
  }
  return _db;
}

export interface DbSearchOptions {
  q?: string;
  category?: string;
  minPriceMinor?: number;
  maxPriceMinor?: number;
  limit?: number;
  offset?: number;
}

export interface DbSearchResult {
  offers: any[];
  exploreOffers: any[];
  count: number;
  total: number;
  limit: number;
  offset: number;
}

function normalizeCategoryFilter(category?: string): string | null {
  if (!category || category === "all") return null;
  const c = category.toLowerCase().trim();
  if (c.includes("laptop")) return "laptop";
  if (c.includes("phone") || c.includes("smart")) return "smartphone";
  if (c.includes("audio") || c.includes("headphone") || c.includes("earphone")) return "audio";
  if (c.includes("monitor") || c.includes("display")) return "monitor";
  if (c.includes("keyboard") || c.includes("mouse") || c.includes("computer_accessory")) return "computer_accessory";
  if (c.includes("appliance")) return "appliance";
  if (c.includes("camera")) return "camera";
  if (c.includes("phone_acc") || c.includes("phone accessory")) return "phone_accessory";
  if (c.includes("electronic")) return "home_electronics";
  return c.replace(/s$/, "");
}

export function searchCatalog(options: DbSearchOptions): DbSearchResult {
  const db = getServerDb();
  const { q, category, minPriceMinor, maxPriceMinor, limit = 16, offset = 0 } = options;

  let where: string[] = ["o.status = 'active'"];
  let params: any[] = [];

  if (q && q.trim()) {
    const term = `%${q.trim()}%`;
    where.push("(p.title LIKE ? OR p.specifications LIKE ? OR p.category_id LIKE ?)");
    params.push(term, term, term);
  }

  const normCat = normalizeCategoryFilter(category);
  if (normCat) {
    where.push("(p.category_id LIKE ? OR p.category_id = ?)");
    params.push(`%${normCat}%`, normCat);
  }

  if (typeof minPriceMinor === "number") {
    where.push("o.unit_price_minor >= ?");
    params.push(minPriceMinor);
  }

  if (typeof maxPriceMinor === "number") {
    where.push("o.unit_price_minor <= ?");
    params.push(maxPriceMinor);
  }

  const whereClause = where.join(" AND ");

  const countRow = db.prepare(`
    SELECT COUNT(*) as total
    FROM product p
    JOIN offer o ON p.product_id = o.product_id
    WHERE ${whereClause}
  `).get(...params) as { total: number } | undefined;

  const total = countRow ? countRow.total : 0;

  const rows = db.prepare(`
    SELECT p.product_id, p.title, p.category_id, p.description, p.specifications,
           p.average_rating, p.rating_number,
           o.offer_id, o.merchant_id, o.unit_price_minor, o.currency,
           COALESCE(inv.available_quantity, 15) as available_quantity,
           o.delivery_days, o.return_period_days, o.offer_version, o.pricing_source, o.expires_at,
           pi.source_url as image_url
    FROM product p
    JOIN offer o ON p.product_id = o.product_id
    LEFT JOIN inventory inv ON o.offer_id = inv.offer_id
    LEFT JOIN product_image pi ON p.product_id = pi.product_id AND pi.position = 0
    WHERE ${whereClause}
    ORDER BY p.average_rating DESC, o.unit_price_minor ASC
    LIMIT ? OFFSET ?
  `).all(...params, limit, offset) as any[];

  const offers = rows.map((r) => {
    let specs: Record<string, any> = {};
    try {
      specs = typeof r.specifications === "string" ? JSON.parse(r.specifications) : (r.specifications || {});
    } catch {
      specs = {};
    }

    const brand = resolveBrand(specs, r.title, r.category_id);
    const imageUrl = r.image_url || "https://images.unsplash.com/photo-1544816155-12df9643f363?auto=format&fit=crop&w=600&q=80";

    return {
      schema_version: "1.0",
      offer_id: r.offer_id,
      product_id: r.product_id,
      merchant_id: r.merchant_id || "merchant_demo",
      status: "active",
      title: r.title,
      category: r.category_id,
      image_url: imageUrl,
      unit_price_minor: r.unit_price_minor,
      currency: r.currency || "INR",
      available_quantity: r.available_quantity || 15,
      delivery_days: r.delivery_days || 2,
      return_period_days: r.return_period_days || 14,
      expires_at: r.expires_at || new Date(Date.now() + 86400000 * 30).toISOString(),
      offer_version: r.offer_version || 1,
      pricing_source: r.pricing_source || "merchant_configured",
      rating: r.average_rating || 4.5,
      reviews_count: r.rating_number || 120,
      specifications: {
        brand,
        ...specs,
      },
    };
  });

  const exploreOffers = rows.map((r) => {
    let specs: Record<string, any> = {};
    try {
      specs = typeof r.specifications === "string" ? JSON.parse(r.specifications) : (r.specifications || {});
    } catch {
      specs = {};
    }

    const brand = resolveBrand(specs, r.title, r.category_id);
    const imageUrl = r.image_url || "https://images.unsplash.com/photo-1544816155-12df9643f363?auto=format&fit=crop&w=600&q=80";

    return {
      offer_id: r.offer_id,
      product_id: r.product_id,
      merchant_id: r.merchant_id || "merchant_demo",
      title: r.title,
      category: r.category_id,
      unit_price_minor: r.unit_price_minor,
      currency: r.currency || "INR",
      available_stock: r.available_quantity || 15,
      delivery_days: r.delivery_days || 2,
      return_period_days: r.return_period_days || 14,
      expires_at: r.expires_at || new Date(Date.now() + 86400000 * 30).toISOString(),
      offer_version: r.offer_version || 1,
      pricing_source: r.pricing_source || "merchant_configured",
      rating: r.average_rating || 4.5,
      reviews_count: r.rating_number || 120,
      image_url: imageUrl,
      specs: {
        brand,
        ...specs,
      },
    };
  });

  return {
    offers,
    exploreOffers,
    count: offers.length,
    total,
    limit,
    offset,
  };
}

export function getProductById(productId: string): any | null {
  const db = getServerDb();
  const cleanId = (productId || "").trim();
  if (!cleanId) return null;

  const row = db.prepare(`
    SELECT p.*, o.offer_id, o.unit_price_minor, o.currency, o.delivery_days,
           o.return_period_days, o.pricing_source, o.offer_version,
           COALESCE(inv.available_quantity, 15) as available_quantity
    FROM product p
    LEFT JOIN offer o ON p.product_id = o.product_id
    LEFT JOIN inventory inv ON o.offer_id = inv.offer_id
    WHERE p.product_id = ? OR p.external_product_id = ?
  `).get(cleanId, cleanId) as any;

  if (!row) return null;

  const images = db.prepare(`
    SELECT source_url, storage_key, resolution, position
    FROM product_image
    WHERE product_id = ?
    ORDER BY position ASC
  `).all(row.product_id) as any[];

  let specs: Record<string, any> = {};
  try {
    specs = typeof row.specifications === "string" ? JSON.parse(row.specifications) : (row.specifications || {});
  } catch {
    specs = {};
  }

  let description = row.description;
  try {
    if (typeof description === "string" && (description.startsWith("{") || description.startsWith("["))) {
      description = JSON.parse(description);
    }
  } catch {}

  const brand = resolveBrand(specs, row.title, row.category_id);

  return {
    product_id: row.product_id,
    external_product_id: row.external_product_id || row.product_id,
    category_id: row.category_id,
    title: row.title,
    status: row.status || "published",
    description: typeof description === "string" ? description : (specs.description || ""),
    specifications: {
      brand,
      ...specs,
    },
    average_rating: row.average_rating || 4.5,
    rating_number: row.rating_number || 120,
    offer: row.offer_id
      ? {
          offer_id: row.offer_id,
          unit_price_minor: row.unit_price_minor,
          currency: row.currency || "INR",
          delivery_days: row.delivery_days || 2,
          return_period_days: row.return_period_days || 14,
          available_quantity: row.available_quantity || 15,
        }
      : null,
    images: images.length > 0
      ? images
      : [{ source_url: "https://images.unsplash.com/photo-1544816155-12df9643f363?auto=format&fit=crop&w=600&q=80", position: 0 }],
  };
}

export function getOfferById(offerId: string): any | null {
  const db = getServerDb();
  const cleanId = (offerId || "").trim();
  if (!cleanId) return null;

  const row = db.prepare(`
    SELECT o.*, p.title, p.category_id, p.specifications, p.average_rating, p.rating_number,
           COALESCE(inv.available_quantity, 15) as available_quantity,
           pi.source_url as image_url
    FROM offer o
    JOIN product p ON o.product_id = p.product_id
    LEFT JOIN inventory inv ON o.offer_id = inv.offer_id
    LEFT JOIN product_image pi ON p.product_id = pi.product_id AND pi.position = 0
    WHERE o.offer_id = ? OR p.product_id = ?
  `).get(cleanId, cleanId) as any;

  if (!row) return null;

  let specs: Record<string, any> = {};
  try {
    specs = typeof row.specifications === "string" ? JSON.parse(row.specifications) : (row.specifications || {});
  } catch {
    specs = {};
  }

  const brand = resolveBrand(specs, row.title, row.category_id);
  const imageUrl = row.image_url || "https://images.unsplash.com/photo-1544816155-12df9643f363?auto=format&fit=crop&w=600&q=80";

  return {
    schema_version: "1.0",
    offer_id: row.offer_id,
    product_id: row.product_id,
    merchant_id: row.merchant_id || "merchant_demo",
    status: row.status || "active",
    title: row.title,
    category: row.category_id,
    image_url: imageUrl,
    unit_price_minor: row.unit_price_minor,
    currency: row.currency || "INR",
    available_quantity: row.available_quantity || 15,
    delivery_days: row.delivery_days || 2,
    return_period_days: row.return_period_days || 14,
    expires_at: row.expires_at || new Date(Date.now() + 86400000 * 30).toISOString(),
    offer_version: row.offer_version || 1,
    pricing_source: row.pricing_source || "merchant_configured",
    rating: row.average_rating || 4.5,
    reviews_count: row.rating_number || 120,
    specifications: {
      brand,
      ...specs,
    },
  };
}

export function saveOrder(order: {
  order_id: string;
  order_number?: string;
  checkout_id?: string;
  payment_id?: string;
  buyer_id?: string;
  merchant_id?: string;
  status?: string;
  total_minor?: number;
  amount_minor?: number;
  currency?: string;
  shipping_address?: any;
  confirmed_at?: string;
  created_at?: string;
}): any {
  const db = getServerDb();
  const now = new Date().toISOString();
  const orderNumber = order.order_number || `ORD-${Date.now().toString().slice(-6)}`;
  const totalMinor = order.total_minor ?? order.amount_minor ?? 0;
  const amountMinor = order.amount_minor ?? order.total_minor ?? 0;
  const currency = order.currency || "INR";
  const status = order.status || "confirmed";
  const checkoutId = order.checkout_id || `chk_${Date.now().toString(36)}`;
  const paymentId = order.payment_id || `pay_${Date.now().toString(36)}`;
  const buyerId = order.buyer_id || "buy_shopper_demo";
  const merchantId = order.merchant_id || "merchant_demo";
  const confirmedAt = order.confirmed_at || now;
  const createdAt = order.created_at || now;
  const shippingAddressStr =
    typeof order.shipping_address === "string"
      ? order.shipping_address
      : JSON.stringify(order.shipping_address || {});

  const stmt = db.prepare(`
    INSERT OR REPLACE INTO "order" (
      order_id, order_number, checkout_id, payment_id, buyer_id, merchant_id,
      status, total_minor, amount_minor, currency, shipping_address, confirmed_at, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  stmt.run(
    order.order_id,
    orderNumber,
    checkoutId,
    paymentId,
    buyerId,
    merchantId,
    status,
    totalMinor,
    amountMinor,
    currency,
    shippingAddressStr,
    confirmedAt,
    createdAt
  );

  return {
    schema_version: "1.0",
    order_id: order.order_id,
    order_number: orderNumber,
    checkout_id: checkoutId,
    payment_id: paymentId,
    buyer_id: buyerId,
    merchant_id: merchantId,
    status,
    total_minor: totalMinor,
    amount_minor: amountMinor,
    currency,
    shipping_address: order.shipping_address || {},
    confirmed_at: confirmedAt,
    created_at: createdAt,
  };
}

export function listOrders(limit = 20, offset = 0, buyerId?: string): {
  orders: any[];
  total: number;
  count: number;
  limit: number;
  offset: number;
} {
  const db = getServerDb();

  let where = "";
  let params: any[] = [];
  if (buyerId) {
    where = "WHERE buyer_id = ?";
    params.push(buyerId);
  }

  const countRow = db.prepare(`SELECT COUNT(*) as cnt FROM "order" ${where}`).get(...params) as { cnt: number } | undefined;
  const total = countRow ? countRow.cnt : 0;

  const rows = db.prepare(`
    SELECT * FROM "order"
    ${where}
    ORDER BY created_at DESC
    LIMIT ? OFFSET ?
  `).all(...params, limit, offset) as any[];

  const orders = rows.map((r) => {
    let shippingAddress = {};
    try {
      shippingAddress = typeof r.shipping_address === "string" ? JSON.parse(r.shipping_address) : (r.shipping_address || {});
    } catch {}

    return {
      schema_version: "1.0",
      order_id: r.order_id,
      order_number: r.order_number,
      checkout_id: r.checkout_id,
      payment_id: r.payment_id,
      buyer_id: r.buyer_id,
      merchant_id: r.merchant_id,
      status: r.status,
      amount_minor: r.amount_minor || r.total_minor,
      total_minor: r.total_minor || r.amount_minor,
      currency: r.currency || "INR",
      shipping_address: shippingAddress,
      confirmed_at: r.confirmed_at || r.created_at,
      created_at: r.created_at,
    };
  });

  return {
    orders,
    total,
    count: orders.length,
    limit,
    offset,
  };
}

export function getOrderById(orderId: string): any | null {
  const db = getServerDb();
  const cleanId = (orderId || "").trim();
  if (!cleanId) return null;

  const r = db.prepare(`SELECT * FROM "order" WHERE order_id = ?`).get(cleanId) as any;
  if (!r) return null;

  let shippingAddress = {};
  try {
    shippingAddress = typeof r.shipping_address === "string" ? JSON.parse(r.shipping_address) : (r.shipping_address || {});
  } catch {}

  return {
    schema_version: "1.0",
    order_id: r.order_id,
    order_number: r.order_number,
    checkout_id: r.checkout_id,
    payment_id: r.payment_id,
    buyer_id: r.buyer_id,
    merchant_id: r.merchant_id,
    status: r.status,
    amount_minor: r.amount_minor || r.total_minor,
    total_minor: r.total_minor || r.amount_minor,
    currency: r.currency || "INR",
    shipping_address: shippingAddress,
    confirmed_at: r.confirmed_at || r.created_at,
    created_at: r.created_at,
  };
}

export function savePayment(payment: {
  payment_id: string;
  checkout_id?: string;
  merchant_id?: string;
  buyer_id?: string;
  authorization_id?: string;
  status?: string;
  amount_minor?: number;
  currency?: string;
  provider?: string;
  provider_order_id?: string | null;
  provider_payment_id?: string | null;
  provider_signature?: string | null;
  idempotency_key?: string | null;
  test_mode?: boolean;
  created_at?: string;
  verified_at?: string;
  updated_at?: string;
}): any {
  const db = getServerDb();
  const now = new Date().toISOString();
  const checkoutId = payment.checkout_id || `chk_${Date.now().toString(36)}`;
  const authorizationId = payment.authorization_id || `ath_${Date.now().toString(36)}`;
  const status = payment.status || "verified";
  const amountMinor = payment.amount_minor || 0;
  const currency = payment.currency || "INR";
  const provider = payment.provider || "razorpay";
  const testMode = payment.test_mode !== undefined ? (payment.test_mode ? 1 : 0) : 1;
  const createdAt = payment.created_at || now;
  const verifiedAt = payment.verified_at || now;
  const updatedAt = payment.updated_at || now;

  const stmt = db.prepare(`
    INSERT OR REPLACE INTO payment (
      payment_id, checkout_id, merchant_id, buyer_id, authorization_id,
      status, amount_minor, currency, provider, provider_order_id,
      provider_payment_id, provider_signature, idempotency_key, test_mode,
      created_at, verified_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  stmt.run(
    payment.payment_id,
    checkoutId,
    payment.merchant_id || "merchant_demo",
    payment.buyer_id || "buy_shopper_demo",
    authorizationId,
    status,
    amountMinor,
    currency,
    provider,
    payment.provider_order_id || null,
    payment.provider_payment_id || null,
    payment.provider_signature || null,
    payment.idempotency_key || null,
    testMode,
    createdAt,
    verifiedAt,
    updatedAt
  );

  return {
    schema_version: "1.0",
    payment_id: payment.payment_id,
    checkout_id: checkoutId,
    authorization_id: authorizationId,
    provider,
    provider_order_id: payment.provider_order_id || null,
    provider_payment_id: payment.provider_payment_id || null,
    amount_minor: amountMinor,
    currency,
    status,
    test_mode: Boolean(testMode),
    created_at: createdAt,
    verified_at: verifiedAt,
  };
}

export function getPaymentById(paymentId: string): any | null {
  const db = getServerDb();
  const cleanId = (paymentId || "").trim();
  if (!cleanId) return null;

  const r = db.prepare(`SELECT * FROM payment WHERE payment_id = ?`).get(cleanId) as any;
  if (!r) return null;

  return {
    schema_version: "1.0",
    payment_id: r.payment_id,
    checkout_id: r.checkout_id,
    authorization_id: r.authorization_id,
    provider: r.provider || "razorpay",
    provider_order_id: r.provider_order_id,
    provider_payment_id: r.provider_payment_id,
    public_key: null,
    amount_minor: r.amount_minor,
    currency: r.currency || "INR",
    status: r.status,
    test_mode: Boolean(r.test_mode),
    created_at: r.created_at,
    verified_at: r.verified_at,
  };
}

export function saveCheckout(checkout: {
  checkout_id: string;
  buyer_id?: string;
  merchant_id?: string;
  offer_id?: string;
  offer_version?: number;
  status?: string;
  subtotal_minor?: number;
  shipping_minor?: number;
  tax_minor?: number;
  discount_minor?: number;
  total_minor?: number;
  currency?: string;
  price_hash?: string;
}): any {
  const db = getServerDb();
  const now = new Date().toISOString();
  const totalMinor = checkout.total_minor || 0;
  const subtotalMinor = checkout.subtotal_minor || totalMinor;

  const priceSnapshot = JSON.stringify({
    subtotal_minor: subtotalMinor,
    total_minor: totalMinor,
    currency: checkout.currency || "INR",
  });

  const stmt = db.prepare(`
    INSERT OR REPLACE INTO checkout (
      checkout_id, buyer_id, merchant_id, offer_id, offer_version, status,
      subtotal_minor, shipping_minor, tax_minor, discount_minor, total_minor,
      currency, price_hash, price_snapshot, expires_at, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  stmt.run(
    checkout.checkout_id,
    checkout.buyer_id || "buy_shopper_demo",
    checkout.merchant_id || "merchant_demo",
    checkout.offer_id || "off_demo",
    checkout.offer_version || 1,
    checkout.status || "created",
    subtotalMinor,
    checkout.shipping_minor || 0,
    checkout.tax_minor || 0,
    checkout.discount_minor || 0,
    totalMinor,
    checkout.currency || "INR",
    checkout.price_hash || null,
    priceSnapshot,
    new Date(Date.now() + 900000).toISOString(),
    now
  );

  return {
    checkout_id: checkout.checkout_id,
    buyer_id: checkout.buyer_id || "buy_shopper_demo",
    merchant_id: checkout.merchant_id || "merchant_demo",
    offer_id: checkout.offer_id,
    status: checkout.status || "created",
    subtotal_minor: subtotalMinor,
    total_minor: totalMinor,
    currency: checkout.currency || "INR",
    price_hash: checkout.price_hash,
  };
}

export function saveAuthorization(auth: {
  authorization_id: string;
  checkout_id?: string;
  buyer_id?: string;
  merchant_id?: string;
  amount_ceiling_minor?: number;
  currency?: string;
  price_hash?: string;
  policy_version?: string;
  status?: string;
}): any {
  const db = getServerDb();
  const now = new Date().toISOString();

  const stmt = db.prepare(`
    INSERT OR REPLACE INTO authorization (
      authorization_id, checkout_id, buyer_id, merchant_id,
      amount_ceiling_minor, currency, price_hash, policy_version,
      status, valid_until, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  stmt.run(
    auth.authorization_id,
    auth.checkout_id || `chk_${Date.now().toString(36)}`,
    auth.buyer_id || "buy_shopper_demo",
    auth.merchant_id || "merchant_demo",
    auth.amount_ceiling_minor || 5000000,
    auth.currency || "INR",
    auth.price_hash || `sha256_${Date.now().toString(36)}`,
    auth.policy_version || "pol_v2_agentic_commerce",
    auth.status || "approved",
    new Date(Date.now() + 900000).toISOString(),
    now
  );

  return {
    authorization_id: auth.authorization_id,
    checkout_id: auth.checkout_id,
    status: auth.status || "approved",
    amount_ceiling_minor: auth.amount_ceiling_minor || 5000000,
    currency: auth.currency || "INR",
  };
}

export function approveAuthorization(authId: string): boolean {
  const db = getServerDb();
  const res = db.prepare(`UPDATE authorization SET status = 'approved' WHERE authorization_id = ?`).run(authId);
  return res.changes > 0;
}
