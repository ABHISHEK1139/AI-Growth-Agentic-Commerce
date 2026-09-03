# Seed catalog artifacts

Two JSONL files in exactly the shape `CatalogService.import_catalog_artifacts`
consumes, so one dataset serves both search paths:

- **PostgreSQL path** — `python -m apps.worker.seed_catalog` imports and publishes
  these files, after which `/api/explore` answers from SQL.
- **Offline path** — `services.offers.seed` reads the same two files directly and
  applies the Python evaluator in `services.offers.constraints`. This is what
  answers when the database is unreachable, and the endpoint says so in
  `catalog_source`.

They are the same records either way. There is no second hardcoded catalog.

## Why these rows

Every laptop row exists to make a filter observable. Seven of the sixteen offers
are there to be *excluded*, so a test that a filter narrows the result set fails
when the filter is dropped:

| Offer | Excluded by |
|---|---|
| `off_seed_lap_04` | `min_memory_gb` — 8GB |
| `off_seed_lap_05` | `max_price_minor` — priced above ₹70,000 |
| `off_seed_lap_06` | `max_delivery_days` — nine days |
| `off_seed_lap_07` | baseline stock check — zero available |
| `off_seed_lap_08` | baseline expiry check — lapsed in 2020 |
| `off_seed_lap_09` | `min_memory_gb` — memory specification absent |
| `off_seed_lap_10` | `min_storage_gb` at 512, and `quantity` above 1 |

## Prices

Hand-set for the demo, in integer paise, and labelled
`pricing_source: merchant_configured` on every row. They are not scraped and not
sampled from a band. Nothing here is a market price.

## Expiry

Active offers expire 2035-06-30 so the demo keeps working; `off_seed_lap_08`
carries a past date on purpose.
