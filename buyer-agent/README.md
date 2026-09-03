# AgentPay Independent Buyer Agent

An autonomous buyer client for AgentPay gateways.

## Guarantees & Non-Privilege Statement

- **No Shared Database / Internal Code**: This client imports no internal repository modules, no database drivers, and no private gateway utilities.
- **Strict Public API**: All operations occur through the public JSON HTTP endpoints (`/.well-known/agent-capability.json`, `/api/v1/auth/tokens`, `/api/v1/agent/*`).
- **Autonomous Gating**: Human approval is conducted in the client's own environment before payment token invocation.
- **Idempotency**: All mutating operations include client-generated idempotency keys.
