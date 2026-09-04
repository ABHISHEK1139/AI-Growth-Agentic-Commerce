# Amazon Appliances Complete Dataset

This directory contains a complete real-world Amazon e-commerce dataset for the **Appliances** category, bundled directly with the repository so that the catalog pipeline and data extraction stages can be run standalone without external downloads.

---

## 📦 Bundled Dataset Files

| File | Size | Records | Description |
| :--- | :--- | :--- | :--- |
| meta_Appliances.jsonl.gz | **63.3 MB** | **94,327** | Complete Amazon Appliances product catalog containing full titles, descriptions, technical specifications, brand metadata, prices, high-resolution image URLs, and category hierarchy. |
| Appliances.jsonl.gz | **63.4 MB** | **500,000** | Real customer reviews with 1-5 star ratings, review titles, user feedback text, verified purchase flags, and timestamps. |

---

## 🚀 Running the Pipeline

You can run the AgentPay catalog processing pipeline across this dataset with:

`ash
# Stage 1: Candidate extraction and normalization
python -m pipeline.build_catalog products

# Stage 2: Quota-based selection
python -m pipeline.build_catalog select

# Stage 3: Image resolution manifest
python -m pipeline.build_catalog images

# Run all stages
python -m pipeline.build_catalog all
`
