from __future__ import annotations

from pydantic import BaseModel, Field

from backend.execution.errors import NodeExecutionError
from backend.execution.trace import TokenCost
from backend.execution.types import ExecutionContext, NodeResult
from backend.registry.base import InputSlotSpec, OutputSlotSpec
from backend.registry.decorators import register_node
from backend.schema.types import TEXT


class LLMCallConfig(BaseModel):
    connection: str
    model: str
    system_prompt: str = ""
    max_tokens: int = Field(gt=0)


@register_node(
    "llm_call",
    inputs=[InputSlotSpec("prompt", TEXT)],
    outputs=[OutputSlotSpec("response", TEXT)],
    config_model=LLMCallConfig,
)
def execute_llm_call(ctx: ExecutionContext) -> NodeResult:
    config = LLMCallConfig.model_validate(ctx.node.config)
    # The named connection -> real client resolution happens entirely
    # upstream (CLI/API layer, backend/connections/resolver.py) before
    # run_graph is ever called (spec-006 §4) -- this node just looks up
    # its already-built client by name. The None case below is a defensive
    # fallback only: in production, validate_graph()'s missing_connection
    # rule plus resolve_connections() already guarantee this is present.
    client = ctx.resources.get("connections", {}).get(config.connection)
    if client is None:
        raise NodeExecutionError(
            f"No resolved client for connection '{config.connection}' -- "
            "it should have been resolved before this run started"
        )
    try:
        response = client.complete(
            model=config.model,
            system_prompt=config.system_prompt,
            prompt=ctx.inputs["prompt"],
            max_tokens=config.max_tokens,
        )
    except Exception as e:
        raise NodeExecutionError(f"LLM call failed: {e}") from e
    return NodeResult(
        outputs={"response": response.text},
        token_cost=TokenCost(
            input_tokens=response.input_tokens, output_tokens=response.output_tokens
        ),
    )
