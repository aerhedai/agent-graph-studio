from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel


@dataclass(frozen=True)
class ConnectionTestResult:
    success: bool
    message: str


@dataclass(frozen=True)
class ToolDefinition:
    """A tool offered to the model during a complete_with_tools call --
    derived from a referenced node's existing schema (spec-008 §5), not
    separately authored."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema shaped: {type, properties, required}


@dataclass(frozen=True)
class ToolCallRequest:
    """One tool call the model requested."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCallResponse:
    text: str | None
    """The model's final answer text. None when tool_calls is non-empty --
    the model requested tool execution instead of answering."""
    tool_calls: list[ToolCallRequest]
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ConnectionDefinition:
    type_name: str
    category: Literal["local", "cloud"]
    config_model: type[BaseModel]
    build_client: Callable[[BaseModel], Any]
    test_connection: Callable[[BaseModel], ConnectionTestResult]
    list_models: Callable[[BaseModel], list[str]] | None = None
    """Optional per-type capability (spec-006 §9): enumerates the real
    models available on this connection's actual backend, e.g. Ollama's
    /api/tags. None for types with no cheap discovery primitive (Anthropic
    today) -- callers must check this before calling GET
    /connections/{name}/models, never assume every type has it."""
    complete_with_tools: Callable[..., ToolCallResponse] | None = None
    """Optional per-type capability (spec-008 §5): a tool-calling-capable
    completion call, given this type's validated config plus (model,
    system_prompt, messages, tools, max_tokens). None for types with no
    tool-calling support wired up. Capability is always checked via
    `complete_with_tools is not None` -- no separate `supports_*` bool
    field, mirroring list_models' own precedent (SPEC-007) exactly, to
    avoid a flag that could drift out of sync with the actual callable."""
    embed: Callable[[BaseModel, str, str], list[float]] | None = None
    """Optional per-type capability (spec-011 §4): given this type's
    validated config, a model name, and a text string, returns that text's
    embedding vector. None for types with no embeddings primitive wired up
    (Anthropic today -- no cheap embeddings endpoint, same precedent as
    list_models). Only `ollama_connection.py` implements this for v1,
    matching this spec's "embeddings: local, via Ollama" design decision.
    Checked via `embed is not None`, same pattern as list_models/
    complete_with_tools -- no separate `supports_*` bool field here either."""


class ConnectionRegistry:
    """Plugin-style registry of connection types, mirroring
    backend/registry/base.py's NodeRegistry exactly -- a class (not a bare
    module-level dict) so it's dependency-injectable for isolated tests."""

    def __init__(self) -> None:
        self._defs: dict[str, ConnectionDefinition] = {}

    def register(self, definition: ConnectionDefinition) -> None:
        if definition.type_name in self._defs:
            raise ValueError(f"Duplicate connection type registration: {definition.type_name}")
        self._defs[definition.type_name] = definition

    def get(self, type_name: str) -> ConnectionDefinition | None:
        return self._defs.get(type_name)

    def all_types(self) -> list[str]:
        return list(self._defs.keys())


default_connection_registry = ConnectionRegistry()


def register_connection_type(
    type_name: str,
    category: Literal["local", "cloud"],
    config_model: type[BaseModel],
    build_client: Callable[[BaseModel], Any],
    test_connection: Callable[[BaseModel], ConnectionTestResult],
    list_models: Callable[[BaseModel], list[str]] | None = None,
    complete_with_tools: Callable[..., ToolCallResponse] | None = None,
    embed: Callable[[BaseModel, str, str], list[float]] | None = None,
    registry: ConnectionRegistry = default_connection_registry,
) -> None:
    """Plain registration call (not a decorator, unlike @register_node) --
    a connection type bundles several things (schema, build_client, test,
    optionally list_models/complete_with_tools/embed) rather than wrapping a
    single execute function. Called as an import side effect at the bottom
    of each connection-type module, same "registration happens on import"
    precedent as the node registry."""
    registry.register(
        ConnectionDefinition(
            type_name=type_name,
            category=category,
            config_model=config_model,
            build_client=build_client,
            test_connection=test_connection,
            list_models=list_models,
            complete_with_tools=complete_with_tools,
            embed=embed,
        )
    )
