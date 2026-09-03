"""JSON Schema registry and deterministic export helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from packages.schemas.v1 import (
    AuthorizationV1,
    CapabilityDocumentV1,
    CheckoutV1,
    IntentV1,
    OfferV1,
    OrderV1,
    PaymentV1,
    ToolArgumentsV1,
)

SCHEMA_MODELS_V1: dict[str, type[BaseModel]] = {
    "intent": IntentV1,
    "offer": OfferV1,
    "checkout": CheckoutV1,
    "authorization": AuthorizationV1,
    "payment": PaymentV1,
    "order": OrderV1,
    "capability_document": CapabilityDocumentV1,
    "tool_arguments": ToolArgumentsV1,
}


def schema_for(name: str) -> dict[str, Any]:
    """Return one versioned public schema by stable artifact name."""
    try:
        model = SCHEMA_MODELS_V1[name]
    except KeyError as exc:
        raise KeyError(f"unknown public schema: {name}") from exc
    schema = model.model_json_schema(mode="validation")
    assert_strict_compatible(schema)
    return schema


def _object_nodes(node: object) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            found.append(node)
        for value in node.values():
            found.extend(_object_nodes(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_object_nodes(value))
    return found


def assert_strict_compatible(schema: dict[str, Any]) -> None:
    """Reject object shapes incompatible with strict structured output.

    Strict OpenAI-compatible schemas close every object and list every property
    in ``required``. Optional semantics are represented by a required nullable
    field rather than by omitting the property.
    """
    for node in _object_nodes(schema):
        properties = node.get("properties", {})
        required = node.get("required", [])
        if node.get("additionalProperties") is not False:
            raise ValueError("strict schemas must set additionalProperties to false")
        if set(required) != set(properties):
            raise ValueError("strict schemas must require every declared property")


def export_json_schemas(output_directory: str | Path) -> tuple[Path, ...]:
    """Write deterministic, versioned JSON Schema artifacts."""
    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in SCHEMA_MODELS_V1:
        path = destination / f"{name}.v1.schema.json"
        payload = json.dumps(schema_for(name), indent=2, sort_keys=True) + "\n"
        path.write_text(payload, encoding="utf-8")
        written.append(path)
    return tuple(written)


JSON_SCHEMAS_V1: dict[str, dict[str, Any]] = {name: schema_for(name) for name in SCHEMA_MODELS_V1}
