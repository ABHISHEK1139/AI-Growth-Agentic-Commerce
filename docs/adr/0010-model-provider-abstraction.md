# ADR-0010: OpenAI-compatible model provider, not a Groq client

- **Status:** Accepted
- **Date:** 2026-08-21
- **Relates to:** Requirement 22, Requirement 38, Task 28, NFR-2
- **Supersedes:** the "Groq-compatible client" wording in the base architecture document

## Context

The model gateway needs a concrete provider. The credential available for development is a
Groq key with a short lifetime (roughly two weeks), which makes the replacement path a
first-class design concern rather than an afterthought.

Writing a `GroqModelProvider` would embed a vendor into the codebase. Every later
migration would then be a code change with its own review and test cycle.

Groq, OpenAI, Together, Fireworks, DeepInfra, OpenRouter, vLLM, LM Studio, and Ollama all
expose the same OpenAI Chat Completions wire format. That format is the de facto standard
for this class of service.

## Decision

Implement **one** provider against the OpenAI-compatible wire format, configured entirely
by environment variables. There is no vendor name in any module, class, or function.

```
MODEL_PROVIDER=openai_compatible    # or: mock
MODEL_BASE_URL=https://api.groq.com/openai/v1
MODEL_API_KEY=...
MODEL_NAME=openai/gpt-oss-120b
```

Transport is plain `httpx`, which is already a pinned dependency. An earlier draft of this
ADR called for the official `openai` SDK; that would be a second HTTP client in the tree
for one route, and the behaviour it brings — timeout, retry budget, token ceiling — is
configuration this system already owns (`MODEL_TIMEOUT_SECONDS`, `MODEL_MAX_RETRIES`,
`MODEL_MAX_TOKENS`) and applies identically to every endpoint.

Switching provider is a configuration change:

| Target | `MODEL_BASE_URL` | `MODEL_NAME` | `MODEL_API_KEY` |
|---|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` | required |
| OpenAI | `https://api.openai.com/v1` | a GPT model id | required |
| Together | `https://api.together.xyz/v1` | a hosted model id | required |
| Local vLLM | `http://localhost:8000/v1` | the served model id | **none** |
| Ollama | `http://localhost:11434/v1` | the pulled model id | **none** |
| llama.cpp server | `http://localhost:8080/v1` | the loaded model id | **none** |

No row is privileged. There is no default endpoint in the code: `MODEL_BASE_URL` is the
only source of a request URL, and a blank one is refused rather than filled in. That was
not true when this ADR was written — the vendor host appeared as a literal in three places
in `services/agent/model.py`, one of them a constructor default, and the `grok` selector
rewrote a configured base URL to a second hardcoded host. A test now scans the module and
fails on any absolute URL.

`mock` remains the default so the golden path and the default test suite need no credential
(Requirement 38.5).

## A credential is required only off this host

`MODEL_API_KEY` is mandatory for an endpoint this deployment does not own and meaningless
for one running beside it. An Ollama or llama.cpp server has nothing to authenticate
against, and sending an empty `Authorization: Bearer` is worse than sending none: the server
accepts it and every log line then records an authenticated call that authenticated nothing.
So the header is omitted entirely when the key is empty.

Startup validation follows the same rule. `validate_for_env()` decides by **parsing the host**
of `MODEL_BASE_URL`, not by substring: `localhost`, `127.0.0.1`, and `::1` need no key, while
anything else — including `https://localhost.example.com/v1`, which contains the word — still
does. A substring test would be a security hole, because a false positive means an
unauthenticated request to a metered host.

The consequence worth stating plainly: **a fully local reasoning model costs nothing per
request.** The hosted path bills every `/api/explore`; the local path bills none of them, at
any volume, and honours the same timeout, retry, and token settings.

## Structured output from a small local model

A 4B model on this host is far likelier than a hosted 70B to answer with prose, or to wrap
the object in a fenced code block. The gateway recovers the object the model actually emitted
— the whole body first, then the first `{...}` span — and **raises** when there is none. It
does not return an empty mapping and it does not supply a field the model omitted.

The failure mode this prevents is specific and quiet: an unreadable body became `None`, then
`{}` one caller up, then an intent with every constraint absent, and `/api/explore` ran a
search with no filters and presented the result as an answer to a question it never parsed.

## Model roles are configuration, not constants

Three distinct roles, three separate settings. None is hardcoded:

| Role | Setting | Development value |
|---|---|---|
| Reasoning and tool selection | `MODEL_NAME` | `openai/gpt-oss-120b` |
| Prompt safety classification | `GUARD_PROVIDER` / `GUARD_MODEL_NAME` | `heuristic` (no model) |
| Content moderation | `MODEL_MODERATION_NAME` | `openai/gpt-oss-safeguard-20b` |

The prompt guard is deliberately **not** configured from the `MODEL_*` family. It once read
`MODEL_API_KEY`, `MODEL_BASE_URL`, and `MODEL_GUARD_NAME`, so enabling the reasoning model
also enabled a second, metered request in front of every query --- two billed calls per
`/api/explore`, one of them 15 tokens of guard. It now has its own provider selector,
endpoint, model, and credential (`GUARD_*`), defaulting to `heuristic`: in-process only, no
network call, no cost. `GUARD_PROVIDER=local` runs Meta Llama Guard over an
OpenAI-compatible endpoint on the same host, also free; `remote` is the billed path and must
be asked for explicitly.

A deployment without a guard model configured falls back to the deterministic
heuristic guard. Prompt safety classification was never the only defence (Requirement 22.3);
the controls that actually hold are tool allowlisting, argument schema validation, the
deterministic policy engine, the confirmation gate, and the absence of any database or
provider path from the agent.

## Provider capability flags, not vendor branches

Compatible endpoints differ in detail. Those differences are declared as capability flags
resolved from configuration, so a new provider is a settings entry rather than an `if`:

| Flag | Why it exists |
|---|---|
| `MODEL_SUPPORTS_JSON_SCHEMA` | whether `response_format: json_schema` is available, versus prompt-guided JSON with local validation |
| `MODEL_STRICT_SCHEMA_REQUIRES_ALL_REQUIRED` | see below |
| `MODEL_SUPPORTS_TOOL_CALLING` | whether native tool calling is available, versus a structured-output fallback |

### Verified against the live endpoint

Groq's `strict: true` structured-output mode rejects a JSON Schema whose `required` array
omits any declared property:

```
invalid JSON schema for response_format: /properties/constraints/required:
`required` is required to be supplied and to be an array including every key in properties
```

This is the same constraint OpenAI documents for its own strict mode, so it is a property of
the standard rather than a Groq quirk. It has a real consequence for the intent schema in
Task 5: **optionality must be expressed as a nullable type, not an absent key.**

```
"max_price_minor": {"type": ["integer", "null"]}     # correct under strict mode
"max_price_minor": {"type": "integer"}                # rejected unless in `required`
```

The schema still sets `additionalProperties: false`, which is what enforces
Requirement 22.2, rejecting unknown financial fields.

## Consequences

- No vendor name appears in `services/` or `packages/`. A migration touches `.env` only.
- Credential rotation, including the expiry of the current development key, is one variable.
- The abstraction is validated by construction: `mock` and `openai_compatible` satisfy the
  same protocol and the same contract test suite, and the real-service tests of
  Requirement 38 skip cleanly when no credential is present.
- Nullable-typed optional fields are slightly more verbose than omitted keys. Accepted: it
  is what the standard requires, and local `jsonschema` validation runs regardless of
  whether the provider enforced anything.
- Adding a provider that lacks structured output means setting one flag and accepting the
  prompt-guided JSON path, which is validated locally either way.
