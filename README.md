# 🛡️ AgentPay — Autonomous Agentic Commerce Gateway
### *Making Merchants Discoverable, Sellable, and Transactable to AI Buyers on Razorpay*

[![Track](https://img.shields.io/badge/Track-01%20AI%20Growth%20%26%20Agentic%20Commerce-blueviolet?style=for-the-badge)](https://github.com/ABHISHEK1139/AI-Growth-Agentic-Commerce)
[![Build](https://img.shields.io/badge/Next.js-14%20(App%20Router)-black?style=for-the-badge&logo=next.js)](https://nextjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Razorpay](https://img.shields.io/badge/Razorpay-Standard%20Checkout%20%26%20Links-0C2340?style=for-the-badge&logo=razorpay)](https://razorpay.com)
[![Tests](https://img.shields.io/badge/Tests-350%2B%20Passing%20(100%25)-success?style=for-the-badge&logo=pytest)](https://pytest.org)
[![Protocol](https://img.shields.io/badge/Protocol-NPCI%20UAP%20%2F%20ACP%20%2F%20AP2%20Ready-orange?style=for-the-badge)](#)

---

## 🛡️ Executive Summary & About AgentPay

**AgentPay is an autonomous agentic commerce gateway built on Razorpay that makes merchants discoverable, sellable, and transactable to AI-powered buyers.**

As AI agents evolve from systems that simply answer questions into systems that can **discover products, compare options, make decisions, and complete purchases**, traditional e-commerce infrastructure faces a major challenge: most online stores are designed for human shoppers, not autonomous AI buyers.

AgentPay addresses this gap by providing a **merchant-side AI commerce gateway** that connects intelligent agents with real commerce infrastructure while keeping every financial action strictly bounded and auditable.

The platform has two core objectives:

---

### 🤖 1. Make Commerce Accessible to AI Buyers

AgentPay allows external autonomous AI agents to interact with a merchant's catalog and commerce APIs programmatically.

It provides:

* **Machine-readable commerce discovery** through `/.well-known/agent-commerce` and `/.well-known/agent-capability.json`
* **Scoped authentication** for external AI agents using short-lived bearer tokens
* **Standardized APIs** for catalog discovery, checkout, authorization, and payment
* A standalone **autonomous buyer agent** capable of discovering products, querying offers, reserving inventory, creating checkout sessions, and executing transactions
* **Cryptographic authorization and payment controls** designed for machine-to-machine commerce

This transforms a traditional storefront from something an AI can merely read into infrastructure that an AI agent can actually **transact with**.

---

### 💰 2. Turn AI Into a Revenue Growth Engine

AgentPay is not limited to autonomous checkout. It also uses AI to help merchants increase revenue.

The platform includes:

#### Conversational Commerce
Customers can interact with an in-app AI shopping assistant using natural language to describe their requirements, budget, and intended use case. The system converts that intent into product-search strategies while financial actions remain controlled by deterministic backend services.

##### Customer-Facing AI Context & Prompt Architecture
* **Target Model Context**: `32K tokens` providing generous room for multi-turn shopping exploration without context overflows.
* **Conversation Budget**: `8K–12K tokens` with aggressive sliding-window trimming (works backwards from latest turns to preserve recent context while preventing token bloat).
* **System Prompt Budget**: `~1.5K–2K tokens` combining compact behavioral instructions with a ground-truth store catalog snapshot.
* **Assistant Response Target**: `~300–500 tokens` (`max_tokens: 500`), keeping replies conversational, direct, and under 150 words normally.
* **Deterministic Commerce Boundary**: The LLM interprets customer intent and recommends options; deterministic backend services remain authoritative over prices, stock availability, cart calculation, discount gating, and Razorpay payments.

#### AI Upsell & Cross-Sell
The recommendation engine analyzes product compatibility and identifies complementary products while verifying real inventory before making recommendations. For example, it can recommend compatible accessories for a laptop rather than simply suggesting unrelated products.

#### AI Campaign Orchestrator
Merchants can provide high-level business objectives such as *“Boost audio accessories velocity.”* The system analyzes inventory and proposes targeted promotional campaigns while enforcing deterministic business constraints such as stock availability, discount limits, and gross-margin requirements.

---

## 🔐 Security-First Agentic Commerce

The central design principle of AgentPay is:

> **The model is useful, but strictly bounded.**

The AI is responsible for understanding intent, reasoning about products, and deciding which commerce tools may be useful. It is **not trusted with unrestricted control over money, inventory, databases, or financial state**.

Instead, sensitive operations are enforced by deterministic services.

AgentPay uses:

* **GuardLLM prompt-safety filtering** to detect adversarial instructions and prompt-injection attempts
* **Strict tool boundaries** through a `CommerceFacade` abstraction
* **Integer minor-unit money calculations** to avoid floating-point financial errors
* **Hard transaction ceilings and auto-approval thresholds**
* **Mandatory authorization gates** for state-changing operations
* **SHA-256 price-hash freezing** to detect price changes during checkout
* **Atomic inventory reservations** with automatic timeout-based release
* **HMAC-SHA256 webhook verification** for payment callbacks
* **Immutable append-only audit logging**
* **Correlation IDs** such as `trace_id`, `request_id`, and `actor_id` for complete transaction tracing
* **Deterministic handling** of inventory races, price changes, authorization expiry, and forged payment events.

This creates a separation between **AI reasoning** and **financial authority**: the AI can propose actions, but deterministic policy and commerce services decide whether those actions are actually permitted.

---

## 🏗️ Technical Architecture

AgentPay is implemented as a **Modular Monolith using Clean Hexagonal Architecture and Domain-Driven Design (DDD)**.

A key architectural principle is that the AI layer has **no direct database access**. Instead, it communicates through a bounded `CommerceFacade` protocol, preventing the language model from directly interacting with persistence or internal business logic.

The major components include:

**Frontend**
* Next.js 14 (App Router)
* Shopper storefront
* AI shopping assistant
* Merchant campaign console
* Merchant policy manager
* Audit explorer
* Security / failure-injection console

**Backend**
* FastAPI modular monolith
* Authentication and authorization scopes
* Agent tool APIs
* Commerce orchestration
* Policy enforcement
* Payment integration

**Commerce Services**
* Checkout state machine
* Inventory reservation manager
* Razorpay payment gateway
* Recommendation engine
* Campaign orchestrator
* Immutable audit ledger

**AI Layer**
* GuardLLM prompt-safety layer
* Tool registry
* Bounded agent execution
* Product reasoning and recommendation logic

**External Agent**
* Standalone autonomous buyer client
* Capability discovery
* Scoped token authentication
* Product search
* Checkout creation
* Policy authorization
* Payment execution

The repository is structured around these independent responsibilities, including dedicated packages for commerce, money, security, schemas, agent execution, checkout, payments, campaigns, recommendations, inventory, and auditing.

---

## 💳 Razorpay Integration

AgentPay uses **Razorpay Standard Checkout APIs** as the payment layer.

The system supports:

1. Server-side calculation of transaction amounts
2. Checkout creation
3. SHA-256 price freezing
4. Authorization and financial policy validation
5. Razorpay checkout/payment execution
6. HMAC-SHA256 webhook verification
7. Immutable transaction auditing

The architecture therefore keeps the AI agent separate from the actual payment authority while still enabling end-to-end autonomous commerce.

---

## 🧠 End-to-End Autonomous Buyer Flow

The included external buyer agent demonstrates machine-to-machine commerce from discovery to payment.

The flow is:

**AI Buyer → Capability Discovery → Authentication → Catalog Search → Product Selection → Checkout → Price Freeze → Policy Authorization → Payment → Audit**

The autonomous buyer first discovers the merchant's capabilities, obtains a scoped access token, searches the catalog, creates a checkout with a frozen price hash, requests policy authorization, and finally executes payment through REST APIs.

This demonstrates that AgentPay is not simply an AI chatbot placed on top of an e-commerce website—it is a **commerce infrastructure layer designed for autonomous agents**.

---

## 📈 Revenue & Business Impact

AgentPay combines agentic commerce with merchant-side AI growth capabilities.

Its recommendation system models a **+2.15% average-order-value uplift** and a **42.5% multi-item attach rate**, while the campaign orchestrator provides merchants with a controlled way to use AI for inventory-driven promotions.

The result is a platform designed around two complementary directions:

* **AI → Merchant**: Agents discover, evaluate, and purchase merchant products.
* **AI → Revenue**: AI helps merchants improve discovery, cross-selling, promotions, and conversion.

---

## 🧪 Reliability & Testing

AgentPay includes a comprehensive automated test suite with **350+ tests**, covering:

* Agentic commerce integration scenarios
* Concurrency and inventory races
* Terminal-state immutability
* Prompt-injection defenses
* SSRF defenses
* Financial-boundary enforcement
* API scope enforcement
* Production frontend build verification

The project also includes a dedicated failure-injection environment for demonstrating how the system behaves under adversarial or inconsistent conditions.

---

## 🌐 Protocol-Ready Design

AgentPay is designed around concepts from emerging agentic commerce standards and protocols, including **NPCI Universal Authenticated Protocol (UAP), Agentic Commerce Protocol (ACP), and AP2**.

Rather than treating AI agents as another frontend, AgentPay treats them as a new class of **authenticated commerce clients** that require machine-readable discovery, scoped permissions, financial authorization, and strong auditability.

---

## 🎯 The Core Idea

Traditional e-commerce asks:
> **“How can we make it easier for humans to buy?”**

Agentic commerce asks:
> **“How can AI agents safely buy on behalf of humans?”**

AgentPay is built to answer the second question.

It combines **AI reasoning + deterministic commerce controls + Razorpay payments + security boundaries + merchant revenue intelligence** into a single platform where autonomous agents can participate in commerce without receiving unrestricted control over money.

### **AI decides what it wants to do.**
### **Deterministic systems decide what it is allowed to do.**
### **Razorpay executes the payment.**
### **The audit ledger records what happened.**

That separation is the foundation of AgentPay's approach to **safe, scalable, and accountable agentic commerce**.


---

## 🏛️ System Architecture Diagram

AgentPay is designed as a **Modular Monolith using Clean Hexagonal Architecture and Domain-Driven Design (DDD)**. Crucially, the AI agent layer has **zero database imports**; it interacts only through a bounded `CommerceFacade` protocol.

```
                    ┌─────────────────────────┐
                    │   External AI Buyer     │
                    │ (Autonomous Client CLI) │
                    └───────────┬─────────────┘
                                │ /.well-known/agent-commerce
                                │ /api/v1/agent/search
                                ▼
┌──────────────────┐    ┌───────────────────────────────────┐
│  Human Shopper   │───▶│         AgentPay Gateway          │
│ (Next.js 14 App) │    │  FastAPI Delivery & Auth Scopes   │
└──────────────────┘    └─────────────────┬─────────────────┘
                                          │
                                          ▼
                        ┌───────────────────────────────────┐
                        │     GuardLLM Prompt Safety        │
                        │ (Heuristic & Meta Llama Guard)    │
                        └─────────────────┬─────────────────┘
                                          │
                                          ▼
                        ┌───────────────────────────────────┐
                        │      CommerceFacade Protocol      │
                        │ (Strict Bounded Agent Execution)  │
                        └─────────────────┬─────────────────┘
                                          │
            ┌─────────────────────────────┼─────────────────────────────┐
            ▼                             ▼                             ▼
┌───────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────┐
│   Checkout & Freeze   │   │     Inventory Manager     │   │   Razorpay Gateway    │
│ SHA-256 Price Hash    │   │ Atomic Lock & Concurrency │   │ Modal / Payment Links │
│ Finite State Machine  │   │  Auto-release on Timeout  │   │ HMAC Webhook Verifier │
└───────────┬───────────┘   └─────────────┬─────────────┘   └───────────┬───────────┘
            │                             │                             │
            └─────────────────────────────┼─────────────────────────────┘
                                          │
                                          ▼
                        ┌───────────────────────────────────┐
                        │    Immutable Append-Only Ledger   │
                        │  Correlation Trace IDs & Auditing │
                        └───────────────────────────────────┘
```

---

## ⚖️ "The Bar" — Security, Boundaries & Failure Handling

| Requirement | Implementation in AgentPay | Evidence / Route |
| :--- | :--- | :--- |
| **Explainable & Gated Money Actions** | Server-calculated integer minor units (paise). Zero floating-point arithmetic. Mandatory confirmation gates for all state-mutating tools. | `services/policy/`, `packages/money/` |
| **Enforced Financial Ceilings** | Hard transaction ceiling (default ₹70,000) and auto-approval limit (default ₹5,000); amounts above require explicit human approval. | `http://localhost:3000/merchant/policy` |
| **Immutable Audit Trail** | Append-only event store recording every prompt assessment, intent extraction, inventory reservation, and payment event with correlation IDs (`trace_id`, `request_id`, `actor_id`). | `http://localhost:3000/merchant/audit` |
| **Graceful Failure Handling** | System deterministically handles inventory contention races, price slippage mid-checkout, mandate expirations, and forged webhooks without inconsistent state. | `http://localhost:3000/scenarios` |

---

## 🖥️ Live Application Surfaces

| Surface | URL | Description |
| :--- | :--- | :--- |
| **Shopper Storefront** | `http://localhost:3000` | E-commerce catalog with AI Shopping Assistant drawer and in-app checkout. |
| **Agent Capability Discovery** | `http://localhost:8000/.well-known/agent-commerce` | Machine-readable capability document for external AI agents. |
| **AI Campaign Orchestrator** | `http://localhost:3000/merchant/campaigns` | Revenue growth agent proposing bounded discount campaigns. |
| **Merchant Policy Manager** | `http://localhost:3000/merchant/policy` | Set transaction ceilings, auto-approval thresholds, and category blocks. |
| **Immutable Audit Explorer** | `http://localhost:3000/merchant/audit` | Live append-only event ledger with distributed trace IDs. |
| **Failure Injection Console** | `http://localhost:3000/scenarios` | Interactive live security playground testing prompt injection and price tampering. |

---

## 🚀 Quick Start & Setup Guide

### Prerequisites
- **Python 3.11 or 3.12**
- **Node.js 18+ & npm**
- *(Optional)* Docker & Docker Compose

---

### Method A: Local Setup (Recommended)

#### 1. Clone the Repository
```bash
git clone https://github.com/ABHISHEK1139/AI-Growth-Agentic-Commerce.git
cd AI-Growth-Agentic-Commerce
```

#### 2. Configure Environment (`.env`)
```bash
cp .env.example .env
```
*(By default, AgentPay runs with offline catalog intelligence and test modes so you can run immediately with zero configuration).*

To enable live Razorpay test credentials or external model providers (Grok, Ollama, Groq):
```env
ALLOW_LIVE_CREDENTIALS=1

# Razorpay Test Mode Credentials
PAYMENT_PROVIDER=razorpay
PAYMENT_IS_TEST_MODE=true
RAZORPAY_KEY_ID=rzp_test_YOUR_KEY_ID
RAZORPAY_KEY_SECRET=YOUR_KEY_SECRET
RAZORPAY_WEBHOOK_SECRET=YOUR_WEBHOOK_SECRET

# Model Configuration (Auto-routed: Grok, Ollama, Groq, or local)
MODEL_PROVIDER=grok
GROK_API_KEY=xai-YOUR_KEY
# For Local Ollama:
# MODEL_PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:11434/v1
```

#### 3. Start the FastAPI Backend
```bash
# Set up Python virtual environment
python -m venv .venv

# Activate:
# On Windows (PowerShell):
.venv\Scripts\activate
# On macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the API gateway (Runs at http://localhost:8000)
uvicorn apps.api.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

#### 4. Start the Next.js Frontend
Open a second terminal window:
```bash
cd apps/web

# Install dependencies
npm install

# Start the development server (Runs at http://localhost:3000)
npm run dev
```

---

### Method B: Docker Compose Setup

```bash
cp .env.example .env
docker compose up --build
```

---

## 🤖 Running the External Autonomous Buyer Agent

To demonstrate real machine-to-machine commerce (Track 01):
```bash
# Ensure your backend is running, then run:
python -m buyer_agent.scenario
```
The autonomous agent will:
1. Fetch machine-readable capabilities from `/.well-known/agent-commerce`
2. Authenticate and mint a scoped bearer token
3. Query catalog offers for laptops under ₹80,000
4. Create a checkout with a frozen price hash
5. Request policy authorization and execute payment via REST APIs

---

## 🧪 Comprehensive Test Suite (350+ Tests)

```bash
# 1. Run Track 01 20-Scenario Integration Suite
pytest tests/integration/test_track1_agentic_commerce_20_scenarios.py -v

# 2. Run 50-Thread Concurrency Chaos & Terminal State Immutability
pytest tests/chaos/test_concurrency_chaos.py -v

# 3. Run Adversarial Prompt Injection & SSRF Defense Suite
pytest tests/security/test_adversarial_empirical_challenge.py -v

# 4. Run Financial Boundary & Scope Enforcement Tests
pytest tests/security/test_financial_boundary_security.py -v

# 5. Run Next.js Production Build Verification
cd apps/web && npm run test:e2e && npm run build
```

---

## 📁 Repository Layout

```
├── apps/
│   ├── api/             # FastAPI modular monolith (routers, auth, middleware)
│   ├── web/             # Next.js 14 App Router storefront & merchant console
│   └── worker/          # Data engineering & catalog seeders
├── buyer-agent/         # Standalone external AI buyer client
├── packages/
│   ├── commerce/        # CommerceFacade protocol (prevents ORM leakage to LLMs)
│   ├── money/           # Strict integer-minor arithmetic (zero float errors)
│   ├── security/        # RBAC roles, tenant scopes, bearer token verification
│   └── schemas/         # Canonical Pydantic V1 API envelopes
├── services/
│   ├── agent/           # GuardLLM prompt safety, tool registry, bounded loop
│   ├── checkout/        # Finite state machine & SHA-256 price hash freeze
│   ├── payments/        # Razorpay adapter, HMAC webhooks, mandate checks
│   ├── campaigns/       # AI Campaign Orchestrator & margin bounds
│   ├── recommendations/ # Catalog-verified cross-sell recommendation engine
│   ├── inventory/       # Atomic stock reservations & release on cancellation
│   └── audit/           # Append-only immutable audit ledger
└── tests/               # 350+ automated tests (chaos, security, contract, unit)
```

---

## 📄 License & Attribution

- **Catalog Dataset**: Amazon Reviews 2023 (McAuley Lab, UC San Diego). Used strictly for non-commercial hackathon demonstration.
- **Protocol References**: Inspired by concepts from NPCI's Universal Authenticated Protocol (UAP), Agentic Commerce Protocol (ACP), and AP2.
- **Payment Processing**: Powered by Razorpay Standard Checkout APIs.
