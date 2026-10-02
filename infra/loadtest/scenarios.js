/**
 * AgentPay load-test scenarios.
 *
 * WHAT THIS IS FOR
 * ----------------
 * `docs/production/READINESS_PLAN.md` claims a capacity model for 60k customers. That
 * claim is an estimate derived from traffic assumptions, and §4 of that document says
 * so. This script exists to turn it into a measurement.
 *
 * It measures the read paths that dominate real traffic: an agent browsing the
 * catalogue, a storefront rendering product pages, and the console reading its audit
 * ledger. It deliberately does NOT drive payment capture, because a load test that
 * creates 60k real payments against a live provider is a different and much more
 * expensive exercise with a different risk profile.
 *
 * THE WEBHOOK PATH IS MEASURED SEPARATELY, AND BY DESIGN
 * -----------------------------------------------------
 * The webhook replay check is the hottest correctness path in the system, and it is a
 * *write* path driven by the payment provider rather than by user traffic. Replaying
 * real signed webhooks at rate here would need a valid HMAC signature and would mutate
 * provider_event, so it is measured with `EXPLAIN (ANALYZE, BUFFERS)` in
 * `docs/production/READINESS_PLAN.md` §2.2 instead. That number is a real measurement,
 * not an estimate: 31.3ms sequential scan vs 0.165ms index lookup at 60k rows.
 *
 * WHY THE OFFER STRIDE IS 200
 * ---------------------------
 * seed.sql assigns offers to merchants with `1 + (g % 200)`, so one merchant owns every
 * 200th offer. Reading that stride means every request resolves to a row this session
 * actually owns. Reading consecutive ids would return OFFER_NOT_FOUND for 199 of every
 * 200 requests, and the harness would report a very fast, entirely fictional service.
 *
 * THRESHOLDS
 * ----------
 * Set from the API image's actual configuration rather than from aspiration:
 * two uvicorn workers behind one container. `p(95)` is asserted rather than merely
 * reported, because a load test nobody fails is a load test nobody reads. A breach
 * prints the breach; it does not silently produce a green run.
 */

import http from 'k6/http';
import { check } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

// Purpose-built so a slow endpoint is attributable rather than averaged into one
// meaningless "API" number.
const offerLatency = new Trend('catalog_offer_ms', true);
const productLatency = new Trend('catalog_product_ms', true);
const auditLatency = new Trend('audit_events_ms', true);
const capabilityLatency = new Trend('capability_ms', true);
const errors = new Counter('unexpected_status');
const notFound = new Rate('not_found_rate');

const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8000';
const SESSION_TOKEN = __ENV.SESSION_TOKEN || '';

// Read from a single merchant's catalogue, on the stride that merchant owns.
const MERCHANT_OFFER_BASE = Number(__ENV.MERCHANT_OFFER_BASE || 1);
const OFFER_STRIDE = Number(__ENV.OFFER_STRIDE || 200);
const MERCHANT_PRODUCT_BASE = Number(__ENV.MERCHANT_PRODUCT_BASE || 1);

// How many rows this merchant owns -- NOT a row count, but the number of *strides*.
//
// seed.sql creates 20,000 offers spread across 200 merchants, so each merchant owns
// 20000/200 = 100 of them. Reads are `base + n * stride` for n in [0, MERCHANT_LIMIT),
// so the largest id reachable is `base + 99 * 200 = 19801` -- inside the seeded range.
//
// This was originally 10,000, which produced ids up to ~2,000,000 against 20,000
// seeded rows: a 75% 404 rate. Latency still looked excellent, because a 404 is fast.
// The `not_found_rate` threshold below is the only reason that was caught rather than
// reported as a healthy service.
const MERCHANT_LIMIT = Number(__ENV.MERCHANT_LIMIT || 100);

const params = {
  headers: {
    'Content-Type': 'application/json',
    Cookie: `agentpay_session=${SESSION_TOKEN}`,
  },
};

function pick(min, max) {
  return Math.floor(Math.random() * (max - min)) + min;
}

function record(name, trend, response) {
  trend.add(response.timings.duration);
  if (response.status !== 200) {
    if (response.status === 404) {
      notFound.add(true);
    } else {
      errors.add(1);
    }
  } else {
    notFound.add(false);
  }
}

/**
 * Catalogue offer read.
 *
 * The single most frequent read in the system: an agent evaluating a product fetches
 * its priced offer, and a storefront page renders one per listing row. It is the read
 * that `ix_offer_merchant_status` was added for.
 */
export function readOffer() {
  const n = pick(0, MERCHANT_LIMIT);
  const offerId = `ld_offer_${MERCHANT_OFFER_BASE + n * OFFER_STRIDE}`;
  const response = http.get(`${BASE_URL}/api/v1/catalog/offers/${offerId}`, params);
  record('offer', offerLatency, response);
  check(response, {
    'offer returns 200': (r) => r.status === 200,
  });
}

/**
 * Catalogue product read.
 *
 * Heavier than the offer read: it joins product, images and specifications, so it is
 * the better single indicator of catalogue read cost.
 */
export function readProduct() {
  const n = pick(0, MERCHANT_LIMIT);
  const productId = `ld_product_${MERCHANT_PRODUCT_BASE + n * OFFER_STRIDE}`;
  const response = http.get(`${BASE_URL}/api/v1/catalog/products/${productId}`, params);
  record('product', productLatency, response);
  check(response, {
    'product returns 200': (r) => r.status === 200,
  });
}

/**
 * Merchant console audit ledger.
 *
 * Support engineers run this constantly, so its latency matters even at low volume. It
 * reads `audit_event` filtered by merchant and ordered by created_at -- the query
 * `ix_audit_event_merchant_created` was added for, over 400k rows.
 */
export function readAuditLedger() {
  const response = http.get(`${BASE_URL}/api/v1/audit/events?limit=50`, params);
  record('audit', auditLatency, response);
  check(response, {
    'audit returns 200': (r) => r.status === 200,
  });
}

/**
 * Capability document.
 *
 * Every agent fetches this once per session before doing anything else, so it is
 * effectively a startup request times the agent count. Small and cacheable, which is
 * exactly why it is the strongest argument for the read cache that phase 2 still owes.
 */
export function readCapability() {
  const response = http.get(`${BASE_URL}/api/v1/capability`, params);
  record('capability', capabilityLatency, response);
  check(response, {
    'capability returns 200': (r) => r.status === 200,
  });
}

/** Setup: fail fast and loudly if the fixture is not actually in place. */
export function setup() {
  const probe = http.get(`${BASE_URL}/api/v1/auth/me`, params);
  if (probe.status !== 200) {
    throw new Error(
      `setup: /api/v1/auth/me returned ${probe.status}. The session token is wrong or ` +
        'the API is unreachable. A run without a valid session measures 401s.',
    );
  }
  const offer = http.get(
    `${BASE_URL}/api/v1/catalog/offers/ld_offer_${MERCHANT_OFFER_BASE}`,
    params,
  );
  if (offer.status !== 200) {
    throw new Error(
      `setup: seeded offer ld_offer_${MERCHANT_OFFER_BASE} returned ${offer.status}. ` +
        'Has infra/loadtest/seed.sql been applied, and does MERCHANT_OFFER_BASE match ' +
        'the merchant this session belongs to?',
    );
  }
  console.log(`setup ok against ${BASE_URL}`);
  return { ok: true };
}

/**
 * The workload.
 *
 * One entry point dispatching to the reads by weight, rather than four concurrent
 * scenarios. That is deliberate and it is the difference between a realistic number and
 * a misleading one: real traffic is a *mixture*, and a mixture shares connection-pool
 * slots, threadpool workers and database connections between endpoints. Four separate
 * scenarios would each look healthy in isolation while never competing for the same
 * resources they would actually contend for in production.
 *
 * Weights approximate §4 of the readiness plan: agent catalogue browsing dominates,
 * the capability document is fetched once per session, and console audit reads are
 * comparatively rare but latency-sensitive because a human is waiting on them.
 */
const OPERATIONS = [
  { fn: readOffer, weight: 45 },
  { fn: readProduct, weight: 35 },
  { fn: readCapability, weight: 15 },
  { fn: readAuditLedger, weight: 5 },
];

const WEIGHT_TOTAL = OPERATIONS.reduce((sum, op) => sum + op.weight, 0);

export default function () {
  let cursor = Math.random() * WEIGHT_TOTAL;
  for (const op of OPERATIONS) {
    cursor -= op.weight;
    if (cursor <= 0) {
      op.fn();
      return;
    }
  }
  readOffer();
}

/**
 * Thresholds.
 *
 * `not_found_rate` is the important one. A catalogue read that 404s is fast, so a
 * harness with a fixture bug will look *excellent* on latency while measuring nothing.
 * Requiring zero 404s makes that failure impossible to mistake for a good result.
 */
export const options = {
  scenarios: {
    mixed_reads: {
      // constant-arrival-rate, not ramping-arrival-rate, and the reason matters.
      //
      // k6 reports `http_reqs` as an average over the *whole* run, so a test with ramp
      // stages reports a number dragged down by the ramp and cannot be compared to the
      // rate it was asked for. Reading 47 req/s out of a run that targeted 100 was not
      // a capacity finding -- it was the average of 20/s, then 100/s, then 0/s. A
      // constant rate makes "offered" and "achieved" directly comparable, which is the
      // only comparison that locates a ceiling.
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.PEAK_RATE || 50),
      timeUnit: '1s',
      duration: __ENV.HOLD_SECONDS || '30s',
      preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 100),
      maxVUs: Number(__ENV.MAX_VUS || 400),
      // Grace period beyond the expected request duration before an iteration is
      // dropped as failed. k6's default 30s silently reclassifies slow requests as
      // errors, which at saturation shows up as an error rate that is really a latency
      // measurement.
      gracefulStop: '10s',
    },
  },
  thresholds: {
    // 800ms is the figure already committed to in the readiness plan's acceptance
    // criteria, so this run either supports that number or contradicts it.
    'http_req_duration{expected_response:true}': ['p(95)<800'],
    'http_req_failed': ['rate<0.01'],
    // The guard against a fast, fictional service.
    not_found_rate: ['rate==0'],
    checks: ['rate>0.99'],
    catalog_offer_ms: ['p(95)<400'],
    audit_events_ms: ['p(95)<600'],
  },
};
