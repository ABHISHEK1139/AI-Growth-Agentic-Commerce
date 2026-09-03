# AgentPay Hackathon Live Demo Script (Task 45 & Track 01 Showcase)

## 1. Zero-Credential Boot
```bash
docker compose up -d --build
curl http://localhost:8000/health
```

## 2. Machine-Readable Discovery & MCP Endpoints
```bash
# Public Agent Capability
curl http://localhost:8000/.well-known/agent-capability.json

# Public Catalog Search
curl -X POST http://localhost:8000/api/explore \
  -H "Content-Type: application/json" \
  -d '{"prompt": "I need a laptop for programming under 70000 with 16GB RAM"}'
```

## 3. Independent External Buyer Scenario
```bash
python -m pytest tests/contract/test_external_buyer_contract.py -v
```

## 4. Full Deterministic Suite Verification (1,114 Tests)
```bash
python -m pytest tests/unit/ tests/contract/ tests/security/ tests/evaluation/ tests/integration/test_track1_agentic_commerce_20_scenarios.py -q
```

## 5. Live Frontend Walkthrough (`http://localhost:3001`)

### A. Shopper Storefront & Contextual AI Drawer
1. Open `http://localhost:3001` — Browse categories, hero deals, and product cards.
2. Click **"Ask AI"** or type in search: *"laptop under 70000 with 16GB RAM"*.
3. Test Multi-Turn Conversational Refinements:
   - *"Only Lenovo"* $\rightarrow$ Catalog automatically filters to Lenovo laptops.
   - *"Actually I need good battery life too"* $\rightarrow$ Shrinks results and adds battery priority tag.
   - *"Show me the cheapest"* $\rightarrow$ Changes sort order to Price: Low to High.
   - *"Which is best?"* $\rightarrow$ Highlights top recommended laptop with detailed justification checklist.
   - *"Forget the Lenovo requirement"* $\rightarrow$ Removes brand filter while preserving budget and RAM constraints.

### B. Product Detail & Review Intelligence (`/product/prd_seed_lap_02`)
1. View zoomable image gallery and lightbox modal.
2. Inspect grouped specs: Performance, Display, Connectivity, Battery.
3. Review Intelligence: 4-category sentiment breakdown (Performance 94%, Battery 89%, Build 96%, Value 96%) + 5-filter review explorer.
4. Product Q&A: Inquires about specs with citations.

### C. 4-Step Gated Checkout & Razorpay Standard Web Checkout (`/checkout`)
1. **Step 1**: Delivery address selection.
2. **Step 2**: Review items & server-side price freeze (`SHA-256 price_hash`).
3. **Step 3**: Deterministic policy authorization gate (evaluated without LLM financial decisions).
4. **Step 4**: Razorpay Standard Web Modal with real test credentials (`rzp_test_TSUsmmMiKz8pjm`).
5. **Failure Demonstrators**: Interactive switches for `PRICE_CHANGED`, `PAYMENT_UNCERTAIN`, and `POLICY_BLOCKED`.

### D. Revenue Growth: Contextual Cross-Sell (`/cart` & `/api/v1/recommendations/cross-sell`)
1. Add laptop to cart $\rightarrow$ Automatic companion accessory recommendations (Type-C 7-in-1 Hub, Mouse, Sleeve).
2. Live AOV expansion projection (+2.15% AOV growth, 42.5% attach rate).

### E. Merchant Campaign Orchestrator (`/merchant/campaigns`)
1. Open `http://localhost:3001/merchant/campaigns`.
2. Input goal: *"Increase sales of slow-moving headphones this weekend without discounting more than 10%"*.
3. Click **"Generate AI Campaign Proposal"**.
4. Review deterministic safety checks:
   - `[✓ Product Active]` `[✓ Stock Level (24 units > 3)]` `[✓ Discount 10.0% ≤ Ceiling]` `[✓ Margin Floor 32.5% ≥ 15%]` `[✓ Duration 3 Days]` `[✓ No Conflicts]`.
5. Click **"Approve & Launch Campaign"** $\rightarrow$ Activates campaign into live production with real-time sales lift & ROI tracking.
