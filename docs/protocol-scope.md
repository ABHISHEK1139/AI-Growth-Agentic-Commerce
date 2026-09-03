# Protocol Scope and Conceptual Lineage (Requirement 44.4)

This document clarifies AgentPay's architectural relationship to emerging agent protocols.

| Protocol / Concept | Relationship | Implementation Details |
|---|---|---|
| **Agent Communication Protocol (ACP)** | Conceptually Inspired | Machine-readable capability discovery served at `/.well-known/agent-capability.json` declaring scopes and bounds. |
| **Agent-to-Payment Protocol (AP2)** | Structurally Implemented | Cryptographic purchase authorization bound to SHA-256 price hash snapshots and single-use tokens. |
| **x402 (HTTP 402 Payment Required)** | Conceptually Inspired | Standard HTTP error mapping returning structured reason codes and retry hints when authorization or payment is needed. |
| **Model Context Protocol (MCP)** | Structurally Implemented | Strict allowlisted tool definitions with validated argument schemas (`ToolArgumentsV1`). |

---

## Non-Certification Notice

AgentPay is an independent implementation designed for merchant-side autonomy and safety. It is not endorsed, certified, or maintained by OpenAI, Anthropic, Google, or any external standard body.
