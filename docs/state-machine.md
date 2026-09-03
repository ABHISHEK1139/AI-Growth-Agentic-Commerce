# AgentPay — State Machine & Transaction Lifecycle

## 1. Checkout State Machine

The AgentPay checkout state machine enforces strict, unidirectional, deterministic transitions for all commerce transactions. Every transition validates preconditions, revalidates price snapshot integrity, locks/releases inventory, and appends an immutable audit event.

```mermaid
stateDiagram-v2
    [*] --> CREATED: Create Checkout (Holds Inventory)
    CREATED --> POLICY_CHECKED: Evaluate Merchant Policy
    
    POLICY_CHECKED --> AUTHORIZED: Auto-Approved (Below Limit)
    POLICY_CHECKED --> AUTHORIZATION_REQUESTED: Above Limit (Human-in-Loop)
    
    AUTHORIZATION_REQUESTED --> AUTHORIZED: Buyer Grants Authorization
    AUTHORIZATION_REQUESTED --> AUTHORIZATION_REJECTED: Buyer Rejects
    
    AUTHORIZED --> PAYMENT_PENDING: Create Payment Order
    PAYMENT_PENDING --> PAID: Razorpay Payment Verified
    PAYMENT_PENDING --> PAYMENT_FAILED: Provider / Card Failure
    
    PAID --> ORDER_CONFIRMED: Commit Inventory & Generate Order
    
    AUTHORIZATION_REJECTED --> CANCELLED: Release Inventory
    PAYMENT_FAILED --> CANCELLED: Release Inventory
    CREATED --> EXPIRED: Sweep Expired (TTL Exceeded)
    POLICY_CHECKED --> EXPIRED: Sweep Expired
    AUTHORIZED --> EXPIRED: Sweep Expired
    EXPIRED --> [*]: Inventory Released
    CANCELLED --> [*]: Final State
    ORDER_CONFIRMED --> [*]: Final State
```

---

## 2. States Specification

| State | Category | Description | Permitted Next Transitions |
|---|---|---|---|
| `CREATED` | Initial | Checkout created from an offer. Inventory held with conditional SQL lock. | `POLICY_CHECKED`, `CANCELLED`, `EXPIRED` |
| `POLICY_CHECKED` | In-Flight | Merchant rules & budget checks evaluated. | `AUTHORIZED`, `AUTHORIZATION_REQUESTED`, `CANCELLED`, `EXPIRED` |
| `AUTHORIZATION_REQUESTED` | In-Flight | Transaction amount exceeds auto-approval threshold; awaiting buyer PIN/signature. | `AUTHORIZED`, `AUTHORIZATION_REJECTED`, `EXPIRED` |
| `AUTHORIZED` | In-Flight | Authorization granted and valid mandate token bound. | `PAYMENT_PENDING`, `CANCELLED`, `EXPIRED` |
| `PAYMENT_PENDING` | In-Flight | Payment order created on Razorpay; awaiting settlement or webhook. | `PAID`, `PAYMENT_FAILED`, `EXPIRED` |
| `PAID` | Pre-Final | Webhook or checkout signature verified via constant-time HMAC-SHA256. | `ORDER_CONFIRMED` |
| `ORDER_CONFIRMED` | Terminal | Inventory committed, permanent `orders` record created. | None |
| `AUTHORIZATION_REJECTED` | Terminal | Buyer refused authorization. Held inventory released immediately. | None |
| `PAYMENT_FAILED` | Terminal | Payment declined or timed out. Held inventory released. | None |
| `CANCELLED` | Terminal | Explicit buyer or system cancellation. Held inventory released. | None |
| `EXPIRED` | Terminal | Checkout or authorization TTL passed without finalization. Background sweeper releases stock. | None |

---

## 3. Inventory Reservation Guarantees

1. **Reservation on Checkout**: Single atomic UPDATE guarded by version token and quantity:
   ```sql
   UPDATE inventory
      SET reserved_quantity = reserved_quantity + :qty,
          version = version + 1
    WHERE offer_id = :offer_id
      AND (available_quantity - reserved_quantity) >= :qty;
   ```
2. **Commit on Verification**: Both available and reserved quantities decremented upon `ORDER_CONFIRMED`.
3. **Automatic Release on Termination**: Transitions to `CANCELLED`, `EXPIRED`, `AUTHORIZATION_REJECTED`, or `PAYMENT_FAILED` release the held reservation idempotently.