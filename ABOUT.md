# 🛡️ About AgentPay

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
