import { python } from "@codemirror/lang-python";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useState } from "react";
import { resolveSlots } from "../api/client";
import type { JsonSchemaProperty, SlotInfo } from "../api/types";
import type { GenericFlowNode } from "../canvas/GenericNode";
import { ConnectionPicker } from "./ConnectionPicker";
import { renderPrimitiveField } from "./fieldRenderers";

interface ConfigPanelProps {
  node: GenericFlowNode;
  onConfigChange: (
    nodeId: string,
    config: Record<string, unknown>,
    inputs: SlotInfo[],
    outputs: SlotInfo[],
  ) => void;
}

// Auto-generated from config_schema (the same Pydantic model the backend
// validates against -- ADR-001's one-schema payoff, spec-005 §4) by default.
// `function_source` is the one deliberate special case (spec-005 §7): a real
// multi-line editor instead of a generic single-line input, since that's a
// foreseeable UX problem worth solving directly.
export function ConfigPanel({ node, onConfigChange }: ConfigPanelProps) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(node.data.config ?? {});
    setError(null);
  }, [node.id]);

  const properties = node.data.configSchema.properties ?? {};

  function setField(name: string, value: unknown) {
    setDraft((d) => ({ ...d, [name]: value }));
  }

  async function handleSave() {
    if (!node) return;
    setSaving(true);
    setError(null);
    try {
      if (node.data.dynamicSchema) {
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
          <label htmlFor={`field-${name}`}>{propSchema.title ?? name}</label>
          {renderField(name, propSchema, draft[name], setField)}
        </div>
      ))}
      {error && <div className="config-panel__error">{error}</div>}
      <button type="submit" disabled={saving}>
        {saving ? "Resolving..." : "Save"}
      </button>
    </form>
  );
}

// `function_source` and `connection` are the two deliberate per-field-name
// special cases (spec-005 §7, spec-006 §4): a real multi-line editor and a
// named-connection picker respectively, instead of a generic single-line
// input, since both are foreseeable UX problems worth solving directly.
// Everything else falls through to the shared type-driven renderer.
function renderField(
  name: string,
  propSchema: JsonSchemaProperty,
  value: unknown,
  setField: (name: string, value: unknown) => void,
) {
  if (name === "function_source") {
    return (
      <CodeMirror
        value={typeof value === "string" ? value : ""}
        height="200px"
        extensions={[python()]}
        onChange={(v) => setField(name, v)}
      />
    );
  }

  if (name === "connection") {
    return (
      <ConnectionPicker
        value={typeof value === "string" ? value : undefined}
        onChange={(connectionName) => setField(name, connectionName)}
      />
    );
  }

  return renderPrimitiveField(name, propSchema, value, setField);
}
