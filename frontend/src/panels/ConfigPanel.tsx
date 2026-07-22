import { python } from "@codemirror/lang-python";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useState } from "react";
import { resolveSlots } from "../api/client";
import type { JsonSchemaProperty, SlotInfo } from "../api/types";
import type { GenericFlowNode } from "../canvas/GenericNode";
import { ConnectionPicker } from "./ConnectionPicker";
import { renderPrimitiveField } from "./fieldRenderers";
import { ModelField } from "./ModelField";

interface ConfigPanelProps {
  node: GenericFlowNode;
  onConfigChange: (
    nodeId: string,
    config: Record<string, unknown>,
    inputs: SlotInfo[],
    outputs: SlotInfo[],
  ) => void;
  connectedSubNodes: { slot: string; node: GenericFlowNode }[];
}

// Auto-generated from config_schema (the same Pydantic model the backend
// validates against -- ADR-001's one-schema payoff, spec-005 §4) by default.
// `function_source` is the one deliberate special case (spec-005 §7): a real
// multi-line editor instead of a generic single-line input, since that's a
// foreseeable UX problem worth solving directly.
export function ConfigPanel({ node, onConfigChange, connectedSubNodes }: ConfigPanelProps) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(node.data.config ?? {});
    setError(null);
  }, [node.id]);

  const properties = node.data.configSchema.properties ?? {};
  // spec-014: a config field's own schema already says whether it's
  // required (JsonSchema.required, generated straight from the Pydantic
  // model -- ADR-001's one-schema payoff) -- this was already typed but
  // unused by the frontend until now.
  const requiredFields = new Set(node.data.configSchema.required ?? []);

  function setField(name: string, value: unknown) {
    setDraft((d) => ({ ...d, [name]: value }));
  }

  async function handleSave() {
    if (!node) return;
    setSaving(true);
    setError(null);
    try {
      if (node.data.resolveSlotsFromSubNode) {
        // spec-012: this node's ports mirror a connected sub-node
        // (webhook_trigger's trigger_adapter), not its own config -- there
        // is nothing to re-resolve via POST /resolve-slots (config-based
        // dynamism only). Ports are kept as-is; onConnect already updates
        // them the moment the relevant sub-node edge is drawn.
        onConfigChange(node.id, draft, node.data.inputs, node.data.outputs);
      } else if (node.data.dynamicSchema) {
        // Re-resolve ports for this instance's new config (SPEC-002's
        // resolve_slots, over HTTP) -- e.g. a code node's params change
        // when function_source changes. Only on save/blur, not per
        // keystroke: mcp_call's resolution spawns a real subprocess.
        const resolved = await resolveSlots(node.data.nodeType, draft);
        onConfigChange(node.id, draft, resolved.inputs, resolved.outputs);
      } else {
        onConfigChange(node.id, draft, node.data.inputs, node.data.outputs);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="config-panel"
      onSubmit={(e) => {
        e.preventDefault();
        void handleSave();
      }}
    >
      {Object.entries(properties).map(([name, propSchema]) => (
        <div key={name} className="config-panel__field">
          <label htmlFor={`field-${name}`}>
            {propSchema.title ?? name}
            {!requiredFields.has(name) && <span className="config-panel__optional-tag">optional</span>}
          </label>
          {renderField(name, propSchema, draft[name], setField, draft)}
        </div>
      ))}
      {error && <div className="config-panel__error">{error}</div>}
      <button type="submit" className="btn btn--primary" disabled={saving}>
        {saving ? "Resolving..." : "Save"}
      </button>

      {connectedSubNodes.length > 0 && (
        <div className="config-panel__sub-nodes">
          <h3 className="config-panel__sub-nodes-heading">Connected sub-nodes</h3>
          <p className="config-panel__sub-nodes-hint">
            Read-only -- click the node on canvas to edit its settings.
          </p>
          {connectedSubNodes.map(({ slot, node: subNode }) => (
            <div key={`${slot}-${subNode.id}`} className="config-panel__sub-node-summary">
              <div className="config-panel__sub-node-summary-header">
                <span className="config-panel__sub-node-slot">{slot}</span>
                <span className="config-panel__sub-node-type">{subNode.data.nodeType}</span>
              </div>
              <dl className="config-panel__sub-node-summary-fields">
                {Object.entries(subNode.data.config).map(([key, value]) => (
                  <div key={key} className="config-panel__sub-node-summary-field">
                    <dt>{key}</dt>
                    <dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      )}
    </form>
  );
}

// `function_source`, `connection`, and `model` are the deliberate per-
// field-name special cases (spec-005 §7, spec-006 §4/§9): a real multi-line
// editor, a named-connection picker, and (when the selected connection
// supports it) a live model dropdown, instead of a generic single-line
// input, since all three are foreseeable UX problems worth solving
// directly. Everything else falls through to the shared type-driven
// renderer. `draft` (the whole in-progress config, not just this field's
// value) is threaded through so `model` can read the sibling `connection`
// field -- a small, general widening rather than a model-specific hack, so
// any future field needing cross-field context gets it for free.
function renderField(
  name: string,
  propSchema: JsonSchemaProperty,
  value: unknown,
  setField: (name: string, value: unknown) => void,
  draft: Record<string, unknown>,
) {
  if (name === "function_source") {
    return (
      <CodeMirror
        value={typeof value === "string" ? value : ""}
        height="200px"
        theme="dark"
        extensions={[python()]}
        onChange={(v) => setField(name, v)}
      />
    );
  }

  // spec-018: mirrors backend/connections/resolver.py's
  // connection_reference_names() rule exactly -- any field that is
  // literally "connection" or ends with "_connection" (bot_token_connection,
  // embedding_model_connection, ...) is a connection reference and gets the
  // real picker, not a plain text box. Was previously an exact match on
  // "connection" only, which is what let a real bot token get typed
  // directly into a graph JSON file (no picker existed to make the
  // reference-vs-value distinction obvious).
  if (name === "connection" || name.endsWith("_connection")) {
    return (
      <ConnectionPicker
        value={typeof value === "string" ? value : undefined}
        onChange={(connectionName) => setField(name, connectionName)}
      />
    );
  }

  if (name === "model") {
    return (
      <ModelField
        value={value}
        onChange={(v) => setField(name, v)}
        connectionName={typeof draft.connection === "string" ? draft.connection : undefined}
      />
    );
  }

  return renderPrimitiveField(name, propSchema, value, setField);
}
