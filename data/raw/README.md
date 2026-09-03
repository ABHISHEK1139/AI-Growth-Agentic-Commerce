# Raw Dataset Files

This directory holds the raw `.jsonl.gz` source files that the catalog pipeline reads.
These files are never modified by the pipeline -- they are read in place via streaming.

## Required files

The pipeline expects six files:

| File | Description | Size |
|------|-------------|------|
| `meta_Electronics.jsonl.gz` | Electronics product metadata | ~2 GB |
| `meta_Cell_Phones_and_Accessories.jsonl.gz` | Phone product metadata | ~400 MB |
| `meta_Appliances.jsonl.gz` | Appliance product metadata | ~100 MB |
| `Electronics.jsonl.gz` | Electronics reviews | ~5 GB |
| `Cell_Phones_and_Accessories.jsonl.gz` | Phone reviews | ~1 GB |
| `Appliances.jsonl.gz` | Appliance reviews | ~200 MB |

## How to get the data

### Option A: Use the source files already in `datasets/`

The six `.jsonl.gz` files are kept in the repository's `datasets/` directory and
are read in place as compressed input. They are never decompressed to disk and
never modified, and `AGENTPAY_RAW_DIR` points at them.

There is deliberately no download step. The files are already present, the
upstream dataset is gated behind an account and a token, and a multi-gigabyte
fetch is not something a build should perform on anyone's behalf.

### Option B: Use synthetic sample data (recommended for development)

```bash
# Generate small deterministic fixtures (~80 records per file)
make sample-data

# Or generate and run the full pipeline in one step
make catalog-demo
```

The sample data generator (`python -m pipeline.sample_data`) creates files in the
same format as the real dataset, so the full pipeline runs end to end. This is the
recommended approach for local development and CI.

## Notes

- These files are excluded from git via `.gitignore`
- The pipeline never writes to this directory
- Set `AGENTPAY_RAW_DIR` in `.env` to point at a different location
- Use `MAX_LINES_DEBUG` to cap how many lines are read per file for fast validation runs
